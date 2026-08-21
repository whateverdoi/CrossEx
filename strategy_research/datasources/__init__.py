"""datasources — 免费数据源装配层（02 票：官方 SDK 薄适配 + httpx + mock 同构）。

统一契约（规格十节纪律 3/4/6）：

- 所有 fetch 失败返回 ``None``（装配层用 :func:`base.error_point` 标 UNKNOWN），
  绝不回退 mock；mock 仅限 ``SR_MOCK=1`` 显式离线模式（各模块内部分支）。
- 数据点四元组包装原语见 :mod:`base`。
"""

from .base import UNKNOWN, error_point, wrap

__all__ = ["UNKNOWN", "error_point", "wrap"]
