# 01 - 后台测试挂死(未禁用 LLM)(2026-08-13)

> 状态:🟢 已修复(测试统一加 `AGENT_DISABLE_LLM=1`)

## 现象

跑 F16G 核验的自动化测试时,进程长时间无输出、看似挂死,最后只能手动停掉。

## 根因

测试脚本**没有设置 `AGENT_DISABLE_LLM=1`**,核验流程在"抽取后"和"比对后"
两处触发了 LLM 复核(`llm_reconcile_extraction` / `llm_review_comparison`),
而 LLM 是慢推理模型,单次数分钟、且多次调用,导致测试卡住超时。

## 修复

- 所有不依赖 LLM 的确定性测试,运行前统一设 `AGENT_DISABLE_LLM=1`,跳过 LLM 复核;
- 该环境变量是全链路 LLM 调用的总开关(`extract_with_llm_fallback`、复核层都读它);
- 需要验证 LLM 路径的测试,单独标注、单独跑。

## 验证

`AGENT_DISABLE_LLM=1 python3 tests/...` 确定性测试秒级通过,不再挂死。

## 教训

- **测试默认要断网/断 LLM**,确定性逻辑测试必须能在无 LLM 环境下跑通。
- LLM 路径(慢、外部依赖)与确定性路径(快、可复现)要能用开关干净分离。
