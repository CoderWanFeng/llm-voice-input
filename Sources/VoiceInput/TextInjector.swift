import AppKit
import ApplicationServices
import CoreGraphics

/// 文本注入：先把文字写入剪贴板，再模拟 Cmd+V 粘贴到当前焦点
final class TextInjector {
    func inject(_ text: String) {
        guard !text.isEmpty else { return }
        NSLog("[Inject] 准备注入文本: \(text.prefix(50))")
        DiagLog.shared.write("[Inject] 准备注入文本: \(text.prefix(50))")

        let pasteboard = NSPasteboard.general

        // 1. 保存原剪贴板内容，结束后恢复
        let previousContents = pasteboard.string(forType: .string)
        NSLog("[Inject] 原剪贴板内容长度: \(previousContents?.count ?? 0)")

        // 2. 写入新文字
        pasteboard.clearContents()
        let success = pasteboard.setString(text, forType: .string)
        if !success {
            NSLog("[Inject] ❌ 写入剪贴板失败")
            DiagLog.shared.write("[Inject] ❌ 写入剪贴板失败")
            return
        }
        NSLog("[Inject] ✅ 剪贴板已写入")
        DiagLog.shared.write("[Inject] ✅ 剪贴板已写入")

        guard ensureAccessibilityPermission() else {
            DiagLog.shared.write("[Inject] ⚠️ 文本已保留在剪贴板，可手动 Cmd+V")
            return
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in
            self?.simulatePaste()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                if let previous = previousContents {
                    pasteboard.clearContents()
                    pasteboard.setString(previous, forType: .string)
                    DiagLog.shared.write("[Inject] 已恢复原剪贴板")
                }
            }
        }
    }

    private func ensureAccessibilityPermission() -> Bool {
        if AXIsProcessTrusted() {
            return true
        }

        DiagLog.shared.write("[Inject] ❌ 未获得辅助功能权限，无法自动粘贴")
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        AXIsProcessTrustedWithOptions(options)

        let alert = NSAlert()
        alert.messageText = "需要辅助功能权限"
        alert.informativeText = "识别结果已写入剪贴板。若要自动粘贴，请在系统设置 → 隐私与安全性 → 辅助功能 中开启 VoiceInput，然后重启应用。"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "打开系统设置")
        alert.addButton(withTitle: "稍后")
        if alert.runModal() == .alertFirstButtonReturn {
            openAccessibilitySettings()
        }
        return false
    }

    private func openAccessibilitySettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") else { return }
        NSWorkspace.shared.open(url)
    }

    private func simulatePaste() {
        let cmdDown = CGEvent(keyboardEventSource: nil, virtualKey: 0x37, keyDown: true)
        cmdDown?.post(tap: .cghidEventTap)

        let vDown = CGEvent(keyboardEventSource: nil, virtualKey: 0x09, keyDown: true)
        vDown?.post(tap: .cghidEventTap)

        usleep(10_000)

        let vUp = CGEvent(keyboardEventSource: nil, virtualKey: 0x09, keyDown: false)
        vUp?.post(tap: .cghidEventTap)

        let cmdUp = CGEvent(keyboardEventSource: nil, virtualKey: 0x37, keyDown: false)
        cmdUp?.post(tap: .cghidEventTap)

        DiagLog.shared.write("[Inject] ✅ 粘贴事件已发送")
    }
}
