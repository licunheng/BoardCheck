## Why

工厂有十几条业务线、上百种产品，每天都有大量设备经过板检（硬件测试）后，由板检上位机将 SN/MAC/ICCID/追溯码 上报到追溯平台。由于板检上位机普遍存在"上报字段错位"或"用内部ID顶替真实SN"等 Bug，导致设备投放市场后频繁出现"SN 对不上追溯码"、"ICCID 对不上追溯码"、"追溯码未录入"等问题。这些 Bug 不能等到用户投诉才发现，必须在数据上报后立即拦截。但目前没有任何自动化工具能跨上百种产品的异构日志完成核验 —— 文本日志格式多样，DB 表结构各异，规则化代码无法穷举，而靠人力处理几万~几十万条数据不现实。

## What Changes

- 新增 **`board-test-verification`** 能力：板检数据核验工具 的端到端工作流（文件导入 → 预处理 → LLM 解析 → 三份报告输出）
- 新增 **`device-rule-plugin`** 能力：以 JSON 文件方式注入设备规则（字段锚点、4G 判定、过滤规则、解析正则）的可扩展插件接口，新增设备无需改代码
- 新增 **`llm-prompt-engine`** 能力：四层 Prompt 组装（System Role / 业务规则 / 输入数据 / 输出约束），支持 4G 设备判定、半结构化日志解析、结构化 JSON 输出
- 新增 **`verification-reports`** 能力：三份报告的确定性生成 —— 详细对比表（xlsx）、重传 CSV（仅未上报/不一致）、AI 分析报告（md）
- 新增 **`rule-self-check`** 能力：新增设备规则的自检机制，用样本验证行保留率与字段抽取成功率，未达标 fail-fast
- 新增 **`factory-gui`** 能力：本地 Web UI，按钮式操作（导入板检数据 / 导入平台CSV / 开始核验 / 导出报告），产线工人可用
- 新增 **`exe-distribution`** 能力：PyInstaller 打包为单文件可执行程序，无 Python/Node 环境依赖

## Capabilities

### New Capabilities

- `board-test-verification`: 端到端核验流程，串联"输入 → 预处理 → LLM 解析 → 比对 → 报告生成"
- `device-rule-plugin`: 设备规则插件机制，通过 `rules/<device_code>/` 目录下的 JSON 文件扩展支持新设备
- `llm-prompt-engine`: 四层 Prompt 模板与组装引擎，支持结构化 JSON 输出、4G 设备判定
- `verification-reports`: 三份报告生成器（xlsx/csv/md）
- `rule-self-check`: 规则自检 CLI，用样本验证规则正确性
- `factory-gui`: 本地 Web UI（4 按钮 + 状态区）
- `exe-distribution`: PyInstaller 单文件 EXE 打包配置

### Modified Capabilities

（无 —— 这是全新能力，不修改任何现有 spec）

## Impact

- **新建代码**：完整的 Python 包 `agent/`，包含 core 模块、设备规则目录、Prompts、Web UI、打包配置
- **新建文档**：项目级 `README.md`、用户操作手册、新增设备规则教程
- **依赖**：`openpyxl`（xlsx 写入）、`pandas`（数据处理）、`requests`（LLM API）、`Flask`（Web UI）、`pyinstaller`（打包）
- **外部依赖**：用户需配置 LLM API Key（Anthropic Claude 或 OpenAI 兼容）
- **数据影响**：仅读取用户提供的板检日志/DB 文件与平台 CSV，不存储任何生产数据
- **运维影响**：第一版打包为单文件 EXE（≈50MB），分发给工厂操作员
- **试点数据**：基于 A02FG（150 条 4G 设备样本，2026-08-04 ~ 08-05）作为首个支持设备类型