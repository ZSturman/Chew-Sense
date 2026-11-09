import Combine
import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// Read-only link to the ChewSense loop service on the Mac, so Live shows the
/// same "where we are" message as Collect and Review. Live pairs as its own
/// device but only reads `GET /v1/status`; it never uploads recordings.
/// Pairing, pinning and finding the Mac on another network are the shared
/// `LoopLink` (apps/shared-capture/LoopLink.swift), as in Collect.
final class LiveMacStatus: ObservableObject {
    typealias Paired = LoopPairing
    typealias LinkError = LoopLink.Failure

    enum State: Equatable {
        case unpaired
        case connecting
        case connected
        case offline(String)
    }

    @Published private(set) var paired: Paired?
    @Published private(set) var state: State = .unpaired
    @Published private(set) var project: ProjectStatus?

    private static let projectKey = "LiveLoopProjectStatus"
    private let defaults: UserDefaults
    let link: LoopLink

    init(defaults: UserDefaults = .standard, link: LoopLink? = nil) {
        self.defaults = defaults
        self.link = link ?? LoopLink(pairingKey: "LiveLoopPairedMac", deviceKey: "LiveLoopDeviceID", defaults: defaults)
        self.link.onChange = { [weak self] in
            guard let self, self.paired != self.link.pairing else { return }
            self.paired = self.link.pairing
        }
        paired = self.link.pairing
        if paired != nil { state = .connecting }
        // The last received message stays visible offline, marked as such.
        project = defaults.data(forKey: Self.projectKey).flatMap { try? ProjectStatus.decode($0) }
    }

    var isCurrent: Bool { state == .connected }

    private var deviceName: String {
        #if canImport(UIKit)
        UIDevice.current.name + " (Live)"
        #else
        "Live"
        #endif
    }

    func pair(host: String, port: Int, code: String, name: String) async throws {
        try await link.pair(host: host, port: port, code: code, name: name, deviceName: deviceName)
        await refresh()
    }

    func forget() {
        link.forget()
        defaults.removeObject(forKey: Self.projectKey)
        project = nil
        state = .unpaired
    }

    /// Fetches the shared project status. Safe to call often; finds the Mac
    /// on its other routes when its last address stops answering.
    func refresh() async {
        guard paired != nil else { state = .unpaired; return }
        if state != .connected { state = .connecting }
        do {
            let object = try await link.status()
            if let shared = (object["pipeline"] as? [String: Any])?["project"] {
                let encoded = try JSONSerialization.data(withJSONObject: shared)
                project = try ProjectStatus.decode(encoded)
                defaults.set(encoded, forKey: Self.projectKey)
            }
            state = .connected
        } catch {
            state = .offline(LoopLink.isUnreachable(error)
                             ? "showing the last message"
                             : (error as? LocalizedError)?.errorDescription ?? error.localizedDescription)
        }
    }
}
