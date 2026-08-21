# 06 — LLM 基础件

**What to build:** LLM 层的全部基础件：四个结构化输出 schema（`TokenAnalysis` / `FactItem` / `ChallengeItem` / `RebuttalItem`）+ 四份 prompt（ANALYZE / FACTS / CHALLENGE / FINALIZE）+ 宽容 validator（变体字段归一 / 默认值 / 白名单）+ 工具注册表（FACTS_TOOLS 5 个 / CHALLENGE_TOOLS 4 个）+ `_extract_json` 容错解析。DeepSeek json_mode 走 `response_format=json_object` 通道，冒烟验证 langchain 兼容性。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] `TokenAnalysis`：含 `direction`（白名单 long/short）；**无 max_loss / invalidation**（Q5 决策：LLM 主观值不配进确定性核验）；TRADE/WATCH 时 trade_structure 必填（进交易计划，不参与风控核验）
- [ ] `FactItem`（dimension 四分析师视角 / topic 7 研究主题 + unknown）/ `ChallengeItem`（stance）/ `RebuttalItem`
- [ ] 宽容 validator：变体字段归一（null / 字符串 / 字段名漂移全部容错）；null 输出可解析不抛异常；白名单校验（direction long/short、dimension、topic）
- [ ] 四份 prompt：ANALYZE_PROMPT / FACTS_PROMPT（六维查询模板 + 7 主题）/ CHALLENGE_PROMPT（对抗者，按决策方向取反预筛）/ FINALIZE_PROMPT
- [ ] `tools.py` 注册表：`FACTS_TOOLS` 5 个（get_tvl_history / get_fees_history / get_funding_history / get_stablecoin_history + search_web 六维查询模板）/ `CHALLENGE_TOOLS` 4 个（**不含 search_web**，对抗者不给联网搜索）；降采样 ≤10 点
- [ ] 工具单测：成功 / 无结果 / 失败 3 种返回；**工具层永不抛异常**
- [ ] `_extract_json`：宽容解析（代码块包裹 / 前后杂质 / 部分损坏容错）
- [ ] DeepSeek json_mode 冒烟：`response_format=json_object` 通道可用（langchain 适配 deepseek 验证）
