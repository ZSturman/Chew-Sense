import BackgroundTasks
import Foundation
import Testing
@testable import ChewSenseCollect

/// Opt-in checks on the paired iPhone against the real Mac and the deployed
/// home-server mailbox, one phase per run:
///
///     TEST_RUNNER_CHEWSENSE_DEVICE_RELAY=<phase> TEST_RUNNER_CHEWSENSE_DEVICE_SESSION=<recording folder>
///
/// They resend a recording the Mac already holds, checked byte for byte
/// against its import receipt beforehand, so the Mac records a duplicate and
/// imports nothing. Recordings are only read. Except for the `background-*`
/// phases they use their own queue in Caches, not the app's.
@MainActor
@Suite(.serialized, .enabled(if: ProcessInfo.processInfo.environment["CHEWSENSE_DEVICE_RELAY"] != nil))
struct DeviceRelayTests {
    static let environment = ProcessInfo.processInfo.environment
    static var phase: String { environment["CHEWSENSE_DEVICE_RELAY"] ?? "" }
    static let megabyte = 1024 * 1024
    static var queue: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("DeviceRelayTest")
    }

    @MainActor final class Probe {
        var task: Task<Void, Never>?
        var cutAt: Int?
        var firstAfterResume: Int?
    }

    private func folder() throws -> URL {
        let name = try #require(Self.environment["CHEWSENSE_DEVICE_SESSION"])
        let folder = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent(name)
        #expect(FileManager.default.fileExists(atPath: folder.appendingPathComponent("session.json").path))
        return folder
    }

    private func manager(fresh: Bool = false) -> (UploadManager, LoopLink) {
        if fresh { try? FileManager.default.removeItem(at: Self.queue) }
        let link = LoopLink(pairingKey: "LoopPairedMac", deviceKey: "LoopDeviceID")
        let connection = MacConnection(catalog: ExperimentCatalogStore(directory: Self.queue.appendingPathComponent("catalog")),
                                       link: link)
        return (UploadManager(connection: connection, recorder: RecorderController(),
                              directory: Self.queue.appendingPathComponent("Uploads")), link)
    }

    private func report(_ values: [String: Any]) {
        let url = Self.queue.appendingPathComponent("result-\(Self.phase).json")
        try? FileManager.default.createDirectory(at: Self.queue, withIntermediateDirectories: true)
        try? JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys]).write(to: url)
    }

    /// The app learned the home server from the Mac by itself, and this iPhone
    /// reaches it over the tailnet with the Pi's public certificate (no ATS exception).
    @Test func config() async throws {
        guard Self.phase == "config" else { return }
        let link = LoopLink(pairingKey: "LoopPairedMac", deviceKey: "LoopDeviceID")
        let config = try #require(link.mailbox.config, "the app has not received the home server from the Mac")
        #expect(URL(string: config.url)?.host == "kiosk-lab.tailef7acc.ts.net")
        #expect(config.fingerprint == nil)
        let copy = try await link.mailbox.status()
        #expect(copy.status["pipeline"] != nil)
        report(["url": config.url, "status_updated_at": copy.updatedAt ?? ""])
    }

    /// Mac away (its service stopped): the recording is sealed and deposited on the
    /// Pi. The transfer is cut off part way, as if the app were closed, and a new
    /// outbox on the same queue continues from the Pi's offset.
    @Test func deposit() async throws {
        guard Self.phase == "deposit" else { return }
        let folder = try folder()
        let probe = Probe()
        let (first, link) = manager(fresh: true)
        first.afterChunk = { offset in
            if probe.cutAt == nil, offset >= 40 * Self.megabyte {
                probe.cutAt = offset
                probe.task?.cancel()
            }
        }
        let started = Date()
        probe.task = first.send(folder)
        await probe.task?.value
        let cut = try #require(probe.cutAt)
        let interrupted = try #require(first.item(for: folder))
        #expect(interrupted.state == .queued)
        #expect(interrupted.attempts == 0)
        let depositID = try #require(interrupted.depositID)
        let held = try await link.mailbox.request("GET", "deposits/\(depositID)").1
        #expect(held["offset"] as? Int == cut)

        let (second, _) = manager()
        second.afterChunk = { offset in if probe.firstAfterResume == nil { probe.firstAfterResume = offset } }
        let resumed = Date()
        await second.run()
        let item = try #require(second.item(for: folder))
        #expect(item.state == .deposited)
        #expect(item.depositID == depositID)
        #expect((probe.firstAfterResume ?? 0) > cut)
        #expect(item.attempts == 0)
        let size = item.sealedSize ?? 0
        report(["deposit_id": depositID, "sealed_bytes": size, "cut_at": cut,
                "seconds_total": Date().timeIntervalSince(started),
                "seconds_after_resume": Date().timeIntervalSince(resumed),
                "megabytes_per_second": Double(size - cut) / Double(Self.megabyte) / Date().timeIntervalSince(resumed)])
    }

    /// Mac back: it collects the deposit; the phone sees the receipt and cleans up
    /// its temporary copies. The recording itself stays.
    @Test func collect() async throws {
        guard Self.phase == "collect" else { return }
        let folder = try folder()
        let (uploads, _) = manager()
        let started = Date()
        for _ in 0..<120 where ![.received, .processed].contains(uploads.item(for: folder)?.state) {
            await uploads.run()
            await uploads.pollStatuses()
            try await Task.sleep(for: .seconds(5))
        }
        let item = try #require(uploads.item(for: folder))
        #expect([.received, .processed].contains(item.state))
        #expect(item.depositID == nil && item.archiveName == nil && item.sealedName == nil)
        let left = (try? FileManager.default.contentsOfDirectory(atPath: Self.queue.appendingPathComponent("Uploads").path)) ?? []
        #expect(left.filter { $0 != "queue.json" }.isEmpty)
        #expect(FileManager.default.fileExists(atPath: folder.appendingPathComponent("session.json").path))
        report(["state": item.state.rawValue, "seconds": Date().timeIntervalSince(started)])
    }

    /// Mac reachable: straight to it, cut off part way and resumed from the Mac's
    /// offset, never through the home server.
    @Test func direct() async throws {
        guard Self.phase == "direct" else { return }
        let folder = try folder()
        let probe = Probe()
        let (first, _) = manager(fresh: true)
        first.afterChunk = { offset in
            if probe.cutAt == nil, offset >= 40 * Self.megabyte {
                probe.cutAt = offset
                probe.task?.cancel()
            }
        }
        let started = Date()
        probe.task = first.send(folder)
        await probe.task?.value
        let cut = try #require(probe.cutAt)
        #expect(first.item(for: folder)?.state == .queued)
        #expect(first.item(for: folder)?.attempts == 0)
        let (second, _) = manager()
        second.afterChunk = { offset in if probe.firstAfterResume == nil { probe.firstAfterResume = offset } }
        await second.run()
        let item = try #require(second.item(for: folder))
        #expect([.received, .processing, .processed].contains(item.state))
        #expect(item.depositID == nil)
        #expect((probe.firstAfterResume ?? 0) > cut)
        report(["archive_bytes": item.size ?? 0, "cut_at": cut, "seconds": Date().timeIntervalSince(started)])
    }

    /// Mac away: put a resend in the app's own queue; it lands on the home server.
    @Test func backgroundDeposit() async throws {
        guard Self.phase == "background-deposit" else { return }
        let folder = try folder()
        let uploads = CollectServices.shared.uploads
        await uploads.requeue(folder)?.value
        #expect(uploads.item(for: folder)?.state == .deposited)
    }

    /// Mac back, app not syncing in the foreground (unit-test host): only the
    /// registered background task brings the item home. Launched through the
    /// scheduler's development hook, the one Xcode's debugger command uses.
    @Test func backgroundCollect() async throws {
        guard Self.phase == "background-collect" else { return }
        let folder = try folder()
        let uploads = CollectServices.shared.uploads
        #expect(uploads.item(for: folder)?.state == .deposited, "something other than the background task already synced")
        let before = UserDefaults.standard.object(forKey: CollectServices.lastBackgroundSyncKey) as? Date
        let request = BGProcessingTaskRequest(identifier: CollectServices.backgroundSyncTask)
        request.requiresNetworkConnectivity = true
        try BGTaskScheduler.shared.submit(request)
        let hook = NSSelectorFromString("_simulateLaunchForTaskWithIdentifier:")
        try #require(BGTaskScheduler.shared.responds(to: hook), "this iOS no longer offers the development launch hook")
        _ = BGTaskScheduler.shared.perform(hook, with: CollectServices.backgroundSyncTask)
        var after: Date?
        for _ in 0..<240 {
            after = UserDefaults.standard.object(forKey: CollectServices.lastBackgroundSyncKey) as? Date
            if let after, after > (before ?? .distantPast) { break }
            try await Task.sleep(for: .milliseconds(500))
        }
        #expect(after.map { $0 > (before ?? .distantPast) } == true, "the background task did not finish a sync")
        #expect([.received, .processed].contains(uploads.item(for: folder)?.state))
        report(["state": uploads.item(for: folder)?.state.rawValue ?? "missing",
                "background_sync_at": after?.description ?? ""])
    }
}
