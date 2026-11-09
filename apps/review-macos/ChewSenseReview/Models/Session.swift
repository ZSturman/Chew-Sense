import Foundation

/// `loop/state/retired/<session_id>.json`: a recording withdrawn from review
/// queues, training and evaluation. Its files are kept unchanged.
struct RecordingRetirement: Decodable, Equatable {
    let sessionId: String
    let retiredAt: String
    let category: String
    let categoryTitle: String
    let reason: String

    /// The retirement for a session in a loop `assisted` or `blind` workspace, if any.
    static func load(sessionID: String, workspaceRoot: URL) -> RecordingRetirement? {
        guard ["assisted", "blind"].contains(workspaceRoot.lastPathComponent) else { return nil }
        let url = workspaceRoot.deletingLastPathComponent()
            .appending(path: "state/retired/\(sessionID).json")
        guard let data = try? Data(contentsOf: url) else { return nil }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(RecordingRetirement.self, from: data)
    }
}

/// Locates session and derived artifacts inside a selected ChewSense workspace.
struct WorkspacePaths {
    let root: URL

    var rawSessions: URL  { root.appending(path: "sessions/raw") }
    var derivedRoot: URL  { root.appending(path: "derived/vision") }

    func derived(for sessionID: String) -> URL { derivedRoot.appending(path: sessionID) }
    func raw(for sessionID: String) -> URL     { rawSessions.appending(path: sessionID) }
}

struct Session: Identifiable, Hashable {
    enum Status: String { case candidates, inReview = "in-review", verified, migrationRequired = "migration-required", retired }

    let id: String
    let rawDir: URL
    let derivedDir: URL
    let videoURL: URL?
    let annotatedVideoURL: URL?
    let framePredictionsCSV: URL?
    let motionCSV: URL?
    let alignedCSV: URL?
    let candidatesJSON: URL?
    let overridesURL: URL
    let status: Status
}
