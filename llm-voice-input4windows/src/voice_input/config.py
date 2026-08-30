"""配置文件读写模块。

支持多 ASR 提供商配置，保持向后兼容：
- 旧配置格式（仅有 app_id + access_token）自动迁移为 volc 提供商
- 新配置格式支持 provider 字段选择不同 ASR 服务提供商

支持的提供商：volc（火山引擎豆包）、xfly（科大讯飞）、tencent（腾讯云）、aliyun（阿里云）

字段说明：
- provider: 当前选中的提供商
- providers: 各提供商的凭证字典
  - 火山引擎豆包: app_id + access_token（可选）
  - 科大讯飞: app_id + app_key
  - 腾讯云: app_id + secret_id + secret_key（AppID 用于 URL 路径，SecretID/SecretKey 用于签名）
  - 阿里云: app_key + access_key_id + access_key_secret（AppKey 用于 StartTranscription 指令，AccessKey 对用于换取 Token）

优先级：环境变量 > 配置文件

打包模式：检测到 sys.frozen（PyInstaller）时，配置文件存放在 %APPDATA%\\VoiceInput\\config.json，
而非项目目录下的 config-template.json，方便用户在打包后的 exe 版本中修改凭证。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .diag_log import DiagLog


# 环境变量键名（兼容旧版 + 新版）
_VOLC_APP_ID_KEY = "VOICEINPUT_APP_ID"
_VOLC_ACCESS_TOKEN_KEY = "VOICEINPUT_ACCESS_TOKEN"
_PROVIDER_KEY = "VOICEINPUT_PROVIDER"

# 提供商ID到中文名的映射
PROVIDER_NAMES: Dict[str, str] = {
    "volc": "火山引擎豆包",
    "xfly": "科大讯飞",
    "tencent": "腾讯云",
    "aliyun": "阿里云",
}

# 提供商ID列表（用于下拉选择）
PROVIDER_LIST = list(PROVIDER_NAMES.keys())


@dataclass
class AppConfig:
    """应用配置：支持多提供商切换。

    Attributes:
        provider: 当前选中的提供商ID
        providers: 各提供商的凭证配置字典，key 为 provider id
        is_from_env: 配置是否来自环境变量（而非用户主动在对话框中配置）
    """

    provider: str = "volc"
    providers: Dict[str, Dict[str, str]] = field(default_factory=dict)
    is_from_env: bool = False

    @property
    def is_configured(self) -> bool:
        """判断当前选中的提供商是否已配置完成。"""
        creds = self.providers.get(self.provider, {})
        # 至少需要一个 app_id / api_key 类型字段
        for key in ("app_id", "api_key", "app_key", "secret_id", "access_key_id"):
            if creds.get(key):
                return True
        return False

    def get_current_credentials(self) -> Dict[str, str]:
        """获取当前提供商的凭证字典。"""
        return self.providers.get(self.provider, {})

    def get_provider_display_name(self) -> str:
        """获取当前提供商的中文名。"""
        return PROVIDER_NAMES.get(self.provider, self.provider)


def _is_frozen() -> bool:
    """检测是否运行在 PyInstaller 打包模式下。

    Returns:
        True 表示打包后的 exe 模式，False 表示开发模式
    """
    return bool(getattr(sys, "frozen", False))


def config_path() -> str:
    """返回配置文件绝对路径。

    - 打包模式（sys.frozen=True）: %APPDATA%\\VoiceInput\\config.json
      与 diag_log.py 中的日志路径保持一致，方便用户查找。
    - 开发模式: 项目内 resources/config-template.json
      通过 __file__ 定位项目根，方便开发调试时直接编辑。
    """
    if _is_frozen():
        # 打包模式：使用用户数据目录
        app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
        config_dir = os.path.join(app_data, "VoiceInput")
        return os.path.join(config_dir, "config.json")
    else:
        # 开发模式：项目内 resources/
        here = os.path.dirname(os.path.abspath(__file__))  # src/voice_input/
        project_root = os.path.dirname(os.path.dirname(here))  # 项目根
        return os.path.join(project_root, "resources", "config-template.json")


def _ensure_default_config(path: str) -> None:
    """确保配置文件存在，不存在则创建默认模板。

    Args:
        path: 配置文件路径
    """
    if not os.path.exists(path):
        default = {
            "provider": "volc",
            "providers": {
                "volc": {"app_id": "", "access_token": ""},
                "xfly": {"app_id": "", "app_key": ""},
                "tencent": {"app_id": "", "secret_id": "", "secret_key": ""},
                "aliyun": {"app_key": "", "access_key_id": "", "access_key_secret": ""},
            },
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        DiagLog.shared().write(f"[Config] 已创建默认配置文件: {path}")


def _migrate_legacy_config(data: Dict[str, Any]) -> AppConfig:
    """将旧格式配置（app_id + access_token）迁移为新格式。

    旧格式示例: {"app_id": "xxx", "access_token": "yyy"}
    新格式示例: {"provider": "volc", "providers": {"volc": {"app_id": "xxx", "access_token": "yyy"}}}
    """
    app_id = str(data.get("app_id", ""))
    access_token = str(data.get("access_token", ""))
    cfg = AppConfig(
        provider="volc",
        providers={
            "volc": {
                "app_id": app_id,
                "access_token": access_token,
            }
        },
    )
    DiagLog.shared().write("[Config] 检测到旧格式配置，已自动迁移为新格式")
    return cfg


def load() -> AppConfig:
    """加载配置：配置文件优先，其次环境变量，最后返回空配置。

    优先级说明：
    - 用户在对话框中保存的配置文件优先级最高（避免环境变量覆盖用户选择）
    - 若配置文件不存在或所选提供商未配置凭证，则回退到环境变量
    - 打包模式下若配置文件不存在，会自动创建默认模板

    环境变量分支：
    - VOICEINPUT_PROVIDER: 指定提供商（可选，默认 volc）
    - VOICEINPUT_APP_ID / VOICEINPUT_ACCESS_TOKEN: 火山引擎豆包凭证
    """
    # 1. 先尝试从配置文件加载（用户保存的配置优先）
    path = config_path()
    # 打包模式下自动创建默认配置
    if _is_frozen():
        _ensure_default_config(path)

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            DiagLog.shared().write(f"[Config] 配置文件读取失败: {e}")
            data = {}
        else:
            # 判断是否为旧格式（包含 app_id 但不含 providers 字段）
            if "providers" not in data and "app_id" in data:
                cfg = _migrate_legacy_config(data)
                DiagLog.shared().write(f"[Config] 从配置文件加载（提供商: {cfg.provider}）")
                return cfg

            # 新格式：检查用户选择的提供商是否已配置凭证
            provider = str(data.get("provider", "volc"))
            providers = data.get("providers", {})
            if not isinstance(providers, dict):
                providers = {}

            # 如果配置文件中已有用户选择的提供商且凭证非空，则使用配置文件
            creds = providers.get(provider, {})
            has_creds = any(
                creds.get(k)
                for k in ("app_id", "api_key", "app_key", "secret_id", "access_key_id", "access_token")
            )
            if has_creds:
                DiagLog.shared().write(f"[Config] 从配置文件加载（提供商: {provider}）")
                return AppConfig(provider=provider, providers=providers)

    # 2. 配置文件无有效凭证，回退到环境变量
    env_provider = os.environ.get(_PROVIDER_KEY, "").strip()
    env_app_id = os.environ.get(_VOLC_APP_ID_KEY, "").strip()
    env_token = os.environ.get(_VOLC_ACCESS_TOKEN_KEY, "").strip()

    if env_app_id:
        provider = env_provider or "volc"
        cfg = AppConfig(
            provider=provider,
            providers={
                provider: {
                    "app_id": env_app_id,
                    "access_token": env_token,
                }
            },
            is_from_env=True,  # 标记为环境变量来源
        )
        DiagLog.shared().write(f"[Config] 从环境变量加载（提供商: {provider}）")
        return cfg

    # 3. 都没有，返回空配置
    return AppConfig()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        DiagLog.shared().write(f"[Config] 配置文件读取失败: {e}")
        return AppConfig()

    # 3. 判断是否为旧格式（包含 app_id 但不含 providers 字段）
    if "providers" not in data and "app_id" in data:
        cfg = _migrate_legacy_config(data)
        return cfg

    # 4. 新格式解析
    provider = str(data.get("provider", "volc"))
    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        providers = {}

    DiagLog.shared().write(f"[Config] 从配置文件加载（提供商: {provider}）")
    return AppConfig(provider=provider, providers=providers)


def save(config: AppConfig) -> None:
    """保存配置到配置文件，自动创建父目录。"""
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "provider": config.provider,
        "providers": config.providers,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        DiagLog.shared().write(f"[Config] 配置已保存到 {path}")
    except OSError as e:
        DiagLog.shared().write(f"[Config] 配置保存失败: {e}")
        raise


def update_provider_credentials(provider: str, credentials: Dict[str, str]) -> AppConfig:
    """更新指定提供商的凭证并保存。

    Args:
        provider: 提供商ID
        credentials: 凭证字典
    """
    cfg = load()
    cfg.provider = provider
    cfg.providers[provider] = credentials
    save(cfg)
    return cfg


def switch_provider(provider: str) -> AppConfig:
    """切换当前使用的提供商并保存。"""
    cfg = load()
    cfg.provider = provider
    save(cfg)
    return cfg
