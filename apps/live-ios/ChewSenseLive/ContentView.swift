//
//  ContentView.swift
//  ChewSenseLive
//
//  ⚠️ DEPRECATED: This file exists only for backward compatibility.
//  Use MainContentView.swift which leverages the modular component structure:
//    - Features/   : Feature extraction (TimeFeatures, FrequencyFeatures, FeatureExtractor)
//    - Managers/   : ChewDetector, MotionManager
//    - Models/     : ChewModel (CoreML wrapper)
//    - Views/      : MainContentView, ConfigurationView
//    - Generated/  : NormalizationConstants (auto-generated from pipeline)
//

import SwiftUI

/// Legacy ContentView wrapper - forwards to MainContentView
/// This maintains backward compatibility with ChewSenseLiveApp.swift
struct ContentView: View {
    var body: some View {
        MainContentView()
    }
}

#Preview {
    ContentView()
}
