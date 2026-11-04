import Foundation
import Combine
import CryptoKit

/// One-tap and automatic "Send to Mac" with resumable, verified transfers.
///
/// The archive is the same hash-checked ZIP that Share exports. Its SHA-256 is
/// announced first; the Mac reports how many bytes it already holds, so an
/// interrupted transfer resumes rather than restarts, and a completed one is
/// never imported twice. Queue state survives app restarts.
final class UploadManager: ObservableObject {
    struct Item: Codable, Identifiable, Equatable {
        enum State: String, Codable { case queued, preparing, uploading, received, processing, processed, needsAttention, failed }

        var folderName: String
        var state: State
        var archiveName: String?
        var sha256: String?
        var size: Int?
        var uploadID: String?
        var bytesSent: Int = 0
        var attempts: Int = 0
        var message: String?
        var updatedAt: Date = Date()

        var id: String { folderName }
        var progress: Double { size.map { $0 > 0 ? Double(bytesSent) / Double($0) : 0 } ?? 0 }
    }

    @Published private(set) var items: [String: Item] = [:]
    @Published var autoUpload: Bool {
        didSet { UserDefaults.standard.set(autoUpload, forKey: "LoopAutoUpload") }
    }

    private let connection: MacConnection
    private let recorder: RecorderController
    private let directory: URL
    private var running = false
    private let chunkSize = 4 * 1024 * 1024

    init(connection: MacConnection, recorder: RecorderController,
         directory: URL = ExperimentCatalogStore.defaultDirectory().appendingPathComponent("Uploads")) {
        self.connection = connection
        self.recorder = recorder
        self.directory = directory
        autoUpload = UserDefaults.standard.bool(forKey: "LoopAutoUpload")
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if let data = try? Data(contentsOf: stateURL),
           let decoded = try? JSONDecoder().decode([String: Item].self, from: data) {
            items = decoded
        }
    }

    private var stateURL: URL { directory.appendingPathComponent("queue.json") }

    private func save() {
        if let data = try? JSONEncoder().encode(items) { try? data.write(to: stateURL, options: .atomic) }
    }

    private func update(_ name: String, _ change: (inout Item) -> Void) {
        guard var item = items[name] else { return }
        change(&item)
        item.updatedAt = Date()
        items[name] = item
        save()
    }

    func item(for folder: URL) -> Item? { items[folder.lastPathComponent] }

    static func needsCoarseReview(_ folder: URL) -> Bool {
        FileManager.default.fileExists(atPath: folder.appendingPathComponent("guided_session.json").path)
            && !FileManager.default.fileExists(atPath: folder.appendingPathComponent("annotations/coarse_labels.json").path)
    }

    func send(_ folder: URL) {
        guard !Self.needsCoarseReview(folder) else { return }
        let name = folder.lastPathComponent
        if let existing = items[name], ![.failed, .needsAttention].contains(existing.state) {
            Task { await run() }
            return
        }
        items[name] = Item(folderName: name, state: .queued)
        save()
        Task { await run() }
    }

    func recordingFinished(_ folder: URL) {
        if autoUpload { send(folder) }
    }

    /// Processes the queue until empty or offline. Safe to call repeatedly.
    func run() async {
        guard !running, connection.paired != nil else { return }
        running = true
        defer { running = false }
        while let next = items.values.filter({ [.queued, .preparing, .uploading].contains($0.state) })
                .sorted(by: { $0.updatedAt < $1.updatedAt }).first {
            do {
                try await transfer(next.folderName)
            } catch {
                let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                update(next.folderName) {
                    $0.attempts += 1
                    $0.message = message
                    // Keep it queued for a later retry unless the archive itself is unusable.
                    $0.state = $0.attempts >= 5 ? .failed : $0.state
                }
                if case .offline = connection.state { return }
                if error is MacConnection.ConnectionError { return }
                try? await Task.sleep(nanoseconds: UInt64(min(60, 2 << min(5, items[next.folderName]?.attempts ?? 1))) * 1_000_000_000)
            }
        }
        await pollStatuses()
    }

    private func sessionFolder(_ name: String) -> URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent(name)
    }

    private func transfer(_ name: String) async throws {
        guard !Self.needsCoarseReview(sessionFolder(name)) else {
            update(name) { $0.state = .needsAttention; $0.message = "Review chewing intervals before sending to your Mac." }
            return
        }
        guard let item = items[name] else { return }
        var archive = item.archiveName.map { directory.appendingPathComponent($0) }
        if archive == nil || !FileManager.default.fileExists(atPath: archive!.path) {
            update(name) { $0.state = .preparing; $0.message = "Preparing archive…" }
            let temporary = try recorder.zipSessionFolder(sessionFolder(name))
            let destination = directory.appendingPathComponent("\(name).zip")
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: temporary, to: destination)
            let digest = try Self.sha256(of: destination)
            let size = (try destination.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
            update(name) {
                $0.archiveName = destination.lastPathComponent
                $0.sha256 = digest
                $0.size = size
                $0.uploadID = nil
                $0.bytesSent = 0
            }
            archive = destination
        }
        guard let archive, let current = items[name], let digest = current.sha256, let size = current.size else { return }
        let sessionID = Self.sessionID(in: sessionFolder(name)) ?? name
        let body = try JSONSerialization.data(withJSONObject: [
            "session_id": sessionID, "archive_sha256": digest, "size_bytes": size])
        let created = try Self.object(try await connection.request("POST", "v1/uploads", body: body))
        guard let uploadID = created["upload_id"] as? String, var offset = created["offset"] as? Int else {
            throw MacConnection.ConnectionError.invalidResponse
        }
        update(name) { $0.state = .uploading; $0.uploadID = uploadID; $0.bytesSent = offset; $0.message = nil }
        if created["state"] as? String != "received" {
            let handle = try FileHandle(forReadingFrom: archive)
            defer { try? handle.close() }
            while offset < size {
                try handle.seek(toOffset: UInt64(offset))
                let chunk = handle.readData(ofLength: chunkSize)
                let reply = try Self.object(try await connection.request(
                    "PUT", "v1/uploads/\(uploadID)", query: "offset=\(offset)", body: chunk))
                guard let next = reply["offset"] as? Int else { throw MacConnection.ConnectionError.invalidResponse }
                offset = next  // A 409 reports the Mac's actual offset; continue from there.
                update(name) { $0.bytesSent = offset }
            }
            let receipt = try Self.object(try await connection.request("POST", "v1/uploads/\(uploadID)/complete"))
            switch receipt["state"] as? String {
            case "received": break
            case "checksum_mismatch":
                update(name) { $0.bytesSent = 0; $0.message = "Checksum mismatch; resending." }
                throw Err("The Mac received a corrupted copy; it will be sent again.")
            default:
                update(name) { $0.bytesSent = receipt["offset"] as? Int ?? 0 }
                throw Err("Upload incomplete; it will resume.")
            }
        }
        update(name) { $0.state = .received; $0.message = "Received by the Mac and checksum verified." }
        recorder.markShared(for: sessionFolder(name))
        try? FileManager.default.removeItem(at: archive)
        update(name) { $0.archiveName = nil }
    }

    func pollStatuses() async {
        for item in items.values where [.received, .processing].contains(item.state) {
            guard let sessionID = Self.sessionID(in: sessionFolder(item.folderName)),
                  let data = try? await connection.request("GET", "v1/sessions/\(sessionID)/status"),
                  let status = try? Self.object(data) else { continue }
            let state = status["state"] as? String
            update(item.folderName) {
                switch state {
                case "processed": $0.state = .processed; $0.message = "Processed on the Mac."
                case "needs_attention", "rejected":
                    $0.state = .needsAttention
                    $0.message = (status["error"] as? String) ?? (status["attention"] as? String) ?? "Needs attention on the Mac."
                default: $0.state = .processing; $0.message = "Processing on the Mac…"
                }
            }
        }
    }

    static func sessionID(in folder: URL) -> String? {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("session.json")),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return object["session_id"] as? String
    }

    static func object(_ data: Data) throws -> [String: Any] {
        guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw MacConnection.ConnectionError.invalidResponse
        }
        return value
    }

    nonisolated static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while case let data = handle.readData(ofLength: 1 << 20), !data.isEmpty { hasher.update(data: data) }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}
