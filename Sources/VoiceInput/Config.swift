import Foundation

/// 配置文件：~/Library/Application Support/VoiceInput/config.json
/// 配置优先级：环境变量 > 配置文件
struct AppConfig {
    var appId: String
    var accessToken: String

    static let defaultAppId = ""
    static let defaultAccessToken = ""

    /// 环境变量键名
    private static let envAppIdKey = "VOICEINPUT_APP_ID"
    private static let envAccessTokenKey = "VOICEINPUT_ACCESS_TOKEN"

    static func load() -> AppConfig {
        // 优先从环境变量读取
        if let envAppId = ProcessInfo.processInfo.environment[envAppIdKey], !envAppId.isEmpty,
           let envToken = ProcessInfo.processInfo.environment[envAccessTokenKey], !envToken.isEmpty {
            DiagLog.shared.write("[Config] 从环境变量加载配置")
            return AppConfig(appId: envAppId, accessToken: envToken)
        }

        // 从配置文件读取
        let url = Self.configURL()
        guard let data = try? Data(contentsOf: url),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return AppConfig(appId: "", accessToken: "")
        }
        DiagLog.shared.write("[Config] 从配置文件加载")
        return AppConfig(
            appId: (dict["app_id"] as? String) ?? "",
            accessToken: (dict["access_token"] as? String) ?? ""
        )
    }

    func save() throws {
        let url = Self.configURL()
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let dict: [String: Any] = [
            "app_id": appId,
            "access_token": accessToken
        ]
        let data = try JSONSerialization.data(withJSONObject: dict, options: [.prettyPrinted])
        try data.write(to: url, options: [.atomic, .completeFileProtection])
    }

    static func configURL() -> URL {
        let appSupport = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first!
        return appSupport
            .appendingPathComponent("VoiceInput")
            .appendingPathComponent("config.json")
    }

    var isConfigured: Bool { !appId.isEmpty && !accessToken.isEmpty }
}
