//
//  EatingDetectorMode.swift
//  ChewSenseLive
//
//  Feature flag for the exported motion-only eating detector. The existing
//  ChewNet path stays the default until the exported detector has physical
//  AirPods evidence; in that default it runs in shadow and is only logged.
//

import Foundation

enum EatingDetectorMode: String, CaseIterable, Identifiable {
    /// ChewNet drives Live; the exported detector is logged to eating_detector.csv.
    case bundledChewNetWithShadow = "chewnet_with_shadow"
    /// The exported detector drives the chewing state; ChewNet is still logged.
    case exportedDetectorExperimental = "exported_experimental"

    static let defaultsKey = "config.eatingDetectorMode"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .bundledChewNetWithShadow: return "ChewNet + shadow comparison"
        case .exportedDetectorExperimental: return "AirPods detector (experimental)"
        }
    }

    var explanation: String {
        switch self {
        case .bundledChewNetWithShadow:
            return "ChewNet supplies the displayed prediction. The experimental AirPods detector runs alongside it and is saved for comparison."
        case .exportedDetectorExperimental:
            return "The experimental AirPods detector supplies the displayed prediction. Its real-world accuracy is still being evaluated."
        }
    }

    static var stored: EatingDetectorMode {
        UserDefaults.standard.string(forKey: defaultsKey).flatMap(EatingDetectorMode.init(rawValue:))
            ?? .bundledChewNetWithShadow
    }
}
