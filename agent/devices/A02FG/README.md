# A02FG 设备规则

> 版本: 1.0.0
> 适用产品: A02FG 4G 网关

## 文件清单

```
A02FG/
├── device.json            # 设备身份卡(型号、4G 判定、字段定义)
├── filter_rules.json      # 文本日志行级过滤规则
├── extraction_rules.json  # 字段抽取规则(正则优先 + LLM 兜底)
├── prompts.md             # 设备专属 Prompt 片段
├── samples/               # 真实样本(自检用)
│   └── (按需放入)
└── README.md              # 本文件
```

## 数据特征

| 字段 | 格式 | 来源 |
|---|---|---|
| SN | `FA0000014000012402608[14hex]` | 仅出现在 `COM\d+:发:` 命令行 |
| MAC | `[12hex]`,以 `0001` 开头 | `COM\d+:收mac:` 行 |
| ICCID | `[19-20位]`,以 `8986` 开头 | `COM\d+:4G CCID:` 行(注意拼写) |
| 追溯码 | `F070XYXI8[14hex]` | `COM\d+:回复追溯码:` 行 |

## 已知 Bug 提醒

平台记录的"SN"列实际上是软件内部生成的 `A系列uuid`(32位乱序hex),**不是真实 SN**。
真实 SN 仅存在于板检日志的下发命令中。本工具的核心任务之一是从日志中还原真实 SN。

## 验证方法

```bash
# 规则自检
python -m agent --device A02FG --rule-check
```