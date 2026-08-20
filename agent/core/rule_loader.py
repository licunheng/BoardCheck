"""设备规则加载器。

从 `agent/devices/<device_code>/` 目录下加载 4 个 JSON/MD 文件,
并对外提供统一的 DeviceRules 对象。

新增设备规则时,只需在 devices/ 下新建一个目录并填好文件,无需修改任何代码。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import DEVICES_DIR, get_devices_search_dirs


@dataclass
class FieldExtractor:
    """单字段的抽取规则"""

    field_name: str
    regex: str
    context_anchor: Optional[str] = None
    post_process: List[str] = field(default_factory=list)
    must_be_present: bool = True
    notes: str = ""
    sanitize_duplicates: bool = False
    authoritative_forms: List[str] = field(default_factory=list)
    compiled_regex: Optional[re.Pattern] = None
    compiled_anchor: Optional[re.Pattern] = None

    def __post_init__(self) -> None:
        # 空字符串/None 表示该字段无规则(如非 4G 设备的 iccid),跳过编译
        if self.regex:
            try:
                self.compiled_regex = re.compile(self.regex, re.IGNORECASE)
            except re.error as e:
                raise ValueError(
                    f"字段 {self.field_name} 的正则不合法: {self.regex} ({e})"
                ) from e
        if self.context_anchor:
            try:
                self.compiled_anchor = re.compile(self.context_anchor, re.IGNORECASE)
            except re.error as e:
                raise ValueError(
                    f"字段 {self.field_name} 的 context_anchor 不合法: {self.context_anchor} ({e})"
                ) from e


@dataclass
class DeviceRules:
    """一个设备的完整规则集"""

    device_code: str
    display_name: str
    is_4g: bool
    version: str
    description: str
    platform_csv_columns: Dict[str, str]
    field_definitions: Dict[str, Dict[str, Any]]
    field_constraints: Dict[str, Any]
    filter_rules: Dict[str, Any]
    extraction_rules: Dict[str, Any]
    prompts_md: str
    rules_dir: Path

    # 编译后的正则(性能优化)
    compiled_keep_patterns: List[re.Pattern] = field(default_factory=list)
    compiled_drop_patterns: List[re.Pattern] = field(default_factory=list)
    field_extractors: Dict[str, FieldExtractor] = field(default_factory=dict)

    @classmethod
    def load(cls, device_code: str) -> "DeviceRules":
        """从 devices/<device_code>/ 加载规则。

        搜索顺序: 用户可写目录优先(EXE 同级 devices),再回退到内置目录。

        Raises:
            FileNotFoundError: 设备目录不存在
            ValueError: 配置文件缺失或格式错误
        """
        rules_dir = _find_rules_dir(device_code)
        if rules_dir is None:
            raise FileNotFoundError(
                f"设备 {device_code!r} 不存在。请检查目录: "
                + " / ".join(str(d / device_code) for d in get_devices_search_dirs())
            )

        # 读取 4 个配置文件
        device_json = _load_json(rules_dir / "device.json", required=True)
        filter_json = _load_json(rules_dir / "filter_rules.json", required=True)
        extraction_json = _load_json(rules_dir / "extraction_rules.json", required=True)
        prompts_md_path = rules_dir / "prompts.md"
        prompts_md = (
            prompts_md_path.read_text(encoding="utf-8")
            if prompts_md_path.is_file()
            else ""
        )

        rules = cls(
            device_code=device_json["device_code"],
            display_name=device_json.get("display_name", device_code),
            is_4g=device_json.get("is_4g", False),
            version=device_json.get("version", "0.0.0"),
            description=device_json.get("description", ""),
            platform_csv_columns=device_json.get("platform_csv_columns", {}),
            field_definitions=device_json.get("field_definitions", {}),
            field_constraints=device_json.get("field_constraints", {}),
            filter_rules=filter_json,
            extraction_rules=extraction_json,
            prompts_md=prompts_md,
            rules_dir=rules_dir,
        )

        # 编译正则
        rules._compile_patterns()
        return rules

    def _compile_patterns(self) -> None:
        """编译过滤与抽取的正则模式。"""
        # 过滤规则
        for p in self.filter_rules.get("keep_patterns", []):
            self.compiled_keep_patterns.append(re.compile(p))
        for p in self.filter_rules.get("drop_patterns", []):
            self.compiled_drop_patterns.append(re.compile(p))

        # 抽取规则
        for field_name, cfg in self.extraction_rules.get("field_extractors", {}).items():
            self.field_extractors[field_name] = FieldExtractor(
                field_name=field_name,
                regex=cfg.get("regex", ""),
                context_anchor=cfg.get("context_anchor"),
                post_process=cfg.get("post_process", []),
                must_be_present=cfg.get("must_be_present", True),
                notes=cfg.get("notes", ""),
                sanitize_duplicates=bool(cfg.get("sanitize_duplicates", False)),
                authoritative_forms=list(cfg.get("authoritative_forms", []) or []),
            )

    # === 辅助方法 ===

    def get_platform_column(self, field_name: str) -> Optional[str]:
        """获取平台 CSV 中某字段对应的列名。"""
        return self.platform_csv_columns.get(field_name)

    def get_4g_indicator(self) -> str:
        """4G 判定的提示词(注入到 prompt)。"""
        if self.is_4g:
            return "本设备是 4G 设备,**必须校验 ICCID 字段**。"
        return "本设备是非 4G 设备,**不需要校验 ICCID 字段**。"

    def is_field_required(self, field_name: str) -> bool:
        """字段是否为必填。"""
        ext = self.field_extractors.get(field_name)
        if ext:
            return ext.must_be_present
        return field_name in ("sn", "mac", "trace_code")

    def to_dict(self) -> Dict[str, Any]:
        """导出为字典(调试用)。"""
        return {
            "device_code": self.device_code,
            "display_name": self.display_name,
            "is_4g": self.is_4g,
            "version": self.version,
            "platform_csv_columns": self.platform_csv_columns,
        }


def _load_json(path: Path, required: bool = True) -> Dict[str, Any]:
    """加载 JSON 文件,不存在时按 required 决定抛错还是返回空字典。"""
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"必填配置文件缺失: {path}")
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _find_rules_dir(device_code: str) -> Optional[Path]:
    """在搜索目录中定位某设备的规则目录(用户目录优先)。"""
    for d in get_devices_search_dirs():
        candidate = d / device_code
        if (candidate / "device.json").is_file():
            return candidate
    return None


def list_supported_devices() -> List[str]:
    """列出所有支持的设备型号(合并用户目录与内置目录,去重)。"""
    codes: List[str] = []
    for d in get_devices_search_dirs():
        if not d.is_dir():
            continue
        for sub in d.iterdir():
            if sub.is_dir() and not sub.name.startswith(("_", ".")):
                if (sub / "device.json").is_file() and sub.name not in codes:
                    codes.append(sub.name)
    return sorted(codes)


def device_exists(device_code: str) -> bool:
    """检查某设备的规则是否存在。"""
    return _find_rules_dir(device_code) is not None