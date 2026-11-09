import Foundation
import Testing
@testable import ChewSenseCollect

/// Finding the Mac by identity: routes, their order, old pairings, and the
/// rule that keeps large recordings off cellular.
@MainActor
struct LoopLinkTests {
    @Test func pairingSavedBeforeRoutesStillDecodes() throws {
        let legacy = #"{"name":"Zacharys-MacBook-Pro.local","host":"192.168.1.20","port":48765,"fingerprint":"ab"}"#
        let pairing = try JSONDecoder().decode(LoopPairing.self, from: Data(legacy.utf8))
        #expect(pairing.routes == nil)
        #expect(pairing.current == LoopRoute(host: "192.168.1.20", port: 48765, kind: .lan))
        // Its `.local` name is still tried, as the November 9 relocation did.
        #expect(pairing.alternatives == [LoopRoute(host: "Zacharys-MacBook-Pro.local", port: 48765, kind: .local)])
    }

    @Test func localNetworkRoutesComeBeforeTheTailnet() {
        let pairing = LoopPairing(name: "Mac", host: "172.20.10.13", port: 48765, fingerprint: "ab", routes: [
            LoopRoute(host: "100.84.135.118", port: 48765, kind: .tailnet),
            LoopRoute(host: "Mac.local", port: 48765, kind: .local),
            LoopRoute(host: "172.20.10.13", port: 48765, kind: .lan),
            LoopRoute(host: "192.168.1.20", port: 48765, kind: .lan),
            LoopRoute(host: "192.168.1.20", port: 48765, kind: .manual),
        ])
        #expect(pairing.current.kind == .lan)
        #expect(pairing.alternatives.map(\.host) == ["192.168.1.20", "Mac.local", "100.84.135.118"])
    }

    @Test func routesFromTheMacSkipUnknownOrMalformedEntries() {
        let routes = LoopRoute.parse([
            ["kind": "tailnet", "host": "100.84.135.118", "port": 48765],
            ["kind": "satellite", "host": "x", "port": 1],
            ["kind": "lan", "host": "", "port": 48765],
            ["kind": "lan", "port": 48765],
        ])
        #expect(routes == [LoopRoute(host: "100.84.135.118", port: 48765, kind: .tailnet)])
        #expect(LoopRoute.parse(nil).isEmpty)
    }

    @Test func tailnetAddressesAreRecognized() {
        #expect(LoopRoute.isTailnet("100.84.135.118"))
        #expect(LoopRoute.isTailnet("100.64.0.1"))
        #expect(!LoopRoute.isTailnet("100.128.0.1"))
        #expect(!LoopRoute.isTailnet("172.20.10.13"))
        #expect(!LoopRoute.isTailnet("Mac.local"))
        #expect(LoopRoute.isTailnet("zacharys-macbook-pro.tailef7acc.ts.net"))
        let typed = LoopPairing(name: "Mac", host: "100.84.135.118", port: 48765, fingerprint: "ab", routes: nil)
        #expect(typed.current.kind == .tailnet)
    }

    @Test func recordingsCrossCellularOnlyToAMacOnTheLocalNetwork() {
        let hotspot = LoopRoute(host: "172.20.10.13", port: 48765, kind: .lan)
        let tailnet = LoopRoute(host: "100.84.135.118", port: 48765, kind: .tailnet)
        #expect(LoopNetwork.allowsRecordingTransfer(over: hotspot, metered: true))
        #expect(!LoopNetwork.allowsRecordingTransfer(over: tailnet, metered: true))
        #expect(LoopNetwork.allowsRecordingTransfer(over: tailnet, metered: false))
    }

    @Test func onlyConnectivityFailuresTriggerASearch() {
        #expect(LoopLink.isUnreachable(URLError(.cannotConnectToHost)))
        #expect(LoopLink.isUnreachable(URLError(.timedOut)))
        #expect(LoopLink.isUnreachable(LoopLink.Failure.pinMismatch))
        #expect(!LoopLink.isUnreachable(LoopLink.Failure.http(401, "pair")))
        #expect(!LoopLink.isUnreachable(URLError(.cancelled)))
    }

    @Test func offlineMessageNamesTheProblemNotTheAddress() {
        let message = MacConnection.offlineDescription(URLError(.timedOut))
        #expect(message.contains("isn't reachable"))
        #expect(!message.contains("172."))
    }

    @Test func automaticSendingIsOnUnlessTurnedOff() {
        let key = "LoopAutoUpload"
        let saved = UserDefaults.standard.object(forKey: key)
        defer { UserDefaults.standard.set(saved, forKey: key) }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let connection = MacConnection(catalog: ExperimentCatalogStore(directory: directory),
                                       link: LoopLink(pairingKey: "test.\(UUID())", deviceKey: "test.device",
                                                      keychainService: "ChewSenseLoop.tests"))
        UserDefaults.standard.removeObject(forKey: key)
        #expect(UploadManager(connection: connection, recorder: RecorderController(), directory: directory).autoUpload)
        UserDefaults.standard.set(false, forKey: key)
        #expect(!UploadManager(connection: connection, recorder: RecorderController(), directory: directory).autoUpload)
    }
}
