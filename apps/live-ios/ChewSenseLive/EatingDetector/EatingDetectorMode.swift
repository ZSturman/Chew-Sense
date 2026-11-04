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
        case .bundledChewNetWithShadow: return "Bundled ChewNet (default)"
        case .exportedDetectorExperimental: return "Exported detector (experimental)"
        }
    }

    var explanation: String {
        switch self {
        case .bundledChewNetWithShadow:
            return "Live uses the bundled ChewNet. The exported motion-only detector runs alongside and is only recorded, so both can be compared after the session."
        case .exportedDetectorExperimental:
            return "The exported detector decides chewing. It matched the Python reference on recorded sessions but has no physical AirPods validation yet."
        }
    }

    static var stored: EatingDetectorMode {
        UserDefaults.standard.string(forKey: defaultsKey).flatMap(EatingDetectorMode.init(rawValue:))
            ?? .bundledChewNetWithShadow
    }
}
