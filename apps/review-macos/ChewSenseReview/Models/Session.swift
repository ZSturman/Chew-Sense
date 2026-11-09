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

/// Suggests a display rotation for videos whose picture is stored sideways
/// with no rotation metadata. Vision's face pose then reads about ±90° in
/// `head_yaw` for every frame (upright recordings read about 0). Checked on the
/// sideways 90-Second Baseline (+1.52 rad, fixed by one quarter turn right);
/// the negative direction is by symmetry. Display only; the file is unchanged.
enum VideoOrientation {
    static func suggestedQuarterTurns(framePredictions url: URL) -> Int? {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        var lines = text.split(separator: "\n", omittingEmptySubsequences: true).makeIterator()
        guard let header = lines.next()?.split(separator: ",", omittingEmptySubsequences: false).map(String.init),
              let yawColumn = header.firstIndex(of: "head_yaw"),
              let faceColumn = header.firstIndex(of: "face_detected") else { return nil }
        var yaws: [Double] = []
        var frames = 0
        while let line = lines.next() {
            frames += 1
            let fields = line.split(separator: ",", omittingEmptySubsequences: false)
            guard fields.count > max(yawColumn, faceColumn), fields[faceColumn] == "True",
                  let yaw = Double(fields[yawColumn]), yaw.isFinite else { continue }
            yaws.append(yaw)
        }
        // Only when the face is seen in most frames and consistently sideways.
        guard frames > 0, Double(yaws.count) / Double(frames) >= 0.5 else { return nil }
        let median = yaws.sorted()[yaws.count / 2]
        if median > 1.2 { return 1 }
        if median < -1.2 { return -1 }
        return nil
    }

    /// Vision output for this recording: its own, or the assisted copy's for a blind review
    /// (only the face pose is read; no detector results).
    static func suggestion(for session: Session, workspaceRoot: URL?) -> Int? {
        var candidates = [session.derivedDir.appending(path: "frame_predictions.csv")]
        if let workspaceRoot, workspaceRoot.lastPathComponent == "blind" {
            candidates.append(workspaceRoot.deletingLastPathComponent()
                .appending(path: "assisted/derived/vision/\(session.id)/frame_predictions.csv"))
        }
        return candidates.lazy.compactMap { FileManager.default.fileExists(atPath: $0.path) ? suggestedQuarterTurns(framePredictions: $0) : nil }.first
    }
}

/// Retires the open recording through the loop's own command
/// (`chewsense-vision loop retire-recording`), so Review and the Mac service
/// apply exactly the same rules: nothing deleted, a write-once record with the
/// reason and file hashes, review tasks and shared status refreshed.
enum RecordingRetirer {
    /// Choices offered in Review (keys must exist in loop/retirement.py CATEGORIES).
    static let categories: [(key: String, title: String)] = [
        ("reviewer_excluded", "Left out by the reviewer"),
        ("protocol_mismatch", "Doesn't match what was asked"),
        ("capture_defect", "Video, motion or timing unusable"),
        ("mimed_chewing_no_food", "Mimed chewing / chewing-like movement without food"),
    ]

    enum Failure: LocalizedError {
        case notLoopWorkspace, missingTool(String), failed(String)
        var errorDescription: String? {
            switch self {
            case .notLoopWorkspace: "Only recordings from the Mac's loop workspace can be left out here."
            case .missingTool(let path): "The ChewSense tools weren't found at \(path)."
            case .failed(let output): output.isEmpty ? "The Mac couldn't leave the recording out." : output
            }
        }
    }

    /// Executable, arguments and environment for one retirement.
    static func command(sessionID: String, workspaceRoot: URL, category: String, reason: String,
                        tool override: URL? = nil) throws
        -> (executable: URL, arguments: [String], environment: [String: String]) {
        let loop = workspaceRoot.deletingLastPathComponent()
        guard ["assisted", "blind"].contains(workspaceRoot.lastPathComponent), loop.lastPathComponent == "loop" else {
            throw Failure.notLoopWorkspace
        }
        let workspace = loop.deletingLastPathComponent()
        let tool = override ?? workspace.deletingLastPathComponent().appending(path: ".venv/bin/chewsense-vision")
        guard FileManager.default.isExecutableFile(atPath: tool.path) else { throw Failure.missingTool(tool.path) }
        var environment = ProcessInfo.processInfo.environment
        environment["CHEWSENSE_WORKSPACE"] = workspace.path
        return (tool, ["loop", "retire-recording", sessionID, "--category", category, "--reason", reason], environment)
    }

    static func retire(sessionID: String, workspaceRoot: URL, category: String, reason: String,
                       tool: URL? = nil) async throws {
        let (executable, arguments, environment) = try command(
            sessionID: sessionID, workspaceRoot: workspaceRoot, category: category, reason: reason, tool: tool)
        try await Task.detached {
            let process = Process()
            process.executableURL = executable
            process.arguments = arguments
            process.environment = environment
            let output = Pipe()
            process.standardOutput = output
            process.standardError = output
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else {
                let text = String(decoding: data, as: UTF8.self)
                let lastLine = text.split(separator: "\n").last.map(String.init) ?? ""
                throw Failure.failed(lastLine)
            }
        }.value
    }
}
