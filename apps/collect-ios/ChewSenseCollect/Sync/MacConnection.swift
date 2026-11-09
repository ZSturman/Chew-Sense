import Foundation
import Combine
import UIKit

/// Collect's view of the Mac: status, experiment catalog and the upload API.
///
/// Pairing, pinned requests and finding the Mac again on another network live
/// in the shared `LoopLink` (apps/shared-capture/LoopLink.swift, also used by
/// Live). Recording never depends on this connection; see docs/COMMUNICATION.md.
final class MacConnection: NSObject, ObservableObject {
    typealias Paired = LoopPairing
    typealias ConnectionError = LoopLink.Failure

    enum State: Equatable {
        case unpaired
        case connecting
        case connected(pending: Int)
        case offline(String)
    }

    @Published private(set) var paired: Paired?
    @Published private(set) var state: State = .unpaired
    @Published private(set) var lastSync: Date?
    /// True while looking for the paired Mac on its other routes.
    @Published private(set) var isSearching = false
    @Published private(set) var pipeline: PipelineStatus?
    /// The shared "where we are" message, also shown by Review and Live.
    /// Decoded separately so an unexpected shape never hides `pipeline`.
    @Published private(set) var project: ProjectStatus?
    /// When the Mac is away: when it last published status to the home server.
    @Published private(set) var homeStatusAt: Date?

    struct PipelineStatus: Decodable {
        struct Goal: Decodable, Identifiable {
            let id: String
            let title: String
            let current: Int
            let target: Int
        }
        struct Stage: Decodable, Identifiable {
            let id: String
            let title: String
            let state: String
            let detail: String
        }
        struct Action: Decodable, Identifiable {
            let id: String
            let kind: String
            let destination: String
            let title: String
            let detail: String
            let experimentId: String?
            let sessionId: String?
        }
        let reviewTasks: Int
        let attentionCount: Int
        let trainingReady: Bool
        let researchTargetsMet: Bool
        let motionAssessmentPassed: Bool?
        let blockers: [String]
        let nextAction: String
        let goals: [Goal]?
        let stages: [Stage]?
        let actions: [Action]?
        let receivedSessions: Int?
        let completeSessions: Int?
        let incompleteSessions: Int?
        let availableReviewTasks: Int?
        let nextExperimentId: String?
    }

    let link: LoopLink
    let catalog: ExperimentCatalogStore

    init(catalog: ExperimentCatalogStore, link: LoopLink? = nil) {
        self.catalog = catalog
        let link = link ?? LoopLink(pairingKey: "LoopPairedMac", deviceKey: "LoopDeviceID")
        self.link = link
        super.init()
        link.usesBonjour = !CollectionTestMode.isEnabled
        link.onChange = { [weak self] in self?.mirror() }
        mirror()
        if paired != nil { state = .connecting }
    }

    private func mirror() {
        if paired != link.pairing { paired = link.pairing }
        if isSearching != link.isSearching { isSearching = link.isSearching }
    }

    // MARK: Pairing

    func pair(host: String, port: Int, code: String, name: String) async throws {
        try await link.pair(host: host, port: port, code: code, name: name, deviceName: UIDevice.current.name)
        await refresh()
    }

    func forget() {
        link.forget()
        pipeline = nil
        project = nil
        state = .unpaired
    }

    /// Looks for the Mac on its other routes now, ignoring the 30-second limit.
    @discardableResult
    func relocate() async -> Bool { await link.findMac(force: true) }

    /// Points the pairing at a typed address once the Mac there proves its identity.
    func updateAddress(host: String, port: Int) async throws { try await link.useAddress(host: host, port: port) }

    // MARK: API

    func request(_ method: String, _ path: String, query: String? = nil, body: Data? = nil) async throws -> Data {
        try await link.request(method, path, query: query, body: body)
    }

    /// Recordings may cross cellular only to a Mac on the same local network.
    var mayTransferRecordings: Bool {
        guard let paired else { return false }
        return LoopNetwork.shared.allowsRecordingTransfer(over: paired.current)
    }

    /// Plain-language reason for the status row. Addresses stay under Details.
    static func offlineDescription(_ error: Error) -> String {
        if LoopLink.isUnreachable(error) {
            return "Your Mac isn't reachable right now. It may be asleep, or the ChewSense service isn't running."
        }
        return (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }

    /// Fetches status and the catalog. Safe to call often.
    func refresh() async {
        guard paired != nil else { state = .unpaired; return }
        if case .offline = state {} else if case .connected = state {} else { state = .connecting }
        do {
            let status = try await link.status()
            try apply(status)
            let since = catalog.source == .bundledSeed ? nil : "since=\(catalog.catalog.catalogVersion)"
            let data = try await request("GET", "v1/catalog", query: since)
            if let object = try JSONSerialization.jsonObject(with: data) as? [String: Any], object["unchanged"] == nil {
                try catalog.apply(data)
            }
            lastSync = Date()
            homeStatusAt = nil
            state = .connected(pending: status["pending"] as? Int ?? 0)
        } catch {
            state = .offline(Self.offlineDescription(error))
            // The Mac is away: show what it last published to the home server, if any.
            if link.mailbox.isConfigured, let copy = try? await link.mailbox.status(), !copy.status.isEmpty {
                try? apply(copy.status)
                homeStatusAt = LoopMailbox.date(copy.updatedAt) ?? Date()
            }
        }
    }

    /// Status from the Mac, or the copy it published to the home server.
    private func apply(_ status: [String: Any]) throws {
        guard var summary = status["pipeline"] as? [String: Any] else { return }
        let shared = summary.removeValue(forKey: "project")
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        pipeline = try decoder.decode(PipelineStatus.self, from: JSONSerialization.data(withJSONObject: summary))
        project = shared.flatMap { try? JSONSerialization.data(withJSONObject: $0) }
            .flatMap { try? ProjectStatus.decode($0) }
    }
}
