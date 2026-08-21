"""运行模式与环境变量（SR_MOCK 全离线模式）+ LLM 工厂。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_deepseek import ChatDeepSeek

MOCK_ENV = "SR_MOCK"


def is_mock_mode() -> bool:
    """SR_MOCK=1 显式离线模式：全链路走 mock，零外部请求。"""
    return os.environ.get(MOCK_ENV) == "1"


def is_manual_tokens() -> bool:
    """SR_TOKENS 手动覆盖：指定币种跳过筛选。"""
    return bool(os.environ.get("SR_TOKENS", "").strip())


def get_llm() -> ChatDeepSeek:
    """DeepSeek 模型（langchain_deepseek 官方集成，06 票）。

    供应商细节不硬编码：api_key 自动读 ``DEEPSEEK_API_KEY``、api_base 自动读
    ``DEEPSEEK_API_BASE``（默认官方端点），model 默认 deepseek-chat 可被
    ``DEEPSEEK_MODEL`` 覆盖；代码零 url/模型名常量。json_mode 走
    ``response_format=json_object`` 通道（deepseek 思考模式不支持 tool_choice
    强制）；构造要求 DEEPSEEK_API_KEY 存在（官方包校验：默认端点无 key 拒绝
    构造，mock 链路不经过本函数）。
    """
    from langchain_deepseek import ChatDeepSeek

    return ChatDeepSeek(
        model=os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat",
        temperature=0.1,
        model_kwargs={"response_format": {"type": "json_object"}},
    )
