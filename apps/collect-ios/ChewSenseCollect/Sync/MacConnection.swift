import Foundation
import Combine
import CryptoKit
import Network
import Security
import UIKit

/// Trusted link to the ChewSense loop service on the Mac.
///
/// * Discovery: Bonjour `_chewsense._tcp`, or a manually entered address.
/// * Pairing: the Mac shows a six-digit code. The phone proves it knows the
///   code *and* which certificate it is talking to:
///   `HMAC-SHA256(code, "pair|device|fingerprint")`. The Mac answers with its
///   own proof, so both sides confirm before a token is stored.
/// * Afterwards every request requires the pinned certificate fingerprint.
final class MacConnection: NSObject, ObservableObject {
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
        case connected(pending: Int)
        case offline(String)
    }

    enum ConnectionError: LocalizedError {
        case notPaired, pinMismatch, http(Int, String), invalidResponse, pairingFailed(String)

        var errorDescription: String? {
            switch self {
            case .notPaired: "Pair this iPhone with the Mac first."
            case .pinMismatch: "The Mac's certificate changed. Pair again to trust it."
            case .http(let code, let message): "Mac responded \(code): \(message)"
            case .invalidResponse: "Unexpected response from the Mac."
            case .pairingFailed(let reason): reason
            }
        }
    }

    @Published private(set) var paired: Paired?
    @Published private(set) var state: State = .unpaired
    @Published private(set) var discovered: [Discovered] = []
    @Published private(set) var discoveryError: String?
    @Published private(set) var lastSync: Date?
    @Published private(set) var pipeline: PipelineStatus?

    struct PipelineStatus: Decodable {
        struct Goal: Decodable, Identifiable {
            let id: String
            let title: String
            let current: Int
            let target: Int
        }
        struct Stage: Decodable, Identifiable {
            let id: String
            let title: String
            let state: String
            let detail: String
        }
        struct Action: Decodable, Identifiable {
            let id: String
            let kind: String
            let destination: String
            let title: String
            let detail: String
            let experimentId: String?
            let sessionId: String?
        }
        let reviewTasks: Int
        let attentionCount: Int
        let trainingReady: Bool
        let researchTargetsMet: Bool
        let motionAssessmentPassed: Bool?
        let blockers: [String]
        let nextAction: String
        let goals: [Goal]?
        let stages: [Stage]?
        let actions: [Action]?
        let receivedSessions: Int?
        let completeSessions: Int?
        let incompleteSessions: Int?
        let availableReviewTasks: Int?
        let nextExperimentId: String?
    }

    private let defaultsKey = "LoopPairedMac"
    private var browser: NWBrowser?
    private var session: URLSession!
    private let trust = PinnedTrust()
    let catalog: ExperimentCatalogStore

    init(catalog: ExperimentCatalogStore) {
        self.catalog = catalog
        super.init()
        session = URLSession(configuration: .ephemeral, delegate: trust, delegateQueue: nil)
        if let data = UserDefaults.standard.data(forKey: defaultsKey),
           let stored = try? JSONDecoder().decode(Paired.self, from: data), Keychain.token() != nil {
            paired = stored
            state = .connecting
        }
    }

    static var deviceID: String {
        if let id = UserDefaults.standard.string(forKey: "LoopDeviceID") { return id }
        let id = UUID().uuidString.lowercased()
        UserDefaults.standard.set(id, forKey: "LoopDeviceID")
        return id
    }

    // MARK: Discovery

    static func discoveryFailureDescription(_ error: NWError) -> String {
        // kDNSServiceErr_PolicyDenied from Apple's dns_sd.h.
        if case .dns(-65570) = error {
            return "Local Network access is blocked. Open Settings and enable Local Network for Chew Sense Collect and Label, then return here."
        }
        return "Mac discovery is unavailable: \(error.localizedDescription). Check that the iPhone and Mac are on the same Wi-Fi, or enter the Mac's address below."
    }

    func startDiscovery() {
        guard browser == nil, !CollectionTestMode.isEnabled else { return }
        discoveryError = nil
        let parameters = NWParameters.tcp
        parameters.includePeerToPeer = false
        let browser = NWBrowser(for: .bonjour(type: "_chewsense._tcp", domain: nil), using: parameters)
        browser.stateUpdateHandler = { [weak self] state in
            switch state {
            case .waiting(let error), .failed(let error):
                self?.discoveryError = Self.discoveryFailureDescription(error)
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
        return try await withCheckedThrowingContinuation { continuation in
            var resumed = false
            connection.stateUpdateHandler = { state in
                guard !resumed else { return }
                switch state {
                case .ready:
                    resumed = true
                    if case let .hostPort(host, port)? = connection.currentPath?.remoteEndpoint {
                        var text = "\(host)"
                        if let percent = text.firstIndex(of: "%") { text = String(text[..<percent]) }
                        continuation.resume(returning: (text, Int(port.rawValue)))
                    } else {
                        continuation.resume(throwing: ConnectionError.invalidResponse)
                    }
                    connection.cancel()
                case .failed(let error):
                    resumed = true
                    continuation.resume(throwing: error)
                    connection.cancel()
                default: break
                }
            }
            connection.start(queue: .global())
        }
    }

    // MARK: Pairing

    func pair(host: String, port: Int, code: String, name: String) async throws {
        let trimmed = code.filter(\.isNumber)
        guard trimmed.count == 6 else { throw ConnectionError.pairingFailed("Enter the six-digit code shown on the Mac.") }
        trust.expected = nil  // Observe the certificate actually presented.
        let base = try Self.baseURL(host: host, port: port)
        _ = try await send("GET", base.appendingPathComponent("v1/hello"), token: nil)
        guard let fingerprint = trust.observed else { throw ConnectionError.invalidResponse }
        let device = Self.deviceID
        let proof = Self.hmac(trimmed, "pair|\(device)|\(fingerprint)")
        let body = try JSONSerialization.data(withJSONObject: [
            "device_id": device, "device_name": UIDevice.current.name, "proof": proof])
        let data: Data
        do {
            data = try await send("POST", base.appendingPathComponent("v1/pair"), token: nil, body: body)
        } catch ConnectionError.http(let code, let message) where code == 403 {
            throw ConnectionError.pairingFailed(message)
        }
        guard let reply = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = reply["device_token"] as? String,
              let serverProof = reply["server_proof"] as? String,
              reply["fingerprint"] as? String == fingerprint,
              serverProof == Self.hmac(trimmed, "server|\(device)|\(fingerprint)|\(token)") else {
            throw ConnectionError.pairingFailed("The Mac could not prove it knows the pairing code.")
        }
        try Keychain.save(token: token)
        let record = Paired(name: name, host: host, port: port, fingerprint: fingerprint)
        UserDefaults.standard.set(try JSONEncoder().encode(record), forKey: defaultsKey)
        trust.expected = fingerprint
        paired = record
        await refresh()
    }

    func forget() {
        Keychain.delete()
        UserDefaults.standard.removeObject(forKey: defaultsKey)
        paired = nil
        pipeline = nil
        state = .unpaired
    }

    // MARK: API

    func request(_ method: String, _ path: String, query: String? = nil, body: Data? = nil) async throws -> Data {
        guard let paired, let token = Keychain.token() else { throw ConnectionError.notPaired }
        trust.expected = paired.fingerprint
        var url = try Self.baseURL(host: paired.host, port: paired.port).appendingPathComponent(path)
        if let query, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            components.query = query
            url = components.url ?? url
        }
        return try await send(method, url, token: token, body: body)
    }

    /// Fetches status and the catalog. Safe to call often.
    func refresh() async {
        guard paired != nil else { state = .unpaired; return }
        if case .offline = state {} else if case .connected = state {} else { state = .connecting }
        do {
            let status = try JSONSerialization.jsonObject(with: try await request("GET", "v1/status")) as? [String: Any]
            if let summary = status?["pipeline"] as? [String: Any] {
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                pipeline = try decoder.decode(PipelineStatus.self, from: JSONSerialization.data(withJSONObject: summary))
            }
            let since = catalog.source == .bundledSeed ? nil : "since=\(catalog.catalog.catalogVersion)"
            let data = try await request("GET", "v1/catalog", query: since)
            if let object = try JSONSerialization.jsonObject(with: data) as? [String: Any], object["unchanged"] == nil {
                try catalog.apply(data)
            }
            lastSync = Date()
            state = .connected(pending: status?["pending"] as? Int ?? 0)
        } catch {
            state = .offline((error as? LocalizedError)?.errorDescription ?? error.localizedDescription)
        }
    }

    private func send(_ method: String, _ url: URL, token: String?, body: Data? = nil) async throws -> Data {
        var request = URLRequest(url: url, timeoutInterval: 30)
        request.httpMethod = method
        request.httpBody = body
        request.setValue(body == nil ? nil : "application/json", forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw ConnectionError.invalidResponse }
            guard (200..<300).contains(http.statusCode) || http.statusCode == 409 else {
                let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                throw ConnectionError.http(http.statusCode, message ?? "request failed")
            }
            return data
        } catch let error as URLError where error.code == .cancelled && trust.pinFailed {
            throw ConnectionError.pinMismatch
        }
    }

    static func baseURL(host: String, port: Int) throws -> URL {
        var components = URLComponents()
        components.scheme = "https"
        components.host = host
        components.port = port
        guard let url = components.url else { throw ConnectionError.invalidResponse }
        return url
    }

    static func hmac(_ key: String, _ message: String) -> String {
        let mac = HMAC<SHA256>.authenticationCode(for: Data(message.utf8), using: SymmetricKey(data: Data(key.utf8)))
        return mac.map { String(format: "%02x", $0) }.joined()
    }
}

/// Accepts only the pinned self-signed certificate; with no pin (pairing), it
/// records the presented fingerprint for the HMAC binding.
nonisolated final class PinnedTrust: NSObject, URLSessionDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var _expected: String?
    private var _observed: String?
    private var _pinFailed = false

    var expected: String? {
        get { lock.withLock { _expected } }
        set { lock.withLock { _expected = newValue; _pinFailed = false } }
    }
    var observed: String? { lock.withLock { _observed } }
    var pinFailed: Bool { lock.withLock { _pinFailed } }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let der = SecCertificateCopyData(leaf) as Data
        let fingerprint = SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
        let pin = expected
        lock.withLock { _observed = fingerprint }
        if let pin, pin != fingerprint {
            lock.withLock { _pinFailed = true }
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}

nonisolated enum Keychain {
    private static let service = "ChewSenseLoop"
    private static let account = "device-token"

    static func save(token: String) throws {
        delete()
        let status = SecItemAdd([
            kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account,
            kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData: Data(token.utf8),
        ] as CFDictionary, nil)
        guard status == errSecSuccess else { throw Err("Could not store the pairing token (\(status)).") }
    }

    static func token() -> String? {
        var item: CFTypeRef?
        let status = SecItemCopyMatching([
            kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account,
            kSecReturnData: true,
        ] as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func delete() {
        SecItemDelete([kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                       kSecAttrAccount: account] as CFDictionary)
    }
}
