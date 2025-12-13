import SwiftUI
import CoreMotion
import CoreML
import Accelerate
import UniformTypeIdentifiers
import Combine

// MARK: - Chewing state machine

enum ChewState {
    case idle
    case chewing
}

struct ChewDetectorConfig {
    let alpha: Double            // EMA smoothing, e.g. 0.4
    let highThreshold: Double    // e.g. 0.6
    let lowThreshold: Double     // e.g. 0.4
    let minStartWindows: Int     // e.g. 3
    let minEndWindows: Int       // e.g. 2
}

final class ChewDetector {
    private var config: ChewDetectorConfig
    private(set) var state: ChewState = .idle
    
    private var smoothedProb: Double = 0.0
    private var aboveHighCount: Int = 0
    private var belowLowCount: Int = 0
    
    init(config: ChewDetectorConfig) {
        self.config = config
    }
    
    struct UpdateResult {
        let state: ChewState
        let startedEpisode: Bool
        let endedEpisode: Bool
        let smoothedProb: Double
    }
    
    func update(probability p: Double) -> UpdateResult {
        // 1) EMA smoothing
        smoothedProb = config.alpha * p + (1.0 - config.alpha) * smoothedProb
        
        var started = false
        var ended = false
        
        switch state {
        case .idle:
            if smoothedProb >= config.highThreshold {
                aboveHighCount += 1
                belowLowCount = 0
                if aboveHighCount >= config.minStartWindows {
                    state = .chewing
                    started = true
                    aboveHighCount = 0
                }
            } else {
                aboveHighCount = 0
                if smoothedProb <= config.lowThreshold {
                    belowLowCount += 1
                } else {
                    belowLowCount = 0
                }
            }
            
        case .chewing:
            if smoothedProb <= config.lowThreshold {
                belowLowCount += 1
                aboveHighCount = 0
                if belowLowCount >= config.minEndWindows {
                    state = .idle
                    ended = true
                    belowLowCount = 0
                }
            } else {
                belowLowCount = 0
                if smoothedProb >= config.highThreshold {
                    aboveHighCount += 1
                } else {
                    aboveHighCount = 0
                }
            }
        }
        
        return UpdateResult(
            state: state,
            startedEpisode: started,
            endedEpisode: ended,
            smoothedProb: smoothedProb
        )
    }
    
    func currentSmoothedProb() -> Double {
        return smoothedProb
    }
}

// MARK: - Model wrapper

/// Replace `ChewNet` with the actual auto-generated CoreML model class name.
/// After you add ChewNet.mlmodel to the project, Xcode will create a class with that name.
final class ChewModel {
    static let shared = ChewModel()
    
    // Use the auto-generated CoreML type (ChewNet) instead of generic MLModel.
    // Make sure ChewNet.mlmodel is added to the target so this type exists.
    private let model: ChewNet
    
    private init() {
        let config = MLModelConfiguration()
        model = try! ChewNet(configuration: config)
    }
    
    /// Predict probability of chewing given a 12-element feature vector.
    /// This assumes the CoreML model input is named `input` and the output is a logit named `logit`.
    /// If your generated model has different names (e.g. `features` / `prob`), adjust accordingly.
    func predictChewProbability(features: [Double]) -> Double {
        precondition(features.count == 12, "Expected 12 features")
        
        // Create MLMultiArray from [Double].
        // The CoreML model expects a rank-2 array with shape [1, 12] of Float32.
        let inputArray = try! MLMultiArray(shape: [1, 12], dataType: .float32)
        for (i, v) in features.enumerated() {
            // Fill using the flat index; CoreML will interpret it as [1, 12].
            inputArray[i] = NSNumber(value: Float(v))
        }
        
        // Adjust ChewNetInput(...) to match the actual generated input struct name.
        let input = ChewNetInput(input: inputArray)
        let output = try! model.prediction(input: input)
        
        // If the model outputs a probability directly (e.g. as a scalar Double), you could do:
        // return output.prob
        
        // If the model output is a 1D MLMultiArray named `logit` with a single element,
        // read the first element and convert it from logit to probability:
        let logitArray = output.logit  // MLMultiArray
        let logit = logitArray[0].doubleValue
        let p = 1.0 / (1.0 + exp(-logit))
        return p
    }
}



// MARK: - Feature normalization (from training)
//
// Fill these arrays with the 12-element mean and std values dumped from chewnet.pth.
// They must match the feature order in computeFeatures(...).
let FEATURE_MEAN: [Double] = [0.0487392358481884, 0.0013371038949117064, 0.054871924221515656, 0.39361074566841125, 0.4668533205986023, 0.012962675653398037, 0.0, 0.0003582321514841169, 0.0, 0.0, 4.997451305389404, 8.736513137817383]

let FEATURE_STD: [Double] = [0.05903511121869087, 0.004085940308868885, 0.06470552831888199, 0.4862076938152313, 0.5923627018928528, 0.013633020222187042, 1.0, 0.0014455115888267756, 1.0, 1.0, 2.540461301803589, 5.1088175773620605]

func normalizeFeatures(_ features: [Double]) -> [Double] {
    precondition(features.count == FEATURE_MEAN.count)
    var out = [Double](repeating: 0.0, count: features.count)
    for i in 0..<features.count {
        let s = FEATURE_STD[i]
        out[i] = (features[i] - FEATURE_MEAN[i]) / (s == 0.0 ? 1.0 : s)
    }
    return out
}

// MARK: - Motion sample + feature computation

struct MotionSample {
    let timestamp: TimeInterval
    let ax: Double
    let ay: Double
    let az: Double
    let gx: Double
    let gy: Double
    let gz: Double
}

/// This MUST match your Python feature extraction (get_features.py).
/// Below is just a stub with a few simple features.
/// You need to implement the same 12 features you used in training.
func computeFeatures(from samples: [MotionSample]) -> [Double] {
    // If not enough samples, return zeros
    guard !samples.isEmpty else {
        return Array(repeating: 0.0, count: 12)
    }
    
    let ax = samples.map { $0.ax }
    let ay = samples.map { $0.ay }
    let az = samples.map { $0.az }
    let gx = samples.map { $0.gx }
    let gy = samples.map { $0.gy }
    let gz = samples.map { $0.gz }
    
    func magnitude(_ x: [Double], _ y: [Double], _ z: [Double]) -> [Double] {
        zip(zip(x, y), z).map { (xy, z) in
            let (x, y) = xy
            return sqrt(x * x + y * y + z * z)
        }
    }
    
    let accelMag = magnitude(ax, ay, az)
    let gyroMag  = magnitude(gx, gy, gz)
    
    func mean(_ x: [Double]) -> Double {
        guard !x.isEmpty else { return 0.0 }
        return x.reduce(0, +) / Double(x.count)
    }
    func variance(_ x: [Double]) -> Double {
        guard !x.isEmpty else { return 0.0 }
        let m = mean(x)
        let v = x.map { ($0 - m) * ($0 - m) }.reduce(0, +) / Double(x.count)
        return v
    }
    func rms(_ x: [Double]) -> Double {
        guard !x.isEmpty else { return 0.0 }
        let s = x.map { $0 * $0 }.reduce(0, +) / Double(x.count)
        return sqrt(s)
    }
    
    // Jerk: diff(accelMag)
    var jerkVals: [Double] = []
    if accelMag.count > 1 {
        for i in 1..<accelMag.count {
            jerkVals.append(accelMag[i] - accelMag[i - 1])
        }
    }
    
    func zeroCrossingRate(_ x: [Double]) -> Double {
        guard x.count > 1 else { return 0.0 }
        var crossings = 0
        for i in 1..<x.count {
            if (x[i - 1] <= 0 && x[i] > 0) || (x[i - 1] >= 0 && x[i] < 0) {
                crossings += 1
            }
        }
        return Double(crossings) / Double(x.count - 1)
    }
    
    let meanAcc   = mean(accelMag)
    let varAcc    = variance(accelMag)
    let rmsAcc    = rms(accelMag)
    let meanGyro  = mean(gyroMag)
    let rmsGyro   = rms(gyroMag)
    let rmsJerk   = rms(jerkVals)
    let zcrAcc    = zeroCrossingRate(accelMag)
    
    // Placeholder values for the remaining features (frequency-based).
    // TODO: implement bandpower / spectral centroid / rolloff using Accelerate
    let chewBandpower_1_3 = 0.0
    let bandpower_0_8_1_5 = 0.0
    let bandpower_2_4     = 0.0
    let spectralCentroid  = 0.0
    let spectralRolloff85 = 0.0
    
    return [
        meanAcc,
        varAcc,
        rmsAcc,
        meanGyro,
        rmsGyro,
        rmsJerk,
        zcrAcc,
        chewBandpower_1_3,
        bandpower_0_8_1_5,
        bandpower_2_4,
        spectralCentroid,
        spectralRolloff85
    ]
}

// MARK: - Motion manager (AirPods + model + logging)

final class MotionManager: ObservableObject {
    private let headphoneManager = CMHeadphoneMotionManager()
    private let detector: ChewDetector
    private let model = ChewModel.shared
    
    private let windowDuration: TimeInterval = 0.75
    private let stepDuration: TimeInterval = 0.10
    
    private var samples: [MotionSample] = []
    private var lastFeatureTime: TimeInterval = 0
    
    @Published var isRunning: Bool = false
    @Published var statusText: String = "Idle"
    @Published var latestProb: Double = 0.0
    @Published var latestSmoothed: Double = 0.0
    @Published var isChewing: Bool = false
    @Published var logPreview: [String] = []
    
    private var logLines: [String] = []
    private let logFileURL: URL
    
    init() {
        let cfg = ChewDetectorConfig(
            alpha: 0.4,
            highThreshold: 0.6,
            lowThreshold: 0.4,
            minStartWindows: 3,
            minEndWindows: 2
        )
        detector = ChewDetector(config: cfg)
        
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        logFileURL = docs.appendingPathComponent("chew_log.txt")
        
        appendLogLine("timestamp,probability,smoothed,state")
    }
    
    func start() {
        guard !isRunning else { return }
        guard headphoneManager.isDeviceMotionAvailable else {
            statusText = "Headphone motion not available. Ensure AirPods are connected."
            return
        }
        
        // CMHeadphoneMotionManager does not expose an update interval; it uses a system-defined rate.
        // We simply start motion updates here.
        headphoneManager.startDeviceMotionUpdates(to: OperationQueue()) { [weak self] motion, error in
            guard let self = self, let motion = motion, error == nil else { return }
            self.handleMotion(motion)
        }
        
        DispatchQueue.main.async {
            self.isRunning = true
            self.statusText = "Running"
        }
    }
    
    func stop() {
        guard isRunning else { return }
        headphoneManager.stopDeviceMotionUpdates()
        DispatchQueue.main.async {
            self.isRunning = false
            self.statusText = "Stopped"
        }
    }
    
    private func handleMotion(_ motion: CMDeviceMotion) {
        let now = motion.timestamp  // seconds since boot
        
        let ua = motion.userAcceleration
        let rr = motion.rotationRate
        
        let sample = MotionSample(
            timestamp: now,
            ax: ua.x,
            ay: ua.y,
            az: ua.z,
            gx: rr.x,
            gy: rr.y,
            gz: rr.z
        )
        
        samples.append(sample)
        
        // Drop old samples beyond windowDuration
        let cutoff = now - windowDuration
        samples.removeAll { $0.timestamp < cutoff }
        
        // Step control
        if now - lastFeatureTime >= stepDuration, let first = samples.first, now - first.timestamp >= windowDuration * 0.8 {
            lastFeatureTime = now
            processWindow()
        }
    }
    
    private func processWindow() {
        let windowSamples = samples
        
        // 1) Compute features
        let features = computeFeatures(from: windowSamples)
        
        // 2) Normalize features using stored mean/std from training
        let normalizedFeatures = normalizeFeatures(features)
        
        // 3) Model prediction
        let p = model.predictChewProbability(features: normalizedFeatures)
        
        // 4) Temporal chewing detector
        let detResult = detector.update(probability: p)
        
        // 5) Log and update UI
        let ts = Date()
        let formatter = ISO8601DateFormatter()
        let tsStr = formatter.string(from: ts)
        
        let stateStr: String
        switch detResult.state {
        case .idle: stateStr = "idle"
        case .chewing: stateStr = "chewing"
        }
        
        let line = "\(tsStr),\(String(format: "%.4f", p)),\(String(format: "%.4f", detResult.smoothedProb)),\(stateStr)"
        appendLogLine(line)
        
        DispatchQueue.main.async {
            self.latestProb = p
            self.latestSmoothed = detResult.smoothedProb
            self.isChewing = (detResult.state == .chewing)
            self.statusText = "Running (\(stateStr))"
        }
    }
    
    private func appendLogLine(_ line: String) {
        logLines.append(line)
        // Keep a short preview for UI
        let tailCount = 50
        if logLines.count > tailCount {
            logPreview = Array(logLines.suffix(tailCount))
        } else {
            logPreview = logLines
        }
    }
    
    /// Writes the log to disk and returns the URL, or nil on error.
    func exportLogFile() -> URL? {
        let text = logLines.joined(separator: "\n")
        do {
            try text.write(to: logFileURL, atomically: true, encoding: .utf8)
            return logFileURL
        } catch {
            print("Failed to write log: \(error)")
            return nil
        }
    }
}

// MARK: - Share sheet wrapper

struct ShareSheet: UIViewControllerRepresentable {
    let activityItems: [Any]
    
    func makeUIViewController(context: Context) -> UIActivityViewController {
        let vc = UIActivityViewController(activityItems: activityItems, applicationActivities: nil)
        return vc
    }
    
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

// MARK: - SwiftUI UI

struct ContentView: View {
    @StateObject private var motionManager = MotionManager()
    
    @State private var isSharePresented = false
    @State private var shareItems: [Any] = []
    
    var body: some View {
        NavigationView {
            VStack(alignment: .leading, spacing: 16) {
                
                HStack {
                    Button(action: {
                        if motionManager.isRunning {
                            motionManager.stop()
                        } else {
                            motionManager.start()
                        }
                    }) {
                        Text(motionManager.isRunning ? "Stop" : "Start")
                            .font(.headline)
                            .padding()
                            .frame(maxWidth: .infinity)
                            .background(motionManager.isRunning ? Color.red.opacity(0.2) : Color.green.opacity(0.2))
                            .cornerRadius(10)
                    }
                }
                
                Text("Status: \(motionManager.statusText)")
                    .font(.subheadline)
                
                Text(String(format: "Latest probability: %.4f", motionManager.latestProb))
                    .font(.subheadline)
                Text(String(format: "Smoothed probability: %.4f", motionManager.latestSmoothed))
                    .font(.subheadline)
                
                Text("Chewing state: \(motionManager.isChewing ? "CHEWING" : "idle")")
                    .font(.title3)
                    .foregroundColor(motionManager.isChewing ? .orange : .secondary)
                    .padding(.top, 4)
                
                Divider()
                
                Text("Recent log:")
                    .font(.headline)
                
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(motionManager.logPreview, id: \.self) { line in
                            Text(line)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .background(Color.gray.opacity(0.05))
                .cornerRadius(8)
                
                Button(action: {
                    if let url = motionManager.exportLogFile() {
                        shareItems = [url]
                        isSharePresented = true
                    }
                }) {
                    Text("Share Log")
                        .font(.headline)
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(Color.blue.opacity(0.2))
                        .cornerRadius(10)
                }
                .padding(.top, 8)
                
                Spacer()
            }
            .padding()
            .navigationTitle("Chew Debug")
            .sheet(isPresented: $isSharePresented) {
                ShareSheet(activityItems: shareItems)
            }
        }
    }
}

