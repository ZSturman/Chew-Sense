//
//  SessionExportSaver.swift
//  ChewSenseDebuggingModel
//
//  Direct-to-disk save flow for reviewed session bundles. Replaces the
//  share-sheet-only export with a real folder picker (Mac Catalyst surfaces
//  the native macOS file panel) and a one-click "Save Again" action that
//  reuses the previously chosen destination via a security-scoped bookmark.
//
//  Usage: place a `SessionExportSaverHost` modifier on a view, drive it via
//  an `@StateObject SessionExportSaverState`, and call its `saveCopy(of:)`
//  / `saveAgain(of:)` helpers from buttons.
//

import SwiftUI
import UIKit
import UniformTypeIdentifiers

// MARK: - Persistent Destination Store

enum SessionExportDestinationStore {
    private static let bookmarkKey = "ChewSense.SessionExport.lastFolderBookmark"
    private static let displayPathKey = "ChewSense.SessionExport.lastFolderPath"

    static var lastDisplayPath: String? {
        UserDefaults.standard.string(forKey: displayPathKey)
    }

    static var hasRememberedFolder: Bool {
        UserDefaults.standard.data(forKey: bookmarkKey) != nil
    }

    static func remember(_ url: URL) {
        UserDefaults.standard.set(url.path, forKey: displayPathKey)
        do {
            #if targetEnvironment(macCatalyst)
            let bookmark = try url.bookmarkData(
                options: [.withSecurityScope],
                includingResourceValuesForKeys: nil,
                relativeTo: nil
            )
            #else
            let bookmark = try url.bookmarkData(
                options: [],
                includingResourceValuesForKeys: nil,
                relativeTo: nil
            )
            #endif
            UserDefaults.standard.set(bookmark, forKey: bookmarkKey)
        } catch {
            // Bookmark generation can fail for certain provider URLs; the
            // display path is still saved so the user knows where it went.
            UserDefaults.standard.removeObject(forKey: bookmarkKey)
        }
    }

    /// Resolves the last destination if it is still reachable. The caller
    /// must call `stopAccessingSecurityScopedResource()` once the copy
    /// completes if `accessGranted` is `true`.
    static func resolveLastFolder() throws -> (url: URL, accessGranted: Bool)? {
        guard let bookmark = UserDefaults.standard.data(forKey: bookmarkKey) else {
            return nil
        }
        var stale = false
        #if targetEnvironment(macCatalyst)
        let resolveOptions: URL.BookmarkResolutionOptions = [.withSecurityScope]
        #else
        let resolveOptions: URL.BookmarkResolutionOptions = []
        #endif
        let url = try URL(
            resolvingBookmarkData: bookmark,
            options: resolveOptions,
            relativeTo: nil,
            bookmarkDataIsStale: &stale
        )
        if stale {
            // Best-effort refresh.
            remember(url)
        }
        let granted = url.startAccessingSecurityScopedResource()
        return (url, granted)
    }

    static func clear() {
        UserDefaults.standard.removeObject(forKey: bookmarkKey)
        UserDefaults.standard.removeObject(forKey: displayPathKey)
    }
}

// MARK: - Copy Helper

enum SessionExportCopier {
    /// Copies the session bundle (a directory) into `destinationFolder`,
    /// overwriting any existing folder of the same name. Returns the URL
    /// of the copy at its new location.
    @discardableResult
    static func copySessionBundle(at sessionURL: URL, into destinationFolder: URL) throws -> URL {
        let fileManager = FileManager.default
        let destination = destinationFolder.appendingPathComponent(sessionURL.lastPathComponent, isDirectory: true)
        if fileManager.fileExists(atPath: destination.path) {
            try fileManager.removeItem(at: destination)
        }
        try fileManager.copyItem(at: sessionURL, to: destination)
        return destination
    }
}

// MARK: - Save Picker (UIDocumentPicker for folders)

struct ExportFolderPicker: UIViewControllerRepresentable {
    let onPick: (URL?) -> Void

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.folder], asCopy: false)
        picker.delegate = context.coordinator
        picker.allowsMultipleSelection = false
        picker.shouldShowFileExtensions = true
        return picker
    }

    func updateUIViewController(_ uiViewController: UIDocumentPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(onPick: onPick)
    }

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let onPick: (URL?) -> Void
        init(onPick: @escaping (URL?) -> Void) { self.onPick = onPick }

        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            onPick(urls.first)
        }

        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
            onPick(nil)
        }
    }
}
