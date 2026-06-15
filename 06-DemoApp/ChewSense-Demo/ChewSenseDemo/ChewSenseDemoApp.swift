//
//  ChewSenseDemoApp.swift
//  ChewSenseDemo
//
//  Entry point. Owns the long-lived MonitoringCoordinator so that toggling
//  monitoring on persists across view rebuilds.
//

import SwiftUI
import UserNotifications

@main
struct ChewSenseDemoApp: App {

    @StateObject private var coordinator: MonitoringCoordinator
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        #if DEBUG
        _coordinator = StateObject(
            wrappedValue: MonitoringCoordinator.makeForLaunchEnvironment()
        )
        #else
        _coordinator = StateObject(wrappedValue: MonitoringCoordinator())
        #endif
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(coordinator)
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey : Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    // Show banners even when the app is in foreground.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound, .list])
    }
}
