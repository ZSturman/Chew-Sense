import CryptoKit
import Foundation

// The home-server mailbox, part of the one phone ↔ Mac client (see LoopLink and
// docs/COMMUNICATION.md). Recordings go straight to the Mac when it answers.
// Only when it doesn't does Collect deposit an encrypted copy on the Pi; the Mac
// collects it when it wakes. The phone learns the address, token and key from
// its paired Mac's pinned /v1/status. There is nothing to set up on the phone.

/// What the paired Mac offers. Kept in this app's keychain.
nonisolated struct LoopMailboxConfig: Codable, Equatable, Sendable {
    var url: String
    var token: String
    var key: String
    var keyID: String?
    /// Only for isolated tests with a self-signed server; the Pi has a public certificate.
    var fingerprint: String?

    static func parse(_ value: Any?) -> LoopMailboxConfig? {
        guard let item = value as? [String: Any], let url = item["url"] as? String, url.hasPrefix("https://"),
              let token = item["token"] as? String, let key = item["key"] as? String,
              Data(base64Encoded: key)?.count == 32 else { return nil }
        return LoopMailboxConfig(url: url, token: token, key: key, keyID: item["key_id"] as? String,
                                 fingerprint: item["fingerprint"] as? String)
    }

    var symmetricKey: SymmetricKey? { Data(base64Encoded: key).map(SymmetricKey.init(data:)) }
}

@MainActor
final class LoopMailbox {
    private(set) var config: LoopMailboxConfig?
    private let keychainService: String
    private var session: URLSession?
    private var pin: PinnedTrust?

    init(keychainService: String) {
        self.keychainService = keychainService + ".mailbox"
        config = Keychain.token(service: self.keychainService)
            .flatMap { try? JSONDecoder().decode(LoopMailboxConfig.self, from: Data($0.utf8)) }
    }

    var isConfigured: Bool { config != nil }

    /// `value` is the `mailbox` field of the Mac's status: an offer, or null to forget it.
    func learn(_ value: Any?) {
        if value is NSNull {
            forget()
            return
        }
        guard let offered = LoopMailboxConfig.parse(value), offered != config,
              let encoded = try? JSONEncoder().encode(offered),
              (try? Keychain.save(token: String(decoding: encoded, as: UTF8.self), service: keychainService)) != nil
        else { return }
        config = offered
        session = nil
    }

    func forget() {
        Keychain.delete(service: keychainService)
        config = nil
        session = nil
    }

    private func urlSession(_ config: LoopMailboxConfig) -> URLSession {
        if let session { return session }
        // System trust for the Pi's public certificate; a pin only when the Mac supplied one.
        let made: URLSession
        if let fingerprint = config.fingerprint {
            let trust = PinnedTrust()
            trust.expected = fingerprint
            pin = trust
            made = URLSession(configuration: .ephemeral, delegate: trust, delegateQueue: nil)
        } else {
            made = URLSession(configuration: .ephemeral)
        }
        session = made
        return made
    }

    /// Sends a request under `/api/v1/chewsense/`. Returns the status and JSON for
    /// 2xx and 409 (offset and checksum conflicts carry details the caller uses).
    func request(_ method: String, _ path: String, json: [String: Any]? = nil, body: Data? = nil,
                 headers: [String: String] = [:], timeout: TimeInterval = 30) async throws -> (Int, [String: Any]) {
        guard let config, let base = URL(string: config.url) else { throw LoopLink.Failure.notPaired }
        var request = URLRequest(url: base.appendingPathComponent("api/v1/chewsense/" + path), timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization")
        request.setValue("1", forHTTPHeaderField: "Home-Services-Contract")
        if let json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        } else if let body {
            request.httpBody = body
            request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        }
        headers.forEach { request.setValue($1, forHTTPHeaderField: $0) }
        do {
            let (data, response) = try await urlSession(config).data(for: request)
            guard let http = response as? HTTPURLResponse else { throw LoopLink.Failure.invalidResponse }
            let object = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            guard (200..<300).contains(http.statusCode) || http.statusCode == 409 else {
                let message = (object["error"] as? [String: Any])?["message"] as? String
                throw LoopLink.Failure.http(http.statusCode, message ?? "request failed")
            }
            return (http.statusCode, object)
        } catch let error as URLError where error.code == .cancelled && (pin?.pinFailed ?? false) {
            throw LoopLink.Failure.pinMismatch
        }
    }

    /// Home Services timestamps, with or without fractional seconds.
    nonisolated static func date(_ text: String?) -> Date? {
        guard let text else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: text)
    }

    /// The status copy the Mac last published, for showing while it sleeps.
    func status() async throws -> (status: [String: Any], sessions: [String: String], updatedAt: String?) {
        let (_, object) = try await request("GET", "status", timeout: 8)
        let copy = object["status"] as? [String: Any] ?? [:]
        return (copy["status"] as? [String: Any] ?? [:], copy["sessions"] as? [String: String] ?? [:],
                object["updated_at"] as? String)
    }
}

/// Encrypts an archive for the mailbox (format `CSMB` v1, read by `mailbox.py`):
/// a header of magic, version and an 8-byte random nonce prefix, then records of
/// a 4-byte big-endian length and AES-256-GCM ciphertext and tag. Each record's
/// nonce is the prefix plus its index; its AAD is the header, index and a final
/// flag, so a cut-short file fails to decrypt.
nonisolated enum LoopSeal {
    static let magic = Data("CSMB".utf8) + Data([1])
    static let chunk = 4 * 1024 * 1024

    static func seal(_ source: URL, to target: URL, key: SymmetricKey, chunkSize: Int = chunk) throws {
        let prefix = Data((0..<8).map { _ in UInt8.random(in: .min ... .max) })
        let header = magic + prefix
        FileManager.default.createFile(atPath: target.path, contents: nil)
        let input = try FileHandle(forReadingFrom: source)
        let output = try FileHandle(forWritingTo: target)
        defer { try? input.close(); try? output.close() }
        try output.write(contentsOf: header)
        var index: UInt32 = 0
        var current = try input.read(upToCount: chunkSize) ?? Data()
        while true {
            let following = current.isEmpty ? Data() : (try input.read(upToCount: chunkSize) ?? Data())
            let final = following.isEmpty
            let aad = header + bigEndian(index) + Data([final ? 1 : 0])
            let nonce = try AES.GCM.Nonce(data: prefix + bigEndian(index))
            let box = try AES.GCM.seal(current, using: key, nonce: nonce, authenticating: aad)
            let sealed = box.ciphertext + box.tag
            try output.write(contentsOf: bigEndian(UInt32(sealed.count)) + sealed)
            if final { return }
            index += 1
            current = following
        }
    }

    private static func bigEndian(_ value: UInt32) -> Data {
        withUnsafeBytes(of: value.bigEndian) { Data($0) }
    }
}
