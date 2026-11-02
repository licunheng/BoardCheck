# CLAUDE.md

本仓库的完整交接文档在 **`HANDOVER.md`**(AI 接手必读)。开发者细节见 `docs/developer-guide.md`,调试经验库见 `docs/debugging/00-索引.md`。

## 立即生效的硬红线

1. **生产数据绝不进 git**:真实日志/平台导出/SN 表(含 SN、MAC、追溯码)不许提交。生产数据放 `00inbox/`(已 ignore);核验产物落 `outputs/`(已 ignore)。提交前 diff 里不得出现 `FA000001`、`F068/F069/F070` 开头的追溯码、12 位 MAC
2. **确定性校验是基线**:回归测试必带 `AGENT_DISABLE_LLM=1`;LLM 只做兜底/复核,默认关
3. **本机 python 默认是 2.7**,必须用 3.12:`C:\Users\li239\AppData\Local\Programs\Python\Python312\python.exe`
4. **Git**:开发在 `clean_main`,推送用 `git push origin HEAD:main`
5. **对不上的数据不许瞎填**:缺数/冲突双侧列出,人工裁决
6. **先方案后实现**:重构/优化先给方案等确认;新问题沉淀到 `docs/debugging/NN-*.md` 并更新索引
