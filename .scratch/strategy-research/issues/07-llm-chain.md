# 07 — 四节点 LLM 链

**What to build:** ③ research_facts（react agent + FACTS_TOOLS 四分析师视角采证）→ ④ decide（json_mode 单次调用决策，含 `_build_summary` 摘要构建）→ ⑤ challenge（react agent + CHALLENGE_TOOLS 对抗，反方预筛按决策方向取反）→ ⑥ finalize（`_invoke_rebuttals` 逐条反驳）四个节点装配；PASS 透传零 LLM 调用。

**Blocked by:** 04（数据收集节点）、06（LLM 基础件）

**Status:** done

- [x] ③ `research_facts`：react agent 四分析师视角（dimension）采证，facts 含 dimension/topic；`recursion_limit=8` 防工具循环失控
- [x] ④ `decide`：json_mode 单次调用，`_build_summary` 摘要构建（含 sentiment_raw 原始值解读引导）；decide 异常 → PASS（确定性兜底）
- [x] ⑤ `challenge`：非 PASS 才调用；≤3 条挑战含 stance；**反方预筛按决策方向取反**（多头取 `direction == "bear"` facts，空头取 `direction == "bull"` facts，规格 Q4 决策）
- [x] ⑥ `finalize`：`_invoke_rebuttals` 逐条 rebutted/accepted；accepted 只降不升（风控只降不升纪律）
- [x] PASS 透传零调用（LLM 调用计数可验证：PASS 路径 ③+④ 共 2 次，⑤⑥ 跳过）
- [x] mock 模式产出非空 facts；字段与 TokenAnalysis 一致（TRADE 必含 direction）
- [x] 单测：空头用例验证 ⑤ 预筛取 bull facts；异常注入（断网跑 challenge）不中断批

## 实现说明

**测试**：`tests/test_nodes_llm.py`（10 项）——mock 全链（6 token）验收 facts/decisions/计数/挑战结构 + 预筛纯函数（多头/空头双向）+ finalize 只降不升（TRADE 降级 + WATCH 不升边界）+ 异常注入（断网 challenge 置空不中断、decide 断网 PASS 兜底）。全量 189 passed + ruff 全绿。

**关键实现决策**（与规格伪代码的差异及理由）：

1. **`create_react_agent` → langchain 1.3.14 的 `create_agent`**：`langchain.agents.create_react_agent` 在 langchain 1.x 已移除（实测 ImportError），新标准接口 `create_agent(model, tools, system_prompt=...)`（ecosystem-primer/langchain 层当前 API），参数名 `system_prompt`（旧版 `prompt`）。返回 CompiledStateGraph，`invoke({"messages": [...], "recursion_limit": 8})` 行为与规格一致。

2. **LLM 层 mock（get_llm SR_MOCK=1 分支）**：与数据源层 mock 同构纪律——`_MockChatModel`（FakeMessagesListChatModel 子类）按 prompt 特征词路由固定 JSON 响应（facts/challenges/rebuttals/decide），`bind_tools` 返回 self（假模型不真调工具直接给最终 JSON，agent 一轮结束）。**全链代码路径真实执行**（create_agent 循环、_extract_json、宽容 validator、降级逻辑），mock 与真实行为同构。调用计数 `_MOCK_CALL_COUNTS` 按 prompt 特征分类，验收"PASS 路径 ③+④ 共 2 次"可精确验证（6 token 全链：facts=6/decide=6/challenge=3/rebuttals=3）。

3. **mock decide 决策映射**（`_MOCK_DECISIONS`）：BTC→TRADE/long、ETH→TRADE/short、SOL→WATCH/long、其余→PASS——确定性 fixtures（与 datasources/mock.py 固定候选同性质），保证 ⑤ 空头预筛（取 bull）、PASS 透传（取空列表）、finalize 降级三条路径在 mock 下全部可测。假模型从摘要首行"研究标的: X"提取 symbol 路由。

4. **④⑥ json_mode 用 `_extract_json` + 宽容 validator，不用 `with_structured_output`**：BaseChatModel 默认 `with_structured_output` 仅支持 tool-calling 策略（mock 假模型解析返回 None）；项目已有 `_extract_json` 宽容解析层（06 票核心组件，规格 ③⑤ 伪代码同款），统一双模式路径更符合"宽容解析纪律"（规格十-5）。`with_retry(stop_after_attempt=2)` 保留（invoke 异常自动重试 1 次），异常/不可解析 → PASS 兜底 + `fallback="json_mode"` 记录（规格 ④-3）。

5. **`get_llm` 新增 `json_mode: bool = False` 参数**：④⑥ 传 `json_mode=True` 装配 `response_format=json_object`；③⑤ react agent 用默认 False——json_object 强制 JSON 输出会干扰工具调用循环。规格 ④-2"with_structured_output(TokenAnalysis, method='json_mode')"的 json_object 通道语义等价保留。

6. **prompt 注入方式**：④⑥ 单次调用 `invoke([("system", DECIDE_PROMPT/FINALIZE_PROMPT), ("human", summary)])`——prompt 是系统消息，摘要只承载数据（真实模式 DeepSeek json_object 模式也要求 prompt 含字段清单）。③⑤ 经 create_agent(system_prompt=...) 注入。实现时曾漏注入导致假模型路由落空（decide 计数 9≠6），测试断言捕获后修复。

7. **摘要构建三函数**：`_facts_summary_lines`（③④ 共用骨架：基本面/市场/微观结构/信号/新闻 ≤3 条，缺失一律 UNKNOWN，行数 <50）、`_build_decide_summary`（骨架 + 事实证据节按 dimension 分组 ≤10 条，无 facts 写"仅依据确定性信号"）、`_build_challenge_summary`（决策全文 + 反方预筛 + 信号）、`_build_finalize_summary`（决策 + 挑战含 severity/refutes/stance + 信号）。费率字段 6 位小数保精度（0.0001 不打成 0.00）。

8. **验收"字段与 TokenAnalysis 一致"**：`TokenAnalysis.model_validate(d).model_dump() == d` 幂等断言（宽容 validator 清洗后的产物再验证仍相等）。
