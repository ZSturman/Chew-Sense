//
//  ChewSenseCollectTests.swift
//  ChewSenseCollectTests
//
//  Created by Zachary Sturman on 11/13/25.
//

import Foundation
import Testing
@testable import ChewSenseCollect

struct ChewSenseCollectTests {

    // MARK: - Helpers

    /// Creates a unique temporary session folder inside the app's documents directory
    /// that mimics the structure used by `RecorderController`.
    private func makeTemporarySessionFolder(nameSuffix: String = UUID().uuidString) throws -> URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        let folder = docs.appendingPathComponent("TestSession-\(nameSuffix)", isDirectory: true)
        // Ensure a clean state for this folder
        try? FileManager.default.removeItem(at: folder)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }
    
    /// Creates an empty dummy .mov file at a unique temporary URL for use with `VideoEditorViewModel` tests.
    private func makeDummyVideoURL(name: String = UUID().uuidString) throws -> URL {
        let tmp = FileManager.default.temporaryDirectory
        let url = tmp.appendingPathComponent("dummy-\(name)").appendingPathExtension("mov")
        FileManager.default.createFile(atPath: url.path, contents: Data(), attributes: nil)
        return url
    }

    // MARK: - RecordingMetadata

    @Test
    func recordingMetadata_isCodableRoundTrip() throws {
        let original = RecordingMetadata(labelled: true, shared: false)

        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(RecordingMetadata.self, from: data)

        #expect(decoded.labelled == original.labelled)
        #expect(decoded.shared == original.shared)
    }

    // MARK: - RecorderController metadata lifecycle

    @Test
    func recorderController_metadataLifecycle_singleFolder() throws {
        let controller = RecorderController()
        let folder = try makeTemporarySessionFolder()

        // Starts with no metadata for a brand-new folder.
        #expect(controller.metadata(for: folder) == nil)

        // Mark as labelled.
        controller.setLabelled(true, for: folder)
        let afterLabel = controller.metadata(for: folder)

        #expect(afterLabel != nil)
        #expect(afterLabel?.labelled == true)
        #expect(afterLabel?.shared == false)

        // Mark as shared.
        controller.markShared(for: folder)
        let afterShare = controller.metadata(for: folder)

        #expect(afterShare != nil)
        #expect(afterShare?.labelled == true)
        #expect(afterShare?.shared == true)

        // Deleting the folder should also clear metadata.
        controller.deleteSessionFolder(folder)
        #expect(controller.metadata(for: folder) == nil)
    }

    @Test
    func recorderController_markSharedForMultipleFolders() throws {
        let controller = RecorderController()
        let folder1 = try makeTemporarySessionFolder(nameSuffix: "1")
        let folder2 = try makeTemporarySessionFolder(nameSuffix: "2")

        // Start with per-folder metadata marked as labelled but not shared.
        controller.setLabelled(true, for: folder1)
        controller.setLabelled(true, for: folder2)

        // Mark both as shared in one call.
        controller.markShared(for: [folder1, folder2])

        let meta1 = controller.metadata(for: folder1)
        let meta2 = controller.metadata(for: folder2)

        #expect(meta1?.labelled == true)
        #expect(meta1?.shared == true)

        #expect(meta2?.labelled == true)
        #expect(meta2?.shared == true)

        // Cleanup
        controller.deleteSessionFolder(folder1)
        controller.deleteSessionFolder(folder2)
    }
    
    @Test
    func recorderController_allSessionFolders_sortedNewestFirst() throws {
        let controller = RecorderController()
        let olderFolder = try makeTemporarySessionFolder(nameSuffix: "Older")
        // Ensure the creation date of the second folder is later.
        sleep(1)
        let newerFolder = try makeTemporarySessionFolder(nameSuffix: "Newer")

        let all = controller.allSessionFolders()

        guard let olderIndex = all.firstIndex(of: olderFolder),
              let newerIndex = all.firstIndex(of: newerFolder) else {
            #expect(Bool(false), "Expected both test folders to be present in allSessionFolders()")
            return
        }

        #expect(newerIndex < olderIndex, "Newer folder should appear before older folder in the list.")

        controller.deleteSessionFolder(olderFolder)
        controller.deleteSessionFolder(newerFolder)
    }

    // MARK: - RecorderController session folder discovery

    @Test
    func recorderController_allSessionFolders_ignoresHiddenDirectories() throws {
        let controller = RecorderController()
        let visibleFolder = try makeTemporarySessionFolder(nameSuffix: "Visible")

        // Create a hidden directory (name starts with ".") that should be ignored.
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        let hiddenName = ".Hidden-\(UUID().uuidString)"
        let hiddenFolder = docs.appendingPathComponent(hiddenName, isDirectory: true)
        try? FileManager.default.removeItem(at: hiddenFolder)
        try FileManager.default.createDirectory(at: hiddenFolder, withIntermediateDirectories: true)

        let all = controller.allSessionFolders()

        // Non-hidden test folder should be present.
        #expect(all.contains(visibleFolder))
        // Hidden folder should not be reported as a session folder.
        #expect(all.contains(hiddenFolder) == false)

        // Cleanup
        controller.deleteSessionFolder(visibleFolder)
        try? FileManager.default.removeItem(at: hiddenFolder)
    }
    
    // MARK: - VideoEditorViewModel marker semantics

    @Test
    func videoEditor_markersAlternateStartStopKinds() throws {
        let videoURL = try makeDummyVideoURL()
        let viewModel = VideoEditorViewModel(videoURL: videoURL)

        viewModel.duration = 10.0
        viewModel.currentTime = 1.0
        viewModel.addMarkerAtCurrentTime()
        viewModel.currentTime = 2.0
        viewModel.addMarkerAtCurrentTime()
        viewModel.currentTime = 3.0
        viewModel.addMarkerAtCurrentTime()

        #expect(viewModel.markers.count == 3)
        #expect(viewModel.markers[0].kind == .startChewing)
        #expect(viewModel.markers[1].kind == .stopChewing)
        #expect(viewModel.markers[2].kind == .startChewing)
    }

    @Test
    func videoEditor_hasUnclosedChewingSegmentWhenOddMarkers() throws {
        let videoURL = try makeDummyVideoURL()
        let viewModel = VideoEditorViewModel(videoURL: videoURL)

        viewModel.duration = 10.0
        viewModel.currentTime = 1.0
        viewModel.addMarkerAtCurrentTime()
        viewModel.currentTime = 2.0
        viewModel.addMarkerAtCurrentTime()

        #expect(viewModel.hasUnclosedChewingSegment == false)

        viewModel.currentTime = 3.0
        viewModel.addMarkerAtCurrentTime()

        #expect(viewModel.hasUnclosedChewingSegment == true)
    }

    // MARK: - VideoEditorViewModel CSV relabelling

    @Test
    func videoEditor_relabelCSVAddsLabelColumnAndMarksChewingRows() throws {
        // Prepare a fake CSV with timestamps.
        let tmp = FileManager.default.temporaryDirectory
        let csvURL = tmp.appendingPathComponent("imu-\(UUID().uuidString)").appendingPathExtension("csv")
        let csv = """
        timestamp,ax,ay,az
        0.0,0,0,0
        0.5,0,0,0
        1.0,0,0,0
        2.0,0,0,0
        """
        try csv.write(to: csvURL, atomically: true, encoding: .utf8)

        let videoURL = try makeDummyVideoURL()
        let viewModel = VideoEditorViewModel(videoURL: videoURL)
        viewModel.duration = 3.0

        // Create chewing markers from 0.5s to 1.5s.
        viewModel.currentTime = 0.5
        viewModel.addMarkerAtCurrentTime()
        viewModel.currentTime = 1.5
        viewModel.addMarkerAtCurrentTime()
        viewModel.completeEditing()

        try viewModel.relabelCSV(at: csvURL)

        let updated = try String(contentsOf: csvURL)
        let lines = updated.split(separator: "\n").map(String.init)

        #expect(lines[0].contains("label"))
        // timestamps 0.0 -> false, 0.5 and 1.0 within chewing -> true, 2.0 -> false
        #expect(lines[1].hasSuffix(",false"))
        #expect(lines[2].hasSuffix(",true"))
        #expect(lines[3].hasSuffix(",true"))
        #expect(lines[4].hasSuffix(",false"))
    }
}
