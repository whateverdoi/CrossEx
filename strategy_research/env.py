"""运行模式与环境变量（SR_MOCK 全离线模式）+ LLM 工厂。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
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

    json_mode=True 装配 ``response_format=json_object``（分支证据单次调用）；
    react agent 用默认 False——json_object 会约束模型只输出 JSON，干扰工具
    调用循环。

    ``SR_MOCK=1`` 返回确定性假模型（LLM 层 mock，与数据源层 mock 同构）：
    按 prompt 特征路由固定 JSON 响应，真实/假模型产出同构，全链代码路径一致。
    """
    if os.environ.get(MOCK_ENV) == "1":
        # responses 仅占位（_generate 按输入路由，不消费列表）；temperature 同步传入
        # 但假模型不消费（确定性输出，温度无意义），仅保持调用面一致
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

#: 假模型调用计数（按 prompt 特征分类；03 票：计数键改为 bull/bear）
_MOCK_CALL_COUNTS = {"bull": 0, "bear": 0}
LIVE_CALL_COUNTS = {"bull": 0, "bear": 0}


class _LiveCallCounter(BaseCallbackHandler):
    """live 模式 LLM 调用计数：on_llm_start 触发（含 with_retry 重试，重试也算实际调用）。"""

    def __init__(self, key: str) -> None:
        self._key = key

    def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
        LIVE_CALL_COUNTS[self._key] += 1


def live_call_counter(key: str) -> BaseCallbackHandler:
    """按调用点取计数 handler（key ∈ bull/bear，03 票）。"""
    return _LiveCallCounter(key)


def reset_call_counts() -> None:
    """运行起点重置调用计数（mock/live 双路径归零）：
    run.json 的 llm_calls 应反映本次运行，而非跨运行累计值。"""
    for key in _MOCK_CALL_COUNTS:
        _MOCK_CALL_COUNTS[key] = 0
        LIVE_CALL_COUNTS[key] = 0

#: 分支 bull 固定响应（02 票）：3 条，basis 引用 mock 恒定快照值
#: （momentum 6.25 / sentiment 固定组件 / 微观结构 OI 0.0），与 mock 全链快照
#: 逐值一致 → 核验必过（交叉验证：test_evidence 全量断言无剔除）
_MOCK_BULL_JSON = """{
  "evidence": [
    {"claim": "动量分为正（TVL 增速加权，确定性计算）", "basis": {"domain": "signals", "field": "momentum.value", "value": "6.25"}, "source": "signals"},
    {"claim": "funding 费率 0.0001 处低位，无多头拥挤", "basis": {"domain": "signals", "field": "sentiment.components.funding", "value": "0.0001"}, "source": "signals"},
    {"claim": "多空人数比 1.05 偏多", "basis": {"domain": "signals", "field": "sentiment.components.ls_ratio_all", "value": "1.05"}, "source": "signals"}
  ]
}"""

#: 分支 bear 固定响应（02 票）：3 条，同上引用 mock 恒定快照值（含跨域微观结构）
_MOCK_BEAR_JSON = """{
  "evidence": [
    {"claim": "OI 24h 无增量（0.0%），缺乏新仓动能", "basis": {"domain": "signals", "field": "sentiment.components.oi_change_24h", "value": "0.0"}, "source": "signals"},
    {"claim": "taker 主动买卖比 1.0 中性，无追涨情绪", "basis": {"domain": "signals", "field": "sentiment.components.taker_bs_ratio_1h", "value": "1.0"}, "source": "signals"},
    {"claim": "微观结构 OI 24h 变化 0.0%，存量换手主导", "basis": {"domain": "microstructure_data", "field": "oi_change_24h.value", "value": "0.0"}, "source": "microstructure_data"}
  ]
}"""


class _MockChatModel(FakeMessagesListChatModel):
    """SR_MOCK=1 确定性假模型：按 prompt 特征路由固定 JSON（07 票）。

    bind_tools 返回 self（假模型不真调工具，直接给最终 JSON，agent 一轮结束）；
    分支的 json_mode 由 nodes 层 ``_extract_json`` 解析，本模型只产 JSON 文本。
    """

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = " ".join(getattr(m, "content", "") or "" for m in messages)
        if "多头证据研究员" in text:  # BULL_PROMPT
            _MOCK_CALL_COUNTS["bull"] += 1
            content = _MOCK_BULL_JSON
        elif "空头证据研究员" in text:  # BEAR_PROMPT
            _MOCK_CALL_COUNTS["bear"] += 1
            content = _MOCK_BEAR_JSON
        else:  # 未知 prompt 兜底：空对象（调用方 .get("evidence") → []）
            content = "{}"
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))]
        )
