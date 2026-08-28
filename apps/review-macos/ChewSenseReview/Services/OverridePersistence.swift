import Foundation

enum OverridePersistence {
    /// Save only the mutable decision draft. Immutable numbered review
    /// revisions and materialized motion targets are created exclusively by
    /// `chewsense-vision finalize-session` after audit succeeds.
    static func save(_ draft: ReviewDraft, session: Session) throws {
        let fm = FileManager.default
        let annotations = session.rawDir.appending(path: "annotations")
        try fm.createDirectory(at: annotations, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(draft)
        try data.write(to: session.overridesURL, options: .atomic)
    }

    static func loadIfPresent(_ url: URL) throws -> ReviewDraft? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(ReviewDraft.self, from: data)
    }
}
