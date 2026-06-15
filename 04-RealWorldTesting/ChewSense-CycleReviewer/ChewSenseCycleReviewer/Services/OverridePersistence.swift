import Foundation

enum OverridePersistence {
    static func save(_ overrides: ReviewOverrides, session: Session, mergedRows: [AlignedLabel]) throws {
        let fm = FileManager.default

        let snapshot = session.derivedDir.appending(path: "motion_aligned_labels.auto.csv")
        if !fm.fileExists(atPath: snapshot.path), fm.fileExists(atPath: session.alignedCSV.path) {
            try fm.copyItem(at: session.alignedCSV, to: snapshot)
        }

        try fm.createDirectory(at: session.rawDir, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(overrides)
        try data.write(to: session.overridesURL, options: .atomic)

        let annotations = session.rawDir.appending(path: "annotations")
        try fm.createDirectory(at: annotations, withIntermediateDirectories: true)
        let revisionURL = nextRevisionURL(in: annotations)
        try data.write(to: revisionURL, options: .atomic)

        try CSVLoader.writeAlignedLabels(mergedRows, to: session.alignedCSV)
    }

    private static func nextRevisionURL(in directory: URL) -> URL {
        let entries = (try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil
        )) ?? []
        let revisions = entries.compactMap { url -> Int? in
            let stem = url.deletingPathExtension().lastPathComponent
            guard stem.hasPrefix("review-overrides-v") else { return nil }
            return Int(stem.replacingOccurrences(of: "review-overrides-v", with: ""))
        }
        let next = (revisions.max() ?? 0) + 1
        return directory.appending(path: String(format: "review-overrides-v%04d.json", next))
    }

    static func loadIfPresent(_ url: URL) -> ReviewOverrides? {
        guard FileManager.default.fileExists(atPath: url.path),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(ReviewOverrides.self, from: data)
    }
}
