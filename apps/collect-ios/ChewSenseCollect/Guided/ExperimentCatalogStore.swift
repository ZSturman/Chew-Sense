import Foundation
import Combine

/// Offline-first experiment catalog: the last successfully synchronized catalog,
/// falling back to the seed bundled with the app. Lives in Application Support,
/// never in Documents (every Documents folder is treated as a recording).
final class ExperimentCatalogStore: ObservableObject {
    @Published private(set) var catalog: ExperimentCatalog
    @Published private(set) var source: Source
    @Published private(set) var lastSyncError: String?
    /// Locally recorded sessions per experiment not yet reflected by the Mac.
    @Published private(set) var localCompleted: [String: Int] = [:]

    enum Source: Equatable { case bundledSeed, synchronized(Date) }

    private let cacheURL: URL
    private let localCountsKey = "GuidedLocalCompleted"

    init(directory: URL = ExperimentCatalogStore.defaultDirectory()) {
        cacheURL = directory.appendingPathComponent("experiment-catalog.json")
        if let data = try? Data(contentsOf: cacheURL), let cached = try? ExperimentCatalog.parse(data) {
            catalog = cached
            let date = (try? cacheURL.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate ?? Date()
            source = .synchronized(date)
        } else {
            catalog = Self.bundledSeed()
            source = .bundledSeed
        }
        localCompleted = UserDefaults.standard.dictionary(forKey: localCountsKey) as? [String: Int] ?? [:]
    }

    static func defaultDirectory() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Guided", isDirectory: true)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        return base
    }

    static func bundledSeed() -> ExperimentCatalog {
        guard let url = Bundle.main.url(forResource: "SeedExperimentCatalog", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let catalog = try? ExperimentCatalog.parse(data) else {
            return ExperimentCatalog(catalogVersion: 0, generatedAt: "", entries: [])
        }
        return catalog
    }

    /// Applies a catalog from the Mac. Invalid data leaves the cache untouched.
    @discardableResult
    func apply(_ data: Data) throws -> ExperimentCatalog {
        do {
            let parsed = try ExperimentCatalog.parse(data)
            try data.write(to: cacheURL, options: .atomic)
            catalog = parsed
            source = .synchronized(Date())
            lastSyncError = nil
            // The Mac's progress now includes everything it has received.
            localCompleted = [:]
            UserDefaults.standard.set(localCompleted, forKey: localCountsKey)
            return parsed
        } catch {
            lastSyncError = error.localizedDescription
            throw error
        }
    }

    func recordLocalCompletion(of experimentID: String) {
        localCompleted[experimentID, default: 0] += 1
        UserDefaults.standard.set(localCompleted, forKey: localCountsKey)
    }

    func remaining(for entry: CatalogEntry) -> Int {
        max(0, entry.progress.remaining - (localCompleted[entry.definition.experimentID] ?? 0))
    }

    func completed(for entry: CatalogEntry) -> Int {
        entry.progress.completedValid + (localCompleted[entry.definition.experimentID] ?? 0)
    }

    var sortedEntries: [CatalogEntry] {
        let rank = ["high": 0, "medium": 1, "low": 2]
        return catalog.entries.sorted {
            let a = (remaining(for: $0) == 0, !$0.definition.required, rank[$0.effectivePriority] ?? 3)
            let b = (remaining(for: $1) == 0, !$1.definition.required, rank[$1.effectivePriority] ?? 3)
            if a.0 != b.0 { return !a.0 }
            if a.1 != b.1 { return !a.1 }
            if a.2 != b.2 { return a.2 < b.2 }
            return $0.definition.title < $1.definition.title
        }
    }
}
