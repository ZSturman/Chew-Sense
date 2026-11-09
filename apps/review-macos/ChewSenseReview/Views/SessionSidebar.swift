import SwiftUI

/// Every recording in the current workspace, for when you want to choose one
/// yourself. Hidden by default; Up next covers the usual path.
struct SessionSidebar: View {
    @EnvironmentObject var state: ReviewerState
    let onClose: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            list
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Recordings").font(.headline)
                Spacer()
                Button { state.refreshSessions() } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(.borderless)
                    .help("Look for new recordings")
                Button(action: onClose) { Image(systemName: "xmark") }
                    .buttonStyle(.borderless)
                    .help("Hide (⌘1)")
            }
            if state.loopRoot != nil {
                Picker("Workspace", selection: workspaceBinding) {
                    Text("Suggestions").tag("assisted")
                    Text("Blind").tag("blind")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .help("Blind recordings are reviewed with suggestions hidden.")
            } else if let root = state.workspaceRoot {
                Text(root.lastPathComponent).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.top, 12)
        .padding(.bottom, 4)
    }

    private var list: some View {
        List(selection: bindingForSelection()) {
            let open = state.sessions.filter { $0.status != .verified && $0.status != .retired }
            let finished = state.sessions.filter { $0.status == .verified }
            let retired = state.sessions.filter { $0.status == .retired }
            if !open.isEmpty {
                Section("To review") { ForEach(open) { row($0) } }
            }
            if !finished.isEmpty {
                Section("Finished") { ForEach(finished) { row($0) } }
            }
            if !retired.isEmpty {
                Section("Kept for research") { ForEach(retired) { row($0) } }
            }
            if state.sessions.isEmpty {
                Text("No recordings here yet.").foregroundStyle(.secondary)
            }
        }
        .listStyle(.sidebar)
    }

    private func row(_ session: Session) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(RecordingName.of(session.id)).lineLimit(1).truncationMode(.middle)
            HStack(spacing: 4) {
                Text(statusLabel(session.status))
                if session.videoURL == nil { Text("· no video") }
            }
            .font(.caption)
            .foregroundStyle(statusColor(session.status))
        }
        .help(session.id)
        .tag(session.id)
    }

    private var workspaceBinding: Binding<String> {
        Binding(
            get: { state.workspaceRoot?.lastPathComponent == "blind" ? "blind" : "assisted" },
            set: { state.openReviewWorkspace($0) }
        )
    }

    private func bindingForSelection() -> Binding<String?> {
        Binding(
            get: { state.selectedSession?.id },
            set: { newID in
                if let id = newID, let s = state.sessions.first(where: { $0.id == id }) {
                    state.loadSession(s)
                }
            }
        )
    }

    private func statusLabel(_ s: Session.Status) -> String {
        switch s {
        case .candidates: "Not started"
        case .inReview: "In progress"
        case .verified: "Finished"
        case .migrationRequired: "Old format: can't open"
        case .retired: "Retired: not used for training or tests"
        }
    }

    private func statusColor(_ s: Session.Status) -> Color {
        switch s {
        case .candidates: .secondary
        case .inReview: .orange
        case .verified: .green
        case .migrationRequired: .secondary
        case .retired: .secondary
        }
    }
}
