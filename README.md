# 板检数据核验工具

> **一键核验板检日志与追溯平台数据,产出 3 份报告,主动拦截 SN↔追溯码错位 Bug**

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#)

---

## 适用场景

工厂生产环节,板检上位机将设备的 SN/MAC/ICCID/追溯码 上报到追溯平台时,常常因为软件 Bug 导致:
- SN 对不上追溯码(实际是软件内部 UUID 顶替了真实 SN)
- ICCID 错位
- 追溯码未正常录入
- 设备身份信息错乱

本工具在板检数据上报后 **第一时间** 自动对比"板检原始日志"与"追溯平台 CSV",产出 3 份报告,让开发同事能立即定位 Bug。

---

## 核心特性

| 特性 | 说明 |
|---|---|
| 🖱 **按钮式 UI** | 产线工人也能用,无需技术背景 |
| 📦 **零环境依赖** | 单文件 EXE,无需安装 Python/Node.js |
| 🔌 **可扩展设备规则** | 新增设备只需添加 JSON 配置,不改代码 |
| 📊 **3 份报告** | 详细对比表 (.xlsx) + 重传 CSV + AI 分析 (.md) |
| ✅ **规则自检** | 新增设备规则后,跑一遍自检确认正确率 |
| 🤖 **LLM 兜底** | 正则失败时调用 LLM 智能解析(可选) |

---

## 快速开始(产线工人)

### 1. 双击运行

下载 `板检数据核验工具.exe`,双击即可。浏览器会自动打开 http://127.0.0.1:5050

### 2. 四步操作

```
┌────────────────────────────────────────────┐
│  步骤 0: 选择设备型号(下拉框,如 A02FG)    │
│  步骤 1: 上传板检日志(.txt/.log/.db)        │
│  步骤 2: 上传平台导出的 CSV                │
│  步骤 3: 点击"开始核验"                     │
│  步骤 4: 下载 3 份报告                      │
└────────────────────────────────────────────┘
```

### 3. 关闭程序

在浏览器窗口关闭后,回到 EXE 的命令行窗口,按 `Ctrl+C` 退出。

---

## 命令行模式(高级用户)

```bash
# 规则自检
板检数据核验工具.exe --device A02FG --rule-check

# 命令行核验(无需 UI)
板检数据核验工具.exe --device A02FG ^
  --raw-log "板检日志.txt" ^
  --platform-csv "平台导出.csv" ^
  --output-dir "./output"
```

---

## 输出报告

### 报告 ①: 详细对比表 (.xlsx)

每追溯码一行,列结构:

| 列 | 含义 |
|---|---|
| 追溯码 | 主键 |
| 原始 SN / 平台 SN / SN 一致性 | 板检日志中的 SN vs 追溯平台 |
| 原始 MAC / 平台 MAC / MAC 一致性 | 同上 |
| 原始 ICCID / 平台 ICCID / ICCID 一致性 | (仅 4G 设备) |
| 原始追溯码 / 平台追溯码 / 追溯码 一致性 | 同上 |
| 整体状态 | MATCH / MISMATCH / RAW_ONLY / PLATFORM_ONLY |
| 备注 | 异常描述 |

状态着色: 🟢 MATCH(一致)| 🔴 MISMATCH(字段错位)| 🟡 RAW_ONLY(仅板检有)

### 报告 ②: 重传 CSV

**仅包含** `MISMATCH` 与 `RAW_ONLY` 的设备,可直接导入追溯平台修复数据。

列结构与追溯平台的导入模板一致。

### 报告 ③: AI 分析报告 (.md)

自然语言描述关键发现、详细现象、根因分析与修复建议。

---

## 项目结构

```
agent_project_plan/
├── agent/                     # 核心代码
│   ├── __main__.py            # CLI 入口
│   ├── app.py                 # Flask Web UI
│   ├── config.py              # 配置管理
│   ├── core/                  # 核心模块
│   │   ├── file_reader.py     # 文件读取(txt/db/csv)
│   │   ├── preprocessor.py    # 行级过滤
│   │   ├── extractor.py       # 字段抽取
│   │   ├── comparator.py      # 比对逻辑
│   │   ├── reporter.py        # 报告生成
│   │   ├── verifier.py        # 端到端编排
│   │   └── rule_loader.py     # 设备规则加载
│   ├── llm/                   # LLM 集成
│   │   ├── client.py          # Anthropic/OpenAI 客户端
│   │   ├── prompts.py         # Prompt 模板
│   │   └── parser.py          # JSON 输出解析
│   ├── devices/               # 设备规则(可扩展)
│   │   ├── _template/         # 新设备模板
│   │   └── A02FG/             # 第一款支持的设备
│   │       ├── device.json
│   │       ├── filter_rules.json
│   │       ├── extraction_rules.json
│   │       ├── prompts.md
│   │       └── samples/
│   ├── rules/_template/       # 规则模板(复制后重命名)
│   └── static/                # Web UI 资源
│       ├── index.html
│       └── app.js
├── tests/
│   └── test_e2e.py            # 端到端测试
├── agent_project.spec         # PyInstaller 打包配置
├── build.py                   # 打包脚本
├── run_agent.py               # 打包入口(避免相对导入问题)
├── requirements.txt           # 运行时依赖
└── README.md                  # 本文件
```

---

## 添加新设备(开发者)

### 步骤 1: 复制模板

```bash
cp -r agent/devices/_template agent/devices/<DEVICE_CODE>
cd agent/devices/<DEVICE_CODE>
```

### 步骤 2: 填写 4 个文件

| 文件 | 作用 |
|---|---|
| `device.json` | 设备身份卡(型号、4G 判定、字段定义) |
| `filter_rules.json` | 文本日志行级过滤正则 |
| `extraction_rules.json` | 字段抽取正则(LLM 兜底开关) |
| `prompts.md` | 设备专属 Prompt 片段(已知陷阱等) |

### 步骤 3: 放入样本

把 1~3 个真实样本(.txt/.log/.db/.csv)放进 `samples/` 目录。

### 步骤 4: 跑自检

```bash
# 开发模式
python -m agent --device <DEVICE_CODE> --rule-check

# EXE 模式
板检数据核验工具.exe --device <DEVICE_CODE> --rule-check
```

通过条件:
- 关键字段成功率 ≥ 90%
- 报告无 ERROR 级警告

---

## LLM 配置(可选)

工具主要靠规则 + 正则,**不需要 LLM 也能工作**。LLM 仅作为正则失败时的兜底
(自动补全正则未抽全的字段)。

### 方式一:Web UI 设置面板(推荐,EXE 用户)

1. 启动工具,浏览器打开 `http://127.0.0.1:5050`
2. 页面上方的 **"⚙️ LLM 设置(可选)"** 区块
3. 选择服务商、填写模型名与 API Key、点 **"保存设置"**,再点 **"测试连接"** 验证
4. 配置保存在 `C:\Users\<你>\.agent\config.json`,下次启动自动加载

### 方式二:环境变量(命令行用户)

```bash
# Windows
set ANTHROPIC_API_KEY=sk-ant-xxxx
set AGENT_LLM_PROVIDER=anthropic
set AGENT_LLM_MODEL=claude-3-5-sonnet-20241022

# 或使用 OpenAI 兼容 API
set OPENAI_API_KEY=sk-xxxx
set AGENT_LLM_PROVIDER=openai_compat
set AGENT_LLM_BASE_URL=https://your-proxy.com/v1
set AGENT_LLM_MODEL=gpt-4o
```

配置优先级:**环境变量 > 用户配置文件 > 内置默认值**。

---

## 开发与构建

### 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

### 跑端到端测试

```bash
python3 tests/test_e2e.py
```

### 打包 EXE

```bash
python3 build.py
# 产物: dist/板检数据核验工具.exe (~15MB)
```

---

## 已知限制(MVP)

| 限制 | 原因 | 计划 |
|---|---|---|
| 时间窗口分组不够精确 | 双串口并行测试时,可能误绑定相邻设备 | v2 引入端口+时间+内容多因子分组 |
| LLM 仅在正则失败时启用 | 当前正则在 A02FG 上足够,LLM 未启用 | 后续可作为可选模式开启 |
| 不支持历史重跑 | 仅处理新批次 | 按用户要求,不在 MVP 范围 |

---

## 路线图

- **v0.1 (MVP - 当前)**: A02FG 4G 设备,正则优先 + LLM 兜底,3 份报告
- **v0.2**: 改进多串口并行测试的字段绑定精度
- **v0.3**: 支持第二个设备类型(待定)
- **v0.4**: 增加钉钉/企微告警(可选)
- **v1.0**: 完善文档、操作视频、自动更新机制

---

## 许可证

MIT License