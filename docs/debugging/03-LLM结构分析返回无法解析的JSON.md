# 03 - 设备配置生成报「LLM 结构分析 返回无法解析的 JSON」(2026-08-14)

> 状态:🟢 已修复(提高 max_tokens + 超时 + 截断检测重试)

## 现象

导入新型号配置时,设备配置生成阶段直接报错:

```
❌ LLM 结构分析 返回无法解析的 JSON,请重试
```

## 根因

LLM 走代理(Anthropic 兼容接口),但代理后端实际是 **deepseek 类推理模型**。
这类模型在复杂 prompt 下,会把 `max_tokens`(当时 4096)的预算**几乎全耗在内部"thinking"上**,
真正输出的文本接近 0 字符 / 被截断 → 前端拿到的不是合法 JSON,解析失败。

## 修复

`device_generator._call_json` 与生成调用整体加固:

1. **提高 max_tokens**:结构分析/规格 16384,生成/修正 32768;
2. **超时拉长**:LLM timeout 120s → 600s(慢推理模型单次可达数分钟);
3. **截断检测**:响应 `stop_reason == "max_tokens"` 时,翻倍 max_tokens 并提示"精简、输出完整 JSON"后重试;
4. **空内容/非 JSON 重试**:解析失败带反馈重试,而非一次性报错;
5. **JSON 解析兜底**:`parse_json_response` 支持 markdown 包裹剥离、首尾大括号截取、常见错误修复。

## 验证

重新生成设备配置可正常产出四件套,不再报"无法解析的 JSON"。

## 教训

- **推理模型的 token 预算含 thinking**,预算不足时输出会被榨干;生成类长输出要给足 max_tokens。
- **检测 stop_reason==max_tokens 是关键信号**,据此自动加大 token 重试,比固定值可靠。
