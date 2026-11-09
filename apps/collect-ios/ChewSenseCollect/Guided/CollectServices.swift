import BackgroundTasks
import Foundation
import Combine

/// Long-lived objects shared by manual collection, guided collection and sync.
/// One instance per process, so a background launch and the UI share one queue.
final class CollectServices: ObservableObject {
    static let shared = CollectServices(recorder: RecorderController())
    /// Lets iOS resume sending while the app is closed (Info.plist lists this identifier).
    static let backgroundSyncTask = "com.chewsense.collect.sync"
    /// When the background task last finished a sync (shown under Details).
    static let lastBackgroundSyncKey = "LoopLastBackgroundSync"
    /// Hosting unit tests: no automatic syncing, so tests never touch the real queue by accident.
    static let isUnitTestHost = ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil

    let recorder: RecorderController
    let catalog: ExperimentCatalogStore
    let connection: MacConnection
    let uploads: UploadManager
    let quality = QualityMonitor()
    private var bag = Set<AnyCancellable>()

    init(recorder: RecorderController) {
        self.recorder = recorder
        catalog = ExperimentCatalogStore()
        connection = MacConnection(catalog: catalog)
        uploads = UploadManager(connection: connection, recorder: recorder)
        // Nested objects publish independently; forward so views stay current.
        for publisher in [catalog.objectWillChange.eraseToAnyPublisher(),
                          connection.objectWillChange.eraseToAnyPublisher(),
                          uploads.objectWillChange.eraseToAnyPublisher()] {
            publisher.receive(on: DispatchQueue.main).sink { [weak self] in self?.objectWillChange.send() }.store(in: &bag)
        }
        // A new network (Wi-Fi, a hotspot) is when the Mac may have become reachable.
        LoopNetwork.shared.onChange { [weak self] in
            guard !Self.isUnitTestHost else { return }
            Task { await self?.synchronize() }
        }
    }

    /// Called when the app becomes active, when the network changes,
    /// periodically while visible, and from the background task.
    func synchronize() async {
        guard !CollectionTestMode.isEnabled else { return }
        await connection.refresh()
        // Sends to the Mac when it answers, to the home server when it doesn't, or waits.
        await uploads.run()
        scheduleBackgroundSync()
    }

    // MARK: Background

    static func registerBackgroundSync() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: backgroundSyncTask, using: .main) { task in
            MainActor.assumeIsolated {
                let work = Task { await CollectServices.shared.synchronize() }
                task.expirationHandler = { work.cancel() }
                Task {
                    await work.value
                    UserDefaults.standard.set(Date(), forKey: CollectServices.lastBackgroundSyncKey)
                    task.setTaskCompleted(success: !CollectServices.shared.uploads.hasPendingWork)
                }
            }
        }
    }

    /// Asks iOS for background time while recordings wait to be sent or processed.
    /// iOS decides when (often while charging); opening the app always syncs at once.
    func scheduleBackgroundSync() {
        guard uploads.hasPendingWork, connection.paired != nil else {
            BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: Self.backgroundSyncTask)
            return
        }
        let request = BGProcessingTaskRequest(identifier: Self.backgroundSyncTask)
        request.requiresNetworkConnectivity = true
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }
}
