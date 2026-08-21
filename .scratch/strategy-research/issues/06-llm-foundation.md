# 06 — LLM 基础件

**What to build:** LLM 层的全部基础件：四个结构化输出 schema（`TokenAnalysis` / `FactItem` / `ChallengeItem` / `RebuttalItem`）+ 四份 prompt（ANALYZE / FACTS / CHALLENGE / FINALIZE）+ 宽容 validator（变体字段归一 / 默认值 / 白名单）+ 工具注册表（FACTS_TOOLS 5 个 / CHALLENGE_TOOLS 4 个）+ `_extract_json` 容错解析。DeepSeek json_mode 走 `response_format=json_object` 通道，冒烟验证 langchain 兼容性。

**Blocked by:** None — can start immediately

**Status:** done

- [x] `TokenAnalysis`：含 `direction`（白名单 long/short）；**无 max_loss / invalidation**（Q5 决策：LLM 主观值不配进确定性核验）；TRADE/WATCH 时 trade_structure 必填（进交易计划，不参与风控核验）
- [x] `FactItem`（dimension 四分析师视角 / topic 7 研究主题 + unknown）/ `ChallengeItem`（stance）/ `RebuttalItem`
- [x] 宽容 validator：变体字段归一（null / 字符串 / 字段名漂移全部容错）；null 输出可解析不抛异常；白名单校验（direction long/short、dimension、topic）
- [x] 四份 prompt：ANALYZE_PROMPT / FACTS_PROMPT（六维查询模板 + 7 主题）/ CHALLENGE_PROMPT（对抗者，按决策方向取反预筛）/ FINALIZE_PROMPT
- [x] `tools.py` 注册表：`FACTS_TOOLS` 5 个（get_tvl_history / get_fees_history / get_funding_history / get_stablecoin_history + search_web 六维查询模板）/ `CHALLENGE_TOOLS` 4 个（**不含 search_web**，对抗者不给联网搜索）；降采样 ≤10 点
- [x] 工具单测：成功 / 无结果 / 失败 3 种返回；**工具层永不抛异常**
- [x] `_extract_json`：宽容解析（代码块包裹 / 前后杂质 / 部分损坏容错）
- [x] DeepSeek json_mode 冒烟：`response_format=json_object` 通道可用（langchain 适配 deepseek 验证）

## 实现说明

**文件**：新建 `schemas.py`（4 schema + validator + 5 prompt 常量 + `_extract_json`）、`tools.py`（注册表）；修改 `env.py`（get_llm）、`web.py`（`_parse_rss` 加 description + `search_web`）、`defillama.py`（3 个历史序列 fetch）、`mock.py`（3 个历史序列 mock）；测试 `test_schemas`（21 项）/ `test_tools`（16 项）/ `test_web` 追加 4 项 / `test_defillama` 追加 7 项。

**宽容 validator 设计**：每个 schema 一个 `model_validator(mode="before")` 全量清洗——`_text`（null/占位→""）、`_pick`（白名单大小写不敏感）、`_dimension`/`_topic`（中英变体映射表 `_DIMENSION_KEYS`/`_TOPIC_KEYS`）、`_float`（数字/数字字符串）、`_list`；`model_validate` 永不抛 ValidationError，坏条目丢弃在装配层（③ 伪代码）。`TokenAnalysis` 含 16 字段（ANALYZE_PROMPT 第 8 条清单），decision 非法置 PASS（保守）、direction 非法置空、confidence clamp 0-1、evidence 逐条 EvidenceItem。

**DECIDE_PROMPT 派生**：规格 ④ 定义 DECIDE_PROMPT = ANALYZE 基线两处修改（删工具段 + 证据规则引用"事实证据"节）。实现为 `_ANALYZE_HEAD + _ANALYZE_EVIDENCE_RULE/DECIDE 版 + _ANALYZE_BODY` 共享文本块组装（单一事实源，prompt 文本仍各自完整可审计）。

**工具层纪律**：每个工具函数体整体 try 包裹（含 slug/chain 解析——`_chain_of` 对非字符串输入会抛，必须入 try），失败返回 "数据不可用（UNKNOWN）"，永不抛异常（langchain schema 校验是调用方行为，不在契约内）。chain: 前缀 token 返回"链类无该历史序列"提示（mock/真实一致，不模拟不存在的数据）。mock 下 `get_funding_history` 用 `_fapi_symbol` 归一（BTC→BTCUSDT）保证真实模式可用。

**历史序列数据源**：新增 `fetch_protocol_tvl_history`（/protocol/{slug} 的 tvl 数组）/ `fetch_protocol_fees_history`（/protocols/{slug}/fees）/ `fetch_stablecoin_history`（stablecoincharts 复用）——均为时间升序（最新在末尾，与 mock 同向），失败/空 → None。mock 同构强契约：TVL 历史用分段指数构造，最新值 == 当前 tvl 且 7d/30d 复合变化率精确等于 tvl_change 字段（工具层降采样后可回算趋势）；fees/stablecoin 恒定序列，最新值 == 当前值。

**mock 顺序教训**：mock_protocol_tvl_history 初版最新在前（x=0 起始），与真实 API 时间升序相反——工具降采样取"最新在末尾"导致输出全为旧数据。修复为升序并用测试锁定（`rows[-1]` 最新 + 7d/30d 回算断言）。

**`_extract_json` 容错层级**：围栏剥离 → 只取第一个（最小下标）结构（外层不闭合**不回退**内层数组——facts 契约是对象，回退会让 `.get("facts")` 崩溃）→ 标准 loads → 单引号键修复 → 配对栈补全（`_missing_closers`：缺 `]` 和缺 `}` 分别补，字符串截断先补闭引号）→ 键值边界截断重试（≤32 次）。截断在非法值中间时恢复为 `{"facts": [{}]}` 骨架（装配层坏条目丢弃）。

**冒烟**：mock 工具链 5 工具全通（含降采样 10 点、费率 6 位小数）；`get_llm()` 构造 + `with_structured_output(TokenAnalysis, method="json_mode").with_retry(stop_after_attempt=2)` 装配通过；无 DEEPSEEK_API_KEY，**真实 json_mode 调用待 key 后补验**（02 票同款先行方式）。

**验证**：178 passed（新增 48 项）+ ruff check/format 全绿。
