"""全局配置管理。

配置优先级:
1. 环境变量 (最高)
2. 用户配置文件 ~/.agent/config.json
3. 项目默认配置 (内置)

API Key 优先通过环境变量或 Web UI 设置面板写入用户配置文件。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# === 内置默认值 ===

# 项目根目录 (Agent 包所在目录)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Agent 内部目录
AGENT_ROOT = Path(__file__).resolve().parent
DEVICES_DIR = AGENT_ROOT / "devices"
TEMPLATES_DIR = AGENT_ROOT / "rules" / "_template"
STATIC_DIR = AGENT_ROOT / "static"

# 用户级配置目录
USER_CONFIG_DIR = Path.home() / ".agent"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.json"

# LLM 默认配置
DEFAULT_LLM_PROVIDER = "anthropic"  # or "openai"
DEFAULT_LLM_MODEL = "claude-3-5-sonnet-20241022"
DEFAULT_LLM_MAX_TOKENS = 4096
DEFAULT_LLM_TEMPERATURE = 0.0  # 核验场景需要确定性

# HTTP 服务
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050

# 输出目录
DEFAULT_OUTPUT_DIR = Path("./output")

# 日志
LOG_LEVEL = os.environ.get("AGENT_LOG_LEVEL", "INFO")


def get_app_base_dir() -> Path:
    """应用基目录。

    - PyInstaller 打包成 EXE 时: 取 EXE 所在目录(单文件解压到临时目录,
      不能用 sys.executable 的临时路径作为数据落点)
    - 源码运行时: 取项目根目录

    这样默认的 uploads/outputs 就落在"工程内",而非 C 盘用户主目录。
    """
    if getattr(sys, "frozen", False):
        # PyInstaller: sys.executable 是 EXE 路径
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def get_storage_dirs() -> Tuple[Path, Path]:
    """返回 (upload_dir, output_dir)。

    优先级: 用户配置(~/.agent/config.json 的 storage 段) > 工程内默认目录。
    """
    data = load_user_config().get("storage") or {}
    base = get_app_base_dir()
    upload_dir = (
        Path(data["upload_dir"]).expanduser()
        if data.get("upload_dir")
        else base / "uploads"
    )
    output_dir = (
        Path(data["output_dir"]).expanduser()
        if data.get("output_dir")
        else base / "outputs"
    )
    return upload_dir, output_dir


def save_storage_dirs(
    upload_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Tuple[Path, Path]:
    """更新存储目录配置并持久化到用户配置文件。返回更新后的 (upload, output)。"""
    data = load_user_config()
    storage = dict(data.get("storage") or {})
    if upload_dir is not None:
        storage["upload_dir"] = upload_dir.strip()
    if output_dir is not None:
        storage["output_dir"] = output_dir.strip()
    data["storage"] = storage
    save_user_config(data)
    return get_storage_dirs()


def get_user_devices_dir() -> Path:
    """可写的设备规则目录(LLM 生成的新配置落在这里)。

    - EXE 打包后: EXE 同级的 devices/(持久化,不随临时解压目录消失)
    - 源码运行: 与内置规则同目录 agent/devices/
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "devices"
    return DEVICES_DIR


def get_devices_search_dirs() -> list:
    """设备规则搜索目录(去重,用户目录优先)。"""
    dirs: list = []
    for d in (get_user_devices_dir(), DEVICES_DIR):
        if d not in dirs:
            dirs.append(d)
    return dirs


@dataclass
class LLMConfig:
    """LLM 调用配置"""

    provider: str = DEFAULT_LLM_PROVIDER
    model: str = DEFAULT_LLM_MODEL
    api_key: str = ""
    base_url: Optional[str] = None
    max_tokens: int = DEFAULT_LLM_MAX_TOKENS
    temperature: float = DEFAULT_LLM_TEMPERATURE
    # 注意:后端若是推理模型(如 deepseek 推理类),复杂任务单次调用可达 2~5 分钟,
    # 超时太短会导致请求被掐断。默认给足 10 分钟。
    timeout_seconds: int = 600

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """从环境变量 + 用户配置文件加载配置 (优先级: 环境变量 > 配置文件 > 默认)。"""
        user_llm = load_user_config().get("llm", {}) or {}
        api_key = (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("AGENT_LLM_API_KEY")
            or user_llm.get("api_key")
            or ""
        )
        provider = (
            os.environ.get("AGENT_LLM_PROVIDER")
            or user_llm.get("provider")
            or DEFAULT_LLM_PROVIDER
        )
        model = (
            os.environ.get("AGENT_LLM_MODEL")
            or user_llm.get("model")
            or DEFAULT_LLM_MODEL
        )
        base_url = os.environ.get("AGENT_LLM_BASE_URL") or user_llm.get("base_url")
        try:
            max_tokens = int(os.environ.get("AGENT_LLM_MAX_TOKENS", str(DEFAULT_LLM_MAX_TOKENS)))
        except ValueError:
            max_tokens = DEFAULT_LLM_MAX_TOKENS
        try:
            temperature = float(os.environ.get("AGENT_LLM_TEMPERATURE", str(DEFAULT_LLM_TEMPERATURE)))
        except ValueError:
            temperature = DEFAULT_LLM_TEMPERATURE
        try:
            timeout_seconds = int(
                os.environ.get("AGENT_LLM_TIMEOUT", str(user_llm.get("timeout_seconds", 600)))
            )
        except ValueError:
            timeout_seconds = 600
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )


@dataclass
class AppConfig:
    """应用全局配置"""

    llm: LLMConfig = field(default_factory=LLMConfig.from_env)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()
        # 可在此处加载 yaml 配置文件,优先级: 环境变量 > yaml > 默认
        return cfg


# 单例配置
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """获取全局配置 (懒加载单例)。"""
    global _config
    if _config is None:
        _config = AppConfig.load()
    return _config


def ensure_user_config_dir() -> Path:
    """确保用户配置目录存在。"""
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return USER_CONFIG_DIR


def load_user_config() -> Dict[str, Any]:
    """读取用户配置文件 (~/.agent/config.json),不存在或损坏时返回空字典。"""
    if USER_CONFIG_PATH.exists():
        try:
            data = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_user_config(data: Dict[str, Any]) -> None:
    """写入用户配置文件。"""
    ensure_user_config_dir()
    USER_CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def update_llm_config(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMConfig:
    """更新 LLM 配置并刷新全局单例。

    - provider/model/api_key 传 None 表示保持不变;api_key 传空串表示保持不变
    - base_url 传 None 表示保持不变;传空串表示清除(回退到默认接口地址)
    """
    data = load_user_config()
    llm = dict(data.get("llm") or {})
    if provider:
        llm["provider"] = provider
    if model:
        llm["model"] = model
    if api_key:
        llm["api_key"] = api_key
    if base_url is not None:
        if base_url:
            llm["base_url"] = base_url
        else:
            llm.pop("base_url", None)
    data["llm"] = llm
    save_user_config(data)

    # 刷新全局单例,使改动立即生效
    global _config
    _config = AppConfig.load()
    return _config.llm


# === LLM 复核开关(人工可控,持久化到用户配置) ===

def load_review_settings() -> Dict[str, bool]:
    """读取 LLM 复核开关。默认:主开关关、独立抽取关、比对复核开(主开关开时才生效)。"""
    data = load_user_config().get("review") or {}
    return {
        "llm_review": bool(data.get("llm_review", False)),
        "independent_extraction": bool(data.get("independent_extraction", False)),
        "comparison_review": bool(data.get("comparison_review", True)),
    }


def save_review_settings(
    llm_review: bool,
    independent_extraction: bool,
    comparison_review: bool,
) -> Dict[str, bool]:
    """持久化 LLM 复核开关,返回保存后的设置。"""
    data = load_user_config()
    data["review"] = {
        "llm_review": bool(llm_review),
        "independent_extraction": bool(independent_extraction),
        "comparison_review": bool(comparison_review),
    }
    save_user_config(data)
    return data["review"]