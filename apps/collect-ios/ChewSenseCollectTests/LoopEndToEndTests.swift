import Foundation
import Testing
@testable import ChewSenseCollect

/// Opt-in: Collect's real transfer code against an isolated Mac service
/// (`pipelines/vision/tests/loop_e2e_harness.py`, started by
/// `tools/loop-e2e`). Synthetic recordings in a temporary folder only; never
/// the wearer's pairing, keychain entry or `workspace/`.
@MainActor
@Suite(.serialized, .enabled(if: ProcessInfo.processInfo.environment["CHEWSENSE_LOOP_FIXTURE"] != nil))
struct LoopEndToEndTests {
    struct Fixture: Decodable {
        let port: Int
        let code: String
        let fingerprint: String
        let sessions: [String]
    }

    static let keychain = "ChewSenseLoop.e2e"
    /// Nothing listens here; connecting fails at once, like a Mac that has moved or sleeps.
    static let deadPort = 1

    @Test func collectReachesTheMacByIdentityAndNeverLosesARecording() async throws {
        let fixturePath = try #require(ProcessInfo.processInfo.environment["CHEWSENSE_LOOP_FIXTURE"])
        let fixtureURL = URL(fileURLWithPath: fixturePath)
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: fixtureURL))
        let suite = "chewsense.e2e.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        let scratch = FileManager.default.temporaryDirectory.appendingPathComponent(suite)
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let folders = fixture.sessions.map { documents.appendingPathComponent(URL(fileURLWithPath: $0).lastPathComponent) }
        defer {
            defaults.removePersistentDomain(forName: suite)
            Keychain.delete(service: Self.keychain)
            try? FileManager.default.removeItem(at: scratch)
            folders.forEach { try? FileManager.default.removeItem(at: $0) }
            try? Data().write(to: fixtureURL.deletingLastPathComponent().appendingPathComponent("done"))
        }
        for (source, folder) in zip(fixture.sessions, folders) {
            try? FileManager.default.removeItem(at: folder)
            try FileManager.default.copyItem(at: URL(fileURLWithPath: source), to: folder)
        }

        // 1. Pair once. The Mac reports how else to reach it.
        let link = makeLink("pairing", defaults)
        try await link.pair(host: "127.0.0.1", port: fixture.port, code: fixture.code, name: "E2E Mac",
                            deviceName: "E2E iPhone")
        #expect(link.pairing?.fingerprint == fixture.fingerprint)
        #expect(link.pairing?.routes == [LoopRoute(host: "127.0.0.1", port: fixture.port, kind: .lan)])

        // 2. The Mac changed network: the saved address stops answering. The
        //    phone finds it on a reported route, pinned identity checked, no new code.
        var stale = try #require(link.pairing)
        stale.port = Self.deadPort
        defaults.set(try JSONEncoder().encode(stale), forKey: "moved")
        let moved = makeLink("moved", defaults)
        let status = try await moved.status()
        #expect(status["service"] as? String == "chewsense-loop")
        #expect(moved.pairing?.port == fixture.port)

        // 3. The Mac is asleep (no route answers). Recordings wait without failing.
        var asleep = stale
        asleep.routes = nil
        defaults.set(try JSONEncoder().encode(asleep), forKey: "asleep")
        let connection = MacConnection(catalog: ExperimentCatalogStore(directory: scratch.appendingPathComponent("catalog"),
                                                                       defaults: defaults),
                                       link: makeLink("asleep", defaults))
        let recorder = RecorderController()
        let uploads = UploadManager(connection: connection, recorder: recorder,
                                    directory: scratch.appendingPathComponent("Uploads"))
        folders.forEach(uploads.send)
        for _ in 0..<3 { await uploads.run() }
        for folder in folders {
            let item = try #require(uploads.item(for: folder))
            #expect(item.state == .queued)
            #expect(item.attempts == 0)
            #expect(item.message?.contains("automatically") == true)
        }
        await connection.refresh()
        guard case .offline(let reason) = connection.state else {
            Issue.record("expected offline, got \(connection.state)")
            return
        }
        #expect(!reason.contains("127.0.0.1"))

        // 4. The Mac answers again: both queued recordings send, verify and process.
        try await connection.updateAddress(host: "127.0.0.1", port: fixture.port)
        try await waitUntil(uploads, folders: folders) { $0 == .processed }

        // 5. The phone forgot its queue (reinstall) and sends one again: the Mac
        //    recognizes the archive; the harness checks no second import or job.
        let fresh = UploadManager(connection: connection, recorder: recorder,
                                  directory: scratch.appendingPathComponent("Uploads-again"))
        fresh.send(folders[0])
        try await waitUntil(fresh, folders: [folders[0]]) { $0 == .processed || $0 == .received }
    }

    private func makeLink(_ key: String, _ defaults: UserDefaults) -> LoopLink {
        let link = LoopLink(pairingKey: key, deviceKey: "device", defaults: defaults, keychainService: Self.keychain)
        link.usesBonjour = false
        return link
    }

    private func waitUntil(_ uploads: UploadManager, folders: [URL],
                           _ done: (UploadManager.Item.State) -> Bool) async throws {
        for _ in 0..<300 {
            await uploads.run()
            await uploads.pollStatuses()
            if folders.allSatisfy({ uploads.item(for: $0).map { done($0.state) } ?? false }) { return }
            try await Task.sleep(for: .milliseconds(200))
        }
        Issue.record("timed out: \(folders.map { uploads.item(for: $0)?.state.rawValue ?? "missing" })")
    }
}
