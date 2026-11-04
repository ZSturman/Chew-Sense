//
//  ContentView.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/13/25.
//

import SwiftUI

struct ContentView: View {
    @StateObject private var controller: RecorderController
    @StateObject private var services: CollectServices
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedFolder: URL?
    @State private var showAirPods = false
    @State private var hasAcceptedDisclaimer = UserDefaults.standard.bool(forKey: "hasAcceptedDisclaimer")
    
    init() {
        let recorder = RecorderController()
        _controller = StateObject(wrappedValue: recorder)
        _services = StateObject(wrappedValue: CollectServices(recorder: recorder))
    }

    var body: some View {
        Group {
            if hasAcceptedDisclaimer {
                mainView
            } else {
                DisclaimerView(hasAcceptedDisclaimer: $hasAcceptedDisclaimer)
            }
        }
        .environmentObject(controller)
        .environmentObject(services)
        .sheet(isPresented: $showAirPods) { AirPodsConnectionSheet(controller: controller) }
        .alert(item: $controller.alert) { a in
            Alert(title: Text(a.title), message: Text(a.message), dismissButton: .default(Text("OK")))
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await services.synchronize() } }
        }
        .task {
            // Light periodic sync while the app is open: catalog and upload status.
            while !Task.isCancelled {
                await services.synchronize()
                try? await Task.sleep(nanoseconds: 60_000_000_000)
            }
        }
    }

    private var collectRequiredDataButton: some View {
        NavigationLink {
            CollectRequiredDataView(services: services)
        } label: {
            HStack(spacing: 12) {
                Image(systemName: "list.bullet.clipboard.fill").font(.title2)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Collect Required Data").font(.headline)
                    Text(requiredSummary).font(.caption).opacity(0.85)
                }
                Spacer()
                Image(systemName: "chevron.right")
            }
            .foregroundStyle(.white)
            .padding(14)
            .background(Color.accentColor, in: RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .padding(.horizontal)
        .padding(.top, 8)
        .accessibilityIdentifier("collectRequiredDataButton")
    }

    private var requiredSummary: String {
        let open = services.catalog.catalog.entries.filter { services.catalog.remaining(for: $0) > 0 }
        let mac: String
        switch services.connection.state {
        case .connected: mac = "Mac connected"
        case .unpaired: mac = "Mac not paired"
        default: mac = "Mac offline"
        }
        return "\(open.count) guided experiments requested · \(mac)"
    }
    
    private var mainView: some View {
        NavigationSplitView {
            VStack(spacing: 0) {
                collectRequiredDataButton
                RecordingsListView(controller: controller, selectedFolder: $selectedFolder)
            }
                .navigationTitle("Recordings")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button("AirPods", systemImage: "airpodspro") { showAirPods = true }
                    }
                    ToolbarItem(placement: .secondaryAction) {
                        Toggle(
                            "Local Review Audio",
                            isOn: $controller.captureReviewAudio
                        )
                        .help("Optional audio is stored locally for VAD and human review. It is never a shipping-model input.")
                    }
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
                    VStack(spacing: 16) {
                        Text("Select or create a recording")
                            .foregroundStyle(.secondary)
                        CollectGuidanceView(stage: .library)
                            .frame(maxWidth: 440)
                    }
                    .padding()
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
