import SwiftUI

/// "Where we are" at the top of Live: the same message Collect and Review show.
struct LiveProjectStatusSection: View {
    @ObservedObject var mac: LiveMacStatus
    @State private var showPairing = false

    var body: some View {
        Section {
            if let project = mac.project {
                ProjectStatusCard(status: project, app: "live", stale: !mac.isCurrent, opener: opener)
                    .padding(.vertical, 4)
            } else {
                Text("Pair Live with your Mac to see where the project is and what to do next. Collect and Review show the same message.")
                    .font(.subheadline)
            }
            Button { showPairing = true } label: {
                Label(connectionTitle, systemImage: mac.paired == nil ? "link.badge.plus" : "desktopcomputer")
                    .font(.subheadline)
            }
            .accessibilityIdentifier("liveMacConnection")
        } header: {
            Text("Where we are")
        }
        .sheet(isPresented: $showPairing) {
            NavigationStack { LiveMacPairingView(mac: mac) }
        }
        .task {
            // Refresh while visible; the Mac only changes the message when evidence changes.
            while !Task.isCancelled {
                await mac.refresh()
                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    /// Collect steps open Chew Sense Collect (at the experiment) when it is installed.
    private func opener(_ step: ProjectStatus.Step) -> (label: String, action: () -> Void)? {
        #if canImport(UIKit)
        guard let (app, link) = ChewSenseLink.forStep(step), app == "collect",
              let url = ChewSenseLink.url(app: app, link), UIApplication.shared.canOpenURL(url) else { return nil }
        return ("Open in Chew Sense Collect", { UIApplication.shared.open(url) })
        #else
        return nil
        #endif
    }

    private var connectionTitle: String {
        switch mac.state {
        case .unpaired: "Connect to your Mac"
        case .connecting: "Connecting to \(mac.paired?.name ?? "Mac")…"
        case .connected: "Connected to \(mac.paired?.name ?? "Mac")"
        case .offline(let reason): "Mac not reachable right now · \(reason)"
        }
    }
}

/// Pair once with the Mac's loop service (status only).
struct LiveMacPairingView: View {
    @ObservedObject var mac: LiveMacStatus
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Form {
            if let paired = mac.paired {
                Section("Your Mac") {
                    LabeledContent("Mac", value: paired.name)
                    Button("Refresh now") { Task { await mac.refresh() } }
                }
                Section {
                    DisclosureGroup("Details") {
                        LoopConnectionDetails(pairing: paired)
                    }
                    .font(.footnote)
                }
                Section {
                    Button("Forget this Mac", role: .destructive) { mac.forget() }
                } footer: {
                    Text("Live only reads the project status. Recordings are sent from Collect.")
                }
            } else {
                LoopPairingForm(
                    appName: "Chew Sense Live",
                    intro: "Pair once. On the Mac, open Chew Sense Review → Pipeline → Pair iPhone. Pick the Mac below and enter the six-digit code. Live only reads status; it never sends recordings.",
                    codeFieldID: "livePairingCodeField"
                ) { host, port, code, name in
                    try await mac.pair(host: host, port: port, code: code, name: name)
                }
            }
        }
        .navigationTitle("Mac Connection")
        .toolbar {
            ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
        }
    }
}
