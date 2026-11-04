import Foundation
import AppKit
import Combine

/// Reads the loop service's status files and sends loopback admin commands.
///
/// Files are read directly so the window works when the service is stopped;
/// actions use `http://127.0.0.1:<admin_port>` with the 0600 admin token from
/// `loop/service/admin.token`. Nothing here reaches the network.
@MainActor
final class PipelineStore: ObservableObject {
    @Published private(set) var loopRoot: URL?
    @Published private(set) var status: [String: Any] = [:]
    @Published private(set) var tasks: [[String: Any]] = []
    @Published private(set) var report: [String: Any] = [:]
    @Published private(set) var catalog: [String: Any] = [:]
    @Published private(set) var serviceReachable = false
    @Published var pairingCode: String?
    @Published var message: String?

    private static let rootKey = "ChewSenseReview.loopRoot"
    private var timer: AnyCancellable?

    init() {
        if let path = UserDefaults.standard.string(forKey: Self.rootKey),
           FileManager.default.fileExists(atPath: path) {
            loopRoot = URL(fileURLWithPath: path)
        }
    }

    /// Infers the loop folder from an `…/loop/assisted` or `…/loop/blind` workspace.
    func adopt(workspace: URL?) {
        guard loopRoot == nil, let workspace,
              ["assisted", "blind"].contains(workspace.lastPathComponent),
              workspace.deletingLastPathComponent().lastPathComponent == "loop" else { return }
        setRoot(workspace.deletingLastPathComponent())
    }

    func chooseRoot() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.message = "Choose the ChewSense workspace/loop folder"
        if panel.runModal() == .OK, let url = panel.url { setRoot(url) }
    }

    private func setRoot(_ url: URL) {
        loopRoot = url
        UserDefaults.standard.set(url.path, forKey: Self.rootKey)
        reload()
    }

    func startPolling() {
        reload()
        timer = Timer.publish(every: 3, on: .main, in: .common).autoconnect().sink { [weak self] _ in
            self?.reload()
        }
    }

    func stopPolling() { timer = nil }

    func reload() {
        guard let root = loopRoot else { return }
        status = Self.read(root.appending(path: "status.json"))
        tasks = Self.read(root.appending(path: "review_tasks.json"))["tasks"] as? [[String: Any]] ?? []
        report = Self.read(root.appending(path: "reports/loop/latest.json"))
        catalog = Self.read(root.appending(path: "experiments/catalog.json"))
        Task { serviceReachable = (try? await admin("GET", "/admin/status")) != nil }
    }

    var assistedWorkspace: URL? { loopRoot?.appending(path: "assisted") }
    var blindWorkspace: URL? { loopRoot?.appending(path: "blind") }

    // MARK: Actions

    func control(_ action: String) async {
        do {
            _ = try await admin("POST", "/admin/\(action)")
            message = "Processing \(action == "stop" ? "stopped; resume to continue" : action + "d")."
        } catch {
            message = error.localizedDescription
        }
        reload()
    }

    func openPairing() async {
        do {
            let window = try await admin("POST", "/admin/pairing")
            pairingCode = window["code"] as? String
        } catch {
            message = error.localizedDescription
        }
    }

    func analyzeNow() async {
        do {
            _ = try await admin("POST", "/admin/analyze")
            message = "Analysis updated."
        } catch {
            message = error.localizedDescription
        }
        reload()
    }

    func retry(job: String) async {
        do { _ = try await admin("POST", "/admin/jobs/\(job)/retry") } catch { message = error.localizedDescription }
        reload()
    }

    private func admin(_ method: String, _ path: String) async throws -> [String: Any] {
        guard let root = loopRoot else { throw PipelineError.noRoot }
        let endpoint = Self.read(root.appending(path: "service/endpoint.json"))
        guard let port = endpoint["admin_port"] as? Int,
              let token = try? String(contentsOf: root.appending(path: "service/admin.token"), encoding: .utf8)
        else { throw PipelineError.notRunning }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)\(path)")!, timeoutInterval: 20)
        request.httpMethod = method
        request.setValue("Bearer \(token.trimmingCharacters(in: .whitespacesAndNewlines))", forHTTPHeaderField: "Authorization")
        if method == "POST" { request.httpBody = Data("{}".utf8) }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw PipelineError.notRunning }
            return (try JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        } catch is URLError {
            throw PipelineError.notRunning
        }
    }

    enum PipelineError: LocalizedError {
        case noRoot, notRunning
        var errorDescription: String? {
            switch self {
            case .noRoot: "Choose the workspace/loop folder first."
            case .notRunning: "The loop service is not running. Start it with tools/loop-service start."
            }
        }
    }

    static func read(_ url: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return object
    }
}
