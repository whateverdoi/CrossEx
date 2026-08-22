# 01 — 趋势特征确定性化

**What to build:** TVL/fees/stablecoin 历史序列从 LLM 工具检索改为确定性采集——在数据采集层拉取序列，纯函数提炼趋势特征（如 `tvl_trend_30d`、`tvl_change_30d`、`fees_trend_30d`、`stablecoin_change_30d`）落盘 fundamental_data，两分支后续可直接消费数值。mock 数据源同步推导（同构纪律），旧 LLM 工具函数暂留为死代码。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [x] 趋势特征纯函数：正常序列、空序列、异常输入三态兜底正确
- [x] fundamental_data 落盘新增趋势特征键（链类/协议类各取所需）
- [x] mock 与真实路径趋势特征逐值一致（交叉验证测试）
- [x] 现有全量测试保持绿色
