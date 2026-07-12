//
//  ContentView.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/13/25.
//

import SwiftUI

struct ContentView: View {
    @StateObject private var controller = RecorderController()
    @State private var selectedFolder: URL?
    @State private var hasAcceptedDisclaimer = UserDefaults.standard.bool(forKey: "hasAcceptedDisclaimer")
    
    var body: some View {
        Group {
            if hasAcceptedDisclaimer {
                mainView
            } else {
                DisclaimerView(hasAcceptedDisclaimer: $hasAcceptedDisclaimer)
            }
        }
        .environmentObject(controller)
        .alert(item: $controller.alert) { a in
            Alert(title: Text(a.title), message: Text(a.message), dismissButton: .default(Text("OK")))
        }
    }
    
    private var mainView: some View {
        NavigationSplitView {
            RecordingsListView(controller: controller, selectedFolder: $selectedFolder)
                .navigationTitle("Recordings")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        NavigationLink(destination: CameraScreen(controller: controller)) {
                            HStack {
                                Image(systemName: "plus")
                                VStack(alignment: .leading) {
                                    Text("New")
                                        .font(.body)
                                }
                            }
                        }
                        .accessibilityIdentifier("newRecordingToolbarButton")
                    }
                }
        } detail: {
            Group {
                if let folder = selectedFolder {
                    RecordingDetailView(folder: folder)
                } else {
                    Text("Select or create a recording")
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}
//import SwiftUI
//
//struct ContentView: View {
//    @StateObject private var controller = RecorderController()
//    @State private var selectedFolder: URL?
//    
//    var body: some View {
//        NavigationSplitView {
//            RecordingsListView(controller: controller, selectedFolder: $selectedFolder)
//                .navigationTitle("Recordings")
//                .toolbar {
//                    ToolbarItem(placement: .primaryAction) {
//                        NavigationLink(destination: CameraScreen(controller: controller)) {
//                            HStack {
//                                Image(systemName: "plus")
//                                VStack(alignment: .leading) {
//                                    Text("New")
//                                        .font(.body)
//                                }
//                            }
//                        }
//                        .accessibilityIdentifier("newRecordingToolbarButton")
//                    }
//                }
//        } detail: {
//            Group {
//                if let folder = selectedFolder {
//                    RecordingDetailView(folder: folder)
//                } else {
//                    Text("Select or create a recording")
//                        .foregroundStyle(.secondary)
//                }
//            }
//
//        }
//        .environmentObject(controller)
//        .alert(item: $controller.alert) { a in
//            Alert(title: Text(a.title), message: Text(a.message), dismissButton: .default(Text("OK")))
//        }
//    }
//}
