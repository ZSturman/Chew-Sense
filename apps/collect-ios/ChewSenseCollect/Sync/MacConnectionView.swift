import SwiftUI

/// Pair once with the Mac service; afterwards status and settings.
struct MacConnectionView: View {
    @ObservedObject var services: CollectServices
    @State private var host = ""
    @State private var port = "48765"
    @State private var code = ""
    @State private var name = "Mac"
    @State private var busy = false
    @State private var error: String?

    private var connection: MacConnection { services.connection }

    var body: some View {
        Form {
            if let paired = connection.paired {
                Section("Paired Mac") {
                    MacStatusRow(connection: connection)
                    LabeledContent("Address", value: "\(paired.host):\(paired.port)")
                    LabeledContent("Certificate", value: String(paired.fingerprint.prefix(16)) + "…")
                    if let last = connection.lastSync {
                        LabeledContent("Last sync", value: last.formatted(date: .omitted, time: .shortened))
                    }
                    Button("Sync now") { Task { await services.synchronize() } }
                }
                Section {
                    Toggle("Send recordings automatically", isOn: Binding(
                        get: { services.uploads.autoUpload }, set: { services.uploads.autoUpload = $0 }))
                } footer: {
                    Text("Recordings upload after they are saved whenever the Mac is reachable. Interrupted transfers resume.")
                }
                Section {
                    Button("Forget this Mac", role: .destructive) { connection.forget() }
                }
            } else {
                Section {
                    Text("On the Mac, open ChewSense Review → Pipeline → Pair iPhone (or run `chewsense-vision loop pair`). Then pick the Mac and enter the six-digit code.")
                        .font(.footnote)
                }
                Section("Macs on this network") {
                    if connection.discovered.isEmpty {
                        Label("Searching… make sure the Mac service is running", systemImage: "antenna.radiowaves.left.and.right")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(connection.discovered) { mac in
                        Button {
                            Task { await select(mac) }
                        } label: {
                            Label(mac.name, systemImage: host.isEmpty ? "desktopcomputer" : "checkmark")
                        }
                    }
                }
                Section("Address") {
                    TextField("Mac IP address or hostname", text: $host)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    TextField("Port", text: $port).keyboardType(.numberPad)
                }
                Section("Pairing code") {
                    TextField("123456", text: $code).keyboardType(.numberPad).font(.title2.monospacedDigit())
                        .accessibilityIdentifier("pairingCodeField")
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
        .onAppear { connection.startDiscovery() }
        .onDisappear { connection.stopDiscovery() }
    }

    private func select(_ mac: MacConnection.Discovered) async {
        do {
            let (resolvedHost, resolvedPort) = try await connection.resolve(mac)
            host = resolvedHost
            port = String(resolvedPort)
            name = mac.name.replacingOccurrences(of: "ChewSense on ", with: "")
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func pair() async {
        busy = true
        defer { busy = false }
        do {
            try await connection.pair(host: host.trimmingCharacters(in: .whitespaces), port: Int(port) ?? 48765,
                                      code: code, name: name)
            error = nil
            await services.uploads.run()
        } catch {
            self.error = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }
}
