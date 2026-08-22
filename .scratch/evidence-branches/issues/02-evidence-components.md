# 02 — 证据组件

**What to build:** 证据体系的数据结构与节点函数（只新建、不接线，旧图照常）：EvidenceItem（claim + basis 结构化三元组 domain/field/value + source，无 confidence 字段）与 BranchOutput（evidence 列表，上限 8 条，按重要性降序）；state 新增 `evidence`（每 token 的 bull_case / bear_case）与 `rejected_evidence` 字段；bull_research / bear_research 分支节点函数（单次结构化输出 + retry + 宽容解析 + 单 token 异常产出空清单不中断批）；evidence_verify 确定性核验纯函数（basis 逐级解引用存在且 value 一致 → 通过，否则剔除并留痕）。

**Blocked by:** 01 — 趋势特征确定性化（分支 prompt 需引用趋势特征字段名，契约先定）

**Status:** ready-for-agent

- [x] 分支节点 mock LLM 单测：产出结构化证据、坏条目丢弃、异常兜底空清单
- [x] 证据 schema 无 confidence 字段、basis 为 domain/field/value 三元组
- [x] 核验纯函数全用例：引用存在 / 字段缺失 / 值不一致 / 嵌套路径 / 未知 domain / 剔除留痕
- [x] 现有全量测试保持绿色
