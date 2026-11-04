//
//  eating-parity: run Live's eating-detector runtime on recorded inputs.
//
//  Built from the same sources as the Live app (see tools/eating-parity/build)
//  and driven by `chewsense-motion eating-parity`, which writes the inputs and
//  compares the outputs with the Python reference.
//
//  Input  <name>.input.bin : "CSEP0001", UInt64 n, Float64 firstEnd,
//                            n x Float64 timestamps, n x 6 x Float32 values
//  Output <name>.swift.bin : "CSEO0001", UInt64 m, UInt64 featureCount, then per
//                            window Float64 end, valid, probability (NaN when
//                            invalid), decoderInput, smoothed, UInt8 chewing,
//                            featureCount x Float32 (NaN when invalid)
//

import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

struct Reader {
    let data: Data
    var offset = 0
    mutating func bytes(_ count: Int) -> Data {
        guard offset + count <= data.count else { fail("truncated input") }
        defer { offset += count }
        return data.subdata(in: offset..<(offset + count))
    }
    mutating func scalar<T>(_: T.Type) -> T {
        bytes(MemoryLayout<T>.size).withUnsafeBytes { $0.loadUnaligned(as: T.self) }
    }
}

let arguments = CommandLine.arguments
guard arguments.count == 4 else { fail("usage: eating-parity <spec.json> <input-dir> <output-dir>") }
let spec: EatingDetectorSpec
do {
    spec = try EatingDetectorSpec.load(from: URL(fileURLWithPath: arguments[1]))
} catch {
    fail("\(error)")
}
let inputDir = URL(fileURLWithPath: arguments[2])
let outputDir = URL(fileURLWithPath: arguments[3])
try? FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
let inputs = (try? FileManager.default.contentsOfDirectory(at: inputDir, includingPropertiesForKeys: nil))?
    .filter { $0.lastPathComponent.hasSuffix(".input.bin") }
    .sorted { $0.lastPathComponent < $1.lastPathComponent } ?? []
guard !inputs.isEmpty else { fail("no *.input.bin files in \(inputDir.path)") }

var totalWindows = 0
var totalSeconds = 0.0
let started = Date()
for url in inputs {
    var reader = Reader(data: try! Data(contentsOf: url))
    guard String(data: reader.bytes(8), encoding: .ascii) == "CSEP0001" else { fail("bad magic in \(url.path)") }
    let count = Int(reader.scalar(UInt64.self))
    let firstEnd = reader.scalar(Double.self)
    let times = (0..<count).map { _ in reader.scalar(Double.self) }
    let values = (0..<(count * 6)).map { _ in reader.scalar(Float.self) }
    let runtime = EatingDetectorRuntime(spec: spec, firstWindowEnd: firstEnd)
    runtime.recordsFeatures = true
    var outputs: [EatingDetectorOutput] = []
    let clock = Date()
    for index in 0..<count {
        let sample = EatingMotionSample(timestamp: times[index], values: Array(values[(index * 6)..<(index * 6 + 6)]))
        outputs.append(contentsOf: runtime.push(sample))
    }
    totalSeconds += Date().timeIntervalSince(clock)
    totalWindows += outputs.count

    var out = Data("CSEO0001".utf8)
    func append<T>(_ value: T) { withUnsafeBytes(of: value) { out.append(contentsOf: $0) } }
    append(UInt64(outputs.count))
    append(UInt64(spec.model.featureCount))
    for output in outputs {
        append(output.windowEnd)
        append(output.validFraction)
        append(output.probability ?? .nan)
        append(output.decoderInput)
        append(output.smoothed)
        append(UInt8(output.chewing ? 1 : 0))
        let features = output.features ?? Array(repeating: Float.nan, count: spec.model.featureCount)
        for value in features { append(value) }
    }
    let name = url.lastPathComponent.replacingOccurrences(of: ".input.bin", with: ".swift.bin")
    try! out.write(to: outputDir.appendingPathComponent(name))
}
let perWindowMs = totalWindows > 0 ? totalSeconds / Double(totalWindows) * 1000 : 0
print("{\"model_id\": \"\(spec.modelId)\", \"recordings\": \(inputs.count), \"windows\": \(totalWindows), "
      + "\"ms_per_window\": \(perWindowMs), \"wall_s\": \(Date().timeIntervalSince(started))}")
