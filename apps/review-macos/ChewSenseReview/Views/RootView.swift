import SwiftUI
import AppKit
import AVFoundation

/// Up next when no recording is open; otherwise the video, a slim header with
/// the review's steps, the timeline, and the guide panel for the current step.
/// Playback shortcuts are scoped to the review window.
struct RootView: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var viewport: TimelineViewport
    @EnvironmentObject var assist: CoarseAssistStore
    @EnvironmentObject var project: ProjectStatusStore

    @State private var showSidebar = false
    @State private var showHelp: Bool = false
    @State private var showSignals = false
    @AppStorage("ChewSenseReview.videoOverlay") private var showChewOverlay = true

    private var plan: GuidedPlan { state.guidedPlan(spansPending: assist.needsReview) }
    /// The step on screen: the reviewer's choice if it is part of this review, else the recommended one.
    private var step: GuidedStep {
        if let chosen = state.chosenStep, plan.steps.contains(chosen) || chosen == .done { return chosen }
        return plan.current
    }
    private var isSpansStep: Bool { step == .spans && state.candidateIntegrityError == nil }

    var body: some View {
        HStack(spacing: 0) {
            if showSidebar {
                SessionSidebar(onClose: { showSidebar = false })
                    .frame(width: 260)
                    .transition(.move(edge: .leading))
                Divider()
            }
            mainStage
            if state.selectedSession?.videoURL != nil {
                Divider()
                GuidePanel(plan: plan, step: step, onPlayClip: playOrPauseFromSelectedCycleStart)
                    .frame(width: 340)
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
        .onAppear { project.start(workspace: state.workspaceRoot) }
        .onChange(of: state.workspaceRoot) { _, root in project.start(workspace: root) }
        .onReceive(NotificationCenter.default.publisher(for: .toggleSidebar)) { _ in
            showSidebar.toggle()
        }
        .onReceive(NotificationCenter.default.publisher(for: .toggleHelp)) { _ in
            showHelp.toggle()
        }
        .onReceive(NotificationCenter.default.publisher(for: .pickWorkspace)) { _ in
            pickWorkspaceRoot()
        }
        .onChange(of: playback.isPlaying) { _, playing in
            if playing {
                viewport.snapToPlayhead(playback.currentTime, duration: max(playback.duration, 1))
            }
        }
        .onChange(of: playback.currentTime) { _, time in
            if playback.isPlaying { state.noteWatched(time) }
        }
        .onChange(of: state.selectedSession?.id) { _, _ in
            // Keep span labels made so far before showing another recording's proposals.
            assist.saveIfDirty()
            loadEffectiveVideo(preserveTime: false)
            assist.load(session: state.isBlindReview || state.retirement != nil ? nil : state.selectedSession,
                        workspaceRoot: state.workspaceRoot)
            if state.selectedSession != nil { showSidebar = false }
        }
        // When the step on screen is finished (e.g. the last suggestion was decided),
        // follow the recommended step instead of staying there.
        .onChange(of: plan.current) { _, _ in
            if let chosen = state.chosenStep, state.isComplete(chosen, spansPending: assist.needsReview) {
                state.chosenStep = nil
            }
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
            UpNextView(showRecordings: { showSidebar = true })
        } else if state.selectedSession?.videoURL == nil {
            VStack(spacing: 16) {
                ContentUnavailableView(
                    "This recording has no video",
                    systemImage: "video.slash",
                    description: Text("Review needs the original .mp4 or .mov in the recording's folder. Nothing was changed.")
                )
                Button("Back to Up next") { state.closeSession() }
                    .buttonStyle(.borderedProminent)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            videoStage
        }
    }

    private var workspacePrompt: some View {
        VStack(spacing: 14) {
            Text("Chew Sense Review").font(.largeTitle.weight(.semibold))
            Text("Choose your ChewSense workspace folder once. After that, Review opens straight to the next recording that needs you.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 460)
            Button("Choose workspace…") { pickWorkspaceRoot() }
                .buttonStyle(.borderedProminent).controlSize(.large)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var videoStage: some View {
        VStack(spacing: 10) {
            ReviewHeader(plan: plan, step: step,
                         isComplete: { state.isComplete($0, spansPending: assist.needsReview) },
                         toggleSidebar: { showSidebar.toggle() },
                         toggleHelp: { showHelp.toggle() },
                         toggleSignals: { showSignals.toggle() })
            GeometryReader { geometry in
                VideoSurface(player: playback.player, quarterTurns: playback.displayQuarterTurns)
                    .equatable()
                    .frame(width: geometry.size.width, height: geometry.size.height)
            }
            .background(Color.black).clipped()
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .overlay { if showChewOverlay && !isSpansStep { ChewOverlay() } }
            .overlay(alignment: .topTrailing) {
                HStack(spacing: 6) {
                    videoButton(showChewOverlay ? "eye" : "eye.slash",
                                help: showChewOverlay ? "Chews flash on the video. Click to hide the flashes."
                                                      : "Show a flash on the video for each marked chew.") {
                        showChewOverlay.toggle()
                    }
                    .accessibilityIdentifier("toggleChewOverlay")
                    rotateButton
                }
                .padding(8)
            }
            if showSignals && !state.isBlindReview && !isSpansStep {
                CycleShapeChart()
                    .frame(height: 110)
            }
            if !isSpansStep && state.candidateIntegrityError == nil { BottomDeck() }
        }
        .padding(12)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay { KeyCaptureView(handler: handleKey).frame(width: 0, height: 0) }
    }

    /// Turns the picture a quarter turn clockwise; remembered for this recording.
    private var rotateButton: some View {
        videoButton("rotate.right",
                    help: playback.rotationIsAutomatic
                        ? "Turned automatically because the video is stored sideways. Click to turn it a quarter turn if it's still wrong; your choice is remembered."
                        : "Turn the picture a quarter turn clockwise. Remembered for this recording; the video file isn't changed.") {
            playback.rotateDisplay(by: 1)
        }
        .accessibilityLabel("Rotate video")
        .accessibilityIdentifier("rotateVideo")
    }

    private func videoButton(_ symbol: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.body.weight(.semibold))
                .frame(width: 18, height: 18)
                .padding(7)
                .background(.black.opacity(0.45), in: Circle())
                .foregroundStyle(.white)
        }
        .buttonStyle(.plain)
        .help(help)
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
        // In Check suggestions the card owns playback (a looping clip with context).
        guard step != .chews else { return }
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
        playback.load(url: url, recordingID: session.id,
                      suggestedQuarterTurns: VideoOrientation.suggestion(for: session, workspaceRoot: state.workspaceRoot))
        if preserveTime, savedTime > 0 {
            playback.seek(to: savedTime)
        }
    }

    private func playOrPauseFromSelectedCycleStart() {
        if playback.isPlaying {
            playback.pause()
            return
        }
        if isSpansStep {
            guard let item = assist.selectedItem else { return }
            assist.startTimer()
            ChewCue.toggle(start: item.start, end: item.end, playback: playback)
            return
        }
        if step == .chews {
            if let entry = state.selectedBite {
                ChewCue.toggle(entry, state: state, playback: playback)
            } else if let chew = state.selectedChew {
                ChewCue.toggle(chew, state: state, playback: playback, viewport: viewport)
            } else {
                playback.play()
            }
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
        // < and > change playback speed in every step.
        let typed = event.charactersIgnoringModifiers ?? ""
        if event.modifierFlags.intersection([.command, .control, .option]).isEmpty, typed == "<" || typed == ">" {
            playback.stepRate(by: typed == "<" ? -1 : 1)
            return nil
        }
        if state.retirement != nil {
            // Watch only: play/pause and frame or quarter-second steps.
            let mods = event.modifierFlags.intersection([.command, .control, .option])
            guard mods.isEmpty else { return event }
            if event.charactersIgnoringModifiers == " " { playOrPauseFromSelectedCycleStart(); return nil }
            if event.keyCode == 123 || event.keyCode == 124 {
                let forward = event.keyCode == 124
                if event.modifierFlags.contains(.shift) { playback.skip(by: forward ? 0.25 : -0.25) }
                else { playback.stepFrames(by: forward ? 1 : -1) }
                return nil
            }
            return event
        }
        let chars = event.charactersIgnoringModifiers ?? ""
        let mods = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            .subtracting([.numericPad, .function, .capsLock])
        guard mods.intersection([.command, .control]).isEmpty else { return event }
        let plain = mods.isEmpty
        let shift = mods == .shift
        let option = mods == .option

        if isSpansStep {
            guard plain else { return event }
            if chars == " " {
                playOrPauseFromSelectedCycleStart()
                return nil
            }
            if chars.lowercased() == "r", let item = assist.selectedItem {
                assist.startTimer()
                ChewCue.play(start: item.start, end: item.end, playback: playback)
                return nil
            }
            if event.keyCode == 123 || event.keyCode == 124 {
                playback.stepFrames(by: event.keyCode == 123 ? -1 : 1)
                return nil
            }
            return event
        }

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
            state.addFoodEntryChecked(at: playback.currentTime); return nil
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
            if step == .chews {
                // Replay the current suggestion from the start of its clip.
                if let entry = state.selectedBite { ChewCue.loop(entry, state: state, playback: playback) }
                else if let chew = state.selectedChew { ChewCue.loop(chew, state: state, playback: playback, viewport: viewport) }
            } else {
                state.loopSelectedChew.toggle()
            }
            return nil
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
                if step == .chews {
                    ChewCue.loop(next, state: state, playback: playback, viewport: viewport)
                } else {
                    playback.seek(to: next.startTS)
                    viewport.focus(on: next, duration: max(playback.duration, 1))
                }
            }
            return nil
        case ("V", _):
            if let id = state.selectedChewID { state.toggleChewVerified(id: id) }
            return nil
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
        // While checking suggestions, stay on the suggestion being checked.
        let current = state.selectedChewID
        if state.addChewFitting(at: playback.currentTime), step == .chews, let current {
            state.selectChew(id: current)
        }
    }

    private func seekToLastMarker() {
        if let c = state.selectedChew {
            playback.seek(to: c.timestamp(for: state.lastTouchedMarker))
        }
    }
}

// MARK: - Video surface (custom AVPlayerLayer-backed view)

private struct VideoSurface: NSViewRepresentable, Equatable {
    let player: AVPlayer
    let quarterTurns: Int

    func makeNSView(context: Context) -> ReviewVideoView {
        ReviewVideoView(player: player, quarterTurns: quarterTurns)
    }

    func updateNSView(_ nsView: ReviewVideoView, context: Context) {
        nsView.configure(player: player, quarterTurns: quarterTurns)
    }
}

/// The custom transport owns playback. Keep the video layer attached and
/// independent of playhead updates and AppKit's media controls.
final class ReviewVideoView: NSView {
    private let videoLayer: AVPlayerLayer
    private var quarterTurns: Int

    init(player: AVPlayer, quarterTurns: Int = 0) {
        videoLayer = AVPlayerLayer(player: player)
        self.quarterTurns = quarterTurns
        super.init(frame: .zero)
        let surface = CALayer()
        surface.backgroundColor = NSColor.black.cgColor
        surface.masksToBounds = true
        videoLayer.videoGravity = .resizeAspect
        surface.addSublayer(videoLayer)
        layer = surface
        wantsLayer = true
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("Use init(player:quarterTurns:)") }

    func configure(player: AVPlayer, quarterTurns: Int) {
        if videoLayer.player !== player { videoLayer.player = player }
        if self.quarterTurns != quarterTurns {
            self.quarterTurns = quarterTurns
            needsLayout = true
        }
    }

    override func layout() {
        super.layout()
        let size = quarterTurns.isMultiple(of: 2)
            ? bounds.size : CGSize(width: bounds.height, height: bounds.width)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        videoLayer.bounds = CGRect(origin: .zero, size: size)
        videoLayer.position = CGPoint(x: bounds.midX, y: bounds.midY)
        videoLayer.setAffineTransform(CGAffineTransform(rotationAngle: -CGFloat(quarterTurns) * .pi / 2))
        CATransaction.commit()
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
