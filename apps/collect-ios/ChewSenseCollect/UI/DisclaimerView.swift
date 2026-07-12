//
//  DisclaimerView.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/17/25.
//

import Foundation
import SwiftUI

struct DisclaimerView: View {
    @Binding var hasAcceptedDisclaimer: Bool
    
    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "info.circle.fill")
                .font(.system(size: 60))
                .foregroundColor(.blue)
            
            Text("Personal Use Only")
                .font(.title.bold())
            
            Text("This app is a personal utility for collecting synchronized video and AirPods motion data.")
                .multilineTextAlignment(.center)
            
            VStack(alignment: .leading, spacing: 12) {
                Label("All data stays on your device", systemImage: "lock.shield")
                Label("No data collection or research", systemImage: "xmark.circle")
                Label("You control what you share", systemImage: "hand.raised")
            }
            .padding()
            .background(Color.secondary.opacity(0.1))
            .cornerRadius(12)
            
            Button("Continue") {
                hasAcceptedDisclaimer = true
                UserDefaults.standard.set(true, forKey: "hasAcceptedDisclaimer")
            }
            .buttonStyle(.borderedProminent)
            .padding(.top)
            
            Spacer()
        }
        .padding()
    }
}
