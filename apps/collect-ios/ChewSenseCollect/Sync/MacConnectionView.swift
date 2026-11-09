import SwiftUI

/// Pair once with the Mac; afterwards only status and one action. Recordings
/// never wait on the Mac: they are kept here and sent when it answers.
struct MacConnectionView: View {
    @ObservedObject var services: CollectServices
    @State private var searchResult: String?

    private var connection: MacConnection { services.connection }

    var body: some View {
        Form {
            if let paired = connection.paired {
                Section("Your Mac") {
                    MacStatusRow(connection: connection)
                    if case .offline = connection.state {
                        Text(connection.link.mailbox.isConfigured
                             ? "Recordings go to your home server and your Mac collects them when it's on. Nothing to do."
                             : "Recordings are kept on this iPhone and sent automatically when your Mac is back. Nothing to do unless this lasts.")
                            .font(.footnote).foregroundStyle(.secondary)
                        if let at = connection.homeStatusAt {
                            Text("Status from your home server, as of \(at.formatted(date: .omitted, time: .shortened)).")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        Button {
                            Task { await tryAgain() }
                        } label: {
                            if connection.isSearching {
                                HStack { ProgressView(); Text("Looking for your Mac…") }
                            } else {
                                Label("Try again now", systemImage: "arrow.clockwise")
                            }
                        }
                        .disabled(connection.isSearching)
                        .accessibilityIdentifier("findMacAgainButton")
                        if let searchResult { Text(searchResult).font(.footnote).foregroundStyle(.orange) }
                    } else {
                        Button("Sync now") { Task { await services.synchronize() } }
                    }
                }
                Section {
                    Toggle("Send recordings automatically", isOn: Binding(
                        get: { services.uploads.autoUpload }, set: { services.uploads.autoUpload = $0 }))
                } footer: {
                    Text("Recordings go to your Mac after they are saved, whenever it can be reached. Interrupted transfers resume; nothing is sent twice.")
                }
                if case .offline = connection.state {
                    Section {
                        DisclosureGroup("If your Mac stays unreachable") {
                            Text("""
                            • Wake the Mac and open Chew Sense Review. Its Pipeline window shows whether the ChewSense service is running.
                            • Put the iPhone and Mac on the same Wi-Fi, or join the Mac to this iPhone's Personal Hotspot. Recordings wait here until then.
                            • Allow Local Network for this app in Settings.
                            """)
                            .font(.footnote)
                            Button("Open Settings") {
                                if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                            }
                        }
                    }
                }
                Section {
                    DisclosureGroup("Details") {
                        LabeledContent("Home server", value: connection.link.mailbox.config.flatMap { URL(string: $0.url)?.host } ?? "not offered by the Mac")
                        LoopConnectionDetails(pairing: paired) { address in
                            try await connection.updateAddress(host: address, port: paired.port)
                            await services.synchronize()
                        }
                        if let last = connection.lastSync {
                            LabeledContent("Last sync", value: last.formatted(date: .omitted, time: .shortened))
                        }
                    }
                    .font(.footnote)
                }
                Section {
                    Button("Forget this Mac", role: .destructive) { connection.forget() }
                }
            } else {
                LoopPairingForm(
                    appName: "Chew Sense Collect",
                    intro: "Pair once. On the Mac, open Chew Sense Review → Pipeline → Pair iPhone. Pick the Mac below and enter the six-digit code. After that the iPhone finds the Mac by itself whenever they share a network, even when its address changes.",
                    codeFieldID: "pairingCodeField"
                ) { host, port, code, name in
                    try await connection.pair(host: host, port: port, code: code, name: name)
                    await services.uploads.run()
                }
            }
        }
        .navigationTitle("Mac Connection")
    }

    private func tryAgain() async {
        searchResult = nil
        await connection.relocate()
        await services.synchronize()
        if case .offline = connection.state {
            searchResult = "Still not reachable. Your recordings are safe here."
        }
    }
}
