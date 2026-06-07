import AppKit
import AVFoundation

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let stateMachine = StateMachine()
    private let config = AppConfig.load()
    private lazy var audioRecorder = AudioRecorder()
    private lazy var hotkeyManager = HotkeyManager()
    private lazy var textInjector = TextInjector()
    private lazy var statusBar = StatusBarController()
    private lazy var floatingPanel = FloatingPanelController()

    private var asr: VolcASRService?
    private var feedTask: Task<Void, Never>?
    private var lastPartial: String = ""

    func applicationDidFinishLaunching(_ notification: Notification) {
        stateMachine.onStateChange = { [weak self] state in
            self?.handleStateChange(state)
        }

        audioRecorder.onAmplitude = { [weak self] rms in
            self?.floatingPanel.pushAmplitude(rms)
        }

        statusBar.start(
            onQuit: { NSApp.terminate(nil) },
            onToggleHotkey: { [weak self] in
                self?.hotkeyManager.enabled.toggle()
                self?.statusBar.updateHotkeyState(self?.hotkeyManager.enabled ?? false)
            },
            onSetASR: { [weak self] in
                self?.openASRConfigDialog()
            },
            onTestHotkey: { [weak self] in
                DiagLog.shared.write("[AppDelegate] 菜单点测试")
                self?.hotkeyManager.triggerManually()
            },
            onTestAuto: { [weak self] in
                DiagLog.shared.write("[AppDelegate] 菜单点 5 秒自动测试")
                self?.hotkeyManager.triggerAutoCycle(duration: 5.0)
            }
        )

        hotkeyManager.onKeyDown = { [weak self] in
            self?.stateMachine.handleHotkeyPressed()
        }
        hotkeyManager.onPermissionRequired = { [weak self] in
            self?.showInputMonitoringHelp()
        }
        hotkeyManager.start()

        if config.isConfigured {
            statusBar.updateModelState(.ready)
        } else {
            statusBar.updateModelState(.failed("未配置豆包语音"))
        }

        requestMicrophonePermission()
    }

    private func showInputMonitoringHelp() {
        let alert = NSAlert()
        alert.messageText = "快捷键需要输入监控权限"
        alert.informativeText = "请在系统设置 → 隐私与安全性 → 输入监控 中开启 VoiceInput，然后重启应用。也可以先用菜单里的测试快捷键验证录音流程。"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "打开系统设置")
        alert.addButton(withTitle: "稍后")
        if alert.runModal() == .alertFirstButtonReturn {
            guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent") else { return }
            NSWorkspace.shared.open(url)
        }
    }

    private func openASRConfigDialog() {
        let current = AppConfig.load()
        let dialog = APIKeyDialog(currentAppId: current.appId, currentToken: current.accessToken)
        if dialog.runModal() {
            let new = AppConfig(
                appId: dialog.resultAppId ?? "",
                accessToken: dialog.resultAccessToken ?? ""
            )
            do {
                try new.save()
                statusBar.updateModelState(new.isConfigured ? .ready : .failed("未配置豆包语音"))
                statusBar.showInfo(new.isConfigured ? "✅ 已保存" : "⚠️ 未配置完整")
            } catch {
                statusBar.showError("保存失败: \(error.localizedDescription)")
            }
        }
    }

    private func handleStateChange(_ state: StateMachine.State) {
        NSLog("[AppDelegate] state -> \(state)")
        DiagLog.shared.write("[AppDelegate] state -> \(state)")

        switch state {
        case .idle:
            NSLog("[AppDelegate] handleStateChange: idle")
            break

        case .recording:
            NSLog("[AppDelegate] handleStateChange: recording -> calling startRecording()")
            startRecording()

        case .transcribing:
            NSLog("[AppDelegate] handleStateChange: transcribing -> calling stopAndRecognize()")
            stopAndRecognize()
        }
    }

    private func startRecording() {
        NSLog("[AppDelegate] startRecording called")
        DiagLog.shared.write("[AppDelegate] startRecording")
        let cfg = AppConfig.load()
        NSLog("[AppDelegate] 配置: appId=\(cfg.appId.prefix(10))..., accessToken=\(cfg.accessToken.prefix(10))..., isConfigured=\(cfg.isConfigured)")
        guard cfg.isConfigured else {
            NSLog("[AppDelegate] 配置未完成，返回")
            statusBar.showError("未配置豆包语音 APP ID / Access Token\n请菜单栏 → 设置豆包语音")
            stateMachine.forceIdle()
            return
        }
        NSLog("[AppDelegate] 配置检查通过")

        floatingPanel.showRecording()
        lastPartial = ""

        // 关键：先建 ASR 连接（不等录音设备启动）
        let svc = VolcASRService(appId: cfg.appId, accessToken: cfg.accessToken)
        svc.onPartial = { [weak self] text in
            guard let self = self else { return }
            DiagLog.shared.write("[ASR] partial: \(text.prefix(50))")
            self.lastPartial = text
            self.floatingPanel.showPartial(text)
        }
        svc.onComplete = { [weak self] result in
            guard let self = self else { return }
            DiagLog.shared.write("[ASR] complete: \(result?.text ?? "<nil>")")
            self.feedTask?.cancel()
            self.feedTask = nil
            if let r = result {
                let text = r.text
                self.textInjector.inject(text)
                self.statusBar.showLastTranscript(text)
                self.floatingPanel.showResult(text)
            } else {
                self.floatingPanel.showError("识别失败")
            }
            self.stateMachine.forceIdle()
        }
        asr = svc
        DiagLog.shared.write("[AppDelegate] ASR start 准备发 fcr")
        svc.start()
        DiagLog.shared.write("[AppDelegate] ASR fcr 已发")

        // 再启动录音（首次可能阻塞 2 秒）
        do {
            DiagLog.shared.write("[AppDelegate] audioRecorder.start() 启动...")
            try audioRecorder.start()
            DiagLog.shared.write("[AppDelegate] audioRecorder.start() 返回")
        } catch {
            DiagLog.shared.write("[AppDelegate] audioRecorder.start() 抛异常: \(error.localizedDescription)")
            floatingPanel.showError("录音启动失败: \(error.localizedDescription)")
            asr?.cancel()
            asr = nil
            stateMachine.forceIdle()
            return
        }

        // 启动喂包任务：每 200ms 拉一包发送
        feedTask = Task { [weak self] in
            var iter: Int = 0
            while !Task.isCancelled {
                iter += 1
                try? await Task.sleep(nanoseconds: 200_000_000)
                guard let self = self else { return }
                let newFloats = self.audioRecorder.pullSinceLast()
                if iter <= 3 || iter % 5 == 0 {
                    DiagLog.shared.write("[Feed] iter=\(iter) 拉取 \(newFloats.count) samples")
                }
                if newFloats.isEmpty { continue }
                let pcm = floatSamplesToInt16LE(newFloats)
                if pcm.isEmpty { continue }
                self.asr?.sendAudio(pcm: pcm)
            }
            DiagLog.shared.write("[Feed] Task 结束 (iter=\(iter))")
        }
    }

    private func stopAndRecognize() {
        DiagLog.shared.write("[AppDelegate] stopAndRecognize")
        _ = audioRecorder.stop()
        asr?.finish()
        floatingPanel.showTranscribing()
    }

    private func requestMicrophonePermission() {
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            DispatchQueue.main.async {
                if !granted {
                    self.statusBar.showError("未获得麦克风权限，请到系统设置中开启")
                }
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        hotkeyManager.stop()
        _ = audioRecorder.stop()
        feedTask?.cancel()
        asr?.cancel()
    }
}
