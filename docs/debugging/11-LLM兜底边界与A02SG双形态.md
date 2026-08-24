# 11 - A02SG 追溯码双形态 + LLM 兜底边界(2026-08-21)

> 状态:🟢 已修复(A02SG 配置兼容裸/回复双形态);同时总结 LLM 兜底的适用边界。
> 触发用户反馈:EXE 核验 A02SG 日志时,报告 0 命中,看不到失败循环的设备,日志"不能正常处理"。

## 现象

A02SG 设备用户上传 `Info-2026-08-15.txt`(14410 行)+ `A02SG板检记录260815.csv`(100 行)核验:
- 抽取记录数=100(全部命中"回复追溯码:")
- 与平台 CSV 0 命中(MAC 段完全不重叠:日志 `00018B831B06` 系列 vs 平台 `00011C093AC4` 系列)
- 失败循环的设备(裸 `追溯码:` 形态)在结果里完全看不到

## 根因

A02SG 日志里追溯码有两种写法,设备配置只覆盖了一种,且 preprocessor 把另一种形态的行挡在门外:

| 形态 | 出现次数 | 用途 | 配置前能否到达抽取器 |
|---|---|---|---|
| `追溯码:F068XYXI8F...`(裸)| 175 | MAC 拿到后,板检中段早出,失败循环止于此 | ❌ 被 `keep_patterns` 丢弃 |
| `回复追溯码:F068XYXI8F...` | 100 | 板检 6 项全部成功 + 4G CCID 拿到后 | ✅ |
| `F068XYXI8F` 总计 | 275 | — | 100/275=36.4% |

两层过滤叠加:
1. `filter_rules.json` 的 `keep_patterns` 只有 `"回复追溯码"`,裸 `追溯码:` 行既不含 `568121440200`/`收mac:`/`4G\s*CCID`,也不含 `回复追溯码` → preprocessor 全部丢弃。
2. `extraction_rules.json` 的 `trace_code.regex` 锁死 `回复追溯码[:：]`,即使裸行侥幸到达,正则也匹配不上。

LLM 兜底为什么没救场(对应用户问"是否可调用 LLM 约束"):
- 节点 2(`extract_with_llm_fallback`)只在**正则完全没抽到**某字段时触发,A02SG 抽到了 100 条"回复追溯码",LLM 不被叫醒
- 节点 2.5/2.7/4.5 都是**抽取后**的复核,看不见被 preprocessor 丢弃的 175 行
- 即使 LLM 看见这些形态,正则一行 `(?:回复)?` 就解决,**用 LLM 是大炮打蚊子**

## 修复

**`devices/A02SG/extraction_rules.json`**:
- `trace_code.regex`: `回复追溯码[:：]\s*(...)` → `(?:回复)?追溯码[:：]\s*(...)`
- `trace_code.context_anchor`: 同上
- `field_anchors.trace_code[0]`: 同上
- `authoritative_forms` 保留为 `["回复追溯码"]`——只让回复形态进入权威,避免一设备拆两条(同码同串口 bucket 走 `_unique_trace_anchors` 时仍优先回复)
- 描述同步更新为"兼容两种形态"

**`devices/A02SG/filter_rules.json`**:
- `keep_patterns` 追加 `"追溯码:"`,让裸形态行通过 preprocessor 到达抽取器

**同步**:`agent/devices/A02SG/{extraction,filter}_rules.json` 同改(EXE 内部打包路径)。

## 验证

| 维度 | 修复前 | 修复后(预期) |
|---|---|---|
| 抽取记录数 | 100 | ≥ 175(裸 + 回复去重后) |
| 唯一追溯码 | 100 | ≥ 175 |
| 失败循环设备可见性 | 不可见 | 可见(裸形态锚点+同串口 SN/MAC/ICCID 兜底) |
| 一设备拆两条风险 | 无 | 无(authoritative_forms 仅锁回复,bucket 内自动优选回复) |
| 与平台 0 命中 | 是 | 仍是(数据本身不同批,见教训 ②) |

## LLM 兜底边界(本次总结)

### 适合用 LLM 的"多变"场景
- 同一字段 5+ 种变体且持续演化
- 字段名随固件/批次漂移(`CCID`/`SIM`/`4G信息`)
- 跨设备混打日志里"挑出当前设备"(上下文关联)
- 1:1 冲突多条候选选哪条(节点 2.7)
- 平台 CSV 列名飘移

### 不适合用 LLM 的场景(本次属于此)
- 已知两种写法(裸/回复)——正则一行 `(?:回复)?` 解决
- 已知所有形态且能枚举——确定性永远赢
- 仅"看起来复杂"但形态可穷举

### 分层策略(回答"约束"问题)
```
Layer 1  正则优先  ─→  覆盖枚举得出的形态(快/稳/便宜)
   │ 漏抽
   ▼
Layer 2  LLM 兜底抽取 ─→  正则拿不到时(慢/贵/救场)
   │ 存疑
   ▼
Layer 3  LLM 冲突复核 ─→  confidence=low 判真值(节点 2.7)
   │ 比对不一致
   ▼
Layer 4  LLM 比对复核 ─→  节点 4.5,只给判注不改结果
```

**"约束"的本质是:Layer 1 越完善 → Layer 2/3 越省 token;**
**LLM 是兜底,不是替代。**

## 教训

1. **新设备配置生成后,必须抽100 行样本统计各关键字段的形态变种频次**(裸/回复、Ret/trace code/发:trace code 等);频次 > 0 的形态都要进 `keep_patterns` + `regex`。
2. **authoritative_forms 不是"regex 只能匹配一种"的开关**——`regex` 负责"识别形态全集",`authoritative_forms` 负责"bucket 内优选"。两者解耦后,失败循环才不会被丢掉。
3. **keep_patterns 与 regex 必须协同**:regex 写得再宽,keep_patterns 把形态挡了也是白搭。新增设备时这两处要一起改。
4. **平台 CSV 与日志是否同批要先验证时间戳**——A02SG 这次的"0 命中"里,数据本身不同批(MAC 段全错开)占了一半原因,与抽取无关。
5. **LLM 不是万能兜底**——被 preprocessor 丢弃的行,LLM 在抽取阶段看不见;LLM 是抽取后复核,不是"先抽一遍正则+LLM"。

## 规则沉淀

- **RULE-NEW-DEVICE-CONFIG-CHECK**:新设备配置上线前,必须做 100 行抽样,统计各关键字段的形态频次,频次 > 0 的全部进 keep_patterns + regex。
- **RULE-DUAL-FORM-AUTHORITATIVE**:已知双形态字段,regex 用 `(?:prefix)?` 兼容全集,authoritative_forms 仅列权威形态,bucket 内优选。
- **RULE-LLM-FALLBACK-BOUNDARY**:LLM 兜底仅对"形态不可枚举"场景;已知形态一律 Layer 1 正则解决。