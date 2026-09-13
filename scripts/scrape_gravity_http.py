#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引力引擎榜单抓取（纯 HTTP，无浏览器 / 无登录态依赖）。

## 为什么有这个模块

项目原本用 Playwright 渲染 https://rank.gravity-engine.com/ 抓微信 / 抖音小游戏榜单。
但该站是 SPA，抓取依赖浏览器加载 + DOM 结构，实际运行不稳定：

    [warn] 引力引擎 goto 第1次失败(Error)，重试…
    [warn] 引力引擎 goto 第2次失败(Error)，重试…
    [warn] 引力引擎 goto 第3次失败(Error)，重试…
    [引力引擎] 抓取失败，降级为仅独立源快照（taptap/ios）:
      Page.goto: net::ERR_CONNECTION_CLOSED at https://rank.gravity-engine.com/#/

一次失败就会让当天的微信 / 抖音数据整块缺失。本模块改为直接调用站点
自身使用的公开接口：纯 HTTP、无浏览器、无登录态、单榜毫秒级返回。

## 接口逆向说明（改代码前必读）

    POST https://api-insight.gravity-engine.com/apprank/api/v1/rank/public_list/

请求需要三个自定义头，值全部在前端计算，服务端不下发任何密钥：

    gravity-timestamp = 毫秒时间戳
    gravity-session   = base64("etg" + 5 位随机数字)     例：ZXRnNzUxNjY= → "etg75166"
    gravity-signature = MD5( str(ts)[3:8] + "11" + session + JSON.stringify(body) )

  - `str(ts)[3:8]` 对应 JS 的 `String(ts).slice(3, 8)`
  - body 必须是**紧凑 JSON**（separators=(",", ":")），键顺序与发送一致
  - MD5 取十六进制小写

响应体的 `data.text` 是 base64 密文，AES-ECB + PKCS7：

    key = g + "gv" + str(ts)[7:11] + "00"    # g = session 的 base64 原文
    明文 = JSON

密钥由本次请求自己的时间戳与 session 派生，请求方完全已知，无需协商。

## 匿名限制

服务端对匿名请求**强制只返回 page=1**，即每榜 TOP20。已验证：改 body 的 page、
加 URL query、只传 query、用 page_num 别名，返回的都是同一批 20 行
（`page_info.total_number` 仍显示 100，但翻不动——是服务端限制，非前端截断）。

要 TOP100 需要登录态（带 Authorization 头）。本项目按「稳定优先」取舍，
接受匿名 TOP20：匿名接口不依赖任何会过期的凭证，是长期无人值守最稳的路径。

## change_direction 从哪来

站点接口不返回涨跌方向，只给 `change`（数字）与 `change_label.first_msg`
（通常只有霸榜时才有值）。本模块用「今日排名 vs 上一份 daily 快照」自行计算
方向，比原先解析 SVG path 颜色的做法更可靠，也顺带保证与 data/diff 的新进口径一致。

依赖：pycryptodome（AES 解密）。安装：
    pip install pycryptodome
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api-insight.gravity-engine.com/apprank/api/v1/rank/public_list/"
ORIGIN = "https://rank.gravity-engine.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DAILY_DIR = ROOT / "data" / "daily"

BEIJING_TZ = timezone(timedelta(hours=8))

# 榜单枚举（由服务端报错信息反查得到，勿臆造）
#   rank_genre: overall | wx_minigame | dy_minigame
#   rank_type : popularity | free | bestseller | most_played | fresh_game
BOARDS = {
    "wx": {
        "label": "微信小游戏",
        "genre": "wx_minigame",
        "types": [("popularity", "人气榜"),
                  ("bestseller", "畅销榜"),
                  ("most_played", "畅玩榜")],
    },
    "douyin": {
        "label": "抖音小游戏",
        "genre": "dy_minigame",
        "types": [("popularity", "热门榜"),
                  ("bestseller", "畅销榜"),
                  ("fresh_game", "新游榜")],
    },
}

PAGE_SIZE = 20  # 服务端固定上限，改大无效


# --------------------------------------------------------------------------
# 请求签名 + 响应解密
# --------------------------------------------------------------------------
def _build_headers(timestamp: int, session: str, body_json: str) -> dict:
    sig = hashlib.md5(
        (str(timestamp)[3:8] + "11" + session + body_json).encode()
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN",
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/",
        "User-Agent": UA,
        "gravity-timestamp": str(timestamp),
        "gravity-session": session,
        "gravity-signature": sig,
    }


def _unpad(data: bytes) -> bytes:
    if not data:
        return data
    n = data[-1]
    return data[:-n] if 1 <= n <= 16 and data[-n:] == bytes([n]) * n else data


def _decrypt(text_b64: str, g: str, timestamp: int) -> dict:
    try:
        from Crypto.Cipher import AES
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "需要 pycryptodome：pip install pycryptodome") from e
    key = (g + "gv" + str(timestamp)[7:11] + "00").encode()
    plain = _unpad(AES.new(key, AES.MODE_ECB).decrypt(base64.b64decode(text_b64)))
    return json.loads(plain.decode("utf-8"))


def fetch_page(rank_type: str, genre: str = "wx_minigame", page: int = 1,
               timeout: int = 30) -> dict:
    """请求单页并解密，返回含 list / page_info 的 dict。"""
    timestamp = int(time.time() * 1000)
    g = "etg" + "".join(random.choices("0123456789", k=5))
    session = base64.b64encode(g.encode()).decode()

    body = {
        "page": page,
        "page_size": PAGE_SIZE,
        "extra_fields": {"change_label": True, "app_genre_ranking": True},
        "filters": [
            {"field": "rank_type", "operator": 1, "values": [rank_type]},
            {"field": "rank_genre", "operator": 1, "values": [genre]},
        ],
    }
    body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    req = urllib.request.Request(
        API, data=body_json.encode(), method="POST",
        headers=_build_headers(timestamp, session, body_json))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]!r}") from e

    text = (resp.get("data") or {}).get("text")
    if not text:
        if resp.get("code") == 0:
            return {"list": [], "page_info": {}}
        raise RuntimeError(f"接口返回异常 code={resp.get('code')} msg={resp.get('msg')}")
    return _decrypt(text, g, timestamp)


def fetch_board(rank_type: str, genre: str,
                max_pages: int = 3) -> tuple[list[dict], dict]:
    """抓一个榜单。匿名态服务端只认 page=1，靠 app_id 去重自然收敛到 20 行。"""
    rows: list[dict] = []
    seen: set = set()
    page_info: dict = {}
    for page in range(1, max_pages + 1):
        data = fetch_page(rank_type, genre, page)
        page_info = data.get("page_info") or page_info
        batch = data.get("list") or []
        fresh = [r for r in batch if r.get("app_id") not in seen]
        if not fresh:
            break
        for r in fresh:
            seen.add(r["app_id"])
            rows.append(r)
        if page >= (page_info.get("total_page") or 1):
            break
        time.sleep(0.3)
    return sorted(rows, key=lambda r: r.get("ranking") or 9999), page_info


# --------------------------------------------------------------------------
# 涨跌方向：用「今日 vs 上一份快照」算，不依赖站点图标
# --------------------------------------------------------------------------
def load_prev_ranks(daily_dir: Path, today_label: str) -> dict:
    """读取日期严格早于 today_label 的最近一份 daily 快照，
    返回 {("wx/人气榜"): {game_name: rank}, ...}。没有历史则返回空 dict。"""
    if not daily_dir.exists():
        return {}
    files = sorted(p for p in daily_dir.glob("*.json") if p.stem < today_label)
    if not files:
        return {}
    snap = json.loads(files[-1].read_text(encoding="utf-8"))
    out: dict = {}
    for plat_key, plat in (snap.get("platforms") or {}).items():
        for board in plat.get("boards") or []:
            key = f"{plat_key}/{board.get('label')}"
            out[key] = {
                r.get("name"): r.get("rank")
                for r in (board.get("rows") or []) if r.get("name")
            }
    return out


def calc_direction(change_label: dict | None, prev_rank, cur_rank) -> tuple[str, str]:
    """返回 (change_direction, change_text)，与原 Playwright 版语义一致：

        top  霸榜          change = "霸榜N天"
        new  本榜首次出现   change = ""            （前端渲染为「新」）
        up   名次上升       change = "3"           （前端渲染为 ▼ 3）
        down 名次下降       change = "2"           （前端渲染为 ▲ 2）
        flat 名次不变       change = "- 稳定"
    """
    first_msg = (change_label or {}).get("first_msg") or ""
    if "霸榜" in first_msg:
        return "top", first_msg
    if prev_rank is None:
        return "new", ""
    if prev_rank == cur_rank:
        return "flat", "- 稳定"
    if cur_rank < prev_rank:
        return "up", str(prev_rank - cur_rank)
    return "down", str(cur_rank - prev_rank)


def normalize_row(raw: dict, prev_ranks: dict, board_key: str) -> dict:
    """把接口原始记录拍平成项目 daily 快照的行结构。"""
    info = raw.get("app_info") or {}
    genre_rank = raw.get("app_genre_ranking") or {}
    rank = raw.get("ranking")
    name = info.get("app_name") or ""
    prev_rank = prev_ranks.get(board_key, {}).get(name)
    direction, change_text = calc_direction(raw.get("change_label"), prev_rank, rank)
    return {
        "rank": rank,
        "name": name,
        "category": info.get("game_type_main_name") or "",
        "category_rank": genre_rank.get("ranking"),
        "subcategory": info.get("game_type_sub_name") or "",
        "slogan": info.get("subtitle") or "",
        "publisher": info.get("publisher_name") or "",
        "change": change_text,
        "change_direction": direction,
        # 以下两个是接口独有、原 Playwright 版拿不到的稳定标识，供分析层使用
        "app_id": info.get("mini_app_id") or str(raw.get("app_id") or ""),
        "publisher_id": info.get("publisher_id"),
    }


# --------------------------------------------------------------------------
# 主入口：产出与 core.do_scrape 完全一致的结构
# --------------------------------------------------------------------------
def scrape_platform(plat_key: str, top_n: int, prev_ranks: dict, log=print) -> dict:
    cfg = BOARDS[plat_key]
    boards_out = []
    for rank_type, label in cfg["types"]:
        board_key = f"{plat_key}/{label}"
        try:
            raw_rows, info = fetch_board(rank_type, cfg["genre"])
        except Exception as e:
            log(f"  [{cfg['label']} / {label}] 抓取失败，跳过: {e}")
            boards_out.append({"label": label, "rows": []})
            continue
        rows = [normalize_row(r, prev_ranks, board_key) for r in raw_rows][:top_n]
        boards_out.append({"label": label, "rows": rows})
        stamp = raw_rows[0].get("stat_datetime") if raw_rows else "?"
        log(f"  {cfg['label']} / {label}: {len(rows)} rows "
            f"(数据日期 {stamp}, 服务端声明 {info.get('total_number')})")
    return {"label": cfg["label"], "boards": boards_out}


def do_scrape(top_n: int = 20, daily_dir: Path | None = None, log=print) -> dict:
    """抓取微信 + 抖音全部榜单，返回与 scrape_rank.do_scrape 同构的 data dict。"""
    daily_dir = daily_dir or DAILY_DIR
    now_bj = datetime.now(BEIJING_TZ)
    today_label = now_bj.strftime("%Y-%m-%d")
    prev_ranks = load_prev_ranks(daily_dir, today_label)

    data = {
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "period": "日榜",
        "logged_in": False,
        "top_n_target": top_n,
        "source": API,
        "platforms": {},
        "prev_snapshot_used": bool(prev_ranks),
    }
    for plat_key in BOARDS:
        data["platforms"][plat_key] = scrape_platform(
            plat_key, top_n, prev_ranks, log=log)
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="引力引擎榜单抓取（纯 HTTP）")
    ap.add_argument("--top-n", type=int, default=20,
                    help="每榜保留行数（匿名上限 20）")
    ap.add_argument("--platform", default="all",
                    help="wx | douyin | all")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = ap.parse_args()

    if args.platform != "all":
        keep = {args.platform: BOARDS[args.platform]}
        globals()["BOARDS"] = keep  # 临时收窄，便于单平台调试

    data = do_scrape(top_n=args.top_n)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    for plat_key, plat in data["platforms"].items():
        for b in plat["boards"]:
            print(f"\n===== {plat['label']} / {b['label']} "
                  f"({len(b['rows'])} 条) =====")
            for r in b["rows"]:
                print(f"  {r['rank']:>3}  {r['name']:<20} "
                      f"{(r['category'] or '-'):<5}{(r['subcategory'] or '-'):<9} "
                      f"{(r['publisher'] or '-'):<24} "
                      f"{r['change_direction']:<5} {r['change']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
