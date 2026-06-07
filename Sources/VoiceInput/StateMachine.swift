import Foundation

/// 状态机：防止录音/识别过程中重复触发
final class StateMachine {
    enum State: Equatable {
        case idle
        case recording
        case transcribing
    }

    var onStateChange: ((State) -> Void)?
    private(set) var state: State = .idle {
        didSet {
            guard oldValue != state else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                self.onStateChange?(self.state)
            }
        }
    }

    /// 全局快捷键按下：按一次进入录音，再按一次进入识别
    func handleHotkeyPressed() {
        switch state {
        case .idle:
            state = .recording
        case .recording:
            state = .transcribing
        case .transcribing:
            break // 识别中忽略
        }
    }

    /// 强制回到 idle（比如出错或识别完成）
    func forceIdle() {
        state = .idle
    }
}
