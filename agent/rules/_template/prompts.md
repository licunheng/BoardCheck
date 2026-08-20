# <DEVICE_CODE> 设备专属 Prompt 片段

> 此文件由 LLM 调用层在组装 Prompt 时自动注入。
> 修改后重启 agent 即可生效。

## 设备类型识别

本设备为 **<DEVICE_NAME>**,<是否 4G 设备说明>。

## 字段抽取规则

### SN (设备序列号)
- 格式: <描述>
- 日志行模式: `<正则>`

### MAC (蓝牙 MAC 地址)
- 格式: <描述>
- 日志行模式: `<正则>`

### ICCID (4G SIM 卡号)
- 格式: <描述>
- 日志行模式: `<正则>`

### 追溯码
- 格式: <描述>
- 日志行模式: `<正则>`

## 同一台设备的"测试循环"

<描述本设备的一次完整测试由哪几行构成,以及如何绑定>

## 已知陷阱(防呆)

<列出该设备的特定陷阱>

## 输出格式(JSON Schema 强制)

```json
{
  "device_records": [
    {
      "sn": "...",
      "mac": "...",
      "iccid": "...",
      "trace_code": "...",
      "com_port": "...",
      "trace_response_time": "...",
      "confidence": "high|medium|low",
      "notes": ""
    }
  ],
  "warnings": [],
  "stats": {}
}
```