# 04 — 数据收集节点

**What to build:** ① 节点：按 tokens 并发拉取市场快照（价格/K 线窗口）+ 微观结构（OI 变化 / 多空比 / taker 比 / funding），共享资源批内只拉一次；单 token 注入异常不中断批（仅该 token 标 error/UNKNOWN）；`incomplete_tokens` 落 State。

**Blocked by:** 01（骨架与占位管线）、02（数据源层）

**Status:** done

- [x] `collect_data` 节点：快照（价格 / ret_7d / ret_30d / ret_90d / ret_1y / listing_days）+ 微观结构（oi_change_24h / oi_change_48h / oi_value_change_24h / ls_ratio_all / ls_ratio_all_change_24h / ls_ratio_top_acc / ls_ratio_top_pos / taker_bs_ratio / board）—— nodes._market/_microstructure，字段契约对齐规格数据字典 73-76 行四元组
- [x] 共享资源批内只拉一次：DeFiLlama 协议/链列表、Binance tickers、fapi 全量索引（premium/price）、聚合表（协议类拉 fees，链类拉 stablecoins/dexs）—— nodes._load_shared 单次拉取，test_mock_shared_fetched_once 计数断言（共享 ==1 / per-token ==6）
- [x] ThreadPoolExecutor 并发 + 各步 try 包裹：数据源失败即标记 error（该数据点 UNKNOWN），不中断整批—— max_workers=4，test_mock_single_token_exception_does_not_abort
- [x] mock 模式（`SR_MOCK=1`）全 6 token 快照齐全；单 token 注入异常不中断批—— test_mock_collect_full_snapshot + test_mock_single_token_exception_does_not_abort；真实数据缺失（bitcoin 无 stablecoin、uniswap 无 fdv）经 _fund_incomplete 按 kind 判定，mock 下 incomplete_tokens==[]
- [x] `state.py` 14 字段 TypedDict 编译通过；后写覆盖语义注释完整（每字段每 symbol 恰好写一次，无 reducer）—— 01 票既有，回归全绿
- [x] `incomplete_tokens` 落 State（缺失数据点 token 列表）—— meta["incomplete_tokens"]，test_mock_single_token_exception_does_not_abort / test_real_failure_marks_unknown_not_abort 断言
