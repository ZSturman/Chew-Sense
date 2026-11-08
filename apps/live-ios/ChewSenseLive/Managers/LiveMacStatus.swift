import Combine
import CryptoKit
import Foundation
import Network
#if canImport(UIKit)
import UIKit
#endif

/// Read-only link to the ChewSense loop service on the Mac, so Live shows the
/// same "where we are" message as Collect and Review. Live pairs as its own
/// device with the same six-digit code flow as Collect, but only reads
/// `GET /v1/status`; it never uploads recordings.
final class LiveMacStatus: ObservableObject {
    struct Paired: Codable, Equatable {
        var name: String
        var host: String
        var port: Int
        var fingerprint: String
    }

    struct Discovered: Identifiable, Hashable {
        var name: String
        var endpoint: NWEndpoint
        var id: String { name }
    }

    enum State: Equatable {
        case unpaired
        case connecting
        case connected
        case offline(String)
    }

    enum LinkError: LocalizedError {
        case notPaired, pinMismatch, http(Int, String), invalidResponse, pairingFailed(String)

        var errorDescription: String? {
            switch self {
            case .notPaired: "Pair Live with the Mac first."
            case .pinMismatch: "The Mac's certificate changed. Pair again to trust it."
            case .http(401, _): "The Mac no longer recognizes Live. Pair again."
            case .http(let code, let message): "Mac responded \(code): \(message)"
            case .invalidResponse: "Unexpected response from the Mac."
            case .pairingFailed(let reason): reason
            }
        }
    }

    @Published private(set) var paired: Paired?
    @Published private(set) var state: State = .unpaired
    @Published private(set) var project: ProjectStatus?
    @Published private(set) var discovered: [Discovered] = []
    @Published private(set) var discoveryError: String?

    private static let pairedKey = "LiveLoopPairedMac"
    private static let projectKey = "LiveLoopProjectStatus"
    private static let deviceKey = "LiveLoopDeviceID"
    private let defaults: UserDefaults
    private let trust = PinnedTrust()
    private var session: URLSession!
    private var browser: NWBrowser?

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        session = URLSession(configuration: .ephemeral, delegate: trust, delegateQueue: nil)
        if let data = defaults.data(forKey: Self.pairedKey),
           let stored = try? JSONDecoder().decode(Paired.self, from: data), Keychain.token() != nil {
            paired = stored
            state = .connecting
        }
        // The last received message stays visible offline, marked as such.
        project = defaults.data(forKey: Self.projectKey).flatMap { try? ProjectStatus.decode($0) }
    }

    var isCurrent: Bool { state == .connected }

    private var deviceID: String {
        if let id = defaults.string(forKey: Self.deviceKey) { return id }
        let id = UUID().uuidString.lowercased()
        defaults.set(id, forKey: Self.deviceKey)
        return id
    }

    private var deviceName: String {
        #if canImport(UIKit)
        UIDevice.current.name + " (Live)"
        #else
        "Live"
        #endif
    }

    // MARK: Discovery

    func startDiscovery() {
        guard browser == nil else { return }
        discoveryError = nil
        let parameters = NWParameters.tcp
        parameters.includePeerToPeer = false
        let browser = NWBrowser(for: .bonjour(type: "_chewsense._tcp", domain: nil), using: parameters)
        browser.stateUpdateHandler = { [weak self] state in
            switch state {
            case .waiting(let error), .failed(let error):
                // kDNSServiceErr_PolicyDenied from Apple's dns_sd.h.
                if case .dns(-65570) = error {
                    self?.discoveryError = "Local Network access is blocked. Open Settings and enable Local Network for Live, then return here."
                } else {
                    self?.discoveryError = "Mac discovery is unavailable: \(error.localizedDescription). Check that the iPhone and Mac are on the same Wi-Fi, or enter the Mac's address below."
                }
            case .ready:
                self?.discoveryError = nil
            default:
                break
            }
        }
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            let found = results.compactMap { result -> Discovered? in
                if case let .service(name, _, _, _) = result.endpoint { return Discovered(name: name, endpoint: result.endpoint) }
                return nil
            }
            DispatchQueue.main.async { self?.discovered = found.sorted { $0.name < $1.name } }
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    func stopDiscovery() {
        browser?.cancel()
        browser = nil
    }

    /// Resolves a Bonjour service to an IPv4 host and port.
    func resolve(_ service: Discovered) async throws -> (String, Int) {
        let parameters = NWParameters.tcp
        (parameters.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options)?.version = .v4
        let connection = NWConnection(to: service.endpoint, using: parameters)
        let once = ResumeOnce()
        return try await withCheckedThrowingContinuation { continuation in
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    guard once.claim() else { return }
                    if case let .hostPort(host, port)? = connection.currentPath?.remoteEndpoint {
                        var text = "\(host)"
                        if let percent = text.firstIndex(of: "%") { text = String(text[..<percent]) }
                        continuation.resume(returning: (text, Int(port.rawValue)))
                    } else {
                        continuation.resume(throwing: LinkError.invalidResponse)
                    }
                    connection.cancel()
                case .failed(let error):
                    guard once.claim() else { return }
                    continuation.resume(throwing: error)
                    connection.cancel()
                default: break
                }
            }
            connection.start(queue: .global())
        }
    }

    // MARK: Pairing (same proofs as Collect's MacConnection)

    func pair(host: String, port: Int, code: String, name: String) async throws {
        let trimmed = code.filter(\.isNumber)
        guard trimmed.count == 6 else { throw LinkError.pairingFailed("Enter the six-digit code shown on the Mac.") }
        trust.expected = nil  // Observe the certificate actually presented.
        let base = try Self.baseURL(host: host, port: port)
        _ = try await send("GET", base.appendingPathComponent("v1/hello"), token: nil)
        guard let fingerprint = trust.observed else { throw LinkError.invalidResponse }
        let device = deviceID
        let proof = Self.hmac(trimmed, "pair|\(device)|\(fingerprint)")
        let body = try JSONSerialization.data(withJSONObject: [
            "device_id": device, "device_name": deviceName, "proof": proof])
        let data: Data
        do {
            data = try await send("POST", base.appendingPathComponent("v1/pair"), token: nil, body: body)
        } catch LinkError.http(let code, let message) where code == 403 {
            throw LinkError.pairingFailed(message)
        }
        guard let reply = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = reply["device_token"] as? String,
              let serverProof = reply["server_proof"] as? String,
              reply["fingerprint"] as? String == fingerprint,
              serverProof == Self.hmac(trimmed, "server|\(device)|\(fingerprint)|\(token)") else {
            throw LinkError.pairingFailed("The Mac could not prove it knows the pairing code.")
        }
        try Keychain.save(token: token)
        let record = Paired(name: name, host: host, port: port, fingerprint: fingerprint)
        defaults.set(try JSONEncoder().encode(record), forKey: Self.pairedKey)
        trust.expected = fingerprint
        paired = record
        await refresh()
    }

    func forget() {
        Keychain.delete()
        defaults.removeObject(forKey: Self.pairedKey)
        defaults.removeObject(forKey: Self.projectKey)
        paired = nil
        project = nil
        state = .unpaired
    }

    // MARK: Status

    /// Fetches the shared project status. Safe to call often.
    func refresh() async {
        guard let paired, let token = Keychain.token() else { state = .unpaired; return }
        if state != .connected { state = .connecting }
        do {
            trust.expected = paired.fingerprint
            let url = try Self.baseURL(host: paired.host, port: paired.port).appendingPathComponent("v1/status")
            let data = try await send("GET", url, token: token)
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            if let shared = (object?["pipeline"] as? [String: Any])?["project"] {
                let encoded = try JSONSerialization.data(withJSONObject: shared)
                project = try ProjectStatus.decode(encoded)
                defaults.set(encoded, forKey: Self.projectKey)
            }
            state = .connected
        } catch {
            state = .offline((error as? LocalizedError)?.errorDescription ?? error.localizedDescription)
        }
    }

    private func send(_ method: String, _ url: URL, token: String?, body: Data? = nil) async throws -> Data {
        var request = URLRequest(url: url, timeoutInterval: 20)
        request.httpMethod = method
        request.httpBody = body
        request.setValue(body == nil ? nil : "application/json", forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw LinkError.invalidResponse }
            guard (200..<300).contains(http.statusCode) else {
                let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                throw LinkError.http(http.statusCode, message ?? "request failed")
            }
            return data
        } catch let error as URLError where error.code == .cancelled && trust.pinFailed {
            throw LinkError.pinMismatch
        }
    }

    static func baseURL(host: String, port: Int) throws -> URL {
        var components = URLComponents()
        components.scheme = "https"
        components.host = host
        components.port = port
        guard let url = components.url else { throw LinkError.invalidResponse }
        return url
    }

    static func hmac(_ key: String, _ message: String) -> String {
        let mac = HMAC<SHA256>.authenticationCode(for: Data(message.utf8), using: SymmetricKey(data: Data(key.utf8)))
        return mac.map { String(format: "%02x", $0) }.joined()
    }
}

/// Resumes a continuation at most once from Network's state callbacks.
nonisolated private final class ResumeOnce: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false

    func claim() -> Bool {
        lock.withLock {
            defer { done = true }
            return !done
        }
    }
}
