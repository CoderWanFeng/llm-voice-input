import AppKit
import QuartzCore

/// 屏幕底部居中的悬浮输入面板
/// - 非激活（不抢焦点，不影响当前 app）
/// - 点击穿透（不拦截用户操作）
/// - 显示录音状态 + 实时波纹 + 识别文本
final class FloatingPanelController {
    private let panel: NSPanel
    private let stateLabel = NSTextField(labelWithString: "🔴 录音中...")
    private let transcriptLabel = NSTextField(wrappingLabelWithString: "")
    private let dotView = PulsingDotView()
    private let waveform = WaveformView()
    private var hideTimer: Timer?

    init() {
        let frame = NSRect(x: 0, y: 0, width: 520, height: 180)
        panel = NSPanel(
            contentRect: frame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.titlebarAppearsTransparent = true
        panel.titleVisibility = .hidden

        // 容器：深色圆角背景
        let container = NSView()
        container.wantsLayer = true
        container.layer?.cornerRadius = 16
        container.layer?.masksToBounds = true
        container.layer?.backgroundColor = NSColor(white: 0.08, alpha: 0.92).cgColor
        container.layer?.borderColor = NSColor(white: 1.0, alpha: 0.08).cgColor
        container.layer?.borderWidth = 1
        panel.contentView = container

        // 阴影单独一层
        panel.contentView?.wantsLayer = true
        panel.contentView?.shadow = NSShadow()
        panel.contentView?.layer?.shadowColor = NSColor.black.cgColor
        panel.contentView?.layer?.shadowOpacity = 0.35
        panel.contentView?.layer?.shadowOffset = NSSize(width: 0, height: -4)
        panel.contentView?.layer?.shadowRadius = 20

        buildLayout(in: container)
    }

    private func buildLayout(in container: NSView) {
        // 顶部：状态点 + 状态文字
        let topRow = NSStackView()
        topRow.orientation = .horizontal
        topRow.alignment = .centerY
        topRow.spacing = 8

        dotView.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            dotView.widthAnchor.constraint(equalToConstant: 10),
            dotView.heightAnchor.constraint(equalToConstant: 10)
        ])

        stateLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        stateLabel.textColor = .white

        topRow.addArrangedSubview(dotView)
        topRow.addArrangedSubview(stateLabel)
        let spacer = NSView()
        topRow.addArrangedSubview(spacer)

        // 中部：波纹
        waveform.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            waveform.heightAnchor.constraint(equalToConstant: 56)
        ])

        // 底部：识别文本
        transcriptLabel.font = .systemFont(ofSize: 17)
        transcriptLabel.textColor = NSColor(white: 0.95, alpha: 1.0)
        transcriptLabel.maximumNumberOfLines = 2
        transcriptLabel.cell?.truncatesLastVisibleLine = true
        transcriptLabel.cell?.lineBreakMode = .byTruncatingTail
        transcriptLabel.lineBreakMode = .byTruncatingTail

        // 整体栈
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 20, bottom: 16, right: 20)
        stack.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(topRow)
        stack.addArrangedSubview(waveform)
        stack.addArrangedSubview(transcriptLabel)

        container.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            stack.topAnchor.constraint(equalTo: container.topAnchor),
            stack.bottomAnchor.constraint(equalTo: container.bottomAnchor)
        ])
    }

    /// 屏幕底部居中
    private func positionAtBottomCenter() {
        guard let screen = NSScreen.main else { return }
        let screenFrame = screen.visibleFrame
        let panelFrame = panel.frame
        let x = screenFrame.midX - panelFrame.width / 2
        let y = screenFrame.minY + 120
        panel.setFrameOrigin(NSPoint(x: x, y: y))
    }

    // MARK: - 公开 API

    func showPartial(_ text: String) {
        transcriptLabel.stringValue = text
    }

    func showRecording() {
        hideTimer?.invalidate()
        positionAtBottomCenter()
        dotView.startPulsing(color: .systemRed)
        stateLabel.stringValue = "🔴 录音中..."
        transcriptLabel.stringValue = ""
        waveform.resume()

        if panel.isVisible {
            panel.alphaValue = 1
        } else {
            panel.alphaValue = 0
            panel.orderFrontRegardless()
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.18
                panel.animator().alphaValue = 1
            }
        }
    }

    func showTranscribing() {
        dotView.stopPulsing()
        dotView.setStaticColor(.systemYellow)
        stateLabel.stringValue = "⏳ 识别中..."
        waveform.pause()
    }

    func showResult(_ text: String) {
        dotView.stopPulsing()
        dotView.setStaticColor(.systemGreen)
        stateLabel.stringValue = "✅ 已识别"
        transcriptLabel.stringValue = text
        waveform.pause()
        hideTimer?.invalidate()
        hideTimer = Timer.scheduledTimer(withTimeInterval: 2.5, repeats: false) { [weak self] _ in
            self?.hide()
        }
    }

    func pushAmplitude(_ rms: Float) {
        waveform.push(rms)
    }

    func showError(_ message: String) {
        dotView.stopPulsing()
        dotView.setStaticColor(.systemOrange)
        stateLabel.stringValue = "⚠️ 错误"
        transcriptLabel.stringValue = message
        waveform.pause()
        hideTimer?.invalidate()
        hideTimer = Timer.scheduledTimer(withTimeInterval: 3.5, repeats: false) { [weak self] _ in
            self?.hide()
        }
    }

    func hide() {
        hideTimer?.invalidate()
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.25
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            self?.panel.orderOut(nil)
        })
    }
}

// MARK: - 波纹视图

private final class WaveformView: NSView {
    private var amplitudes: [Float] = Array(repeating: 0, count: 48)
    private let barCount = 48
    private let barWidth: CGFloat = 4
    private let barSpacing: CGFloat = 3
    private var paused = false

    func push(_ rms: Float) {
        if paused { return }
        // 麦克风 RMS 通常很小 (0.001-0.1)，放大 10 倍后归一化
        let normalized = min(rms * 10.0, 1.0)
        amplitudes.removeFirst()
        amplitudes.append(normalized)
        needsDisplay = true
    }

    func pause() {
        paused = true
    }

    func resume() {
        paused = false
        // 清零历史
        amplitudes = Array(repeating: 0, count: barCount)
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let totalWidth = CGFloat(barCount) * (barWidth + barSpacing) - barSpacing
        let startX = (bounds.width - totalWidth) / 2
        let centerY = bounds.midY
        let maxHeight = bounds.height * 0.9

        for (i, amp) in amplitudes.enumerated() {
            let h = max(CGFloat(amp) * maxHeight, 2)
            let x = startX + CGFloat(i) * (barWidth + barSpacing)
            let rect = NSRect(x: x, y: centerY - h / 2, width: barWidth, height: h)
            let path = NSBezierPath(roundedRect: rect, xRadius: barWidth / 2, yRadius: barWidth / 2)
            colorFor(amp: amp).setFill()
            path.fill()
        }
    }

    private func colorFor(amp: Float) -> NSColor {
        if amp < 0.35 {
            return NSColor(red: 0.40, green: 0.85, blue: 0.55, alpha: 1.0)   // 绿
        } else if amp < 0.7 {
            return NSColor(red: 0.98, green: 0.80, blue: 0.30, alpha: 1.0)   // 黄
        } else {
            return NSColor(red: 0.98, green: 0.45, blue: 0.40, alpha: 1.0)   // 红
        }
    }
}

// MARK: - 脉动小圆点

private final class PulsingDotView: NSView {
    private var color: NSColor = .systemRed
    private var timer: Timer?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.cornerRadius = 5
        layer?.backgroundColor = color.cgColor
    }

    required init?(coder: NSCoder) { fatalError() }

    func startPulsing(color: NSColor) {
        self.color = color
        layer?.backgroundColor = color.cgColor
        timer?.invalidate()
        var phase: CGFloat = 0
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            phase += 0.18
            let scale = 1.0 + 0.35 * sin(phase)
            self.layer?.transform = CATransform3DMakeScale(scale, scale, 1)
        }
    }

    func stopPulsing() {
        timer?.invalidate()
        timer = nil
        layer?.transform = CATransform3DIdentity
    }

    func setStaticColor(_ color: NSColor) {
        self.color = color
        layer?.backgroundColor = color.cgColor
    }
}
