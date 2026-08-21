"""运行模式与环境变量（SR_MOCK 全离线模式）+ LLM 工厂。"""

from __future__ import annotations

import os

MOCK_ENV = "SR_MOCK"
LLM_API_KEY_ENV = "DEEPSEEK_API_KEY"
LLM_MODEL = "deepseek-chat"
LLM_BASE_URL = "https://api.deepseek.com"


def is_mock_mode() -> bool:
    """SR_MOCK=1 显式离线模式：全链路走 mock，零外部请求。"""
    return os.environ.get(MOCK_ENV) == "1"


def is_manual_tokens() -> bool:
    """SR_TOKENS 手动覆盖：指定币种跳过筛选。"""
    return bool(os.environ.get("SR_TOKENS", "").strip())


def get_llm():
    """DeepSeek deepseek-chat（langchain-openai 适配，06 票）。

    json_mode 走 ``response_format=json_object`` 通道（deepseek 思考模式不支持
    tool_choice 强制）；无 DEEPSEEK_API_KEY 时仍可构造（惰性，invoke 才请求），
    供 mock 链路与冒烟验证使用。
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=os.environ.get(LLM_API_KEY_ENV) or "sk-",
        base_url=LLM_BASE_URL,
        temperature=0.1,
        model_kwargs={"response_format": {"type": "json_object"}},
    )
