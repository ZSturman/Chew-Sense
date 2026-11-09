import AVFoundation
import Foundation

@MainActor
final class PlaybackController: ObservableObject {
    let player = AVPlayer()

    @Published private(set) var currentURL: URL?
    @Published private(set) var currentTime: Double = 0
    @Published private(set) var duration: Double = 0
    @Published private(set) var isPlaying = false
    @Published private(set) var isLooping = false
    @Published var playbackRate: Double = 1.0
    @Published private(set) var displayQuarterTurns = 0

    func rotateDisplay(by turns: Int) {
        displayQuarterTurns = ((displayQuarterTurns + turns) % 4 + 4) % 4
        if let currentURL {
            UserDefaults.standard.set(displayQuarterTurns, forKey: "ReviewDisplayRotation:\(currentURL.path)")
        }
    }

    /// Set to true while a marker drag is in progress. While true, loop logic
    /// will not auto-seek so the drag interaction can't be interrupted.
    var suppressLoop: Bool = false

    private var timeObserver: Any?
    private var endObserver: NSObjectProtocol?
    private var durationTask: Task<Void, Never>?
    private var rangeStart: Double?
    private var rangeEnd: Double?
    private var pendingSeek: UUID?
    private var queuedFrameSteps = 0

    deinit {
        durationTask?.cancel()
        if let endObserver { NotificationCenter.default.removeObserver(endObserver) }
        if let timeObserver {
            player.removeTimeObserver(timeObserver)
        }
    }

    func load(url: URL) {
        guard currentURL != url else { return }
        pause()
        removeTimeObserver()
        currentURL = url
        displayQuarterTurns = UserDefaults.standard.integer(forKey: "ReviewDisplayRotation:\(url.path)")
        currentTime = 0
        duration = 0
        pendingSeek = nil
        queuedFrameSteps = 0
        durationTask?.cancel()
        rangeStart = nil
        rangeEnd = nil
        isLooping = false
        let item = AVPlayerItem(url: url)
        player.replaceCurrentItem(with: item)
        if let endObserver { NotificationCenter.default.removeObserver(endObserver) }
        endObserver = NotificationCenter.default.addObserver(
            forName: AVPlayerItem.didPlayToEndTimeNotification, object: item, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.didPlayToEnd() }
        }
        installTimeObserver()
        loadDuration(for: item.asset)
    }

    func play() {
        // At the very end with no clip range, Play starts the recording again.
        if rangeStart == nil, duration > 0, currentTime >= duration - 0.05 { seek(to: 0) }
        player.playImmediately(atRate: Float(playbackRate))
        isPlaying = true
    }

    /// The video ran out: loop a clip that reaches the end, otherwise stop so
    /// the controls show Play again instead of a stale Pause.
    func didPlayToEnd() {
        if isLooping, !suppressLoop, let rangeStart {
            seek(to: rangeStart)
            play()
            return
        }
        isPlaying = false
        if duration > 0 { currentTime = duration }
    }

    func pause() {
        player.pause()
        isPlaying = false
    }

    func togglePlay() { isPlaying ? pause() : play() }

    /// Resume inside an already-set clip range; otherwise start the clip over.
    func resumeOrRestart(start: Double, end: Double, loop: Bool) {
        if let rangeStart, let rangeEnd, abs(rangeStart - start) < 1e-6, abs(rangeEnd - end) < 1e-6,
           currentTime >= start, currentTime < end - 0.02 {
            isLooping = loop
            play()
        } else {
            playRange(start: start, end: end, loop: loop)
        }
    }

    /// Switch looping of the current clip on or off without interrupting it.
    func setLooping(_ loop: Bool) {
        if rangeStart != nil { isLooping = loop }
    }

    func seek(to seconds: Double) {
        let target = clampedTime(seconds)
        let request = UUID()
        pendingSeek = request
        queuedFrameSteps = 0
        currentTime = target
        player.seek(
            to: CMTime(seconds: target, preferredTimescale: 60_000),
            toleranceBefore: .zero,
            toleranceAfter: .zero
        ) { [weak self] finished in
            Task { @MainActor in
                guard let self, self.pendingSeek == request else { return }
                self.pendingSeek = nil
                let steps = self.queuedFrameSteps
                self.queuedFrameSteps = 0
                self.tick()
                if finished, steps != 0 { self.stepFrames(by: steps) }
            }
        }
    }

    func stepFrames(by count: Int) {
        guard let item = player.currentItem, count != 0 else { return }
        if isPlaying || player.rate != 0 { pause() }
        if pendingSeek != nil {
            queuedFrameSteps += count
            return
        }
        guard count > 0 ? item.canStepForward : item.canStepBackward else { return }
        item.step(byCount: count)
    }

    func skip(by delta: Double) { seek(to: currentTime + delta) }
    func seekToStart() { seek(to: 0) }
    func seekToEnd() {
        guard duration.isFinite, duration > 0 else { return }
        seek(to: max(0, duration - 0.05))
    }

    func setPlaybackRate(_ rate: Double) {
        playbackRate = rate
        if isPlaying { player.rate = Float(rate) }
    }

    func setLoop(start: Double, end: Double, enabled: Bool) {
        guard start < end else { clearLoop(); return }
        rangeStart = max(0, start)
        rangeEnd = max(rangeStart ?? start, end)
        isLooping = enabled
    }

    func clearLoop() {
        rangeStart = nil
        rangeEnd = nil
        isLooping = false
    }

    func playRange(start: Double, end: Double, loop: Bool) {
        setLoop(start: start, end: end, enabled: loop)
        seek(to: start)
        play()
    }

    /// Resume playback within a cycle window. If the playhead is already
    /// inside `[start, end - frameEpsilon)`, leave it where it is so the
    /// reviewer can scrub mid-cycle and press play to continue. Otherwise
    /// (before the cycle, or at/after the last frame) snap back to `start`.
    func playWithinCycle(start: Double, end: Double) {
        let frameEpsilon = 1.0 / 30.0
        if currentTime < start || currentTime >= max(start, end - frameEpsilon) {
            seek(to: start)
        }
        play()
    }

    private func installTimeObserver() {
        timeObserver = player.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 1.0 / 30.0, preferredTimescale: 600),
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.tick()
            }
        }
    }

    private func removeTimeObserver() {
        if let timeObserver {
            player.removeTimeObserver(timeObserver)
            self.timeObserver = nil
        }
    }

    private func tick() {
        guard pendingSeek == nil else { return }
        let seconds = player.currentTime().seconds
        guard seconds.isFinite else { return }
        if currentTime != seconds { currentTime = seconds }

        guard isPlaying, !suppressLoop, let rangeEnd, seconds >= rangeEnd else { return }
        if isLooping, let rangeStart {
            seek(to: rangeStart)
            if isPlaying { play() }
        } else {
            pause()
            seek(to: rangeEnd)
        }
    }

    private func loadDuration(for asset: AVAsset) {
        durationTask = Task { [weak self] in
            do {
                let loadedDuration = try await asset.load(.duration)
                let seconds = CMTimeGetSeconds(loadedDuration)
                guard !Task.isCancelled, seconds.isFinite, seconds > 0 else { return }
                await MainActor.run { self?.duration = seconds }
            } catch {
                await MainActor.run { self?.duration = 0 }
            }
        }
    }

    private func clampedTime(_ seconds: Double) -> Double {
        let lowerBound = max(0, seconds)
        guard duration.isFinite, duration > 0 else { return lowerBound }
        return min(lowerBound, duration)
    }
}
