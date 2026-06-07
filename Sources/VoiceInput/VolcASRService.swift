import Foundation

/// 火山引擎豆包流式 ASR (bigmodel v1) 客户端
final class VolcASRService {
    struct Result {
        let text: String
        let isFinal: Bool
    }

    var onPartial: ((String) -> Void)?
    var onComplete: ((Result?) -> Void)?

    private let appId: String
    private let accessToken: String
    private let endpoint = URL(string: "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async")!
    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private var nextSeq: Int32 = 2
    private var sendFinished: Bool = false
    private var closed: Bool = false

    init(appId: String, accessToken: String) {
        self.appId = appId
        self.accessToken = accessToken
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: cfg)
    }

    // MARK: - 对外 API

    func start() {
        DiagLog.shared.write("[ASR] start() called")
        nextSeq = 2
        sendFinished = false
        closed = false

        var req = URLRequest(url: endpoint)
        req.setValue(appId, forHTTPHeaderField: "X-Api-App-Key")
        req.setValue(accessToken, forHTTPHeaderField: "X-Api-Access-Key")
        req.setValue("volc.bigasr.sauc.duration", forHTTPHeaderField: "X-Api-Resource-Id")
        req.setValue(UUID().uuidString, forHTTPHeaderField: "X-Api-Connect-Id")

        let ws = session.webSocketTask(with: req)
        task = ws
        ws.resume()
        DiagLog.shared.write("[ASR] WebSocket task resumed")

        let config: [String: Any] = [
            "user": ["uid": "voiceinput-mac"],
            "audio": [
                "format": "pcm",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
                "codec": "raw"
            ],
            "request": [
                "model_name": "bigmodel",
                "enable_itn": true,
                "enable_punc": true,
                "result_type": "full"
            ]
        ]
        sendFrame(messageType: 0b0001, flags: 0, seq: nil, payload: jsonBytes(config))
        DiagLog.shared.write("[ASR] fcr sent, 启动 receive 循环")

        beginReceive()
    }

    func sendAudio(pcm: Data) {
        guard !sendFinished, !closed, task != nil else {
            DiagLog.shared.write("[ASR] sendAudio 跳过 (sendFinished=\(sendFinished) closed=\(closed) task=\(task != nil))")
            return
        }
        let s = nextSeq
        nextSeq += 1
        sendFrame(messageType: 0b0010, flags: 0b0001, seq: s, payload: pcm)
        if s == 2 {
            DiagLog.shared.write("[ASR] 第一个 audio 包 seq=2 发送 (\(pcm.count) bytes)")
        }
    }

    func finish() {
        guard !sendFinished, !closed, task != nil else { return }
        sendFinished = true
        let s = -nextSeq
        DiagLog.shared.write("[ASR] finish() 发最后一包 seq=\(s) (nextSeq=\(nextSeq))")
        sendFrame(messageType: 0b0010, flags: 0b0011, seq: s, payload: Data())
    }

    func cancel() {
        sendFinished = true
        closed = true
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }

    // MARK: - 协议层

    private func sendFrame(messageType: UInt8, flags: UInt8, seq: Int32?, payload: Data) {
        guard let task = task else { return }
        var frame = Data([0x11, (messageType << 4) | flags, 0x10, 0x00])
        if let s = seq {
            var bigS = s.bigEndian
            withUnsafeBytes(of: &bigS) { frame.append(contentsOf: $0) }
        }
        var size = UInt32(payload.count).bigEndian
        withUnsafeBytes(of: &size) { frame.append(contentsOf: $0) }
        frame.append(payload)

        task.send(.data(frame)) { [weak self] error in
            if let error = error {
                DiagLog.shared.write("[ASR] send error: \(error.localizedDescription)")
                self?.fail("send failed: \(error.localizedDescription)")
            }
        }
    }

    private func jsonBytes(_ obj: [String: Any]) -> Data {
        return (try? JSONSerialization.data(withJSONObject: obj)) ?? Data()
    }

    // MARK: - 接收循环

    private func beginReceive() {
        guard let task = task, !closed else {
            DiagLog.shared.write("[ASR] beginReceive: task 是 nil 或已 closed!")
            return
        }
        task.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .failure(let err):
                DiagLog.shared.write("[ASR] receive error: \(err.localizedDescription)")
                self.fail("ws recv: \(err.localizedDescription)")
            case .success(let message):
                let dataSize: Int
                let dataHex: String
                switch message {
                case .data(let d):
                    dataSize = d.count
                    dataHex = d.prefix(20).map { String(format: "%02x", $0) }.joined(separator: " ")
                case .string(let s):
                    dataSize = s.utf8.count
                    dataHex = "(string)"
                    if let d = s.data(using: .utf8) { self.handleFrame(d) }
                    if !self.closed { self.beginReceive() }
                    return
                @unknown default:
                    dataSize = 0
                    dataHex = "?"
                }
                DiagLog.shared.write("[ASR] 收到 \(dataSize) bytes: \(dataHex)")
                switch message {
                case .data(let d):
                    self.handleFrame(d)
                default: break
                }
                if !self.closed {
                    self.beginReceive()
                }
            }
        }
    }

    private func handleFrame(_ data: Data) {
        guard data.count >= 4 else { return }
        let b1 = data[1]
        let msgType = (b1 >> 4) & 0x0F
        let flags = b1 & 0x0F
        let hasSeq = (flags == 0b0001 || flags == 0b0011)
        var offset = 4 + (hasSeq ? 4 : 0)
        guard data.count >= offset + 4 else { return }
        let size = data.withUnsafeBytes { ptr -> UInt32 in
            ptr.load(fromByteOffset: offset, as: UInt32.self).bigEndian
        }
        offset += 4
        guard data.count >= offset + Int(size) else { return }
        let payload = data.subdata(in: offset..<(offset + Int(size)))

        DiagLog.shared.write("[ASR] handleFrame: msgType=\(String(msgType, radix: 2)) size=\(size)")

        if msgType == 0b1111 {
            let msg = String(data: payload, encoding: .utf8) ?? "<binary>"
            DiagLog.shared.write("[ASR] ❌ 错误帧: \(msg)")
            fail("server error: \(msg)")
            return
        }
        if msgType == 0b1001 {
            guard let json = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else {
                DiagLog.shared.write("[ASR] JSON 解析失败: \(payload.prefix(200).map { String(format: "%02x", $0) }.joined())")
                return
            }
            guard let result = json["result"] as? [String: Any] else {
                DiagLog.shared.write("[ASR] 没有 result 字段: \(json)")
                return
            }
            let text = (result["text"] as? String) ?? ""
            let utterances = result["utterances"] as? [[String: Any]] ?? []
            let isDefinite = utterances.contains { ($0["definite"] as? Bool) == true }
            DiagLog.shared.write("[ASR] resp: text=\(text.prefix(80)) definite=\(isDefinite) utter=\(utterances.count)")
            if !text.isEmpty {
                DispatchQueue.main.async { self.onPartial?(text) }
            }
            if isDefinite {
                closed = true
                DispatchQueue.main.async { self.onComplete?(Result(text: text, isFinal: true)) }
                close()
            }
        }
    }

    private func close() {
        closed = true
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
    }

    private func fail(_ message: String) {
        sendFinished = true
        closed = true
        DiagLog.shared.write("[ASR] ❌ fail: \(message)")
        DispatchQueue.main.async {
            self.onComplete?(nil)
        }
        close()
    }
}
