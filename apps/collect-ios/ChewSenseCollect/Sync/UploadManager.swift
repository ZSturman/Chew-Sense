import Foundation
import Combine
import CryptoKit
import UIKit

/// The phone's outbox: recordings go to the Mac automatically, with
/// resumable, verified transfers.
///
/// Order of preference, tried again automatically until one works:
/// 1. **The Mac directly**, on whichever route answers (LoopLink).
/// 2. **The home-server mailbox**, when the Mac doesn't answer: the archive is
///    encrypted on the phone and deposited on the Pi; the Mac collects it when
///    it wakes (LoopMailbox, `loop/mailbox.py`). Never over cellular.
/// 3. **Waiting** here, when neither is reachable.
///
/// The archive is the same hash-checked ZIP that Share exports. Its SHA-256 is
/// announced first; the receiver reports how many bytes it already holds, so an
/// interrupted transfer resumes rather than restarts, and a completed one is
/// never imported twice. Queue state survives app restarts. The recording
/// folder is never deleted; only the temporary ZIP and sealed copies, and only
/// after the Mac's verified receipt.
final class UploadManager: ObservableObject {
    struct Item: Codable, Identifiable, Equatable {
        enum State: String, Codable {
            case queued, preparing, uploading, deposited, received, processing, processed, needsAttention, failed
        }

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
        // Home-server fallback; optional so queues saved by older builds still decode.
        var depositID: String?
        var sealedName: String?
        var sealedSHA256: String?
        var sealedSize: Int?

        init(folderName: String, state: State) {
            self.folderName = folderName
            self.state = state
        }

        var id: String { folderName }
        var progress: Double {
            let total = depositID != nil ? sealedSize : size  // bytes of whichever copy is being sent
            return total.map { $0 > 0 ? Double(bytesSent) / Double($0) : 0 } ?? 0
        }
    }

    @Published private(set) var items: [String: Item] = [:]
    @Published var autoUpload: Bool {
        didSet { UserDefaults.standard.set(autoUpload, forKey: "LoopAutoUpload") }
    }
    /// Test hook: called after each chunk is accepted (direct or deposit).
    var afterChunk: ((Int) async -> Void)?

    private let connection: MacConnection
    private let recorder: RecorderController
    private let directory: URL
    private var running = false
    private let chunkSize: Int

    init(connection: MacConnection, recorder: RecorderController,
         directory: URL = ExperimentCatalogStore.defaultDirectory().appendingPathComponent("Uploads"),
         chunkSize: Int = 4 * 1024 * 1024) {
        self.connection = connection
        self.recorder = recorder
        self.directory = directory
        self.chunkSize = chunkSize
        // On unless the wearer turned it off.
        autoUpload = UserDefaults.standard.object(forKey: "LoopAutoUpload") as? Bool ?? true
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

    private static let pendingStates: Set<Item.State> = [.queued, .preparing, .uploading]
    static let waitingForMac = "Saved on this iPhone. It will go to your Mac automatically when the Mac is reachable."
    static let waitingForWiFi = "Saved on this iPhone. It will go to your Mac on Wi-Fi (the Mac is only reachable over the internet right now)."
    static let deposited = "Delivered to your home server. Your Mac collects it automatically when it's on."
    static let received = "Received by the Mac and checksum verified."

    /// Recordings still to send, or sent and not yet processed.
    var hasPendingWork: Bool {
        items.values.contains {
            Self.pendingStates.contains($0.state) || [.deposited, .received, .processing].contains($0.state)
        }
    }

    /// Marks unsent items as waiting, with a plain reason; does not reorder the
    /// queue. Upload IDs, deposits and offsets are kept, so sending resumes where it stopped.
    private func noteWaiting(_ message: String) {
        for (name, item) in items where Self.pendingStates.contains(item.state) {
            items[name]?.state = .queued
            items[name]?.message = message
        }
        save()
    }

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

    /// Sends what can be sent now: to the Mac, else to the home server, else
    /// leaves it waiting. Safe to call repeatedly; nothing needs a person.
    func run() async {
        guard !running, connection.paired != nil, !recorder.isRecording else { return }
        running = true
        defer { running = false }
        // Leaving the app mid-transfer finishes the current work instead of aborting it.
        let assertion = BackgroundAssertion("Send recordings to your Mac")
        defer { assertion.end() }
        var macAway = false
        while let next = items.values.filter({ Self.pendingStates.contains($0.state) })
                .sorted(by: { $0.updatedAt < $1.updatedAt }).first {
            guard !recorder.isRecording else { return }
            let name = next.folderName
            do {
                if macAway { throw URLError(.cannotConnectToHost) }  // checked once per run
                guard connection.mayTransferRecordings else {
                    noteWaiting(Self.waitingForWiFi)
                    break
                }
                try await transfer(name)
            } catch let error where LoopLink.isUnreachable(error) {
                // The Mac is away. Not a failure: try the home server, else wait.
                macAway = true
                if await deposit(name) { continue }
                noteWaiting(Self.waitingForMac)
                break
            } catch {
                let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                update(name) {
                    $0.attempts += 1
                    $0.message = message
                    // Keep it queued for a later retry unless the archive itself is unusable.
                    $0.state = $0.attempts >= 5 ? .failed : $0.state
                }
                if error is MacConnection.ConnectionError { break }
                try? await Task.sleep(nanoseconds: UInt64(min(60, 2 << min(5, items[name]?.attempts ?? 1))) * 1_000_000_000)
            }
        }
        await pollStatuses()
    }

    private func sessionFolder(_ name: String) -> URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent(name)
    }

    /// The verified ZIP for a recording, made once and kept until the Mac's receipt.
    private func archive(_ name: String) throws -> URL? {
        guard let item = items[name] else { return nil }
        if let existing = item.archiveName.map({ directory.appendingPathComponent($0) }),
           FileManager.default.fileExists(atPath: existing.path), item.sha256 != nil {
            return existing
        }
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
            // A new ZIP means any sealed copy is stale.
            $0.sealedName = nil
            $0.sealedSHA256 = nil
            $0.sealedSize = nil
        }
        return destination
    }

    // MARK: Directly to the Mac

    private func transfer(_ name: String) async throws {
        guard !Self.needsCoarseReview(sessionFolder(name)) else {
            update(name) { $0.state = .needsAttention; $0.message = "Review chewing intervals before sending to your Mac." }
            return
        }
        guard let archive = try archive(name), let current = items[name], let digest = current.sha256,
              let size = current.size else { return }
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
                await afterChunk?(offset)
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
        await receivedByMac(name)
    }

    /// The Mac holds a verified copy: drop the temporary files and any unfinished deposit.
    private func receivedByMac(_ name: String) async {
        guard let item = items[name] else { return }
        update(name) { $0.state = .received; $0.message = Self.received }
        recorder.markShared(for: sessionFolder(name))
        if let deposit = item.depositID, item.state != .deposited {
            _ = try? await connection.link.mailbox.request("DELETE", "deposits/\(deposit)", timeout: 8)
        }
        for file in [item.archiveName, item.sealedName].compactMap({ $0 }) {
            try? FileManager.default.removeItem(at: directory.appendingPathComponent(file))
        }
        update(name) {
            $0.archiveName = nil
            $0.sealedName = nil
            $0.depositID = nil
        }
    }

    // MARK: To the home server while the Mac is away

    /// Deposits one recording, encrypted, on the home server. Returns true once
    /// the server verified the whole sealed copy. Never counts as a failed
    /// attempt: the recording stays queued on the phone if this doesn't work.
    private func deposit(_ name: String) async -> Bool {
        let mailbox = connection.link.mailbox
        guard let config = mailbox.config, let key = config.symmetricKey, !LoopNetwork.shared.isMetered,
              !Self.needsCoarseReview(sessionFolder(name)) else { return false }
        do {
            guard let archive = try archive(name), let digest = items[name]?.sha256 else { return false }
            var sealed = items[name]?.sealedName.map { directory.appendingPathComponent($0) }
            if sealed == nil || !FileManager.default.fileExists(atPath: sealed!.path) {
                let target = directory.appendingPathComponent("\(name).sealed")
                try? FileManager.default.removeItem(at: target)
                try LoopSeal.seal(archive, to: target, key: key, chunkSize: min(LoopSeal.chunk, chunkSize))
                let sealedDigest = try Self.sha256(of: target)
                let sealedSize = (try target.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                update(name) {
                    $0.sealedName = target.lastPathComponent
                    $0.sealedSHA256 = sealedDigest
                    $0.sealedSize = sealedSize
                    $0.depositID = nil
                }
                sealed = target
            }
            guard let sealed, let current = items[name], let sealedDigest = current.sealedSHA256,
                  let size = current.sealedSize else { return false }
            let (_, created) = try await mailbox.request("POST", "deposits", json: [
                "session_id": Self.sessionID(in: sessionFolder(name)) ?? name, "size": size,
                "sha256": sealedDigest, "archive_sha256": digest, "device": UIDevice.current.name])
            guard let depositID = created["id"] as? String, var offset = created["offset"] as? Int else { return false }
            switch created["state"] as? String {
            case "open", "waiting":
                break
            case "collected":
                update(name) { $0.state = .deposited; $0.depositID = depositID }
                await receivedByMac(name)
                return true
            default:
                // That sealed copy was cancelled or abandoned on the server: seal afresh next time.
                update(name) { $0.sealedName = nil; $0.depositID = nil; $0.bytesSent = 0 }
                try? FileManager.default.removeItem(at: sealed)
                return false
            }
            update(name) { $0.state = .uploading; $0.depositID = depositID; $0.bytesSent = offset
                           $0.message = "Sending to your home server…" }
            if created["state"] as? String == "open" {
                let handle = try FileHandle(forReadingFrom: sealed)
                defer { try? handle.close() }
                while offset < size {
                    try handle.seek(toOffset: UInt64(offset))
                    let chunk = handle.readData(ofLength: chunkSize)
                    let (status, reply) = try await mailbox.request(
                        "PUT", "deposits/\(depositID)/content", body: chunk, headers: ["Upload-Offset": String(offset)])
                    let details = (reply["error"] as? [String: Any])?["details"] as? [String: Any]
                    guard let next = status == 409 ? details?["expected_offset"] as? Int : reply["offset"] as? Int
                    else { return false }
                    offset = next
                    update(name) { $0.bytesSent = offset }
                    await afterChunk?(offset)
                }
                let (status, done) = try await mailbox.request("POST", "deposits/\(depositID)/complete")
                if status == 409 {
                    // A damaged copy is discarded on the server; seal and send afresh next time.
                    update(name) { $0.sealedName = nil; $0.depositID = nil; $0.bytesSent = 0 }
                    try? FileManager.default.removeItem(at: sealed)
                    return false
                }
                guard done["state"] as? String == "waiting" else { return false }
            }
            update(name) { $0.state = .deposited; $0.message = Self.deposited }
            try? FileManager.default.removeItem(at: sealed)  // the server verified it; the ZIP stays until the Mac's receipt
            update(name) { $0.sealedName = nil }
            return true
        } catch {
            // Server unreachable, full, or its disk missing: wait here and try again later.
            return false
        }
    }

    // MARK: Status

    func pollStatuses() async {
        var homeSessions: [String: String]?
        func fromHome() async -> [String: String] {
            if let homeSessions { return homeSessions }
            let sessions = (try? await connection.link.mailbox.status().sessions) ?? [:]
            homeSessions = sessions
            return sessions
        }
        for item in items.values where item.state == .deposited {
            guard let deposit = item.depositID,
                  let reply = try? await connection.link.mailbox.request("GET", "deposits/\(deposit)", timeout: 8)
            else { continue }
            let view = reply.1
            switch view["state"] as? String {
            case "collected":
                await receivedByMac(item.folderName)
            case "rejected":
                let receipt = view["receipt"] as? [String: Any]
                update(item.folderName) {
                    $0.state = .needsAttention
                    $0.message = (receipt?["detail"] as? String).map { "Your Mac couldn't use the copy: \($0)" }
                        ?? "Your Mac couldn't use the copy. Send it again."
                    $0.depositID = nil
                }
            case "abandoned", "cancelled", nil:
                update(item.folderName) { $0.state = .queued; $0.depositID = nil; $0.message = Self.waitingForMac }
            default:
                break
            }
        }
        for item in items.values where [.received, .processing].contains(item.state) {
            guard let sessionID = Self.sessionID(in: sessionFolder(item.folderName)) else { continue }
            var state: String?
            var status: [String: Any] = [:]
            if let data = try? await connection.request("GET", "v1/sessions/\(sessionID)/status"),
               let object = try? Self.object(data) {
                status = object
                state = object["state"] as? String
            } else {
                state = await fromHome()[sessionID]  // the Mac is away; its last published state
            }
            guard let state else { continue }
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

/// Lets work started in the foreground finish after the app leaves the screen.
final class BackgroundAssertion {
    private var identifier: UIBackgroundTaskIdentifier = .invalid

    init(_ name: String) {
        identifier = UIApplication.shared.beginBackgroundTask(withName: name) { [weak self] in
            MainActor.assumeIsolated { self?.end() }
        }
    }

    func end() {
        guard identifier != .invalid else { return }
        UIApplication.shared.endBackgroundTask(identifier)
        identifier = .invalid
    }
}
