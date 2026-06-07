import AVFoundation
import Foundation

/// 麦克风录音：16kHz mono
final class AudioRecorder {
    var onAmplitude: ((Float) -> Void)?

    private let engine = AVAudioEngine()
    private let queue = DispatchQueue(label: "com.voiceinput.audio.recorder")
    private var allSamples: [Float] = []
    private var consumedCount: Int = 0
    private var targetFormat: AVAudioFormat?
    private var inputFormat: AVAudioFormat?

    // 诊断
    private var tapCallCount: Int = 0
    private var lastTapLogTime: TimeInterval = 0

    func start() throws {
        _ = stop()
        allSamples.removeAll(keepingCapacity: true)
        consumedCount = 0
        tapCallCount = 0
        lastTapLogTime = 0

        let inputNode = engine.inputNode
        let inputFormat = inputNode.inputFormat(forBus: 0)
        self.inputFormat = inputFormat
        DiagLog.shared.write("[Audio] input format: \(inputFormat.sampleRate)Hz \(inputFormat.channelCount)ch")

        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: 16_000,
            channels: 1,
            interleaved: false
        ) else {
            throw NSError(domain: "AudioRecorder", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "无法创建目标音频格式"])
        }
        self.targetFormat = targetFormat

        // bufferSize = 1024 at 48kHz ≈ 21ms
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            guard let self = self else { return }
            self.handleBuffer(buffer)
        }

        engine.prepare()
        try engine.start()
        DiagLog.shared.write("[Audio] engine.start() ok, isRunning=\(engine.isRunning)")
    }

    func stop() -> [Float] {
        if engine.isRunning {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
        }
        return queue.sync { allSamples }
    }

    func pullSinceLast() -> [Float] {
        queue.sync {
            let total = allSamples.count
            guard total > consumedCount else { return [] }
            let new = Array(allSamples[consumedCount..<total])
            consumedCount = total
            return new
        }
    }

    private func handleBuffer(_ buffer: AVAudioPCMBuffer) {
        tapCallCount += 1

        guard let targetFormat = self.targetFormat else { return }

        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let outCapacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 1024)
        guard let outBuffer = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: outCapacity) else {
            return
        }

        // 每次都创建新的 converter，避免状态污染
        guard let converter = AVAudioConverter(from: buffer.format, to: targetFormat) else {
            return
        }

        var error: NSError?
        var endOfInput = false
        let inputBlock: AVAudioConverterInputBlock = { _, outStatus in
            if endOfInput {
                outStatus.pointee = .endOfStream
                return nil
            }
            outStatus.pointee = .haveData
            endOfInput = true
            return buffer
        }

        converter.convert(to: outBuffer, error: &error, withInputFrom: inputBlock)
        if let err = error {
            DiagLog.shared.write("[Audio] convert 失败: \(err.localizedDescription)")
            return
        }

        guard let channelData = outBuffer.floatChannelData?[0] else {
            return
        }

        let length = Int(outBuffer.frameLength)
        guard length > 0 else { return }

        let array = Array(UnsafeBufferPointer(start: channelData, count: length))
        queue.async { self.allSamples.append(contentsOf: array) }

        // RMS for waveform
        var sumSq: Float = 0
        for i in 0..<length {
            let s = channelData[i]
            sumSq += s * s
        }
        let rms = sqrt(sumSq / Float(length))
        DispatchQueue.main.async { [weak self] in
            self?.onAmplitude?(rms)
        }

        // 每 1 秒打一次累计
        let now = Date().timeIntervalSince1970
        if now - lastTapLogTime > 1.0 {
            lastTapLogTime = now
            queue.sync {
                DiagLog.shared.write("[Audio] tap=\(tapCallCount) 累计=\(self.allSamples.count)")
            }
        }
    }
}

func floatSamplesToInt16LE(_ samples: [Float]) -> Data {
    var data = Data()
    data.reserveCapacity(samples.count * 2)
    for s in samples {
        let clamped = max(-1.0, min(1.0, s))
        let int16 = Int16(clamped * 32_767)
        var v = int16.littleEndian
        withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
    }
    return data
}
