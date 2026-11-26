"""浏览器启动兜底：优先 Playwright 自带内核，缺了就退回系统已装的 Edge / Chrome。

为什么需要这一层
----------------
`python -m playwright install chromium` 在国内网络下经常卡在 CDN 上（实测 13 分钟零字节，
目录里只剩一个 `__dirlock`）。但这几套断言验证的是**页面渲染与交互**，
用机器上已有的 Chromium 系浏览器同样成立，没必要因为下载不下来就判功能失败。

选择顺序
--------
1. `ICH_BROWSER_CHANNEL` 指定了就只用它（`msedge` / `chrome`）
2. 否则：Playwright 自带内核 → `msedge` → `chrome`

三个都起不来才抛错，并且错误信息里带上最后一次的真实原因，
避免又变成一句笼统的「浏览器不可用」。
"""

from __future__ import annotations

import os

FALLBACK_CHANNELS = ("msedge", "chrome")


def _candidates() -> tuple[str | None, ...]:
    forced = os.environ.get("ICH_BROWSER_CHANNEL", "").strip()
    return (forced,) if forced else (None, *FALLBACK_CHANNELS)


async def launch_async(p, **kwargs):
    """异步 API（`async_playwright()`）下的启动兜底。"""
    last: Exception | None = None
    for channel in _candidates():
        try:
            if channel:
                return await p.chromium.launch(channel=channel, **kwargs)
            return await p.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001  逐个渠道降级，全部失败才报错
            last = exc
    raise RuntimeError(f"无法启动浏览器（自带内核与 {FALLBACK_CHANNELS} 均不可用）：{last}")


def launch_sync(p, **kwargs):
    """同步 API（`sync_playwright()`）下的启动兜底。"""
    last: Exception | None = None
    for channel in _candidates():
        try:
            if channel:
                return p.chromium.launch(channel=channel, **kwargs)
            return p.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"无法启动浏览器（自带内核与 {FALLBACK_CHANNELS} 均不可用）：{last}")
