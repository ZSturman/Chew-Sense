import Foundation
import Combine
import CoreMedia
import CoreMotion
import QuartzCore
import Vision

/// Lightweight on-device recording-quality signals.
///
/// * Face/jaw: `VNDetectFaceRectanglesRequest` at about 4 Hz, one frame at a
///   time on a utility queue; frames arriving while busy are skipped, so the
///   capture pipeline is never blocked.
/// * Lighting: mean luma from a sparse grid of the Y plane.
/// * Phone motion: `CMMotionManager` user acceleration at 10 Hz.
/// * AirPods: freshness of the last recorded headphone sample.
///
/// These are heuristics for prompting the wearer, not measurements used as
/// labels. Thresholds need confirmation on a physical device.
final class QualityMonitor: ObservableObject {
    @Published private(set) var snapshot = QualitySnapshot(faceVisible: nil, jawVisible: nil, brightness: nil,
                                                           phoneMotionG: nil, motionFresh: false, captureRunning: true)

    private let analyzer = FrameAnalyzer()
    private let phoneMotion = CMMotionManager()
    private var timer: AnyCancellable?
    private var motionFreshProvider: () -> Bool = { false }
    private var captureRunningProvider: () -> Bool = { true }

    /// The closure the recorder calls for every video frame (any queue).
    var frameObserver: (CMSampleBuffer) -> Void { analyzer.observe }

    func start(motionFresh: @escaping () -> Bool, captureRunning: @escaping () -> Bool) {
        motionFreshProvider = motionFresh
        captureRunningProvider = captureRunning
        if phoneMotion.isDeviceMotionAvailable, !phoneMotion.isDeviceMotionActive {
            phoneMotion.deviceMotionUpdateInterval = 0.1
            phoneMotion.startDeviceMotionUpdates()
        }
        timer = Timer.publish(every: 0.1, on: .main, in: .common).autoconnect().sink { [weak self] _ in
            self?.refresh()
        }
    }

    func stop() {
        timer = nil
        phoneMotion.stopDeviceMotionUpdates()
    }

    private func refresh() {
        let frame = analyzer.latest()
        var phone: Double?
        if let a = phoneMotion.deviceMotion?.userAcceleration {
            phone = (a.x * a.x + a.y * a.y + a.z * a.z).squareRoot()
        }
        let next = QualitySnapshot(
            faceVisible: frame.stale ? nil : frame.faceVisible,
            jawVisible: frame.stale ? nil : frame.jawVisible,
            brightness: frame.stale ? nil : frame.brightness,
            phoneMotionG: phone,
            motionFresh: motionFreshProvider(),
            captureRunning: captureRunningProvider() && !frame.stale
        )
        if next != snapshot { snapshot = next }
    }
}

/// Thread-safe frame analysis shared with the capture queue.
nonisolated final class FrameAnalyzer: @unchecked Sendable {
    struct Result {
        var faceVisible: Bool?
        var jawVisible: Bool?
        var brightness: Double?
        var stale: Bool
    }

    private let lock = NSLock()
    private let queue = DispatchQueue(label: "chewsense.quality", qos: .utility)
    private var busy = false
    private var lastAnalysis = 0.0
    private var lastFrameArrival = 0.0
    private var faceVisible: Bool?
    private var jawVisible: Bool?
    private var brightness: Double?
    let interval = 0.25
    let staleAfter = 1.0

    func observe(_ buffer: CMSampleBuffer) {
        let now = CACurrentMediaTime()
        lock.lock()
        lastFrameArrival = now
        guard !busy, now - lastAnalysis >= interval, let pixels = CMSampleBufferGetImageBuffer(buffer) else {
            lock.unlock()
            return
        }
        busy = true
        lastAnalysis = now
        lock.unlock()
        queue.async { [weak self] in self?.analyze(pixels) }
    }

    func latest() -> Result {
        lock.lock()
        defer { lock.unlock() }
        let stale = lastFrameArrival == 0 || CACurrentMediaTime() - lastFrameArrival > staleAfter
        return Result(faceVisible: faceVisible, jawVisible: jawVisible, brightness: brightness, stale: stale)
    }

    private func analyze(_ pixels: CVPixelBuffer) {
        let luma = Self.meanLuma(pixels)
        let request = VNDetectFaceRectanglesRequest()
        let handler = VNImageRequestHandler(cvPixelBuffer: pixels, orientation: .up, options: [:])
        var face: Bool?
        var jaw: Bool?
        if (try? handler.perform([request])) != nil {
            let faces = request.results ?? []
            // Vision coordinates are normalized with the origin at bottom-left.
            if let box = faces.max(by: { $0.boundingBox.height < $1.boundingBox.height })?.boundingBox {
                let inside = box.minX >= -0.02 && box.maxX <= 1.02 && box.maxY <= 1.02
                face = inside && box.height >= 0.12
                // Leave room below the detected face for the chin/jaw.
                jaw = face == true && box.minY >= 0.04
            } else {
                face = false
                jaw = false
            }
        }
        lock.lock()
        faceVisible = face
        jawVisible = jaw
        brightness = luma
        busy = false
        lock.unlock()
    }

    /// Mean of a 16×16 grid of luma samples, 0…1. Returns nil for non-YUV buffers.
    static func meanLuma(_ pixels: CVPixelBuffer) -> Double? {
        let format = CVPixelBufferGetPixelFormatType(pixels)
        guard format == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange
                || format == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange else { return nil }
        CVPixelBufferLockBaseAddress(pixels, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixels, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddressOfPlane(pixels, 0) else { return nil }
        let width = CVPixelBufferGetWidthOfPlane(pixels, 0)
        let height = CVPixelBufferGetHeightOfPlane(pixels, 0)
        let stride = CVPixelBufferGetBytesPerRowOfPlane(pixels, 0)
        let bytes = base.assumingMemoryBound(to: UInt8.self)
        var total = 0
        let steps = 16
        for row in 0..<steps {
            let y = (row * height) / steps + height / (2 * steps)
            for column in 0..<steps {
                let x = (column * width) / steps + width / (2 * steps)
                total += Int(bytes[y * stride + x])
            }
        }
        let mean = Double(total) / Double(steps * steps)
        // Video-range luma spans 16…235.
        return format == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
            ? max(0, min(1, (mean - 16) / 219)) : mean / 255
    }
}
