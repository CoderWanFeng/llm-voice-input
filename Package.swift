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
        // 零外部依赖：使用系统自带的 Speech.framework + AVFoundation + AppKit
    ],
    targets: [
        .executableTarget(
            name: "VoiceInput",
            dependencies: [],
            path: "Sources/VoiceInput"
        )
    ]
)
