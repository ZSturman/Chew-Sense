import Foundation

/// Locates session and derived artifacts inside a selected ChewSense workspace.
struct WorkspacePaths {
    let root: URL

    var rawSessions: URL  { root.appending(path: "sessions/raw") }
    var derivedRoot: URL  { root.appending(path: "derived/vision") }

    func derived(for sessionID: String) -> URL { derivedRoot.appending(path: sessionID) }
    func raw(for sessionID: String) -> URL     { rawSessions.appending(path: sessionID) }
}

struct Session: Identifiable, Hashable {
    enum Status: String { case auto, inReview = "in-review", verified }

    let id: String
    let rawDir: URL
    let derivedDir: URL
    let videoURL: URL?
    let annotatedVideoURL: URL?
    let framePredictionsCSV: URL?
    let motionCSV: URL?
    let alignedCSV: URL
    let chewCyclesJSON: URL
    let overridesURL: URL
    let status: Status
}
