"""运行模式与环境变量（SR_MOCK 全离线模式）+ LLM 工厂。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

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


def get_llm(temperature: float = 0.1) -> ChatDeepSeek:
    """DeepSeek 模型（langchain ``init_chat_model`` 标准接口，06 票）。

    供应商细节零硬编码：api_key 自动读 ``DEEPSEEK_API_KEY``（.env 逐级查找
    加载）、api_base 自动读 ``DEEPSEEK_API_BASE``（默认官方端点）、model 默认
    deepseek-chat（规格技术栈基线）可被 ``DEEPSEEK_MODEL`` 覆盖。json_mode 走
    ``response_format=json_object`` 通道（deepseek 思考模式不支持 tool_choice
    强制）。
    """
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
        model_kwargs={"response_format": {"type": "json_object"}},
    )
