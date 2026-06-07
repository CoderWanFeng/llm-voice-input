import AppKit

// 菜单栏 App 入口
let app = NSApplication.shared
app.setActivationPolicy(.accessory) // 不在 Dock 显示
let delegate = AppDelegate()
app.delegate = delegate
app.run()
