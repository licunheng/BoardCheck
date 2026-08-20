# Tasks — 统一演进方案:引擎底座 + 配置生成管线精进 + LLM 可观测

> 状态:待用户审核确认后实施。
> 顺序建议:先引擎底座(Part1) → 再生成管线(Part2) → 再报告界面(Part3) → 最后回归+打包。

## Part 1 — 确定性引擎底座(代码)

- [ ] E1.1 `field_constraints` schema 支持 `mac_derived_from_trace` / `mac_trace_suffix_len`(默认12)/ `sn_strip_prefix` / `sn_strip_suffix`;`rule_loader` 读取
- [ ] E1.2 `extractor._mac_matches_trace` / `_find_mac_by_trace_suffix` 改用配置的派生长度(缺省12,不写死8)
- [ ] E1.3 `llm_review` 的 MAC 裁决判据同步改为读配置派生长度
- [ ] E2.1 `extractor` 绑 MAC/SN/ICCID: 派生优先(命中即用) + 时间窗兜底;`window_seconds` 可配(默认收窄至60s)
- [ ] E3.1 `extraction_rules.json` `trace_code` 支持 `authoritative_forms`
- [ ] E3.2 `extractor._parse_key_lines` / `extract_from_text_log`: trace_code 锚点唯一化(同码同串口只留权威形态/首个)
- [ ] E4.1 `llm_review.extract_records_with_llm` 统计 chunk 成功/0条/解析失败
- [ ] E4.2 全空转 → `llm_meta.extraction_effective=false`;比对复核 reviewed==0 且有冲突同样告警

## Part 2 — 配置生成管线(LLM 多节点深挖)

- [ ] G1.1 `_analyze_morphology` 改为逐字段穷举 + 权威形态判定(每字段单独调用,输出含行号)
- [ ] G2.1 新增派生关系实测节点: 喂平台CSV+日志,输出 `suffix_len`/头尾码 strip 等量化结论,写入 spec
- [ ] G3.1 `_validate_and_preview` 加「值合法性」门槛(含COM/含冒号/长度异常→判失败)
- [ ] G4.1 新增审查者节点: 生成四件套后调用 LLM 挑逻辑漏洞,审查意见作为回炉输入
- [ ] G5.1 回炉上限放宽至5轮;回炉带自检报告+样例+当前配置
- [ ] G5.2 仍不达标 → 明确标「需人工补充规则」,不静默交付
- [ ] G6.1 四件套拆分生成/校验,降低单次输出长度
- [ ] G7.1 样例不足时标注推测正则并提示真实日志验证

## Part 3 — 报告与界面

- [ ] R1.1 `reporter.generate_md` 复核章节区分「生效 / 空转」并显式告警
- [ ] R1.2 `app.js`/`index.html` 展示 LLM 复核生效状态
- [ ] R2.1 前端展示配置生成的五维质量报告(抽全率/重复率/冲突/值合法性/平台列名)

## Part 4 — 回归与打包

- [ ] P4.1 补回归测试: MAC 错绑(34→≈0)、锚点唯一化(码×3→×1)、值合法性、空转探针、派生可配
- [ ] P4.2 用 F16G 真实日志实测: quality.conflicts 25→0、MAC✗ 34→≈0
- [ ] P4.3 A02FG 不回归(派生可配/无权威形态回退)
- [ ] P4.4 跑通 tests/test_device_generation_fixes.py 全部测试
- [ ] P4.5 重打包 EXE 并覆盖项目根目录

## 验收标准(同 proposal)

- F16G 重跑: MAC ✗ 34→≈0;quality.conflicts 25→0;trace_code 锚点数 ≈ 真实设备数。
- 生成配置: 派生长度/头尾码/权威锚点实测写入;自检含值合法性;回炉直到达标。
- LLM 复核空转: 报告与界面明确提示"复核未生效"。
- A02FG 不回归。
