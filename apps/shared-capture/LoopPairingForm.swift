import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// First pairing with the Mac, shared by Collect and Live: pick the Mac and
/// type the six-digit code from Review → Pipeline → Pair iPhone. Typing an
/// address is tucked away for networks that block discovery.
struct LoopPairingForm: View {
    let intro: String
    let codeFieldID: String
    let pair: (_ host: String, _ port: Int, _ code: String, _ name: String) async throws -> Void

    @StateObject private var discovery: LoopDiscovery
    @State private var selected: LoopDiscovery.Found?
    @State private var host = ""
    @State private var port = "48765"
    @State private var code = ""
    @State private var name = "Mac"
    @State private var manual = false
    @State private var busy = false
    @State private var error: String?

    init(appName: String, intro: String, codeFieldID: String,
         pair: @escaping (_ host: String, _ port: Int, _ code: String, _ name: String) async throws -> Void) {
        self.intro = intro
        self.codeFieldID = codeFieldID
        self.pair = pair
        _discovery = StateObject(wrappedValue: LoopDiscovery(appName: appName))
    }

    var body: some View {
        Group {
            Section {
                Text(intro).font(.footnote)
            }
            Section("Your Mac") {
                if let message = discovery.error {
                    Text(message).font(.footnote).foregroundStyle(.red)
                    #if canImport(UIKit)
                    Button("Open Settings") {
                        if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                    }
                    #endif
                    Button("Search again") {
                        discovery.stop()
                        discovery.start()
                    }
                } else if discovery.found.isEmpty {
                    Label("Looking for your Mac… keep Chew Sense Review's Mac nearby and awake.",
                          systemImage: "antenna.radiowaves.left.and.right")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                ForEach(discovery.found) { found in
                    Button {
                        Task { await select(found) }
                    } label: {
                        Label(Self.displayName(found.name),
                              systemImage: selected == found ? "checkmark.circle.fill" : "desktopcomputer")
                    }
                }
                DisclosureGroup("Enter address manually", isExpanded: $manual) {
                    TextField("Mac IP address or name", text: $host)
                        #if canImport(UIKit)
                        .textInputAutocapitalization(.never).keyboardType(.URL)
                        #endif
                        .autocorrectionDisabled()
                    TextField("Port", text: $port)
                }
                .font(.footnote)
            }
            Section("Pairing code") {
                TextField("123456", text: $code).font(.title2.monospacedDigit())
                    #if canImport(UIKit)
                    .keyboardType(.numberPad)
                    #endif
                    .accessibilityIdentifier(codeFieldID)
                Button {
                    Task { await submit() }
                } label: {
                    if busy { ProgressView() } else { Text("Pair") }
                }
                .disabled(busy || host.isEmpty || code.filter(\.isNumber).count != 6)
                if let error { Text(error).font(.footnote).foregroundStyle(.red) }
            }
        }
        .onAppear { discovery.start() }
        .onDisappear { discovery.stop() }
    }

    static func displayName(_ service: String) -> String {
        service.replacingOccurrences(of: "ChewSense on ", with: "").replacingOccurrences(of: ".local", with: "")
    }

    private func select(_ found: LoopDiscovery.Found) async {
        do {
            let (resolvedHost, resolvedPort) = try await LoopDiscovery.resolve(found)
            host = resolvedHost
            port = String(resolvedPort)
            name = found.name.replacingOccurrences(of: "ChewSense on ", with: "")
            selected = found
            error = nil
        } catch {
            self.error = "Couldn't reach that Mac. Make sure it is awake and try again."
        }
    }

    private func submit() async {
        busy = true
        defer { busy = false }
        do {
            try await pair(host.trimmingCharacters(in: .whitespaces), Int(port) ?? 48765, code, name)
            error = nil
            discovery.stop()
        } catch {
            self.error = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }
}

/// Technical details of a pairing, shown only on request.
struct LoopConnectionDetails: View {
    let pairing: LoopPairing
    /// Rarely needed: point the pairing at an address the Mac can't report.
    var useAddress: ((String) async throws -> Void)?

    @State private var address = ""
    @State private var result: String?

    var body: some View {
        LabeledContent("Reached through", value: Self.describe(pairing.current.kind))
        LabeledContent("Address", value: "\(pairing.host):\(pairing.port)")
        let others = pairing.alternatives
        if !others.isEmpty {
            LabeledContent("Also tries", value: others.map { Self.describe($0.kind) }.joined(separator: ", "))
        }
        LabeledContent("Mac identity", value: String(pairing.fingerprint.prefix(16)) + "…")
        if let useAddress {
            HStack {
                TextField("Use another address", text: $address)
                    #if canImport(UIKit)
                    .textInputAutocapitalization(.never).keyboardType(.URL)
                    #endif
                    .autocorrectionDisabled()
                Button("Use") {
                    Task {
                        do {
                            try await useAddress(address.trimmingCharacters(in: .whitespaces))
                            result = nil
                            address = ""
                        } catch {
                            result = "That address didn't answer as your Mac."
                        }
                    }
                }
                .disabled(address.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let result { Text(result).font(.footnote).foregroundStyle(.orange) }
        }
    }

    static func describe(_ kind: LoopRoute.Kind) -> String {
        switch kind {
        case .lan: "this network"
        case .local: "this network (by name)"
        case .manual: "an address you entered"
        case .tailnet: "Tailscale (any network)"
        }
    }
}
