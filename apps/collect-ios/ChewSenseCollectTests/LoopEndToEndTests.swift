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
        let control: String?
        let mailbox: Bool?
    }

    /// Offsets seen by the transfer hook.
    @MainActor final class Chunks {
        var interruptedAt: Int?
        var afterResume: [Int] = []
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
        // The first two synthetic recordings belong to this test; the relay test uses the rest.
        let sources = Array(fixture.sessions.prefix(2))
        let folders = sources.map { documents.appendingPathComponent(URL(fileURLWithPath: $0).lastPathComponent) }
        defer {
            defaults.removePersistentDomain(forName: suite)
            Keychain.delete(service: Self.keychain)
            try? FileManager.default.removeItem(at: scratch)
            folders.forEach { try? FileManager.default.removeItem(at: $0) }
        }
        for (source, folder) in zip(sources, folders) {
            try? FileManager.default.removeItem(at: folder)
            try FileManager.default.copyItem(at: URL(fileURLWithPath: source), to: folder)
        }

        // 1. Pair once. The Mac reports how else to reach it.
        var code = fixture.code
        if let control = fixture.control { code = try await command("pairing", URL(fileURLWithPath: control)) }
        let link = makeLink("pairing", defaults)
        try await link.pair(host: "127.0.0.1", port: fixture.port, code: code, name: "E2E Mac",
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

        // 3. The Mac is asleep (no route answers) and there is no home server.
        //    Recordings wait without failing.
        link.mailbox.forget()
        var asleep = stale
        asleep.routes = nil
        defaults.set(try JSONEncoder().encode(asleep), forKey: "asleep")
        let connection = MacConnection(catalog: ExperimentCatalogStore(directory: scratch.appendingPathComponent("catalog"),
                                                                       defaults: defaults),
                                       link: makeLink("asleep", defaults))
        let recorder = RecorderController()
        let uploads = UploadManager(connection: connection, recorder: recorder,
                                    directory: scratch.appendingPathComponent("Uploads"))
        folders.forEach { uploads.send($0) }
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

    @Test func homeServerCarriesRecordingsWhileTheMacIsAway() async throws {
        let fixturePath = try #require(ProcessInfo.processInfo.environment["CHEWSENSE_LOOP_FIXTURE"])
        let fixtureURL = URL(fileURLWithPath: fixturePath)
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: fixtureURL))
        guard fixture.mailbox == true, let controlPath = fixture.control else { return }
        let control = URL(fileURLWithPath: controlPath)
        let suite = "chewsense.e2e.relay.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        let scratch = FileManager.default.temporaryDirectory.appendingPathComponent(suite)
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let sources = Array(fixture.sessions.dropFirst(2))
        let folders = sources.map { documents.appendingPathComponent(URL(fileURLWithPath: $0).lastPathComponent) }
        defer {
            defaults.removePersistentDomain(forName: suite)
            Keychain.delete(service: Self.keychain)
            Keychain.delete(service: Self.keychain + ".mailbox")
            try? FileManager.default.removeItem(at: scratch)
            folders.forEach { try? FileManager.default.removeItem(at: $0) }
        }
        for (source, folder) in zip(sources, folders) {
            try? FileManager.default.removeItem(at: folder)
            try FileManager.default.copyItem(at: URL(fileURLWithPath: source), to: folder)
        }
        let (direct, deposited, waiting, resumed) = (folders[0], folders[1], folders[2], folders[3])

        // Pairing hands the phone the home server too; nothing to set up.
        let code = try await command("pairing", control)
        let link = makeLink("relay", defaults)
        try await link.pair(host: "127.0.0.1", port: fixture.port, code: code, name: "E2E Mac", deviceName: "E2E iPhone")
        _ = try await link.status()
        #expect(link.mailbox.isConfigured)
        let connection = MacConnection(catalog: ExperimentCatalogStore(directory: scratch.appendingPathComponent("catalog"),
                                                                       defaults: defaults), link: link)
        let uploads = UploadManager(connection: connection, recorder: RecorderController(),
                                    directory: scratch.appendingPathComponent("Uploads"), chunkSize: 8 * 1024)
        let chunks = Chunks()

        // 1. The Mac answers: straight to it, never through the home server.
        uploads.send(direct)
        try await waitUntil(uploads, folders: [direct]) { $0 == .processed }
        #expect(uploads.item(for: direct)?.depositID == nil)

        // 2. The Mac sleeps: the recording goes to the home server, encrypted.
        //    The home server goes down mid-deposit; the deposit resumes where it stopped.
        _ = try await command("mac-sleep", control)
        uploads.afterChunk = { offset in
            if chunks.interruptedAt == nil {
                chunks.interruptedAt = offset
                _ = try? await self.command("pi-stop", control)
            }
        }
        uploads.send(deposited)
        try await waitFor { chunks.interruptedAt != nil && uploads.item(for: deposited)?.state == .queued }
        let depositID = try #require(uploads.item(for: deposited)?.depositID)
        #expect(uploads.item(for: deposited)?.attempts == 0)
        uploads.afterChunk = { chunks.afterResume.append($0) }
        _ = try await command("pi-start", control)
        let view = try await link.mailbox.request("GET", "deposits/\(depositID)").1
        #expect(view["offset"] as? Int == chunks.interruptedAt)
        try await waitUntil(uploads, folders: [deposited]) { $0 == .deposited }
        #expect(uploads.item(for: deposited)?.depositID == depositID)
        #expect((chunks.afterResume.first ?? 0) > (chunks.interruptedAt ?? .max))

        // 3. Neither the Mac nor the home server answers: it waits, never fails.
        _ = try await command("pi-stop", control)
        uploads.afterChunk = nil
        uploads.send(waiting)
        try await waitFor { uploads.item(for: waiting)?.message == UploadManager.waitingForMac }
        #expect(uploads.item(for: waiting)?.state == .queued)
        #expect(uploads.item(for: waiting)?.attempts == 0)
        _ = try await command("pi-start", control)
        try await waitUntil(uploads, folders: [waiting]) { $0 == .deposited }

        // 4. The Mac wakes and sleeps again mid-transfer (home server down, so
        //    nothing is diverted); the direct transfer resumes, not restarts.
        _ = try await command("pi-stop", control)
        _ = try await command("mac-wake", control)
        chunks.interruptedAt = nil
        chunks.afterResume = []
        uploads.afterChunk = { offset in
            if chunks.interruptedAt == nil {
                chunks.interruptedAt = offset
                _ = try? await self.command("mac-sleep", control)
            } else {
                chunks.afterResume.append(offset)
            }
        }
        uploads.send(resumed)
        try await waitFor { chunks.interruptedAt != nil && uploads.item(for: resumed)?.state == .queued }
        _ = try await command("mac-wake", control)
        _ = try await command("pi-start", control)

        // 5. Awake again, the Mac collects what waited on the home server, verifies,
        //    imports and acknowledges; every recording ends processed on the phone.
        try await waitUntil(uploads, folders: [resumed, deposited, waiting]) { $0 == .processed }
        #expect(uploads.item(for: resumed)?.depositID == nil)
        #expect((chunks.afterResume.first ?? 0) > (chunks.interruptedAt ?? .max))
        for folder in [direct, deposited, waiting, resumed] {
            #expect(FileManager.default.fileExists(atPath: folder.appendingPathComponent("session.json").path))
        }
        let leftovers = (try? FileManager.default.contentsOfDirectory(atPath: scratch.appendingPathComponent("Uploads").path)) ?? []
        #expect(leftovers.filter { $0 != "queue.json" }.isEmpty)
    }

    /// Asks the harness to act (mac-sleep, mac-wake, pi-stop, pi-start, pairing) and waits for it.
    private func command(_ action: String, _ control: URL) async throws -> String {
        let number = String(format: "%015.0f", Date().timeIntervalSince1970 * 1_000_000)
        try Data().write(to: control.appendingPathComponent("\(number)-\(action)"))
        let done = control.appendingPathComponent("\(number).done")
        for _ in 0..<600 {
            if let answer = try? String(contentsOf: done, encoding: .utf8) { return answer }
            try await Task.sleep(for: .milliseconds(50))
        }
        Issue.record("the harness did not carry out \(action)")
        return ""
    }

    private func waitFor(_ condition: () -> Bool) async throws {
        for _ in 0..<600 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(50))
        }
        Issue.record("timed out waiting for a condition")
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
