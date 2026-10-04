"""配置、凭据与日志（外围零件）。

规矩 R15：凭据不落明文、不进代码、不进日志。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from .constants import MAX_RECORD_MS, MIN_RECORD_MS

APP_NAME = "voice_input"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
# 两个日志文件分工：app.log 记全流程（INFO+），error.log 只记报错（WARNING+）
LOG_FILE = CONFIG_DIR / "app.log"
ERROR_LOG_FILE = CONFIG_DIR / "error.log"


class Config(BaseModel):
    """非敏感配置，落盘为 json，可手改（因此用 pydantic 校验）。"""

    hotkey: str = Field(default="ctrl+alt+space", description="全局热键组合")
    hotkey_backend: str = Field(default="win32", description="win32 | pynput")
    device: Optional[str] = Field(default=None, description="麦克风设备名，None 表示系统默认")
    asr_provider: str = Field(default="mock", description="mock | tencent | openai")
    asr_endpoint: str = Field(default="", description="云端 ASR 端点（openai 兼容方式用）")
    asr_model: str = Field(default="whisper-1", description="openai 兼容方式的模型名")
    asr_region: str = Field(default="ap-shanghai", description="腾讯云地域")
    asr_engine: str = Field(default="16k_zh", description="腾讯云引擎：16k_zh 中文")
    asr_timeout_s: float = Field(default=8.0, ge=1.0, le=60.0)
    polish_enabled: bool = Field(default=False, description="是否开启文本加工")
    overlay_enabled: bool = Field(default=True)
    overlay_position: str = Field(default="bottom-center", description="bottom-center | caret")
    min_record_ms: int = Field(default=MIN_RECORD_MS, ge=0)
    max_record_ms: int = Field(default=MAX_RECORD_MS, ge=1000)
    fallback_uia: bool = Field(default=False, description="粘贴失败时是否尝试 UIA 兜底")
    log_level: str = Field(default="INFO", description="DEBUG | INFO | WARNING | ERROR")

    @classmethod
    def load(cls) -> "Config":
        """读配置，损坏或缺字段时回退默认值（手改 json 是常态，必须宽容）。"""
        if not CONFIG_FILE.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return cls(**raw)
        except (json.JSONDecodeError, ValidationError, TypeError):
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )


class Credential:
    """敏感凭据：走 Windows 凭据管理器，读不到再回退环境变量。

    规矩 R15：密钥不进代码、不进 json、不进日志，只在系统凭据库里加密存放。
    """

    SERVICE = APP_NAME

    @staticmethod
    def backend_name() -> str:
        """当前 keyring 后端名，用于设置界面告诉用户密钥到底存哪了。"""
        try:
            import keyring

            return type(keyring.get_keyring()).__name__
        except Exception:
            return "不可用"

    @staticmethod
    def get(key: str) -> Optional[str]:
        try:
            import keyring

            val = keyring.get_password(Credential.SERVICE, key)
            if val:
                return val
        except Exception:
            pass
        return os.environ.get(f"{APP_NAME.upper()}_{key.upper()}")

    @staticmethod
    def set(key: str, value: str) -> bool:
        try:
            import keyring

            keyring.set_password(Credential.SERVICE, key, value)
            return True
        except Exception:
            return False

    @staticmethod
    def delete(key: str) -> bool:
        try:
            import keyring

            keyring.delete_password(Credential.SERVICE, key)
            return True
        except Exception:
            return False

    @staticmethod
    def mask(value: Optional[str]) -> str:
        """给界面用的脱敏展示，避免密钥被截图泄出去。"""
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * 6}{value[-4:]}"


class RedactingFilter(logging.Filter):
    """日志脱敏：抹掉疑似密钥与音频数据，满足 R15。"""

    PATTERNS = [
        (re.compile(r"(?i)(secret|key|token|password)[\"'\s:=]+[\w\-]{8,}"), r"\1=***"),
        (re.compile(r"AKID[\w]{8,}"), "AKID***"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            for pat, repl in self.PATTERNS:
                msg = pat.sub(repl, msg)
            record.msg = msg
            record.args = ()
        except Exception:
            pass
        return True


_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """双文件日志：app.log 记全流程，error.log 只记报错，方便只看问题。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(level)
    for h in list(logger.handlers):  # 重复初始化时不留残handler
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    detail = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d)\n  %(message)s"
    )

    # 全流程日志：INFO 及以上，滚动 1MB × 3
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    fh.addFilter(RedactingFilter())
    logger.addHandler(fh)

    # 错误日志：WARNING 及以上，带文件名行号，便于定位
    eh = logging.handlers.RotatingFileHandler(
        ERROR_LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    eh.setLevel(logging.WARNING)
    eh.setFormatter(detail)
    eh.addFilter(RedactingFilter())
    logger.addHandler(eh)

    # 打包成 --noconsole 的 exe 后 sys.stderr 是 None，此时绝不能加控制台句柄，
    # 否则每次写日志都会在 logging 内部抛 AttributeError
    if getattr(sys, "stderr", None) is not None:
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        sh.addFilter(RedactingFilter())
        logger.addHandler(sh)

    return logger


def install_exception_hooks(logger: Optional[logging.Logger] = None) -> None:
    """把未捕获异常也写进日志，否则后台线程/Qt 槽里崩了只会静默消失。"""
    log = logger or logging.getLogger(APP_NAME)

    def _hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical(
            "未捕获异常：%s", exc, exc_info=(exc_type, exc, tb)
        )

    sys.excepthook = _hook

    def _thread_hook(args) -> None:
        log.critical(
            "线程 %s 未捕获异常：%s",
            getattr(args, "thread", None) and args.thread.name,
            args.exc_value,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    try:
        import threading

        threading.excepthook = _thread_hook
    except Exception:
        pass

    # Qt 自身的警告/错误（如 QObject 跨线程调用）也收进来
    try:
        from PySide6.QtCore import qInstallMessageHandler

        def _qt_hook(mode, context, message):
            text = f"Qt[{context.file}:{context.line}] {message}"
            if mode in (1, 2):  # QtWarningMsg / QtCriticalMsg
                log.warning(text)
            elif mode == 3:  # QtFatalMsg
                log.error(text)
            else:
                log.debug(text)

        qInstallMessageHandler(_qt_hook)
    except Exception:
        pass


def clear_logs() -> None:
    """清空两个日志文件（设置界面的"清空日志"用）。"""
    logger = logging.getLogger(APP_NAME)
    for h in list(logger.handlers):
        if not isinstance(h, logging.FileHandler):
            continue
        path = Path(h.baseFilename)
        try:
            h.close()
            path.write_text("", encoding="utf-8")
            h._open()  # noqa: SLF001  重开句柄，避免截断后写入产生空洞
        except Exception:
            pass


def tail_log(path: Optional[Path] = None, lines: int = 300) -> str:
    """读日志尾部，供日志查看器显示。"""
    target = path or LOG_FILE
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return "\n".join(text.splitlines()[-lines:])
