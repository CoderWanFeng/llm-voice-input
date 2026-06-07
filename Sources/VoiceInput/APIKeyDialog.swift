import AppKit

/// 输入 / 修改 豆包语音 配置的小弹框
/// 字段：APP ID + Access Token
final class APIKeyDialog: NSObject, NSWindowDelegate {
    private let window: NSWindow
    private let appIdField: NSTextField
    private let tokenField: NSTextField
    private(set) var resultAppId: String?
    private(set) var resultAccessToken: String?

    init(currentAppId: String, currentToken: String) {
        let frame = NSRect(x: 0, y: 0, width: 520, height: 200)
        window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        appIdField = NSTextField(string: currentAppId)
        tokenField = NSTextField(string: currentToken)
        super.init()
        window.title = "VoiceInput · 设置豆包语音"
        window.isReleasedWhenClosed = false
        buildUI(frame: frame)
        window.contentView = content
        window.center()
        window.delegate = self
    }

    private var content: NSView!

    private func buildUI(frame: NSRect) {
        let view = NSView(frame: frame)

        let appLabel = NSTextField(labelWithString: "APP ID:")
        appLabel.translatesAutoresizingMaskIntoConstraints = false

        appIdField.translatesAutoresizingMaskIntoConstraints = false
        appIdField.placeholderString = "输入 APP ID"

        let tokenLabel = NSTextField(labelWithString: "Access Token:")
        tokenLabel.translatesAutoresizingMaskIntoConstraints = false

        tokenField.translatesAutoresizingMaskIntoConstraints = false
        tokenField.placeholderString = "输入 Access Token"

        let saveBtn = NSButton(title: "保存", target: nil, action: nil)
        saveBtn.translatesAutoresizingMaskIntoConstraints = false
        saveBtn.bezelStyle = .rounded
        saveBtn.keyEquivalent = "\r"

        let cancelBtn = NSButton(title: "取消", target: nil, action: nil)
        cancelBtn.translatesAutoresizingMaskIntoConstraints = false
        cancelBtn.bezelStyle = .rounded
        cancelBtn.keyEquivalent = "\u{1b}"

        view.addSubview(appLabel)
        view.addSubview(appIdField)
        view.addSubview(tokenLabel)
        view.addSubview(tokenField)
        view.addSubview(saveBtn)
        view.addSubview(cancelBtn)

        NSLayoutConstraint.activate([
            appLabel.topAnchor.constraint(equalTo: view.topAnchor, constant: 20),
            appLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 20),
            appLabel.widthAnchor.constraint(equalToConstant: 110),

            appIdField.centerYAnchor.constraint(equalTo: appLabel.centerYAnchor),
            appIdField.leadingAnchor.constraint(equalTo: appLabel.trailingAnchor, constant: 8),
            appIdField.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20),

            tokenLabel.topAnchor.constraint(equalTo: appIdField.bottomAnchor, constant: 14),
            tokenLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 20),
            tokenLabel.widthAnchor.constraint(equalToConstant: 110),

            tokenField.centerYAnchor.constraint(equalTo: tokenLabel.centerYAnchor),
            tokenField.leadingAnchor.constraint(equalTo: tokenLabel.trailingAnchor, constant: 8),
            tokenField.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20),

            saveBtn.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -16),
            saveBtn.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20),

            cancelBtn.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -16),
            cancelBtn.trailingAnchor.constraint(equalTo: saveBtn.leadingAnchor, constant: -8),
        ])

        saveBtn.target = self
        saveBtn.action = #selector(saveClicked)
        cancelBtn.target = self
        cancelBtn.action = #selector(cancelClicked)

        content = view
    }

    @discardableResult
    func runModal() -> Bool {
        NSApp.runModal(for: window)
        return resultAppId != nil
    }

    @objc private func saveClicked() {
        resultAppId = appIdField.stringValue
        resultAccessToken = tokenField.stringValue
        NSApp.stopModal(withCode: .OK)
        window.close()
    }

    @objc private func cancelClicked() {
        resultAppId = nil
        resultAccessToken = nil
        NSApp.stopModal(withCode: .cancel)
        window.close()
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.stopModal(withCode: .cancel)
    }
}
