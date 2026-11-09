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
        case .offline(let reason): "Mac not reachable · \(reason)"
        }
    }
}

/// Pair once with the Mac's loop service (status only).
struct LiveMacPairingView: View {
    @ObservedObject var mac: LiveMacStatus
    @Environment(\.dismiss) private var dismiss
    @State private var host = ""
    @State private var port = "48765"
    @State private var code = ""
    @State private var name = "Mac"
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        Form {
            if let paired = mac.paired {
                Section("Paired Mac") {
                    LabeledContent("Mac", value: paired.name)
                    LabeledContent("Address", value: "\(paired.host):\(paired.port)")
                    LabeledContent("Certificate", value: String(paired.fingerprint.prefix(16)) + "…")
                    Button("Refresh now") { Task { await mac.refresh() } }
                }
                Section {
                    Button("Forget this Mac", role: .destructive) { mac.forget() }
                } footer: {
                    Text("Live only reads the project status. Recordings are sent from Collect.")
                }
            } else {
                Section {
                    Text("On the Mac, open ChewSense Review → Pipeline → Pair iPhone (or run `chewsense-vision loop pair`). Then pick the Mac and enter the six-digit code. Live only reads status; it never sends recordings.")
                        .font(.footnote)
                }
                Section("Macs on this network") {
                    if let discoveryError = mac.discoveryError {
                        Text(discoveryError).font(.footnote).foregroundStyle(.red)
                        #if canImport(UIKit)
                        Button("Open Settings") {
                            if let url = URL(string: UIApplication.openSettingsURLString) {
                                UIApplication.shared.open(url)
                            }
                        }
                        #endif
                        Button("Retry discovery") {
                            mac.stopDiscovery()
                            mac.startDiscovery()
                        }
                    } else if mac.discovered.isEmpty {
                        Label("Searching… make sure the Mac service is running", systemImage: "antenna.radiowaves.left.and.right")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(mac.discovered) { found in
                        Button {
                            Task { await select(found) }
                        } label: {
                            Label(found.name, systemImage: host.isEmpty ? "desktopcomputer" : "checkmark")
                        }
                    }
                }
                Section("Address") {
                    TextField("Mac IP address or hostname", text: $host)
                        #if canImport(UIKit)
                        .textInputAutocapitalization(.never).keyboardType(.URL)
                        #endif
                        .autocorrectionDisabled()
                    TextField("Port", text: $port)
                }
                Section("Pairing code") {
                    TextField("123456", text: $code).font(.title2.monospacedDigit())
                        #if canImport(UIKit)
                        .keyboardType(.numberPad)
                        #endif
                        .accessibilityIdentifier("livePairingCodeField")
                    Button {
                        Task { await pair() }
                    } label: {
                        if busy { ProgressView() } else { Text("Pair") }
                    }
                    .disabled(busy || host.isEmpty || code.filter(\.isNumber).count != 6)
                    if let error { Text(error).font(.footnote).foregroundStyle(.red) }
                }
            }
        }
        .navigationTitle("Mac Connection")
        .toolbar {
            ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
        }
        .onAppear { if mac.paired == nil { mac.startDiscovery() } }
        .onDisappear { mac.stopDiscovery() }
    }

    private func select(_ found: LiveMacStatus.Discovered) async {
        do {
            let (resolvedHost, resolvedPort) = try await mac.resolve(found)
            host = resolvedHost
            port = String(resolvedPort)
            name = found.name.replacingOccurrences(of: "ChewSense on ", with: "")
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func pair() async {
        busy = true
        defer { busy = false }
        do {
            try await mac.pair(host: host.trimmingCharacters(in: .whitespaces), port: Int(port) ?? 48765,
                               code: code, name: name)
            error = nil
            mac.stopDiscovery()
        } catch {
            self.error = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }
}
