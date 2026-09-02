# 板检数据核验工具 · 开发者阅读指引

> 面向后续维护者。读完本文应能:(1)说清一次核验的完整数据流;(2)定位每个阶段对应的文件与函数;(3)安全地新增设备、改引擎、加 LLM 节点;(4)复现调试与打包流程。

---

## 1. 工程定位

工厂板检数据核验工具:从**板检原始日志**(串口交互 txt/log/sqlite/xls)抽取 SN/MAC/ICCID/追溯码,与**追溯平台导出 CSV**逐字段比对,产出 3 份报告(对比表 xlsx + 重传 CSV + AI 分析 md),用于**第一时间拦截 SN↔追溯码错位等平台 Bug**。

**形态**:单文件 EXE + 本地 Web UI(Flask),产线工人双击即用;设备差异全部写在**每设备四件套配置**里,引擎对所有设备通用。

---

## 2. 顶层结构

```
agent_project_plan/
├── run_agent.py              # EXE 启动入口(PyInstaller 用,绕相对导入)
├── agent_project.spec        # PyInstaller 打包配置(单文件 EXE,~19MB)
├── build.py                  # 打包脚本(可选)
├── requirements.txt
├── 板检数据核验工具.exe      # 当前可分发 EXE(根目录 = 部署副本)
├── dist/                     # PyInstaller 产物(新 EXE 先落这里,再 cp 到根目录)
│
├── agent/                    # 引擎包(打包进 EXE)
│   ├── __main__.py           # python -m agent 入口
│   ├── app.py                # Flask Web 服务 + REST API
│   ├── config.py             # 配置/路径(LLM/存储/设备搜索目录)
│   ├── core/                 # 核验核心(见 §3)
│   ├── llm/                  # LLM 客户端/解析/提示词
│   ├── devices/              # 内置设备四件套(源码模式)
│   ├── rules/_template/      # 设备配置模板
│   ├── static/               # Web UI(index.html / app.js)
│   └── tools/rule_check.py   # 设备规则自检工具
│
├── devices/                  # EXE 同级设备目录(运行时优先加载,见 §4)
├── output_config/            # skill 生成的设备配置 + 报告交付物
├── docs/                     # 文档(见 §7)
│   └── debugging/            # 调试经验清单(00-索引.md 为入口)
├── tests/                    # 回归测试
├── uploads/                  # 上传的板检日志/平台 CSV(运行时)
└── outputs/                  # 生成的报告(运行时)
```

---

## 3. 核验数据流(核心!)

一次核验在 `agent/core/verifier.py::run_verification()` 中串联 6 个阶段:

```
[输入: 板检日志 txt + 平台 CSV]
        │
        ▼
① 预处理    preprocessor.preprocess_raw_file()      — 行级过滤(keep/drop)
        │
        ▼
② 字段抽取  extractor.extract_from_text_log()       — 正则优先
   ②a LLM兜底 extractor.extract_with_llm_fallback() — 缺字段才调 LLM(可选)
        │
        ▼
②.5 LLM独立抽取交叉验证  llm_review.llm_reconcile_extraction()  — 阶段一(可选,默认关)
        │
        ▼
②.6 数据质量层  quality.dedupe_and_validate()       — 去重 + 1:1 硬校验 + ICCID 长度(纯确定性)
        │
        ▼
②.7 LLM定向复核  llm_review.llm_review_conflicts()  — 节点1:低置信度/冲突记录 → 喂锚点窗口判真值(可选)
        │
        ▼
③ 平台列解析  verifier._resolve_platform_columns()  — 精确→模糊自动纠错
        │
        ▼
④ 比对        comparator.compare_records()          — 日志真值 vs 平台真值(纯算术)
        │
        ▼
④.5 LLM比对复核  llm_review.llm_review_comparison()  — 阶段二:不一致记录判定(可选)
        │
        ▼
⑤ 报告        reporter.generate_all_reports()       — xlsx + csv + md 三件套
```

### 各文件职责(core/)

| 文件 | 职责 | 关键函数 |
|---|---|---|
| `preprocessor.py` | 行级过滤(keep_patterns 命中保留 / drop_patterns 剔除) | `preprocess_raw_file`, `preprocess_text_log` |
| `extractor.py` | 字段抽取:解析关键行 → trace 锚点唯一化 → 绑定 SN/MAC/ICCID | `extract_from_text_log`, `_unique_trace_anchors`, `_nearest_after_same_port` |
| `rule_loader.py` | 加载设备四件套,编译正则 | `DeviceRules.load`, `_find_rules_dir` |
| `quality.py` | 数据质量:按追溯码归一去重、组内众数仲裁、跨设备 1:1 唯一性硬校验、ICCID 长度校验 | `dedupe_and_validate`, `field_equivalent` |
| `comparator.py` | 比对:逐字段判一致,剥平台头尾码,标记覆盖度缺口 | `compare_records`, `field_equivalent` |
| `verifier.py` | 编排器:串联上述所有阶段 + LLM 开关解析 + 平台列解析 | `run_verification`, `_resolve_platform_columns` |
| `llm_review.py` | LLM 复核:阶段一(独立抽取)/节点 1(定向复核)/阶段二(比对复核) | `llm_reconcile_extraction`, `llm_review_conflicts`, `llm_review_comparison` |
| `reporter.py` | 生成 xlsx 对比表 + 重传 CSV + AI 分析 md | `generate_all_reports` |
| `device_generator.py` | 设备配置自动生成(skill 调用,多轮 LLM) | `generate_device_config` |
| `file_reader.py` | 读取 txt/log/sqlite/csv/xls(内容嗅探) | `read_raw_file`, `read_spreadsheet` |

### LLM 调用节点全景

| 阶段 | 函数 | 触发条件 | 空转探针 |
|---|---|---|---|
| ②a LLM 兜底 | `extract_with_llm_fallback` | 正则未抽全 + 有 API Key + `llm_fallback.enabled` | 无 |
| ②.5 独立抽取交叉验证 | `llm_reconcile_extraction` | `llm_review` 主开关 + `independent_extraction` 子开关 | chunk 0 条告警 |
| **②.7 定向复核(节点 1)** | `llm_review_conflicts` | 同 ②.5 开关;只复核 `confidence="low"` 记录 | 全 0 响应告警 |
| ④.5 比对复核 | `llm_review_comparison` | `llm_review` 主开关 + `comparison_review` 子开关 | 复核 0 条告警 |

**安全红线**:LLM 永不静默覆盖确定性结果。所有 LLM 值必须过 `_passes_constraint`(SN 格式 / MAC 派生 / ICCID 格式)硬校验才采纳;不合法就丢弃并记 notes。

---

## 4. 设备四件套(新增设备必读)

每个设备的差异全部写在 4 个文件里,引擎通用:

```
devices/<device_code>/
├── device.json            # 设备名片 + 平台列映射 + 字段约束/派生关系
├── extraction_rules.json  # 字段抽取正则 + 权威形态 + 绑定窗口
├── filter_rules.json      # 行级过滤 keep/drop
└── prompts.md             # 喂给 LLM 的设备提示词(兜底/复核用)
```

**加载顺序**(`config.py::get_devices_search_dirs`):
1. **EXE 同级 `devices/`**(运行时优先,用户可热替换,无需重打包)
2. **`agent/devices/`**(打包内置,源码模式)

**关键约束**(`device.json::field_constraints`):
- `mac_derived_from_trace` / `mac_trace_suffix_len` — MAC 由追溯码末 N 位派生(实测)
- `sn_strip_suffix` / `sn_strip_prefix` — 平台 SN 头尾码剥离
- `iccid_starts_with` / `iccid_length` — ICCID 格式
- `sn_format` — SN 正则

**生成方式**:用 skill `device-config-generator` 从样例日志 + 平台 CSV 自动生成,参考 `output_config/<device>/README.md`(含实测依据)。

---

## 5. 已知平台缺陷(所有 A02/F16G 同族共用)

| 现象 | 真值在哪 | 报告表现 |
|---|---|---|
| 平台 `A系列uuid` 是软件内部 UUID,非真实 SN | 日志 `发:568121440200` / `收 :id:` 行 | SN 全部不一致,双侧列出供人工裁决 |
| 平台 `4G信息` 存的是上位机粘连串的第一个脏前缀 | 日志 `4G CCID:` / `收 :ICCID:` 行,取粘连串最后一个完整号 | ICCID 不一致,双侧列出 |
| 同串口同码多次重试,失败循环偷上一台设备字段 | 成功循环(锚点之后)的字段 | 1:1 冲突,质量层标记低置信度,节点 1 定向复核 |

---

## 6. 调试经验清单(必读)

`docs/debugging/00-索引.md` 是入口,10 个真实问题按时间倒序排列,每篇结构固定:
**现象 → 环境 → 根因 → 修复 → 验证 → 教训/规则**。

| # | 问题 | 一句话根因 |
|---|---|---|
| 10 | SN 错绑偷上一台设备旧 SN | 失败循环锚点在成功循环之前,"就近"绑定抓到上一台 id |
| 09 | 粘连 ICCID 偷邻居设备 ICCID | 旧正则对粘连串整体失配 → 字段缺失 → 就近绑定偷下一台 |
| 08 | F16G MAC 大面积错绑 | trace_code 多形态重复锚点 + 双串口交错 + MAC 派生判据用错 |
| 07 | 重传表大量重复追溯码 + ICCID 一值多码 | 抽取无去重、无 1:1 校验、组内无众数仲裁 |
| 06 | 平台列名映射幻觉 | 平台原生 .xls 表头漂移,CSV 是"转了一道" |
| 05 | 核验被 LLM 复核拖慢 | LLM 复核默认开、不可关、推理模型空转 |
| 04 | 交付物出现 COM口/字段名 | context_anchor 无捕获组时,抽取器把前缀当字段值返回 |

**通用调试心法**(提炼自 10 篇):
1. 先复现再修,永不凭症状猜;系统分四层:预处理→抽取→数据质量→比对,逐层打日志找断点
2. LLM 参与的部分必须有"空转探针":抽 0 条、复核 0 条、解析失败要显式上报
3. 确定性校验是兜底,不是可选项;LLM 是增强不是依赖
4. 捕获组约定是生命线:regex/context_anchor 必须有包住"值"的 `()`
5. 平台是真值源:以平台原生格式为准,杜绝"中间转换"造成表头漂移
6. 缺值不等于可以抓邻居:先保证每字段有值,再谈就近
7. 绑定优先锚点后:失败循环的上一台设备旧值恰在锚点之前,"就近"会偷过来

---

## 7. 维护常见任务

### 7.1 新增一台设备
1. 放样例:`Device_doc/<code>/`(日志 txt + 平台 csv)
2. 调 skill `device-config-generator`(或参考 `output_config/` 下已有配置手写)
3. 产出四件套到 `output_config/<code>/`
4. 复制到 `agent/devices/<code>/`(源码)与 `devices/<code>/`(EXE 同级)
5. 自测:`python -m agent` 或 EXE,跑一遍核验,确认抽全率 ≥80%、无 missing_fields

### 7.2 改引擎(抽取/绑定/比对逻辑)
- 先写**失败测试**(`tests/test_device_generation_fixes.py` 是主回归套件)
- 改完后跑全套测试 + 用真实日志回归(06-23/06-24/05-27 三份 F16G + A02EG/A02SG)
- 重打包 EXE 并部署(见 §8)

### 7.3 加 LLM 复核节点
- 在 `agent/core/llm_review.py` 加函数,模式参考 `llm_review_conflicts`:
  - 显式开关(`enabled=None` 回退设备配置)
  - `AGENT_DISABLE_LLM` 拦截
  - `create_default_client()` 为 None 时优雅跳过
  - 硬约束仲裁(`_passes_constraint`)后才采纳
  - 空转探针(全 0 响应/0 条时明确告警)
- 在 `verifier.py` 对应阶段插入调用,把 meta 写入 `llm_meta`

---

## 8. 测试与打包

### 测试
```bash
# 主回归套件(不依赖 LLM)
AGENT_DISABLE_LLM=1 python tests/test_device_generation_fixes.py

# 用真实日志端到端回归(改引擎后必跑)
python -c "from agent.core.verifier import run_verification; ..."
```

### 打包 EXE
```bash
pyinstaller agent_project.spec --noconfirm
cp dist/板检数据核验工具.exe 板检数据核验工具.exe   # 部署到根目录
```
- 产物:`dist/板检数据核验工具.exe`(~19MB,含 Python 运行时 + Flask + openpyxl + requests)
- EXE 启动后读 **EXE 同级 `devices/`**(热替换)优先于内置 `agent/devices/`
- 改 `agent/static/`(UI)或 `agent/core/`(引擎)后**必须重打包**;改 `devices/<code>/`(设备配置)**无需重打包**

### Python 解释器
- 源码复现/打包:`/c/Users/li239/AppData/Local/Python/bin/python.exe`(Python 3.14,依赖齐全)
- 纯分析脚本(UTF-8 输出):`/c/Users/li239/AppData/Local/Python/Programs/Python/Python312/python.exe`(Python 3.12)

---

## 9. 一句话记住

**引擎通用,差异在配置;确定性校验是基线,LLM 是增强;平台是真值,日志是锚点;先复现再修,永不凭症状猜。**
