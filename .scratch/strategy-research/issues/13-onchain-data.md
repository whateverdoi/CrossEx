# 13 — 链上持币集中度 + 代币解锁结构（数据源调研受阻，标记 skipped）

**What was planned（② 提升准确性清单第 2 项）:** 新币（≤100 天上市）最大的
死因是控盘与砸盘，补充两类证据：
1. 链上持币集中度（前 10 地址占比、交易所净流量）
2. 代币解锁结构（vesting schedule，未流通比例）

**Status:** skipped（免费可核验链路当前不完整；用户拍板"不行先跳过，记录后走 3、4"）

## 数据源调研结论（2026-08-21 实测）

| 候选源 | 是否覆盖目标 | 实测结果 |
|---|---|---|
| CoinGecko 免费 API（无 key） | 无持币集中度；有 circulating/total/max supply（未流通比例可作解锁压力代理）+ community_data（twitter/telegram） | 网络 SSL EOF（Cloudflare 被断）；有 key 的 Pro API 才有持币集中度 |
| DefiLlama（项目现有依赖） | 无 holder 分布、无 unlock schedule | 网络 SSL EOF（同 Cloudflare） |
| TokenUnlocks | 真解锁 schedule | 需 API key |
| Etherscan / BSCScan | ETH/BSC top holders | 需 API key |
| CoinGlass | 交易所净流入流出 | 需 API key |
| **Solana 公共 RPC**（api.mainnet-beta.solana.com） | getTokenLargestAccounts + getTokenSupply → 前 20 账户占比 = 真集中度 | **实测连通（getHealth 200）**——唯一免费可核验源；但缺 symbol→mint 映射（Jupiter token.jup.ag Cloudflare 不通、api.jup.ag token 端点 404），且仅覆盖 Solana 链币 |

## 结论

免费、无 key、确定性可核验的持币集中度/解锁 schedule 链路当前不完整：
- 网络层：Cloudflare 系（coingecko/defillama/token.jup.ag）被 TLS 阻断（binance、
  solana RPC 正常）——区域网络问题，非代码问题
- 数据层：唯一通的 Solana RPC 缺 mint 映射源；其余真数据源（TokenUnlocks/
  Etherscan/CoinGlass）全部要 key

## 未来可行路径（按优先级）

a. **网络恢复后**：CoinGecko 供给结构信号（circulating/total → `unlocked_ratio`
   + 社区热度）——无 key、实现成本低，是对②"解锁抛压"的最佳确定性代理
b. **用户提供 key**：TokenUnlocks（真解锁 schedule）/ Etherscan（真 top holders）
   / CoinGlass（交易所净流量）任一即可完整实现②
c. **Solana mint 映射源恢复**（Jupiter token API 或维护静态已知映射表）：
   Solana RPC 链上集中度——仅覆盖 Solana 链候选，其余链 UNAVAILABLE（UNKNOWN 纪律）

**Blocked by:** 外部数据源（网络/API key），非代码依赖。
