import Foundation
import Combine

/// Long-lived objects shared by manual collection, guided collection and sync.
final class CollectServices: ObservableObject {
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
    }

    /// Called when the app becomes active and periodically while visible.
    func synchronize() async {
        guard !CollectionTestMode.isEnabled else { return }
        await connection.refresh()
        if case .connected = connection.state { await uploads.run() }
    }
}
