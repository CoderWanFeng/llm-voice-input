import AppKit
import CoreGraphics

/// 全局快捷键监听：基于 CGEventTap
/// 默认快捷键 Option + K
final class HotkeyManager {
    var onKeyDown: (() -> Void)?
    var onPermissionRequired: (() -> Void)?
    var enabled: Bool = true

    private var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    private var consumesEvents = true
    private var lastTriggerAt: TimeInterval = 0
    private let debounceInterval: TimeInterval = 0.3

    // 快捷键配置：Option + K
    private let requiredFlags: CGEventFlags = [.maskAlternate]
    private let keyCode: CGKeyCode = 40 // K

    func start() {
        stop()
        let eventMask = (1 << CGEventType.keyDown.rawValue)
        let modes: [(CGEventTapOptions, Bool, String)] = [
            (.defaultTap, true, "拦截模式"),
            (.listenOnly, false, "只监听模式")
        ]

        for mode in modes {
            if let tap = CGEvent.tapCreate(
                tap: .cgSessionEventTap,
                place: .headInsertEventTap,
                options: mode.0,
                eventsOfInterest: CGEventMask(eventMask),
                callback: HotkeyManager.callback,
                userInfo: Unmanaged.passUnretained(self).toOpaque()
            ) {
                consumesEvents = mode.1
                eventTap = tap
                let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
                runLoopSource = source
                CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
                CGEvent.tapEnable(tap: tap, enable: true)
                NSLog("[Hotkey] ✅ CGEventTap 已创建，监听 Option+K (\(mode.2))")
                DiagLog.shared.write("[Hotkey] ✅ CGEventTap 已创建，监听 Option+K (\(mode.2))")
                return
            }
        }

        NSLog("[Hotkey] ❌ CGEventTap 创建失败 → 未授权 Input Monitoring？")
        DiagLog.shared.write("[Hotkey] ❌ CGEventTap 创建失败 → 未授权输入监控")
        DispatchQueue.main.async { [weak self] in
            self?.onPermissionRequired?()
        }
    }

    func stop() {
        if let tap = eventTap {
            CGEvent.tapEnable(tap: tap, enable: false)
        }
        if let source = runLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
        }
        eventTap = nil
        runLoopSource = nil
    }

    /// 手动触发一次（菜单"测试快捷键"按钮用）
    func triggerManually() {
        DispatchQueue.main.async { [weak self] in
            DiagLog.shared.write("[Hotkey] 🔘 手动触发 onKeyDown")
            self?.onKeyDown?()
        }
    }

    /// 5 秒自动测试：触发一次，5 秒后再触发一次（用于一键验证完整链路）
    func triggerAutoCycle(duration: TimeInterval = 5.0) {
        DiagLog.shared.write("[Hotkey] ⏱️ 5秒自动测试开始")
        triggerManually()
        DispatchQueue.main.asyncAfter(deadline: .now() + duration) { [weak self] in
            DiagLog.shared.write("[Hotkey] ⏱️ 5秒到，自动结束")
            self?.onKeyDown?()
        }
    }

    fileprivate func handleEvent(proxy: CGEventTapProxy, type: CGEventType, event: CGEvent) -> Unmanaged<CGEvent>? {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let tap = eventTap {
                CGEvent.tapEnable(tap: tap, enable: true)
                DiagLog.shared.write("[Hotkey] ⚠️ CGEventTap 被系统禁用，已尝试重新启用")
            }
            return Unmanaged.passUnretained(event)
        }

        guard type == .keyDown, enabled else { return Unmanaged.passUnretained(event) }

        let flags = event.flags
        let code = event.getIntegerValueField(.keyboardEventKeycode)
        // 每次 keydown 都打日志，帮助排查
        NSLog("[Hotkey] keydown: keycode=%d flags=0x%x", code, flags.rawValue)

        let cleanedFlags = flags.intersection([.maskControl, .maskAlternate, .maskShift, .maskCommand])
        NSLog("[Hotkey]   cleanedFlags=0x%x required=0x%x", cleanedFlags.rawValue, requiredFlags.rawValue)

        guard cleanedFlags == requiredFlags else { return Unmanaged.passUnretained(event) }
        guard code == Int64(keyCode) else { return Unmanaged.passUnretained(event) }

        let now = ProcessInfo.processInfo.systemUptime
        if now - lastTriggerAt < debounceInterval {
            return Unmanaged.passUnretained(event)
        }
        lastTriggerAt = now

        NSLog("[Hotkey] ✅ 匹配成功，触发 onKeyDown")
        DispatchQueue.main.async { [weak self] in
            self?.onKeyDown?()
        }

        if consumesEvents {
            return nil
        }
        return Unmanaged.passUnretained(event)
    }

    private static let callback: CGEventTapCallBack = { proxy, type, event, userInfo in
        guard let userInfo = userInfo else { return Unmanaged.passUnretained(event) }
        let manager = Unmanaged<HotkeyManager>.fromOpaque(userInfo).takeUnretainedValue()
        return manager.handleEvent(proxy: proxy, type: type, event: event)
    }
}
