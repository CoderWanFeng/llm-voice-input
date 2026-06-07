import Foundation

/// 诊断日志：直接写文件，不依赖 os_log
/// 写到 /tmp/voiceinput-app.log
final class DiagLog {
    static let shared = DiagLog()

    private let url: URL
    private let handle: FileHandle?

    init() {
        url = URL(fileURLWithPath: "/tmp/voiceinput-app.log")
        // 清空旧文件
        try? FileManager.default.removeItem(at: url)
        FileManager.default.createFile(atPath: url.path, contents: nil)
        handle = try? FileHandle(forWritingTo: url)
        write("=== VoiceInput 启动 \(Date()) ===")
    }

    func write(_ msg: String) {
        let ts = ISO8601DateFormatter().string(from: Date())
        let line = "[\(ts)] \(msg)\n"
        guard let data = line.data(using: .utf8) else { return }
        handle?.write(data)
        // 不需要 synchronizeFile，每次小写入即可
    }
}
