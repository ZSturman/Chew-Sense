import Combine
import CryptoKit
import Foundation
import Network

// The one client for the Mac's loop service, compiled into Collect and Live.
// Communication model: docs/COMMUNICATION.md. The phone never needs the Mac to
// record; the Mac is a worker the phone reaches by identity (its pinned
// certificate), over whichever route answers, never by a typed address.

/// One way to reach the paired Mac. An address is only a hint: a route is used
/// only after the Mac there presents the pinned certificate and accepts the token.
nonisolated struct LoopRoute: Codable, Hashable, Sendable {
    enum Kind: String, Codable, Sendable {
        case lan, local, manual, tailnet

        /// Search order: this network first, then the tailnet (any network).
        var rank: Int {
            switch self {
            case .lan: 0
            case .local: 1
            case .manual: 2
            case .tailnet: 3
            }
        }
    }

    var host: String
    var port: Int
    var kind: Kind

    /// Routes the Mac reported (`routes.py`). Unknown kinds from newer Macs are skipped.
    static func parse(_ value: Any?) -> [LoopRoute] {
        (value as? [[String: Any]] ?? []).compactMap { item in
            guard let host = item["host"] as? String, !host.isEmpty, let port = item["port"] as? Int,
                  let kind = (item["kind"] as? String).flatMap(Kind.init(rawValue:)) else { return nil }
            return LoopRoute(host: host, port: port, kind: kind)
        }
    }

    /// A MagicDNS name, or an address from Tailscale's 100.64.0.0/10. Only the
    /// name works from the apps: their ATS exception covers `ts.net`, while iOS
    /// refuses the self-signed certificate on a 100.x address (-9802).
    static func isTailnet(_ host: String) -> Bool {
        if host.hasSuffix(".ts.net") { return true }
        let parts = host.split(separator: ".").compactMap { Int($0) }
        return parts.count == 4 && parts[0] == 100 && (64...127).contains(parts[1])
    }
}

/// A paired Mac. `host`/`port` is the route that last answered; `routes` are
/// all the routes the Mac reported. Records saved before routes existed decode
/// with `routes == nil` and keep working.
nonisolated struct LoopPairing: Codable, Equatable, Sendable {
    var name: String
    var host: String
    var port: Int
    var fingerprint: String
    var routes: [LoopRoute]?

    var current: LoopRoute {
        routes?.first { $0.host == host && $0.port == port }
            ?? LoopRoute(host: host, port: port, kind: LoopRoute.isTailnet(host) ? .tailnet : .lan)
    }

    /// Other routes to try, best first. Older pairings stored a `.local` name only as `name`.
    var alternatives: [LoopRoute] {
        var list = routes ?? []
        if name.hasSuffix(".local"), !list.contains(where: { $0.host == name }) {
            list.append(LoopRoute(host: name, port: port, kind: .local))
        }
        var seen = Set<String>()
        return list
            .filter { !($0.host == host && $0.port == port) && seen.insert("\($0.host):\($0.port)").inserted }
            .enumerated()
            .sorted { ($0.element.kind.rank, $0.offset) < ($1.element.kind.rank, $1.offset) }
            .map(\.element)
    }
}

@MainActor
final class LoopLink {
    nonisolated enum Failure: LocalizedError, Equatable {
        case notPaired, pinMismatch, http(Int, String), invalidResponse, pairingFailed(String)

        var errorDescription: String? {
            switch self {
            case .notPaired: "Pair with your Mac first."
            case .pinMismatch: "Your Mac's identity changed. Pair again to trust it."
            case .http(401, _): "Your Mac no longer recognizes this app. Pair again."
            case .http(let code, let message): "The Mac responded \(code): \(message)"
            case .invalidResponse: "Unexpected response from the Mac."
            case .pairingFailed(let reason): reason
            }
        }
    }

    private(set) var pairing: LoopPairing? { didSet { onChange?() } }
    /// True while looking for the Mac on its other routes.
    private(set) var isSearching = false { didSet { onChange?() } }
    /// Called after `pairing` or `isSearching` changes so the owner can publish.
    var onChange: (() -> Void)?
    /// Unit tests run without Bonjour.
    var usesBonjour = true

    static let searchInterval: TimeInterval = 30
    private let trust = PinnedTrust()
    private let session: URLSession
    private let defaults: UserDefaults
    private let pairingKey: String
    private let deviceKey: String
    private let keychainService: String
    private var lastSearch: Date?

    /// Keys stay per app (`LoopPairedMac` in Collect, `LiveLoopPairedMac` in
    /// Live) so existing pairings carry over unchanged.
    init(pairingKey: String, deviceKey: String, defaults: UserDefaults = .standard,
         keychainService: String = Keychain.defaultService) {
        self.pairingKey = pairingKey
        self.deviceKey = deviceKey
        self.defaults = defaults
        self.keychainService = keychainService
        session = URLSession(configuration: .ephemeral, delegate: trust, delegateQueue: nil)
        if let data = defaults.data(forKey: pairingKey),
           let stored = try? JSONDecoder().decode(LoopPairing.self, from: data), token != nil {
            pairing = stored
        }
    }

    var token: String? { Keychain.token(service: keychainService) }

    var deviceID: String {
        if let id = defaults.string(forKey: deviceKey) { return id }
        let id = UUID().uuidString.lowercased()
        defaults.set(id, forKey: deviceKey)
        return id
    }

    // MARK: Pairing

    /// The Mac shows a six-digit code. The phone proves it knows the code and
    /// which certificate it sees: `HMAC-SHA256(code, "pair|device|fingerprint")`.
    /// The Mac answers with its own proof, so both sides confirm before a token is stored.
    func pair(host: String, port: Int, code: String, name: String, deviceName: String) async throws {
        let digits = code.filter(\.isNumber)
        guard digits.count == 6 else { throw Failure.pairingFailed("Enter the six-digit code shown on the Mac.") }
        trust.expected = nil  // Observe the certificate actually presented.
        let base = try Self.baseURL(host: host, port: port)
        _ = try await send("GET", base.appendingPathComponent("v1/hello"), token: nil)
        guard let fingerprint = trust.observed else { throw Failure.invalidResponse }
        let device = deviceID
        let body = try JSONSerialization.data(withJSONObject: [
            "device_id": device, "device_name": deviceName,
            "proof": Self.hmac(digits, "pair|\(device)|\(fingerprint)")])
        let data: Data
        do {
            data = try await send("POST", base.appendingPathComponent("v1/pair"), token: nil, body: body)
        } catch Failure.http(let status, let message) where status == 403 {
            throw Failure.pairingFailed(message)
        }
        guard let reply = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = reply["device_token"] as? String,
              let serverProof = reply["server_proof"] as? String,
              reply["fingerprint"] as? String == fingerprint,
              serverProof == Self.hmac(digits, "server|\(device)|\(fingerprint)|\(token)") else {
            throw Failure.pairingFailed("The Mac could not prove it knows the pairing code.")
        }
        try Keychain.save(token: token, service: keychainService)
        trust.expected = fingerprint
        let routes = LoopRoute.parse(reply["routes"])
        save(LoopPairing(name: name, host: host, port: port, fingerprint: fingerprint,
                         routes: routes.isEmpty ? nil : routes))
    }

    func forget() {
        Keychain.delete(service: keychainService)
        defaults.removeObject(forKey: pairingKey)
        pairing = nil
    }

    // MARK: Requests

    /// Sends a request to the paired Mac. When its last address does not
    /// answer, looks for it on its other routes (at most every 30 s) and
    /// retries once there.
    func request(_ method: String, _ path: String, query: String? = nil, body: Data? = nil,
                 timeout: TimeInterval = 30) async throws -> Data {
        guard let pairing, let token else { throw Failure.notPaired }
        do {
            return try await send(method, try Self.url(pairing.current, path, query), token: token,
                                  body: body, timeout: timeout)
        } catch let error where Self.isUnreachable(error) {
            guard await findMac(), let moved = self.pairing else { throw error }
            return try await send(method, try Self.url(moved.current, path, query), token: token,
                                  body: body, timeout: timeout)
        }
    }

    /// `GET /v1/status`, remembering any routes the Mac reports. A short timeout,
    /// so a stale address gives way to the other routes quickly.
    func status() async throws -> [String: Any] {
        let data = try await request("GET", "v1/status", timeout: 8)
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let object else { throw Failure.invalidResponse }
        learnRoutes(LoopRoute.parse(object["routes"]))
        return object
    }

    func learnRoutes(_ routes: [LoopRoute]) {
        guard var record = pairing, !routes.isEmpty, routes != record.routes else { return }
        record.routes = routes
        save(record)
    }

    // MARK: Finding the Mac again

    /// Tries the Mac's other routes, then Bonjour, and switches to the first
    /// one where the pinned certificate and this device's token are accepted.
    /// The pairing is kept; no code or address is needed.
    @discardableResult
    func findMac(force: Bool = false) async -> Bool {
        guard let pairing, token != nil, !isSearching else { return false }
        if !force, let lastSearch, Date().timeIntervalSince(lastSearch) < Self.searchInterval { return false }
        isSearching = true
        defer { isSearching = false }
        lastSearch = Date()
        let known = pairing.alternatives
        if let route = await firstAnswering(known) {
            use(route)
            return true
        }
        guard usesBonjour else { return false }
        var found: [LoopRoute] = []
        let services = await LoopDiscovery.browseOnce(seconds: 3)
            .sorted { $0.name.hasSuffix(pairing.name) && !$1.name.hasSuffix(pairing.name) }
        for service in services {
            if let (host, port) = try? await LoopDiscovery.resolve(service) {
                let route = LoopRoute(host: host, port: port, kind: .lan)
                if route != pairing.current, !known.contains(where: { $0.host == host && $0.port == port }) {
                    found.append(route)
                }
            }
        }
        if let route = await firstAnswering(found) {
            use(route)
            return true
        }
        return false
    }

    /// Points the pairing at an address typed by the user (rarely needed).
    func useAddress(host: String, port: Int) async throws {
        guard pairing != nil, let token else { throw Failure.notPaired }
        let route = LoopRoute(host: host, port: port, kind: LoopRoute.isTailnet(host) ? .tailnet : .manual)
        _ = try await send("GET", try Self.url(route, "v1/status", nil), token: token, timeout: 8)
        use(route)
    }

    /// Probes routes together; the best-ranked one that answers wins.
    private func firstAnswering(_ routes: [LoopRoute]) async -> LoopRoute? {
        guard !routes.isEmpty, let token else { return nil }
        let answered = await withTaskGroup(of: Int?.self) { group in
            for (index, route) in routes.enumerated() {
                group.addTask { (await self.answers(route, token: token)) ? index : nil }
            }
            var indices: [Int] = []
            for await index in group { if let index { indices.append(index) } }
            return indices
        }
        return answered.min().map { routes[$0] }
    }

    /// Throws unless the paired Mac answers on `route` with its pinned identity. Changes nothing.
    func check(_ route: LoopRoute) async throws {
        guard let token else { throw Failure.notPaired }
        _ = try await send("GET", try Self.url(route, "v1/status", nil), token: token, timeout: 5)
    }

    private func answers(_ route: LoopRoute, token: String) async -> Bool {
        guard let url = try? Self.url(route, "v1/status", nil) else { return false }
        return (try? await send("GET", url, token: token, timeout: 5)) != nil
    }

    private func use(_ route: LoopRoute) {
        guard var record = pairing else { return }
        record.host = route.host
        record.port = route.port
        if !(record.routes ?? []).contains(route) { record.routes = (record.routes ?? []) + [route] }
        save(record)
    }

    private func save(_ record: LoopPairing) {
        if let data = try? JSONEncoder().encode(record) { defaults.set(data, forKey: pairingKey) }
        pairing = record
    }

    // MARK: Transport

    private func send(_ method: String, _ url: URL, token: String?, body: Data? = nil,
                      timeout: TimeInterval = 30) async throws -> Data {
        if token != nil { trust.expected = pairing?.fingerprint }
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = method
        request.httpBody = body
        request.setValue(body == nil ? nil : "application/json", forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw Failure.invalidResponse }
            // 409 reports the Mac's actual upload offset; the caller continues from there.
            guard (200..<300).contains(http.statusCode) || http.statusCode == 409 else {
                let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                throw Failure.http(http.statusCode, message ?? "request failed")
            }
            return data
        } catch let error as URLError where error.code == .cancelled && trust.pinFailed {
            throw Failure.pinMismatch
        }
    }

    /// Errors after which another route may work. A different certificate at
    /// the old address means another device now has it, not a new Mac.
    nonisolated static func isUnreachable(_ error: Error) -> Bool {
        if case Failure.pinMismatch = error { return true }
        guard let error = error as? URLError else { return false }
        return [.cannotConnectToHost, .cannotFindHost, .timedOut, .networkConnectionLost, .dnsLookupFailed,
                .notConnectedToInternet, .secureConnectionFailed, .cannotLoadFromNetwork].contains(error.code)
    }

    nonisolated static func url(_ route: LoopRoute, _ path: String, _ query: String?) throws -> URL {
        var url = try baseURL(host: route.host, port: route.port).appendingPathComponent(path)
        if let query, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            components.query = query
            url = components.url ?? url
        }
        return url
    }

    nonisolated static func baseURL(host: String, port: Int) throws -> URL {
        var components = URLComponents()
        components.scheme = "https"
        components.host = host
        components.port = port
        guard let url = components.url else { throw Failure.invalidResponse }
        return url
    }

    nonisolated static func hmac(_ key: String, _ message: String) -> String {
        let mac = HMAC<SHA256>.authenticationCode(for: Data(message.utf8), using: SymmetricKey(data: Data(key.utf8)))
        return mac.map { String(format: "%02x", $0) }.joined()
    }
}

// MARK: - Bonjour

/// Bonjour discovery of `_chewsense._tcp`: for first pairing, and as a last
/// resort when the Mac's known routes stop answering.
@MainActor
final class LoopDiscovery: ObservableObject {
    nonisolated struct Found: Identifiable, Hashable, Sendable {
        var name: String
        var endpoint: NWEndpoint
        var id: String { name }
    }

    @Published private(set) var found: [Found] = []
    @Published private(set) var error: String?
    private let appName: String
    private var browser: NWBrowser?

    init(appName: String) { self.appName = appName }

    func start() {
        guard browser == nil else { return }
        error = nil
        let browser = NWBrowser(for: .bonjour(type: "_chewsense._tcp", domain: nil), using: Self.parameters)
        // Started on the main queue, so the callbacks run on the main actor.
        browser.stateUpdateHandler = { [weak self] state in
            MainActor.assumeIsolated {
                guard let self else { return }
                switch state {
                case .waiting(let error), .failed(let error):
                    self.error = Self.failureDescription(error, appName: self.appName)
                case .ready:
                    self.error = nil
                default:
                    break
                }
            }
        }
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            MainActor.assumeIsolated { self?.found = Self.services(results) }
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    func stop() {
        browser?.cancel()
        browser = nil
    }

    static func failureDescription(_ error: NWError, appName: String) -> String {
        // kDNSServiceErr_PolicyDenied from Apple's dns_sd.h.
        if case .dns(-65570) = error {
            return "Local Network access is off. Open Settings and turn on Local Network for \(appName), then return here."
        }
        return "Can't search this network right now (\(error.localizedDescription))."
    }

    nonisolated private static var parameters: NWParameters {
        let parameters = NWParameters.tcp
        parameters.includePeerToPeer = false
        return parameters
    }

    nonisolated private static func services(_ results: Set<NWBrowser.Result>) -> [Found] {
        results.compactMap { result -> Found? in
            if case let .service(name, _, _, _) = result.endpoint { return Found(name: name, endpoint: result.endpoint) }
            return nil
        }
        .sorted { $0.name < $1.name }
    }

    static func browseOnce(seconds: Double) async -> [Found] {
        let browser = NWBrowser(for: .bonjour(type: "_chewsense._tcp", domain: nil), using: parameters)
        browser.start(queue: .main)
        try? await Task.sleep(for: .seconds(seconds))
        let found = services(browser.browseResults)
        browser.cancel()
        return found
    }

    /// Resolves a Bonjour service to an IPv4 host and port.
    nonisolated static func resolve(_ service: Found, timeout: TimeInterval = 6) async throws -> (String, Int) {
        let parameters = NWParameters.tcp
        (parameters.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options)?.version = .v4
        let connection = NWConnection(to: service.endpoint, using: parameters)
        let queue = DispatchQueue(label: "chewsense.resolve")
        let once = ResumeOnce()
        return try await withCheckedThrowingContinuation { continuation in
            queue.asyncAfter(deadline: .now() + timeout) {
                guard once.claim() else { return }
                continuation.resume(throwing: URLError(.timedOut))
                connection.cancel()
            }
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    guard once.claim() else { return }
                    if case let .hostPort(host, port)? = connection.currentPath?.remoteEndpoint {
                        var text = "\(host)"
                        if let percent = text.firstIndex(of: "%") { text = String(text[..<percent]) }
                        continuation.resume(returning: (text, Int(port.rawValue)))
                    } else {
                        continuation.resume(throwing: LoopLink.Failure.invalidResponse)
                    }
                    connection.cancel()
                case .failed(let error):
                    guard once.claim() else { return }
                    continuation.resume(throwing: error)
                    connection.cancel()
                default: break
                }
            }
            connection.start(queue: queue)
        }
    }
}

/// Resumes a continuation at most once from Network's callbacks.
nonisolated final class ResumeOnce: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false

    func claim() -> Bool {
        lock.withLock {
            defer { done = true }
            return !done
        }
    }
}

// MARK: - The phone's own network

/// Watches this device's network: sync again when it changes, and keep large
/// recordings off cellular or Low Data Mode when the Mac is reachable only
/// across the internet (its tailnet route).
@MainActor
final class LoopNetwork {
    static let shared = LoopNetwork()

    private(set) var isMetered = false
    private(set) var isOnline = true
    private let monitor = NWPathMonitor()
    private var handlers: [() -> Void] = []

    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            MainActor.assumeIsolated { self?.update(path) }
        }
        monitor.start(queue: .main)
    }

    /// Called on the main actor whenever the path changes.
    func onChange(_ handler: @escaping () -> Void) { handlers.append(handler) }

    private func update(_ path: NWPath) {
        isMetered = path.isExpensive || path.isConstrained
        isOnline = path.status == .satisfied
        // Joining a network (another Wi-Fi, a hotspot) is when the Mac may have become reachable.
        if isOnline { handlers.forEach { $0() } }
    }

    /// Recordings may cross a metered connection only to a Mac on the same
    /// local network (for example a Mac joined to this iPhone's hotspot).
    func allowsRecordingTransfer(over route: LoopRoute) -> Bool {
        Self.allowsRecordingTransfer(over: route, metered: isMetered)
    }

    nonisolated static func allowsRecordingTransfer(over route: LoopRoute, metered: Bool) -> Bool {
        !(metered && route.kind == .tailnet)
    }
}
