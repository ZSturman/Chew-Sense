import SwiftUI
import AppKit
import AVKit

/// Dedicated video area, toolbar, section navigator and optional evidence panels.
/// Playback shortcuts are scoped to the review window.
struct RootView: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var viewport: TimelineViewport
    @EnvironmentObject var assist: CoarseAssistStore

    @State private var showSidebar: Bool = true
    @State private var showAssist = false
    @State private var showHelp: Bool = false
    @State private var showReviewQueue: Bool = false
    @State private var showSections = true
    @State private var showSignals = false

    var body: some View {
        HStack(spacing: 0) {
            if showSidebar {
                SessionSidebar()
                    .frame(width: 240)
                    .transition(.move(edge: .leading))
                Divider()
            }
            mainStage
            if showAssist, state.selectedSession != nil {
                Divider()
                CoarseAssistPanel(onClose: { withAnimation { showAssist = false } })
                    .frame(width: 380)
                    .transition(.move(edge: .trailing))
            } else if showReviewQueue {
                Divider()
                ReviewQueuePanel(onClose: { withAnimation { showReviewQueue = false } })
                    .frame(width: 360)
                    .transition(.move(edge: .trailing))
            } else if showSections, state.selectedSession != nil {
                Divider()
                ReviewSectionsPanel().frame(width: 250)
            }
        }
        .overlay(alignment: .trailing) {
            if showHelp {
                HelpPanel(onClose: { withAnimation { showHelp = false } })
                    .frame(width: 360)
                    .transition(.move(edge: .trailing))
            }
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .animation(.easeInOut(duration: 0.18), value: showSidebar)
        .animation(.easeInOut(duration: 0.18), value: showHelp)
        .animation(.easeInOut(duration: 0.18), value: showReviewQueue)
        .onReceive(NotificationCenter.default.publisher(for: .toggleSidebar)) { _ in
            showSidebar.toggle()
        }
        .onReceive(NotificationCenter.default.publisher(for: .toggleHelp)) { _ in
            showHelp.toggle()
        }
        .onReceive(NotificationCenter.default.publisher(for: .pickWorkspace)) { _ in
            pickWorkspaceRoot()
        }
        .onReceive(NotificationCenter.default.publisher(for: .toggleReviewQueue)) { _ in
            withAnimation { showReviewQueue.toggle() }
        }
        .onChange(of: playback.isPlaying) { _, playing in
            if playing {
                viewport.snapToPlayhead(playback.currentTime, duration: max(playback.duration, 1))
            }
        }
        .onChange(of: state.selectedSession?.id) { _, _ in
            loadEffectiveVideo(preserveTime: false)
            assist.load(session: state.isBlindReview ? nil : state.selectedSession, workspaceRoot: state.workspaceRoot)
            if state.selectedSession != nil { showSidebar = false }
        }
        .onChange(of: state.useAnnotatedVideo) { _, _ in
            loadEffectiveVideo(preserveTime: true)
        }
        .onChange(of: state.selectedChewID) { _, _ in applySelectionBounds() }
        .onChange(of: state.loopSelectedChew) { _, _ in applySelectionBounds() }
    }

    // MARK: - Main stage

    @ViewBuilder
    private var mainStage: some View {
        if state.workspaceRoot == nil {
            workspacePrompt
        } else if state.selectedSession == nil {
            VStack(spacing: 16) {
                ContentUnavailableView(
                    "Pick a session",
                    systemImage: "video.circle",
                    description: Text("Choose a session in the sidebar to start labeling chews.")
                )
                ReviewGuidanceView(stage: .workspace)
                    .frame(maxWidth: 560)
            }
        } else if state.selectedSession?.videoURL == nil {
            ContentUnavailableView(
                "No video for this session",
                systemImage: "video.slash",
                description: Text("Place an .mp4 / .mov in the session’s raw folder.")
            )
        } else {
            videoStage
        }
    }

    private var workspacePrompt: some View {
        VStack(spacing: 14) {
            Text("ChewSense Review").font(.title2).bold()
            Text("Choose the ChewSense workspace directory to discover sessions.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 520)
            Button("Choose workspace…") { pickWorkspaceRoot() }
                .buttonStyle(.borderedProminent)
            ReviewGuidanceView(stage: .workspace)
                .frame(maxWidth: 560)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var videoStage: some View {
        VStack(spacing: 8) {
            HUDTop(toggleSidebar: { showSidebar.toggle() },
                   toggleHelp: { showHelp.toggle() },
                   toggleReviewQueue: { showReviewQueue.toggle(); if showReviewQueue { showSections = false; showAssist = false } },
                   toggleSections: { showSections.toggle(); if showSections { showReviewQueue = false; showAssist = false } },
                   toggleAssist: { showAssist.toggle(); if showAssist { showReviewQueue = false; showSections = false } },
                   toggleSignals: { showSignals.toggle() })
            Text(state.isBlindReview
                 ? "Independent benchmark: add actual chews in the phone-labeled sections, then check for misses elsewhere. Routine recordings use automatic proposals."
                 : "Phone intervals are already imported. Jump to a section, check the automatic proposals, and correct mistakes. Instructions are navigation context, not labels.")
                .font(.caption).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
            GeometryReader { geometry in
                let sideways = playback.displayQuarterTurns % 2 != 0
                VideoSurface()
                    .frame(width: sideways ? geometry.size.height : geometry.size.width,
                           height: sideways ? geometry.size.width : geometry.size.height)
                    .rotationEffect(.degrees(Double(playback.displayQuarterTurns) * 90))
                    .frame(width: geometry.size.width, height: geometry.size.height)
            }
            .background(Color.black).clipped()
            if showSignals && !state.isBlindReview {
                    CycleShapeChart()
                        .frame(height: 110)
            }
            BottomDeck()
        }
        .padding(10)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay { KeyCaptureView(handler: handleKey).frame(width: 0, height: 0) }
    }

    // MARK: - Helpers

    private func pickWorkspaceRoot() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Select ChewSense workspace"
        if panel.runModal() == .OK, let url = panel.url {
            state.setWorkspaceRoot(url)
        }
    }

    private func applySelectionBounds() {
        // Cycle-selected state: when a chew is selected, bound playback so
        // pressing play starts at the cycle's start and ends at its end.
        // The loop toggle (R) optionally restarts at the end instead of pausing.
        guard let chew = state.selectedChew else {
            playback.clearLoop()
            return
        }
        playback.setLoop(start: chew.startTS, end: chew.endTS, enabled: state.loopSelectedChew)
    }

    /// Resolves the URL to feed AVPlayer based on the current Raw/Annotated
    /// toggle, falling back to the raw video when the annotated reference is
    /// missing. When `preserveTime` is true the playhead is restored after
    /// the swap so the reviewer can compare frames at the same timestamp.
    private func loadEffectiveVideo(preserveTime: Bool) {
        guard let session = state.selectedSession else {
            playback.pause()
            playback.clearLoop()
            return
        }
        let url: URL? = (state.useAnnotatedVideo && !state.isBlindReview ? session.annotatedVideoURL : nil)
            ?? session.videoURL
        guard let url else { return }
        let savedTime = preserveTime ? playback.currentTime : 0
        playback.load(url: url)
        if preserveTime, savedTime > 0 {
            playback.seek(to: savedTime)
        }
    }

    private func playOrPauseFromSelectedCycleStart() {
        if playback.isPlaying {
            playback.pause()
            return
        }
        if let chew = state.selectedChew {
            playback.setLoop(start: chew.startTS, end: chew.endTS, enabled: state.loopSelectedChew)
            playback.playWithinCycle(start: chew.startTS, end: chew.endTS)
        } else {
            playback.play()
        }
    }

    // MARK: - Key handler

    private func handleKey(_ event: NSEvent, held: Set<String>) -> NSEvent? {
        guard state.selectedSession != nil else { return event }
        let chars = event.charactersIgnoringModifiers ?? ""
        let mods = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            .subtracting([.numericPad, .function, .capsLock])
        guard mods.intersection([.command, .control]).isEmpty else { return event }
        let plain = mods.isEmpty
        let shift = mods == .shift
        let option = mods == .option

        // S/P/E + ←/→ chord: nudge that marker on the selected chew.
        // Holding s/p/e suppresses their normal key actions while down.
        let chordMarker: ChewMarker? = {
            if held.contains("s") { return .start }
            if held.contains("p") { return .peak }
            if held.contains("e") { return .end }
            return nil
        }()
        if let marker = chordMarker, (event.keyCode == 123 || event.keyCode == 124) {
            let frames: Double = shift ? 5.0 : 1.0
            let dir: Double = (event.keyCode == 124) ? 1.0 : -1.0
            let delta = dir * frames / 30.0
            if let chew = state.selectedChew {
                let target = chew.timestamp(for: marker) + delta
                state.updateMarker(chewID: chew.id, marker: marker, to: target, recordUndo: true)
                if let updated = state.chews.first(where: { $0.id == chew.id }) {
                    playback.seek(to: updated.timestamp(for: marker))
                }
            }
            return nil
        }
        // While a chord key is held, swallow plain s/p/e keystrokes so they
        // don't trigger their unrelated actions (e.g. p = previous chew).
        if !held.isEmpty, plain, ["s", "p", "e"].contains(chars.lowercased()) {
            return nil
        }

        switch (chars, mods) {
        case (" ", _) where plain:
            playOrPauseFromSelectedCycleStart(); return nil
        case ("1", _) where plain:
            setMarkerAtPlayhead(.start); return nil
        case ("2", _) where plain:
            setMarkerAtPlayhead(.peak); return nil
        case ("3", _) where plain:
            setMarkerAtPlayhead(.end); return nil
        case ("a", _), ("A", _):
            quickAddAtPlayhead(); return nil
        case ("d", _), ("D", _):
            if let id = state.selectedBiteID {
                state.rejectBite(id: id)
            } else {
                state.deleteSelectedOrAt(videoTime: playback.currentTime)
            }
            return nil
        case ("b", _), ("B", _):
            state.addBite(at: playback.currentTime); return nil
        case ("n", _), ("N", _):
            if let c = state.selectNextChew() { playback.seek(to: c.startTS) }; return nil
        case ("p", _), ("P", _):
            if let c = state.selectPreviousChew() { playback.seek(to: c.startTS) }; return nil
        case ("[", _) where plain:
            if let c = state.selectedChew { playback.seek(to: c.startTS) }; return nil
        case ("]", _) where plain:
            if let c = state.selectedChew { playback.seek(to: c.endTS) }; return nil
        case ("j", _), ("J", _):
            playback.skip(by: -1.0); return nil
        case ("l", _), ("L", _):
            playback.skip(by: 1.0); return nil
        case ("k", _), ("K", _):
            playOrPauseFromSelectedCycleStart(); return nil
        case ("r", _), ("R", _):
            state.loopSelectedChew.toggle(); return nil
        case ("o", _), ("O", _):
            if state.selectedSession?.annotatedVideoURL != nil {
                if !state.isBlindReview { state.useAnnotatedVideo.toggle() }
            } else {
                state.statusMessage = "No annotated_video.mp4 for this session"
            }
            return nil
        case ("=", _), ("+", _):
            viewport.zoomIn(pivot: playback.currentTime); return nil
        case ("-", _), ("_", _):
            viewport.zoomOut(pivot: playback.currentTime); return nil
        case ("0", _) where plain:
            viewport.fit(); return nil
        case ("f", _), ("F", _):
            if let c = state.selectedChew {
                viewport.focus(on: c, duration: max(playback.duration, 1))
            }
            return nil
        case (",", _) where plain:
            if let m = state.previousMarker(before: playback.currentTime) {
                playback.seek(to: m.time)
                state.selectChew(id: m.chewID)
                state.lastTouchedMarker = m.marker
            }
            return nil
        case (".", _) where plain:
            if let m = state.nextMarker(after: playback.currentTime) {
                playback.seek(to: m.time)
                state.selectChew(id: m.chewID)
                state.lastTouchedMarker = m.marker
            }
            return nil
        case ("v", _) where plain:
            if let next = state.verifyAndAdvance() {
                playback.seek(to: next.startTS)
                viewport.focus(on: next, duration: max(playback.duration, 1))
            }
            return nil
        case ("V", _):
            if let id = state.selectedChewID { state.toggleChewVerified(id: id) }
            return nil
        case ("m", _), ("M", _):
            showReviewQueue.toggle(); return nil
        default: break
        }

        // Arrow keys: ← → step, ⇧ skip, ⌥ nudge marker
        switch event.keyCode {
        case 123: // left
            if option { state.nudgeLastTouchedMarker(by: -1.0/30.0); seekToLastMarker() }
            else if shift { playback.skip(by: -0.25) }
            else if plain { playback.stepFrames(by: -1) }
            else { return event }
            return nil
        case 124: // right
            if option { state.nudgeLastTouchedMarker(by: 1.0/30.0); seekToLastMarker() }
            else if shift { playback.skip(by: 0.25) }
            else if plain { playback.stepFrames(by: 1) }
            else { return event }
            return nil
        case 51, 117: // delete, fwd-delete
            state.deleteSelectedOrAt(videoTime: playback.currentTime); return nil
        case 115: // home
            playback.seekToStart(); return nil
        case 119: // end
            playback.seekToEnd(); return nil
        default: break
        }
        return event
    }

    private func setMarkerAtPlayhead(_ marker: ChewMarker) {
        let t = playback.currentTime
        if let chew = state.selectedChew {
            state.updateMarker(chewID: chew.id, marker: marker, to: t)
            state.lastTouchedMarker = marker
        } else {
            // No selection — quick-add a chew if space allows.
            quickAddAtPlayhead()
        }
    }

    private func quickAddAtPlayhead() {
        let t = playback.currentTime
        let half = 0.21
        state.addChew(start: max(0, t - half), end: t + half, peak: t)
    }

    private func seekToLastMarker() {
        if let c = state.selectedChew {
            playback.seek(to: c.timestamp(for: state.lastTouchedMarker))
        }
    }
}

enum ReviewGuidanceStage: Equatable {
    case workspace
    case decisions(remainingStatus: String)
    case finalize

    var title: String {
        switch self {
        case .workspace: "Open a review workspace"
        case .decisions: "Review every proposed event"
        case .finalize: "Freeze this completed review"
        }
    }

    var nextStep: String {
        switch self {
        case .workspace:
            "Choose the workspace containing sessions/raw and derived/vision. Then select a session with an immutable candidate revision."
        case .decisions:
            "Use Review Queue to accept, edit, reject, or mark each chew and food-entry candidate uncertain or unobservable; then complete the full-video pass."
        case .finalize:
            "Save Overrides, run audit-session, then run finalize-session for this exact candidate revision. Do not edit candidate files."
        }
    }

    var doneWhen: String {
        switch self {
        case .workspace: "Done when the session loads without a lineage error."
        case .decisions(let status): "Done when \(status)."
        case .finalize: "Done when audit-session passes after finalization."
        }
    }
}

private struct ReviewGuidanceView: View {
    let stage: ReviewGuidanceStage

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Label(stage.title, systemImage: "list.number")
                .font(.subheadline.weight(.semibold))
            Text(stage.nextStep)
            Text(stage.doneWhen)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .font(.footnote)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.blue.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
        .accessibilityIdentifier("reviewGuidanceCard")
    }
}

// MARK: - Video surface (custom AVPlayerLayer-backed view)

private struct ReviewSectionsPanel: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var viewport: TimelineViewport

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Recording sections").font(.headline)
            Toggle("Phone labels", isOn: $state.showPhoneSections)
            Toggle("Requested actions", isOn: $state.showInstructionSections)
            Toggle("Chewing sections only", isOn: $state.chewingSectionsOnly)
            if state.isBlindReview {
                Text("Detector predictions stay hidden for this benchmark. Optional requested actions help navigation; check what actually happened in the video.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text("Blue: your reviewed phone labels. Purple: requested actions, never verified labels.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Button("Whole recording") {
                state.focusedSectionID = nil
                playback.clearLoop()
                viewport.fit()
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(state.visibleReviewSections) { section in
                        Button {
                            playback.pause()
                            state.selectChew(id: nil)
                            state.focusedSectionID = section.id
                            playback.setLoop(start: section.start, end: section.end, enabled: false)
                            playback.seek(to: section.start)
                            viewport.center = (section.start + section.end) / 2
                            viewport.zoom = max(1, playback.duration / max(1, section.end - section.start + 1))
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(section.title).font(.callout.weight(.semibold))
                                Text(String(format: "%.1f–%.1f s · %@", section.start, section.end,
                                            section.source == .phone ? "Phone label" : "Requested"))
                                    .font(.caption.monospacedDigit())
                            }
                            .frame(maxWidth: .infinity, alignment: .leading).padding(10)
                            .background((section.source == .phone ? Color.blue : Color.purple).opacity(state.focusedSectionID == section.id ? 0.3 : 0.1),
                                        in: RoundedRectangle(cornerRadius: 8))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            if state.focusedReviewSection != nil {
                Text("The chew queue is focused on this section. Other events stay pending; filtering never verifies or excludes them.")
                    .font(.caption).foregroundStyle(.secondary)
                if !state.isBlindReview {
                    Button("Accept proposals I reviewed here") { state.acceptReviewedSectionProposals() }
                        .disabled(state.reviewQueueChews.allSatisfy { state.disposition(for: $0.id) != nil })
                }
            }
        }
        .toggleStyle(.checkbox)
        .padding(14)
        .frame(maxHeight: .infinity)
        .background(Color(nsColor: .controlBackgroundColor))
    }
}

private struct VideoSurface: NSViewRepresentable {
    @EnvironmentObject var playback: PlaybackController

    func makeNSView(context: Context) -> AVPlayerView {
        let v = AVPlayerView()
        v.controlsStyle = .none
        v.showsFullScreenToggleButton = false
        v.player = playback.player
        v.videoGravity = .resizeAspect
        return v
    }

    func updateNSView(_ nsView: AVPlayerView, context: Context) {
        if nsView.player !== playback.player {
            nsView.player = playback.player
        }
    }
}

// MARK: - Key capture scoped to the review window

private struct KeyCaptureView: NSViewRepresentable {
    let handler: (NSEvent, Set<String>) -> NSEvent?

    func makeNSView(context: Context) -> KeyCaptureNSView {
        let v = KeyCaptureNSView()
        v.handler = handler
        return v
    }

    func updateNSView(_ nsView: KeyCaptureNSView, context: Context) {
        nsView.handler = handler
    }

    static func dismantleNSView(_ nsView: KeyCaptureNSView, coordinator: ()) {
        nsView.removeMonitors()
    }
}

final class KeyCaptureNSView: NSView {
    var handler: ((NSEvent, Set<String>) -> NSEvent?)?
    /// Currently-held marker chord keys (s/p/e). Read by the handler to
    /// decide whether to treat ←/→ as a marker nudge.
    private(set) var heldChordKeys: Set<String> = []
    private var resignObserver: NSObjectProtocol?
    private var keyMonitor: Any?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        removeMonitors()
        guard let window else { return }
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp]) { [weak self] event in
            guard let self, let owningWindow = self.window,
                  event.window === owningWindow, owningWindow.attachedSheet == nil else { return event }
            return self.handle(event, in: owningWindow)
        }
        resignObserver = NotificationCenter.default.addObserver(
            forName: NSWindow.didResignKeyNotification, object: window, queue: .main
        ) { [weak self] _ in
            self?.heldChordKeys.removeAll()
        }
    }

    private func handle(_ event: NSEvent, in window: NSWindow) -> NSEvent? {
        let chars = (event.charactersIgnoringModifiers ?? "").lowercased()
        if event.type == .keyUp {
            heldChordKeys.remove(chars)
            return event
        }
        if window.firstResponder is NSText || (window.firstResponder as? NSTextField)?.isEditable == true {
            heldChordKeys.removeAll()
            return event
        }
        let modsClean = event.modifierFlags
            .intersection(.deviceIndependentFlagsMask)
            .subtracting([.shift, .numericPad, .function, .capsLock])
        if !event.isARepeat, modsClean.isEmpty, ["s", "p", "e"].contains(chars) {
            heldChordKeys.insert(chars)
        }
        guard let handler else { return event }
        return handler(event, heldChordKeys)
    }

    func removeMonitors() {
        if let keyMonitor {
            NSEvent.removeMonitor(keyMonitor)
            self.keyMonitor = nil
        }
        if let resignObserver {
            NotificationCenter.default.removeObserver(resignObserver)
            self.resignObserver = nil
        }
        heldChordKeys.removeAll()
    }

    deinit {
        if let keyMonitor { NSEvent.removeMonitor(keyMonitor) }
        if let resignObserver { NotificationCenter.default.removeObserver(resignObserver) }
    }
}
