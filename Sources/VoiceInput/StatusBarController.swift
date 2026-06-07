import AppKit

/// 菜单栏 UI
final class StatusBarController: NSObject {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private let stateItem = NSMenuItem(title: "状态：待机", action: nil, keyEquivalent: "")
    private let modelItem = NSMenuItem(title: "模型：未加载", action: nil, keyEquivalent: "")
    private let hotkeyItem = NSMenuItem(title: "启用快捷键", action: nil, keyEquivalent: "")
    private var lastTranscriptItem: NSMenuItem?

    private var onQuit: (() -> Void)?
    private var onToggleHotkey: (() -> Void)?
    private var onSetASR: (() -> Void)?
    private var onTestHotkey: (() -> Void)?
    private var onTestAuto: (() -> Void)?

    func start(
        onQuit: @escaping () -> Void,
        onToggleHotkey: @escaping () -> Void,
        onSetASR: @escaping () -> Void,
        onTestHotkey: @escaping () -> Void,
        onTestAuto: @escaping () -> Void
    ) {
        self.onQuit = onQuit
        self.onToggleHotkey = onToggleHotkey
        self.onSetASR = onSetASR
        self.onTestHotkey = onTestHotkey
        self.onTestAuto = onTestAuto

        if let button = statusItem.button {
            button.title = "🎙️"
            button.toolTip = "VoiceInput - 按 Option+K 开始/结束录音"
        }

        menu.addItem(stateItem)
        menu.addItem(modelItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(hotkeyItem)

        let setKeyItem = NSMenuItem(title: "设置豆包语音…", action: #selector(setASR), keyEquivalent: "")
        setKeyItem.target = self
        menu.addItem(setKeyItem)

        let testItem = NSMenuItem(title: "🔧 测试快捷键（绕过 CGEventTap）", action: #selector(testHotkey), keyEquivalent: "")
        testItem.target = self
        menu.addItem(testItem)

        let autoItem = NSMenuItem(title: "🎙️ 5秒自动录音测试（一次点击完整流程）", action: #selector(testAuto), keyEquivalent: "")
        autoItem.target = self
        menu.addItem(autoItem)

        menu.addItem(NSMenuItem.separator())
        let quitItem = NSMenuItem(title: "退出", action: #selector(quitApp), keyEquivalent: "q")
        menu.addItem(quitItem)

        hotkeyItem.target = self
        hotkeyItem.action = #selector(toggleHotkey)
        hotkeyItem.state = .on
        quitItem.target = self

        statusItem.menu = menu
    }

    func updateState(_ state: StateMachine.State) {
        switch state {
        case .idle:
            stateItem.title = "状态：待机"
        case .recording:
            stateItem.title = "状态：🔴 录音中..."
        case .transcribing:
            stateItem.title = "状态：⏳ 识别中..."
        }
    }

    enum ModelState {
        case notLoaded
        case ready
        case failed(String)
    }

    func updateModelState(_ state: ModelState) {
        switch state {
        case .notLoaded:
            modelItem.title = "模型：未加载"
        case .ready:
            modelItem.title = "模型：✅ 豆包 ASR 就绪"
        case .failed(let msg):
            modelItem.title = "模型：❌ \(msg)"
        }
    }

    func updateHotkeyState(_ enabled: Bool) {
        hotkeyItem.state = enabled ? .on : .off
    }

    func showLastTranscript(_ text: String) {
        let preview = text.count > 60 ? String(text.prefix(60)) + "..." : text
        if lastTranscriptItem == nil {
            let item = NSMenuItem(title: "上次：\(preview)", action: nil, keyEquivalent: "")
            menu.insertItem(item, at: 2)
            lastTranscriptItem = item
        } else {
            lastTranscriptItem?.title = "上次：\(preview)"
        }
    }

    func showError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "VoiceInput"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "好")
        alert.runModal()
    }

    func showInfo(_ message: String) {
        statusItem.button?.title = message
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            self?.statusItem.button?.title = "🎙️"
        }
    }

    @objc private func toggleHotkey() {
        onToggleHotkey?()
    }

    @objc private func setASR() {
        onSetASR?()
    }

    @objc private func testHotkey() {
        onTestHotkey?()
    }

    @objc private func testAuto() {
        onTestAuto?()
    }

    @objc private func quitApp() {
        onQuit?()
    }
}
