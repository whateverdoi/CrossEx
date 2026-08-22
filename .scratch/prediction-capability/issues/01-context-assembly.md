# 01 — LLM 上下文装配模块：prompt + 摘要构建 + 解读规则收敛一处

**What to build:** LLM 的输入接口（四条 prompt、四个摘要构建器、情绪解读规则注记）从四个文件收敛为一个装配模块——改 prompt 解读规则时，契约测试保证渲染同步，不再靠手工与字面量测试维持。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] 新模块持有全部 prompt 常量；schemas 层仅作兼容再导出，现有导入方不破坏
- [ ] 摘要构建器（facts/decide/challenge/finalize 四个 + 共用渲染行）移入新模块，nodes 变薄
- [ ] 情绪解读规则注记单一来源（signals 与 mock 共用同一常量，删除逐字复制）
- [ ] 契约测试：DECIDE_PROMPT 引用的关键字段在渲染出的 decide 摘要中必现（如 funding_pctile_90d、oi_price_divergence、tvl_change_7d）；四节标题与 prompt 引用一致
- [ ] 全量测试通过（mock 模式零外部请求）

