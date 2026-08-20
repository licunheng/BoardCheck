# 设备规则模板

> 复制本目录为 `devices/<DEVICE_CODE>/`,然后逐个填写 4 个 JSON/MD 文件即可。

## 复制方法

```bash
cp -r rules/_template devices/<DEVICE_CODE>
cd devices/<DEVICE_CODE>
```

## 文件清单

| 文件 | 作用 |
|---|---|
| `device.json` | 设备身份卡(型号、4G 判定、字段定义) |
| `filter_rules.json` | 文本日志行级过滤规则(保留/剔除正则) |
| `extraction_rules.json` | 字段抽取规则(正则优先 + LLM 兜底) |
| `prompts.md` | 设备专属 Prompt 片段(LLM 调用时注入) |
| `samples/` | 真实样本(自检用,建议放 1~3 个) |

## 填写步骤

1. **device.json**: 把 `<DEVICE_CODE>` / `<DEVICE_NAME>` 替换为真实值,设置 `is_4g`,填写 4 个字段的正则与示例
2. **filter_rules.json**: 根据该设备日志的真实格式,列出 `keep_patterns`(关键行)与 `drop_patterns`(无关行)
3. **extraction_rules.json**: 4 个字段的精确正则 + 后处理(如 `trim`、`upper`)
4. **prompts.md**: 用自然语言描述该设备的特征,特别是**已知陷阱**(让 LLM 避免犯同类错误)
5. **samples/**: 放入 1~3 个真实样本(.txt/.log 或 .md 都行)

## 验证

```bash
# 验证规则有效性
python -m agent --device <DEVICE_CODE> --rule-check
```

成功指标:
- 行保留率 ≥ 95%
- 4 字段抽取成功率 ≥ 99%
- 自检报告无 ERROR 级警告

## 接口稳定性

所有 JSON 字段名在主版本内保持稳定。新增字段向后兼容,删除/重命名字段需升 major 版本号。