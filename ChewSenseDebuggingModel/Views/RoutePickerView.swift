//
//  RoutePickerView.swift
//  ChewSenseDebuggingModel
//
//  System route picker for manually selecting AirPods or another audio route.
//

import AVKit
import SwiftUI

struct RoutePickerView: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView()
        view.prioritizesVideoDevices = false
        view.tintColor = .label
        if #available(iOS 13.0, *) {
            view.activeTintColor = .systemBlue
        }
        return view
    }

    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}
