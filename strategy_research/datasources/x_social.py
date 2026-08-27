"""x_social 数据源：项目方 X 账号社交数据（移植自外部项目 fetchX，browser-use 有头抓取）。

- ``fetch_x_stats``：粉丝数（页面原样文本）+ 近 30 条推文互动（likes/reposts/comments/views，
  原样文本，**不含推文内容**）——滚动加载 4 次覆盖约 1-2 周
- ``resolve_handle``：CoinGecko → CoinPaprika 免费 API 解析币种 → X handle（带本地缓存）
- ``parse_compact_number``：页面紧凑数字文本（16.3万 / 163K / 6,144）→ float
- ``post_frequency_days``：平均发帖间隔（天），由推文相对时间跨度派生

确定性提取（无 LLM，结果 100% 可信）。关键约束（fetchX 踩坑记录）：
- x.com 反爬拦 headless（HTTP 403）→ 必须**有头模式**（DISPLAY=:0，无图形环境需 xvfb）
- 每币约 30-60 秒（页面加载 + 滚动 + 自旋等互动数完整）
- 失败返回 ``None``（装配层标 UNKNOWN，失败即失败不回退 mock；mock 仅 SR_MOCK=1）
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import re
import time
from datetime import datetime
from pathlib import Path

import httpx

from .. import env
from . import mock

TIMEOUT_SECONDS = 15.0
#: 币种 → X handle 解析缓存（随包落盘，重复运行零解析请求）
CACHE_FILE = Path(__file__).resolve().parent / "coin_handles.json"

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """惰性单例 httpx 客户端（sync，进程退出时关闭连接池）。"""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT_SECONDS)
        atexit.register(_client.close)
    return _client


# ---------------------------------------------------------------------------
# 数值解析（页面原样文本 → float；失败 None，UNKNOWN 纪律）
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^\s*([\d][\d.,]*)\s*([KkMmB]|万)?\s*$")
_NUM_MULT = {"K": 1e3, "k": 1e3, "M": 1e6, "m": 1e6, "B": 1e9, "b": 1e9, "万": 1e4}


def parse_compact_number(text: object) -> float | None:
    """紧凑数字文本 → float：``16.3万→163000`` / ``163K→163000`` / ``6,144→6144``。

    千分位逗号剥离；中英文单位（万/K/M/B）映射；其余格式 → None（UNKNOWN 纪律）。
    """
    if not isinstance(text, str):
        return None
    m = _NUM_RE.match(text)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    mult = _NUM_MULT.get(m.group(2) or "", 1.0)
    return num * mult


# ---------------------------------------------------------------------------
# 相对时间解析（"2h" / "15h" / "Jul 16" / "8月2日" → 多少秒前；越新越小）
# ---------------------------------------------------------------------------

_MONTHS = {
    month.lower(): i + 1
    for i, month in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
}


def _rel_ago(rel: str | None) -> float:
    """把相对时间转成可比较的 '多少秒前'，越小越新。

    - 相对格式（2h/15h/1d）→ 秒
    - 月日格式（Jul 16 / 8月2日）→ 按今年该日推算（假设过去，未来则去年）
    - 无法解析 → inf（视为旧帖，不参与发帖频率计算）
    """
    if not rel:
        return float("inf")
    s = rel.strip().lower()
    if s in ("now", "just now", "刚刚"):
        return 0.0
    m = re.match(r"(\d+)\s*([smhdwy])", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}[unit]
        return n * mult
    # 页面月日按查看者本地时区渲染，比较基准同为本地墙钟（取 aware 只为口径显式）
    now = datetime.now().astimezone()
    m = re.match(r"([a-z]{3})\s+(\d{1,2})", s)
    if m and m.group(1) in _MONTHS:
        month, day = _MONTHS[m.group(1)], int(m.group(2))
    else:
        m = re.match(r"(\d{1,2})月(\d{1,2})日", s)
        if not m:
            return float("inf")
        month, day = int(m.group(1)), int(m.group(2))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ts = today.replace(year=now.year, month=month, day=day)
    if ts > today:  # 未来（跨年）→ 去年
        ts = ts.replace(year=now.year - 1)
    return max(0.0, (now - ts).total_seconds())


# ---------------------------------------------------------------------------
# 币种 → X handle 解析（CoinGecko 首选 / CoinPaprika 兜底，免费 API 零 key）
# 带本地缓存与 429 重试；测试可注入 httpx.Client（MockTransport）
# ---------------------------------------------------------------------------


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    """缓存写失败不阻断（下次运行重新解析即可）。"""
    with contextlib.suppress(OSError):
        CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def resolve_handle(coin: str, client: httpx.Client | None = None) -> str | None:
    """通过 CoinGecko/CoinPaprika 把币种符号解析为官方 X handle，失败返回 None。

    结果缓存到 coin_handles.json，命中缓存时零网络请求；CoinGecko 429 限流
    自动重试（最多 3 次，间隔递增），失败回退 CoinPaprika。
    """
    coin = coin.lower()
    cache = _load_cache()
    if coin in cache:
        return cache[coin]
    c = client or _get_client()
    # 两个零 key 免费接口，任一 provider 出错就换下一个（整体失败返回 None →
    # 装配层标 UNKNOWN）；suppress 表达的是「该 provider 不可用」，不是「出错了继续跑」
    with contextlib.suppress(Exception):  # CoinGecko（首选）
        r = c.get(
            "https://api.coingecko.com/api/v3/search",
            params={"query": coin},
        )
        r.raise_for_status()
        candidates = [
            cnd for cnd in r.json().get("coins", [])
            if cnd.get("symbol", "").lower() == coin
        ]
        if candidates:
            # 优先选有市值的（更可能是主币）
            candidates.sort(key=lambda cnd: cnd.get("market_cap_rank") or 10**9)
            cid = candidates[0]["id"]
            r2 = None
            for attempt in range(3):  # 429 限流重试（间隔递增）
                r2 = c.get(f"https://api.coingecko.com/api/v3/coins/{cid}")
                if r2.status_code != 429:
                    break
                time.sleep(2 ** (attempt + 1))
            r2.raise_for_status()
            handle = (r2.json().get("links") or {}).get("twitter_screen_name")
            if handle:
                cache[coin] = handle
                _save_cache(cache)
                return handle
    with contextlib.suppress(Exception):  # CoinPaprika（备选）
        r = c.get(
            "https://api.coinpaprika.com/v1/search/",
            params={"q": coin, "c": "currencies"},
            follow_redirects=True,
        )
        r.raise_for_status()
        items = [cnd for cnd in r.json() if cnd.get("symbol", "").lower() == coin]
        if items:
            cid = items[0]["id"]
            r2 = c.get(f"https://api.coinpaprika.com/v1/coins/{cid}")
            r2.raise_for_status()
            handle = (r2.json().get("twitter_username") or "").strip()
            if handle:
                cache[coin] = handle
                _save_cache(cache)
                return handle
    return None


# ---------------------------------------------------------------------------
# 确定性 JS 提取（fetchX 移植 + 滚动加载改造）
# 选择器基于新版 X 前端 DOM 实测（中英文 locale 通用）：
#   - 资料数字从 body 文本正则提取（粉丝/关注/帖子）
#   - 推文按 a[href*="/status/"] 的 status id 去重（跨滚动轮累积），向上找 ARTICLE
#   - 互动数：Reply/Repost/Like 按钮 aria-label + 文本数字；views 在
#     aria-label="View count" 附近；按钮数字序列兜底
#   - 滚动加载：前 4 轮尝试滚动到底部（每轮间隔 2s），收集 DOM 中全部推文
#   - 自旋等待：粉丝数 + ≥20 条 + 最新帖互动 ≥3 项齐了再 resolve（≤24s 兜底）
#   - 置顶帖自动排除；不提取推文文本（无交易论证价值）
# ---------------------------------------------------------------------------

EXTRACT_JS = r"""(...args) => {
return new Promise(function(resolve){
  var MAX_TRY = 12, SCROLL_ROUNDS = 4, tries = 0;
  var byId = {};
  function extract(){
    var bt = document.body.innerText || '';
    var prof = {name:null, handle:null, followers:null, following:null, posts:null};
    var fm = bt.match(/([0-9][0-9.,KkMm\u4e07]*)\s*Followers?/i);
    if(fm) prof.followers = fm[1];
    var fgm = bt.match(/([0-9][0-9.,KkMm\u4e07]*)\s*Following/i);
    if(fgm) prof.following = fgm[1];
    var pm = bt.match(/([0-9][0-9.,]*)\s*posts?/i);
    if(pm) prof.posts = pm[1];
    var lines = bt.split('\n').map(function(s){return s.trim();}).filter(Boolean);
    for(var i=0;i<lines.length;i++){ if(/^@\w+$/.test(lines[i])){ prof.handle = lines[i]; prof.name = lines[i-1]||null; break; } }
    var statusAs = Array.from(document.querySelectorAll('a[href*="/status/"]'));
    statusAs.forEach(function(sa){
      var href = sa.getAttribute('href')||'';
      var m = href.match(/\/status\/(\d+)/);
      if(!m) return;
      var id = m[1];
      if(!byId[id]){
        var clean = href.split('/photo')[0].split('?')[0];
        var full = clean.indexOf('http')===0 ? clean : 'https://x.com'+clean;
        byId[id] = {href: full};
      }
    });
    var tweets = [];
    Object.keys(byId).forEach(function(id){
      var href = byId[id].href;
      var target = null;
      statusAs.forEach(function(sa){
        if(target) return;
        var h = sa.getAttribute('href')||'';
        if(h.indexOf('/status/'+id)>=0){
          var el = sa;
          for(var j=0;j<12;j++){ el = el.parentElement; if(!el) break; if(el.tagName==='ARTICLE'){ target=el; break; } }
        }
      });
      if(!target) return;
      var full = target.innerText || '';
      var rel = null;
      var mm = full.match(/@\w+\s*\n([^\n]+)/);
      if(mm) rel = mm[1].trim();
      function numText(el){ var t=(el&&el.textContent||'').trim(); return /^[\d][\d.,KkMm\u4e07]*$/.test(t)&&t.length<=8?t:null; }
      var eng = {};
      target.querySelectorAll('[aria-label]').forEach(function(e){
        var l=(e.getAttribute('aria-label')||'').toLowerCase();
        var n=numText(e);
        if(l==='reply'&&n) eng.replies=n;
        if(l==='repost'&&n) eng.reposts=n;
        if(l==='like'&&n) eng.likes=n;
      });
      var vc = target.querySelector('[aria-label="View count"]');
      if(vc){ var vn=numText(vc.parentElement)||numText(vc.parentElement.parentElement); if(vn) eng.views=vn; }
      if(!eng.replies||!eng.reposts||!eng.likes||!eng.views){
        var nums=[];
        target.querySelectorAll('button,[role="button"]').forEach(function(b){ var n=numText(b); if(n) nums.push(n); });
        var u=[]; for(var i=0;i<nums.length;i++){ if(nums[i]!==u[u.length-1]) u.push(nums[i]); }
        if(u.length>=4){ eng.replies=eng.replies||u[u.length-4]; eng.reposts=eng.reposts||u[u.length-3]; eng.likes=eng.likes||u[u.length-2]; if(!eng.views) eng.views=u[u.length-1]; }
        else if(u.length===3){ if(!eng.reposts) eng.reposts=u[u.length-3]; if(!eng.likes) eng.likes=u[u.length-2]; if(!eng.views) eng.views=u[u.length-1]; }
      }
      tweets.push({
        id: id,
        url: href,
        rel: rel,
        eng: eng,
        pinned: full.indexOf('Pinned')>=0 || full.indexOf('\u5df2\u7f6e\u9876')>=0
      });
    });
    tweets.sort(function(a,b){ return a.pinned===b.pinned ? 0 : (a.pinned?1:-1); });
    return JSON.stringify({profile:prof, posts:tweets.slice(0,40), url:location.href});
  }
  function attempt(){
    tries++;
    var out = null;
    try{
      if(tries<=SCROLL_ROUNDS){ var db = document.body; if(db){ window.scrollTo(0, db.scrollHeight); } }
      out = extract();
    }catch(e){ out = JSON.stringify({profile:null, posts:[], err:String(e)}); }
    var d = null;
    try{ d = JSON.parse(out); }catch(e){ resolve(out); return; }
    var ok = d.profile && d.profile.followers;
    var norm = d.posts.filter(function(p){ return !p.pinned; });
    var latest = norm.length ? norm[0] : null;
    var engCnt = latest ? Object.keys(latest.eng).length : 0;
    if(ok && norm.length>=20 && engCnt>=3){ resolve(out); return; }
    if(tries>=MAX_TRY){ resolve(out); return; }
    setTimeout(attempt, 2000);
  }
  attempt();
});
}"""


# ---------------------------------------------------------------------------
# Python 结构化（返回 dict，与数据源契约一致；缺失字段 None，UNKNOWN 纪律）
# ---------------------------------------------------------------------------


def build_stats(extracted: str) -> dict | None:
    """JS 提取结果 → ``{account_name, handle, follower_count, posts}``。

    posts 为互动序列：``[{likes, reposts, comments, views, time}]``（页面原样文本），
    按相对时间升序（**最新在末尾**，与 mock/信号层序列契约一致）；置顶帖排除；
    无任何推文 → None。
    """
    try:
        data = json.loads(extracted)
    except Exception:
        return None
    prof = data.get("profile") or {}
    posts = data.get("posts") or []
    normal = [p for p in posts if not p.get("pinned")] or posts
    if not normal:
        return None
    normal.sort(key=lambda p: _rel_ago(p.get("rel") or ""), reverse=True)  # 旧→新
    out: list[dict] = []
    for p in normal[:40]:
        eng = p.get("eng") or {}
        out.append(
            {
                "likes": eng.get("likes"),
                "reposts": eng.get("reposts"),
                "comments": eng.get("replies"),
                "views": eng.get("views"),
                "time": p.get("rel"),
            }
        )
    return {
        "account_name": prof.get("name"),
        "handle": prof.get("handle"),
        "follower_count": prof.get("followers"),
        "posts": out,
    }


def post_frequency_days(posts: list[dict] | None) -> float | None:
    """平均发帖间隔（天）：时间跨度 / (n-1)；可解析时间 <2 条 → None。

    posts 为 build_stats 输出的互动序列（时间升序，最新在末尾）。
    """
    secs = [_rel_ago(p.get("time")) for p in posts or []]
    secs = [s for s in secs if s != float("inf")]
    if len(secs) < 2:
        return None
    span = max(secs) - min(secs)
    if span <= 0:
        return None
    return round(span / (len(secs) - 1) / 86400.0, 2)


# ---------------------------------------------------------------------------
# 有头浏览器抓取（x.com 反爬：headless 必 403，必须 DISPLAY=:0）
# ---------------------------------------------------------------------------


async def fetch_handle_stats(handle: str) -> dict | None:
    """有头浏览器 + 确定性 JS 提取指定 X 账号的数据 → build_stats dict。"""
    from browser_use.browser.session import (
        BrowserSession,  # 延迟导入（mock 模式零依赖）
    )

    profile_url = f"https://x.com/{handle}"
    browser = BrowserSession(
        channel="chromium",
        headless=False,
        enable_default_extensions=False,
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
        args=[
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    try:
        await browser.start()
        page = await browser.get_current_page()
        if page is None:
            page = await browser.new_page()
        for attempt in range(3):  # x.com 偶发导航超时，重试
            try:
                await page.goto(profile_url)
                break
            except Exception:
                await asyncio.sleep(3)
        # JS 内部滚动加载 + 自旋等待数据完整（最多约 24 秒）
        try:
            result = await page.evaluate(EXTRACT_JS)
        except Exception:
            return None  # 页面崩溃/导航中断等 evaluate 级异常：失败即失败
        return build_stats(result)
    finally:
        await browser.stop()


def fetch_x_stats(
    coin: str, handle: str | None = None, client: httpx.Client | None = None
) -> dict | None:
    """组合入口：粉丝数 + 近 30 条推文互动序列；失败返回 None。

    - ``SR_MOCK=1`` → ``mock.mock_x_stats``（零外部请求）
    - ``handle`` 未给时经 ``resolve_handle`` 解析（CoinGecko/CoinPaprika）
    - 解析/抓取/结构化任一失败 → None（数据源契约，失败即失败不回退 mock）
    """
    if env.is_mock_mode():
        return mock.mock_x_stats(coin)
    if handle is None:
        handle = resolve_handle(coin, client)
        if not handle:
            return None
    return asyncio.run(fetch_handle_stats(handle))
