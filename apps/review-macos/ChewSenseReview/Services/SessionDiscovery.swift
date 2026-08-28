import Foundation

struct SessionDiscovery {
    let paths: WorkspacePaths
    private let fm = FileManager.default

    func listSessions() -> [Session] {
        guard let derivedContents = try? fm.contentsOfDirectory(
            at: paths.derivedRoot,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }

        var sessions: [Session] = []
        for derivedDir in derivedContents where isDirectory(derivedDir) {
            let sid = derivedDir.lastPathComponent
            let rawDir = paths.raw(for: sid)
            let annotations = rawDir.appending(path: "annotations")
            let draftURL = annotations.appending(path: "review-draft.json")
            let draft = loadDraftMetadata(draftURL)
            let candidates = candidateURL(in: derivedDir, linkedRevision: draft?.candidateRevision)

            // Legacy sessions remain visible but cannot be silently reviewed as
            // v2. Selecting one gives an explicit migration instruction.
            let legacy = derivedDir.appending(path: "chew_cycles.json")
            guard candidates != nil || fm.fileExists(atPath: legacy.path) else { continue }

            let video = findFirst(in: rawDir, extensions: ["mov", "mp4", "MOV", "MP4"])
            let motion = findFirst(in: rawDir, extensions: ["csv"])
            let aligned = derivedDir.appending(path: "motion_aligned_labels.csv")
            let alignedURL = fm.fileExists(atPath: aligned.path) ? aligned : nil
            let status: Session.Status
            if candidates == nil {
                status = .migrationRequired
            } else if draft?.verified == true {
                status = .verified
            } else if draft != nil {
                status = .inReview
            } else {
                status = .candidates
            }

            let annotated = derivedDir.appending(path: "annotated_video.mp4")
            let annotatedURL = fm.fileExists(atPath: annotated.path) ? annotated : nil
            let framePreds = derivedDir.appending(path: "frame_predictions.csv")
            let framePredsURL = fm.fileExists(atPath: framePreds.path) ? framePreds : nil

            sessions.append(Session(
                id: sid,
                rawDir: rawDir,
                derivedDir: derivedDir,
                videoURL: video,
                annotatedVideoURL: annotatedURL,
                framePredictionsCSV: framePredsURL,
                motionCSV: motion,
                alignedCSV: alignedURL,
                candidatesJSON: candidates,
                overridesURL: draftURL,
                status: status
            ))
        }
        return sessions.sorted { $0.id < $1.id }
    }

    private func isDirectory(_ url: URL) -> Bool {
        (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
    }

    private func findFirst(in dir: URL, extensions: [String]) -> URL? {
        guard let entries = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) else { return nil }
        return entries.first { extensions.contains($0.pathExtension) }
    }

    private func loadDraftMetadata(_ url: URL) -> ReviewDraft? {
        guard fm.fileExists(atPath: url.path), let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(ReviewDraft.self, from: data)
    }

    private func candidateURL(in directory: URL, linkedRevision: Int?) -> URL? {
        if let linkedRevision {
            // A draft must always reopen the exact immutable artifact it names.
            // Returning the expected path even when missing lets loadSession
            // surface an actionable lineage error rather than selecting another
            // revision behind the reviewer's back.
            return directory.appending(path: String(format: "candidates-v%04d.json", linkedRevision))
        }
        let entries = (try? fm.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? []
        let revisions: [(Int, URL)] = entries.compactMap { url in
            let name = url.lastPathComponent
            guard name.hasPrefix("candidates-v"), name.hasSuffix(".json") else { return nil }
            let start = name.index(name.startIndex, offsetBy: "candidates-v".count)
            let end = name.index(name.endIndex, offsetBy: -".json".count)
            guard let revision = Int(name[start..<end]) else { return nil }
            return (revision, url)
        }
        return revisions.max { $0.0 < $1.0 }?.1
    }
}
