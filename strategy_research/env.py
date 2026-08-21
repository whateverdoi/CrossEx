"""运行模式与环境变量（SR_MOCK 全离线模式）+ LLM 工厂。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

if TYPE_CHECKING:
    from langchain_deepseek import ChatDeepSeek

MOCK_ENV = "SR_MOCK"


def _find_dotenv() -> Path | None:
    """从本文件所在目录逐级向上查找 .env，找到第一个存在的就返回。

    这样无论包放在哪个目录，只要 .env 在它自己或任一上级目录就能被找到。
    """
    here = Path(__file__).resolve()
    for directory in [here.parent, *here.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


_dotenv_path = _find_dotenv()
if _dotenv_path is not None:
    load_dotenv(_dotenv_path)


def is_mock_mode() -> bool:
    """SR_MOCK=1 显式离线模式：全链路走 mock，零外部请求。"""
    return os.environ.get(MOCK_ENV) == "1"


def is_manual_tokens() -> bool:
    """SR_TOKENS 手动覆盖：指定币种跳过筛选。"""
    return bool(os.environ.get("SR_TOKENS", "").strip())


def get_llm(
    temperature: float = 0.1, *, json_mode: bool = False
) -> ChatDeepSeek | _MockChatModel:
    """LLM 工厂（06 票 init_chat_model 标准接口 + 07 票 mock 分支）。

    供应商细节零硬编码：api_key 自动读 ``DEEPSEEK_API_KEY``（.env 逐级查找
    加载）、api_base 自动读 ``DEEPSEEK_API_BASE``（默认官方端点）、model 默认
    deepseek-chat（规格技术栈基线）可被 ``DEEPSEEK_MODEL`` 覆盖。

    json_mode=True 装配 ``response_format=json_object``（④⑥ 决策/复审单次调用）；
    ③⑤ react agent 用默认 False——json_object 会约束模型只输出 JSON，干扰工具
    调用循环。

    ``SR_MOCK=1`` 返回确定性假模型（LLM 层 mock，与数据源层 mock 同构）：
    按 prompt 特征路由固定 JSON 响应，真实/假模型产出同构，全链代码路径一致。
    """
    if os.environ.get(MOCK_ENV) == "1":
        # responses 仅占位（_generate 按输入路由，不消费列表）
        return _MockChatModel(
            responses=[AIMessage(content="")], temperature=temperature
        )
    from langchain.chat_models import init_chat_model

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError(
            "未找到 DEEPSEEK_API_KEY：请在包目录或任一上级目录放置含该变量的 "
            ".env 文件（参考 .env.example），当前搜索起点："
            f"{Path(__file__).resolve().parent}"
        )
    return init_chat_model(
        os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat",
        temperature=temperature,
        model_kwargs=(
            {"response_format": {"type": "json_object"}} if json_mode else {}
        ),
    )


# ── LLM 层 mock（07 票：SR_MOCK=1 全离线确定性假模型）──────────────────

#: 假模型调用计数（按 prompt 特征分类；验收"PASS 透传零调用"可验证）
_MOCK_CALL_COUNTS = {"facts": 0, "decide": 0, "challenge": 0, "rebuttals": 0}

#: mock 决策映射（确定性 fixtures，与 datasources/mock.py 固定候选同性质；
#: 其余 symbol → PASS，保证 PASS 透传路径可测）
_MOCK_DECISIONS = {
    "BTC": ("TRADE", "long", 0.7),
    "ETH": ("TRADE", "short", 0.65),
    "SOL": ("WATCH", "long", 0.5),
}

#: ③ facts 固定响应：6 条覆盖四维度/三方向/多 topic（FactItem 全字段合法）
_MOCK_FACTS_JSON = """{
  "facts": [
    {"claim": "TVL 30d 变化 +12.5%，7d +5.1%（协议）", "source": "defillama", "timestamp": "2026-08-20", "direction": "bull", "dimension": "fundamentals", "topic": "project"},
    {"claim": "fees 24h $0.58M，7d 累计 $3.9M 连续增长", "source": "defillama", "timestamp": "2026-08-20", "direction": "bull", "dimension": "fundamentals", "topic": "adoption"},
    {"claim": "价格 7d +8.2%，30d +21.0%，90d +45.1%", "source": "binance", "timestamp": "2026-08-20", "direction": "bull", "dimension": "market", "topic": "unknown"},
    {"claim": "funding 0.0001 低于 7d 均值 0.00012（无拥挤）", "source": "binance_futures", "timestamp": "2026-08-20", "direction": "bull", "dimension": "sentiment", "topic": "unknown"},
    {"claim": "2026-Q3 解锁流通量 1.2%，抛压临近", "source": "bing", "timestamp": "2026-08-20", "direction": "bear", "dimension": "news", "topic": "unlock"},
    {"claim": "搜索显示核心团队匿名，2024 年 A 轮融资", "source": "bing", "timestamp": "2026-08-20", "direction": "neutral", "dimension": "news", "topic": "team"}
  ]
}"""

#: ⑤ challenges 固定响应：2 条 ≤3，stance 双视角，refutes 指向决策字段
_MOCK_CHALLENGES_JSON = """{
  "challenges": [
    {"claim": "TVL 30d 增速环比放缓，错价依据不可持续", "evidence": "defillama: TVL 30d +12.5%，低于此前 30d 增速", "severity": "high", "refutes": "mispricing", "stance": "conservative"},
    {"claim": "2026-Q3 解锁 1.2% 流通量，催化剂窗口存疑", "evidence": "bing: 2026-Q3 解锁流通量 1.2%", "severity": "medium", "refutes": "catalyst", "stance": "aggressive"}
  ]
}"""

#: ⑥ rebuttals 固定响应：1 accepted + 1 rebutted（accepted 只降不升可验证）
_MOCK_REBUTTALS_JSON = """{
  "rebuttals": [
    {"challenge_claim": "TVL 30d 增速环比放缓，错价依据不可持续", "response": "TVL 增速仍为正且 fees 同步增长，增长质量未恶化，维持错价判断", "outcome": "rebutted"},
    {"challenge_claim": "2026-Q3 解锁 1.2% 流通量，催化剂窗口存疑", "response": "承认解锁抛压风险，纳入风险清单并下调置信度", "outcome": "accepted"}
  ]
}"""


def _mock_decide_json(text: str) -> str:
    """④ decide 固定响应：按摘要首行的"研究标的"取映射，其余 PASS。

    TRADE/WATCH 时 trade_structure 必填（规格：进交易计划）；PASS 全空。
    """
    m = re.search(r"研究标的[:：]\s*([A-Za-z0-9]+)", text)
    symbol = m.group(1) if m else ""
    decision, direction, confidence = _MOCK_DECISIONS.get(symbol, ("PASS", "", 0.0))
    return json.dumps(
        {
            "symbol": symbol,
            "decision": decision,
            "direction": direction,
            "confidence": confidence,
            "fundamental_thesis": "TVL 与 fees 同步增长，基本面增速领先价格（mock）",
            "market_thesis": "价格 30d +21%，低于基本面增速，定价未充分反映（mock）",
            "market_implied_expectation": "市场隐含预期偏保守，未定价增长持续性（mock）",
            "mispricing": "mc_tvl 低于同类均值，存在低估空间（mock）",
            "catalyst": "TVL 增长动能延续与生态采用扩张（mock）",
            "risks": [],
            "evidence": [
                {
                    "claim": "TVL 30d +12.5%",
                    "source": "defillama",
                    "timestamp": "2026-08-20",
                }
            ],
            "data_quality": "mock 数据，字段齐全",
            "fundamental_score": 6.5,
            "quadrant": "III",
            "valuation_summary": "估值低于同类均值（mock）",
            "trade_structure": (
                "分批建仓，回撤 5% 止损（mock）" if decision != "PASS" else ""
            ),
        },
        ensure_ascii=False,
    )


class _MockChatModel(FakeMessagesListChatModel):
    """SR_MOCK=1 确定性假模型：按 prompt 特征路由固定 JSON（07 票）。

    bind_tools 返回 self（假模型不真调工具，直接给最终 JSON，agent 一轮结束）；
    ④⑥ 的 json_mode 由 nodes 层 ``_extract_json`` 解析，本模型只产 JSON 文本。
    """

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = " ".join(getattr(m, "content", "") or "" for m in messages)
        if "事实收集员" in text:  # FACTS_PROMPT
            _MOCK_CALL_COUNTS["facts"] += 1
            content = _MOCK_FACTS_JSON
        elif "对抗官" in text:  # CHALLENGE_PROMPT
            _MOCK_CALL_COUNTS["challenge"] += 1
            content = _MOCK_CHALLENGES_JSON
        elif "复审员" in text:  # FINALIZE_PROMPT
            _MOCK_CALL_COUNTS["rebuttals"] += 1
            content = _MOCK_REBUTTALS_JSON
        else:  # DECIDE_PROMPT（策略研究员）
            _MOCK_CALL_COUNTS["decide"] += 1
            content = _mock_decide_json(text)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))]
        )
