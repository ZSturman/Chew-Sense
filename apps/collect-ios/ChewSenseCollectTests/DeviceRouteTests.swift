import Foundation
import Testing
@testable import ChewSenseCollect

/// Opt-in check on the paired physical iPhone: the Mac answers, with its
/// pinned identity, on each route given in TEST_RUNNER_CHEWSENSE_DEVICE_ROUTES
/// (`kind=host:port,…`, e.g. `tailnet=100.84.135.118:48765`). Uses Collect's
/// existing pairing read-only: nothing is saved, sent or uploaded.
@MainActor
@Suite(.enabled(if: ProcessInfo.processInfo.environment["CHEWSENSE_DEVICE_ROUTES"] != nil))
struct DeviceRouteTests {
    @Test func pairedMacAnswersOnEveryRoute() async throws {
        #if targetEnvironment(simulator)
        return
        #else
        let spec = try #require(ProcessInfo.processInfo.environment["CHEWSENSE_DEVICE_ROUTES"])
        let link = LoopLink(pairingKey: "LoopPairedMac", deviceKey: "LoopDeviceID")
        let before = try #require(link.pairing, "Collect is not paired on this device")
        for item in spec.split(separator: ",") {
            let parts = item.split(separator: "=", maxSplits: 1)
            let address = try #require(parts.last?.split(separator: ":"))
            let kind = try #require(LoopRoute.Kind(rawValue: String(parts[0])))
            let route = LoopRoute(host: String(address[0]), port: Int(address[1]) ?? 48765, kind: kind)
            do {
                try await link.check(route)
            } catch {
                Issue.record("no pinned answer on \(route.kind.rawValue) \(route.host): \(error)")
            }
        }
        #expect(link.pairing == before)
        #endif
    }
}
