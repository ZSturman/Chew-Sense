//
//  ChewSenseCollectApp.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/13/25.
//

import SwiftUI

@main
struct ChewSenseCollectApp: App {
    init() {
        CollectionTestMode.resetAppStateIfNeeded()
        // Must be registered before launch finishes; the task may launch the app in the background.
        CollectServices.registerBackgroundSync()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
