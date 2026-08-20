# 配置导入指南

> 适用:本 agent 已实现"配置即资产"——引擎代码对所有设备通用，**每台设备的差异全部写在四件套配置文件里**。本文说明如何把 Claude Code 用 skill 生成的四件套导入到 agent,使其在核验时被正确施行。

## 1. 需要准备哪四个文件

| 文件 | 必填 | 作用 |
|------|------|------|
| `device.json` | ✅ | 设备名片 + 平台列名映射 + 字段约束 + 派生关系(派生字段填 `field_constraints.mac_derived_from_trace` / `mac_trace_suffix_len` / `sn_strip_suffix`) |
| `extraction_rules.json` | ✅ | 字段抽取规则:每个字段的 `regex` + `context_anchor` + `authoritative_forms`(trace_code 锚点用) |
| `filter_rules.json` | ✅ | 行级过滤:`keep_patterns` 保留关键行;`drop_patterns` 剔除流程提示/原始数据等干扰行 |
| `prompts.md` | 可选 | 喂给 LLM 复核/兜底用的设备专属提示词;空文件也可运行(只跑确定性流程) |

模板可参考 `agent/devices/A02FG/` 下的四件套。

## 2. 放在哪里(三个等价位置,优先级从高到低)

| 位置 | 何时用 |
|------|--------|
| `EXE 同级目录/devices/<设备代号>/` | **最常见**。EXE 运行时只读这里,所见即所得;PyInstaller 打包后无需重新打包即可换配置 |
| `项目根目录/devices/<设备代号>/` | 开发期/源码运行(`python run_agent.py`)时的可写目录 |
| `agent/devices/<设备代号>/` | 引擎内置的兜底位置(只读)。一般放 A02FG 这种基线参考配置,避免被覆盖 |

> 同一个设备代号同时存在多份时,按上述优先级加载。`extras/A02FG/README.md` 和 `devices/A02FG/` 描述了完整加载顺序。

## 3. 文件名与目录名约定

- 目录名 = `device_code`(英文字母数字+下划线/中划线,首字符字母数字,最长 32),与 `device.json.device_code` 字段值一致
- 目录示例:`devices/F16G/`、`agent/devices/A02FG/`
- 文件名固定四个,不可改名

## 4. 关键字段必填项(踩坑红线)

### 4.1 `device.json.platform_csv_columns`

```json
{
  "sn": "设备编号",        // ← 必须是"有数据的真值列",不是空列
  "trace_code": "追溯码",
  "mac": "蓝牙mac",
  "iccid": "4G信息"
}
```

> **红线**:把 `sn` 配到空列(如 `写入SN`)会让平台 SN 永远为 None,跳过比对,报告看起来"160 match"实则 SN 字段根本没参与。**该列必须有非空数据**。
> 如果平台表头漂移(同设备不同日期列名不同),由 Claude Code skill 根据上传文件**动态生成**配置,**不要写死**。

### 4.2 `extraction_rules.json` 的捕获组约定

```json
{
  "field_extractors": {
    "sn": {
      "regex": "id:\\s*([0-9A-Fa-f]{32})46",     // ← 必须有 () 捕获"值",不要只写锚点前缀
      "context_anchor": "id:\\s*([0-9A-Fa-f]{32})46"
    }
  }
}
```

> **红线**:`regex` 或 `context_anchor` 里**必须**有括号 `()` 把"字段值本身"包起来。否则抽取器会把 `收 :mac:` 整行当字段值返回(经典"COM口字段名"bug)。
> 锚点必须带上下文标签(如 `收\\s*:id:`),**不要**只写 `id:`(会被 `ICCID:` 里的 `ID:` 子串误命中)。

### 4.3 trace_code 的权威形态

```json
{
  "field_extractors": {
    "trace_code": {
      "regex": "trace\\s*code:\\s*([A-Za-z0-9]+)",
      "context_anchor": "trace\\s*code:\\s*([A-Za-z0-9]+)",
      "authoritative_forms": ["发\\s*:"]   // ← 推荐填,避免一设备拆3条记录
    }
  }
}
```

> 日志里同一条追溯码常以 3 种形态出现(`发: trace code:` / `收 :trace code:` / `trace code:`)。填 `authoritative_forms: ["发\\s*:"]` 让抽取器只取权威形态,一设备一条记录。

### 4.4 字段约束(可选,推荐填)

```json
{
  "field_constraints": {
    "mac_derived_from_trace": true,    // MAC 由追溯码派生(派生关系已知时必填)
    "mac_trace_suffix_len": 12,        // 派生后 N 位,F16G/A02FG 测得为 12
    "sn_strip_suffix": "46",            // 平台 SN 尾码 46 需剥掉
    "iccid_starts_with": "8986",
    "iccid_length": [19, 20],
    "sn_format": "^[0-9A-Fa-f]{32}$"   // 用作比对合法性兜底
  }
}
```

## 5. 导入步骤(落地操作)

### 5.1 通过 EXE 运行的产线电脑

```bash
# 在 EXE 同级目录
mkdir devices/F16G
# 拷贝 Claude Code 生成的四件套
cp /path/from/claude-code/device.json devices/F16G/
cp /path/from/claude-code/extraction_rules.json devices/F16G/
cp /path/from/claude-code/filter_rules.json devices/F16G/
cp /path/from/claude-code/prompts.md devices/F16G/
# 重启 EXE, 启动后下拉框里就会多出 "F16G" 选项
```

### 5.2 通过源码开发/调试

```bash
# 在项目根目录
mkdir devices/F16G
# 拷贝文件...
# 用 `python run_agent.py` 启动, 下拉框里选择 F16G
```

### 5.3 重命名或覆盖设备

**不能**热重命名——目录名即设备代号,改名需要重启服务(EXE 重启或 Web 进程重启)。要改设备代号,直接新建对应名字的目录。

## 6. 验证导入成功(三种快速检查)

1. **EXE/Web 下拉框里能看到设备代号** — 加载失败通常说明 JSON 语法错或目录权限错
2. **拿该设备的真实样本跑一次端到端核验**(不开 LLM,纯确定性):
   ```bash
   AGENT_DISABLE_LLM=1 python run_agent.py
   ```
3. **看报告的"校验覆盖度提示"行** — 是否有"XX(N)未参与比对"。若 SN(165)未比对,说明 `platform_csv_columns.sn` 配错;若 MAC(N)未比对,说明 `extraction_rules.json.field_extractors.mac.regex` 抽不到

## 7. 常见错误清单(快速对照表)

| 现象 | 根因 | 修复 |
|------|------|------|
| 报告里 N match,但实际"X match"是虚的 | 某字段一端为 None(列名配错或正则没抽到),不计入 mismatch | 看"校验覆盖度提示"找缺失字段 → 改 `platform_csv_columns` 指向真值列 / 改 `extraction_rules.json` 正则 |
| 交付物里出现"COM77:收 :MAC:"字段名 | `regex` 或 `context_anchor` 没有捕获组 `()` | 加 `(...)` 把字段值包起来 |
| 同一设备拆出 N 条记录 | `trace_code` 多种行形态都被当锚点 | 在 `extraction_rules.json` 加 `"authoritative_forms": ["发\\s*:"]` |
| 字段值比实际多 1-2 位(尾码残留) | 平台带尾码(如 SN 末 46),未在引擎里剥掉 | 在 `device.json.field_constraints.sn_strip_suffix` 写 `"46"` |
| 报告 0 match,全部 mismatch | 正则没匹配上,或 `field_definitions` 与 `extraction_rules.json` 不一致 | 拿一行原始日志,手动跑一下正则;确保两份文件字段名一致 |
| ICCID 大量被截断(19 位) | 上位机粘连串(31-40 位),现有 `\\b(8986\\d{16})\\b` 不匹配 | 让 Claude Code skill 检测粘连串并生成 `sanitize_duplicates: true` 配置 |

## 8. 由 Claude Code skill 生成配置的推荐流程

1. 拿**该设备的真实样例日志** + **该批次真实平台 CSV** 给 skill
2. skill 输出四件套,按本文 § 4 检查捕获组/权威形态/列名真值列
3. 把四件套放进 `devices/<设备代号>/`(EXE 同级)
4. 拿更多样本回归,看"校验覆盖度提示"是否清空(所有字段都参与比对 = 配置正确)
5. 写一行 `_samples/` 样本引用(可选,方便后续回归)

## 9. 写在最后

- **配置是资产、引擎是平台**——加新设备 = 加一个目录,不写代码
- **校验覆盖度提示** 是判断配置对错的最快信号(报告里直接列出"哪些字段没真参与比对")
- **fuzzy 匹配是兜底,不是默认**——表头一变就兜错(例如 06-23 的 SN 在 A系列uuid 列、06-24 在设备编号 列,无法一份配置通用);Claude Code skill 端按所选文件**动态生成**列名映射是正解
