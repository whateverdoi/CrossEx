# 05 — 退役清理与规格回写

**What to build:** 删除全部死代码——旧节点函数（research_facts / decide / challenge / finalize / risk_check）、旧 schema（TokenAnalysis 等 decision 体系）、旧 prompt（FACTS / CHALLENGE / FINALIZE）、tools.py 历史序列工具、review.py 校准及其测试；master spec（AgentArchitecture_Combined.md）按 spec 回写（新拓扑、证据 schema、退役清单、工件新定义）。

**Blocked by:** 03 — 拓扑切换；04 — 报告与工件改造

**Status:** ready-for-agent

- [ ] 旧符号零死引用（检索无命中）
- [ ] review.py 校准及测试退役
- [ ] 全量测试绿 + ruff 干净
- [ ] master spec 回写完成（新拓扑 / 证据 schema / 工件定义）
