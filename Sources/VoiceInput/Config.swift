import Foundation

/// 配置文件：~/Library/Application Support/VoiceInput/config.json
struct AppConfig {
    var appId: String
    var accessToken: String

    static let defaultAppId = ""
    static let defaultAccessToken = ""

    static func load() -> AppConfig {
        let url = Self.configURL()
        guard let data = try? Data(contentsOf: url),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return AppConfig(appId: "", accessToken: "")
        }
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
