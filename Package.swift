// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "VoiceInput",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "VoiceInput", targets: ["VoiceInput"])
    ],
    dependencies: [
        // 零三方 Swift 依赖：使用系统框架 + 火山引擎豆包 ASR WebSocket API
    ],
    targets: [
        .executableTarget(
            name: "VoiceInput",
            dependencies: [],
            path: "Sources/VoiceInput"
        )
    ]
)
