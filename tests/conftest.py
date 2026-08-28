"""pytest 共享配置：默认 mock 模式，避免测试误发外部请求。"""

from __future__ import annotations

import os
import shutil

os.environ.setdefault("SR_MOCK", "1")


def pytest_sessionfinish(session, exitstatus):
    """会话结束即清 tmp 基座：测试不落任何产物（/tmp/pytest-of-<user>/ 无残留）。
    结果输出不受影响，跑通与否照常可见。"""
    factory = getattr(session.config, "_tmp_path_factory", None)
    if factory is None:
        return
    base = factory.getbasetemp()
    shutil.rmtree(base, ignore_errors=True)
    # pytest 会在退出前清理 pytest-current 死软链但不删空目录：先清软链再移除父目录
    try:
        for child in base.parent.iterdir():
            if child.is_symlink() and not child.exists():
                child.unlink()
        base.parent.rmdir()  # 非空（其他会话活跃）则跳过，不影响并发
    except OSError:
        pass
