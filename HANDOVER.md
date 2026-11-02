# 板检数据核验工具 · AI 交接文档

> **写给 AI 助手**:这是本仓库的完整交接文档。读完本文 + `docs/developer-guide.md`,你应当能直接接手开发、调试、核验任务,不需要向用户追问背景。最后更新:2026-09-30。

---

## 1. 这是什么项目(30 秒版)

工厂**板检数据核验工具**:从板检原始日志(串口交互 txt)抽取每台设备的 **SN / MAC / ICCID / 追溯码**,与**追溯平台导出表格(.xls/.xlsx/.csv)**逐字段比对,产出 3 份报告(对比表 xlsx + 重传 CSV + AI 分析 md),用于**第一时间拦截 SN↔追溯码错位等平台 Bug**。

- 形态:单文件 EXE + 本地 Web UI(Flask),产线工人双击即用
- 核心设计:**引擎代码对所有设备通用,设备差异 100% 写在每设备的「四件套」配置里**(见 §4)
- 已支持设备:`A02EG` / `A02FG` / `A02SG` / `F16G` / `FD01G`(目录在 `agent/devices/`)

## 2. 结构地图

```
agent_project_plan/
├── run_agent.py            # 启动入口(python run_agent.py)
├── agent/
│   ├── app.py              # Flask Web 服务 + REST API(含 /api/stats/summary)
│   ├── config.py           # 路径/LLM 配置;设备搜索目录优先级
│   ├── static/             # Web UI(侧边栏多页工作台,路由式切换)
│   ├── core/               # 核验核心:
│   │   ├── rule_loader.py      # DeviceRules.load() 加载四件套并编译正则
│   │   ├── preprocessor.py     # 行级 keep/drop 过滤
│   │   ├── extractor.py        # 字段抽取(正则优先,LLM 兜底)
│   │   ├── quality.py          # 去重 + 1:1 唯一性硬校验
│   │   ├── comparator.py       # 日志 vs 平台比对
│   │   ├── verifier.py         # 端到端编排(run_verification)+ 平台列名解析/自愈
│   │   ├── reporter.py         # 生成 xlsx/csv/md 三报告
│   │   ├── llm_review.py       # 3 阶段 LLM 复核(默认关)
│   │   ├── file_reader.py      # 按魔数嗅探读 xls/xlsx/csv/html/sqlite
│   │   └── device_generator.py # 四件套生成器
│   ├── devices/<设备代号>/  # 内置四件套(device.json / extraction_rules.json /
│   │                       #   filter_rules.json / prompts.md)
│   └── llm/ + tools/rule_check.py
├── devices/                # EXE 同级/项目根设备目录(运行时优先加载)
├── docs/
│   ├── developer-guide.md      # 开发者阅读指引(数据流逐文件讲解)★必读
│   ├── config-import-guide.md  # 四件套导入指南(红线清单)
│   ├── 新增设备规则教程.md
│   ├── 用户操作手册.md
│   └── debugging/              # 16 篇真实调试案例 + 00-索引.md(16 条心法)★必读
├── ui_prototypes/          # 4 版 UI 设计原型稿
├── outputs/                # 核验产物(gitignore,不进 git)
└── 00inbox/                # 用户投递的生产数据(gitignore,绝不进 git)
```

## 3. 核验流水线(verifier.run_verification)

1. **预处理**:按 `filter_rules.json` 的 keep/drop 正则过滤行
2. **抽取(按文件独立)**:文本日志逐文件抽取再合并 —— 防跨日时间窗错绑(debugging/12);绑定方向由配置 `sn_direction` 决定(A02SG 的 SN 在锚点**前**,F16G 在**后**,debugging/14);时间窗 `temporal_proximity` + `tie_breaker=trace_code_response_time`
3. **数据质量层(纯确定性)**:归一去重 + 1:1 唯一性硬校验,冲突双侧保留人工裁决
4. **平台表解析**:列名精确匹配 → 模糊匹配(阈值 0.75)→ 纠错写回 device.json 自愈
5. **比对 + 报告**:追溯码为主键 1:1 比对;不一致全部双侧列出,**绝不单侧裁决**

## 4. 四件套配置(加设备 = 加目录,零代码)

| 文件 | 内容要点 |
|------|---------|
| `device.json` | `platform_csv_columns`(平台列映射)、`field_constraints`(含 `mac_derived_from_trace` 派生绑定)、`is_4g` |
| `extraction_rules.json` | 每字段 `regex` + `context_anchor` + `authoritative_forms` + `session_binding`(窗口/方向/tie_breaker)+ `llm_fallback` |
| `filter_rules.json` | `keep_patterns` / `drop_patterns`(混打日志剔除其他设备段) |
| `prompts.md` | 设备专属 LLM 提示词(可空,只跑确定性流程) |

加载优先级:**EXE 同级 `devices/` > 项目根 `devices/` > `agent/devices/` 内置**。完整红线见 `docs/config-import-guide.md`。

## 5. 硬红线(违反必翻车,历史已验证 16 次)

1. **生产数据绝不进 git**(日志/平台导出/SN 表含真实 SN/MAC/追溯码)。已 ignore:`00inbox/`、`outputs/`、`output_config/`、`trace/`、`sample_data/`、`*.exe`、`安装包.zip`。新收到的生产数据放 `00inbox/` 或先补 ignore
2. **regex/context_anchor 必须用 `()` 包住字段值**,否则取到标签(debugging/04)
3. **`trace_code` 必填 `authoritative_forms`**(A02SG 有「回复追溯码:」权威形态与裸「追溯码:」中段形态,debugging/11)
4. **追溯码/SN 前缀是批次变体,不是字面量**:写正则前先扫平台全量唯一前缀分布,变体段用 `{n}` 放宽;keep_patterns 与 regex 必须同步放宽(debugging/13/16)
5. **绑定方向设备化**:`sn_direction` before/after 取决于固件,不能全局默认(debugging/10/14)
6. **hex 字段去重要有 hex 意识**:`isdigit()` 不识别 hex ICCID,禁用子串截断(debugging/14)
7. **缺值不许抓邻居**:就近绑定兜底会偷窗口内其他设备的值(debugging/09)
8. **确定性校验是基线,LLM 是增强**:回归测试一律 `AGENT_DISABLE_LLM=1`;`llm_verification` 默认关(推理模型空转,debugging/05/08)
9. **平台是真值源,但平台也有缺陷**:A02 系平台 `A系列uuid` 是软件内部 UUID ≠ 真实 SN(真实 SN 是 FA 开头 32 位,只在日志)——比对会全量报 SN mismatch,属已知现象,报告双侧列出
10. **跨文件 = 跨日期**:HH:MM:SS 无日期,任何时间窗逻辑不得跨文件

## 6. LLM 策略(重要)

- 抽取:`regex_first_llm_fallback` —— LLM 只在正则失败时兜底
- 复核:3 阶段(独立抽取交叉验证/冲突定向复核/比对复核),主开关 `llm_review`,默认关
- 环境变量:`AGENT_DISABLE_LLM=1` 强制全关(测试/回归必带)
- LLM 参与处必须有「空转探针」:抽 0 条/解析失败必须显式上报,不许静默降级

## 7. 常用操作

```bash
# 运行(Web UI:http://localhost:5000)
python run_agent.py

# 纯确定性回归(不触发 LLM)
AGENT_DISABLE_LLM=1 python run_agent.py

# 设备规则自检
python -m agent.tools.rule_check A02SG

# 打包 EXE(PyInstaller,产物先落 dist/ 再 cp 到根目录)
pyinstaller agent_project.spec

# 跑一次核验(也可直接调 API)
from agent.core.verifier import run_verification
run_verification(device_code="A02SG", raw_path=<txt>, platform_csv_path=<xls>,
                 output_dir=Path("outputs/xxx"), llm_review=False)
```

**本机注意**:系统默认 `python` 是 2.7.9(D:\00MID),**必须用 3.12**:`C:\Users\li239\AppData\Local\Programs\Python\Python312\python.exe`。依赖见 `requirements.txt`(openpyxl 读 xlsx、xlrd 读真 xls)。

**平台表格陷阱**:平台导出常出现「.xls 后缀实为 xlsx 内容」,`file_reader.read_spreadsheet` 按魔数嗅探,不要按扩展名判断。

## 8. Git 工作流

- 开发分支:本地 `clean_main`(干净历史,无生产数据);推送:`git push origin HEAD:main`(clean_main 跟踪远端 main,直接 `git push` 会报 upstream 不匹配,属正常)
- 远端:`github.com/licunheng/BoardCheck`
- 提交信息风格:中文、conventional 前缀(`feat:` / `docs:` / `fix:`)
- **提交前必查**:diff 里不得出现真实 SN(FA000001...)、MAC、追溯码(F068/F069/F070...)、客户名称

## 9. 调试经验库(接手必读)

`docs/debugging/00-索引.md` 是入口:16 篇真实案例(现象→根因→修复→验证→教训)+ 底部 16 条「通用调试心法」。本文件 §5 的红线全部源自那里,每条红线都有对应案例编号。

**新问题处理约定**:按编号递增新建 `docs/debugging/NN-标题.md`(结构:现象/环境/根因/修复/验证/教训),并更新 00-索引.md 表格。**不许**把新问题的解决过程只留在对话里。

## 10. 最近工作快照(2026-09-30)

- **Web UI 重构**:侧边栏多页工作台(工作台/核验/历史/设备/生成/设置),路由式切换;新增 `/api/stats/summary`(设备数/本月批次/LLM 复核批次);4 版原型稿沉淀在 `ui_prototypes/`
- **A02SG SN 错位问题实战**(客户反馈,数据在 `00inbox/20260930-*/`):平台 SN 列全是内部 UUID,客户给的「实际需要的SN.xlsx」也是 UUID。正确做法 = 从板检日志抽 FA 真实 SN,按追溯码 1:1 回填生成新表(SN 全小写 fa);63 条中 54 条匹配,9 条(前缀 F070/F069/8R 批次)日志里没有 → 空着并列缺数清单,不许瞎填
- 之前:FD01G 设备接入、跨设备时序差异修复、多文件独立抽取、hex ICCID 去重(见 git log)

## 11. 给 AI 的行为约定(用户偏好)

- **先方案后实现**:优化/重构类需求,先给方案等确认再动代码;运行中的 EXE 先停再改
- **配置生成宁慢勿错**:生成新设备四件套时要充分分析,生成后必须用样例日志试抽验证
- **对不上的数据不许瞎填**:缺数/冲突要显式列出,双侧呈现,人工裁决
- 一次性脚本可以直接跑,但要留下可复现的命令或产物说明
