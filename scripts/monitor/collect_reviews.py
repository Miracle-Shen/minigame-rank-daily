#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""玩家评论采集（只采本期准备展开的 1–2 款）。

## 为什么只采 1–2 款

全量抓 50 款游戏的评论，成本高、失败面大，而且**用不上** ——
群消息每期最多展开 2 款，其余只有名次数据。所以这里先复用
story.py 的候选排序，取排在最前面的 N 款（默认 2 款）再去取评论。

## 数据源口径

  优先用「公开且可验证、能拿到原始评论链接」的来源：

    appstore    iTunes 客户评论 RSS（无需登录，评论自带 review 链接与日期）
    taptap      TapTap 公开 Web 接口（尽力而为，接口稳定时就取）
    googleplay  Google Play 应用页（Google 已改为动态加载，多数情况取不到）

  微信 / 抖音小游戏**没有**公开可验证的评论来源，直接留空 ——
  宁可这一期不写「玩家」那一行，也不拿商店总评分或第三方转载凑。

## 匹配规则（不合规就不用）

  * 先按归一化名（与产品档案管线同一套）在**当期快照**里找到这款游戏，
    再取它的平台 ID（优先产品档案 identity.platform_app_ids，其次榜单行的 app_id/package）；
  * 没有任何平台 ID → 该来源直接跳过，不猜、不搜索；
  * 只保留最近 30 天、有正文、评分合法的评论。

## 产出

  data/reviews/YYYY-MM-DD.json（日期 = 周报 week_end）

失败不阻断：所有网络异常都降级成 `sources[].error` 记录，文件照常写出，
没有评论就是没有评论 —— 下游渲染层自然不写玩家反馈。

命令行：
    python scripts/monitor/collect_reviews.py --dry-run       # 只看会采谁
    python scripts/monitor/collect_reviews.py                 # 采最新一期
    python scripts/monitor/collect_reviews.py --max-games 1 --only appstore
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analyze as A  # noqa: E402
import clone as CL  # noqa: E402
import reviews as RV  # noqa: E402
import story as S  # noqa: E402

ROOT = HERE.parents[1]
OUT_DIR = ROOT / "data" / "reviews"
BEIJING = timezone(timedelta(hours=8))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 8
PER_SOURCE_LIMIT = 40      # 单个来源最多取多少条（够统计，也够人工抽查）
MAX_AGE_DAYS = RV.MAX_AGE_DAYS

# 来源 → 它对应的平台 ID 键
SOURCE_PLATFORM = {"appstore": "ios", "googleplay": "android", "taptap": "taptap"}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _get(url: str, timeout: int = TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _day(s: str) -> str:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    return m.group(0) if m else ""


# --------------------------------------------------------------------------
# 各来源
# --------------------------------------------------------------------------
def fetch_appstore(app_id: str, country: str = "cn") -> tuple[list[dict], str]:
    """iTunes 客户评论 RSS。返回 (评论列表, 错误说明)。"""
    url = (f"https://itunes.apple.com/{country}/rss/customerreviews/"
           f"page=1/id={app_id}/sortby=mostrecent/json")
    try:
        raw = _get(url)
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"

    entries = ((data.get("feed") or {}).get("entry") or [])
    if isinstance(entries, dict):
        entries = [entries]

    out = []
    app_page = f"https://apps.apple.com/{country}/app/id{app_id}?see-all=reviews"
    for e in entries:
        rating = ((e.get("im:rating") or {}).get("label") or "").strip()
        if not rating:            # 第一条是 App 自身信息，不是评论
            continue
        text = ((e.get("content") or {}).get("label") or "").strip()
        link = ((e.get("id") or {}).get("label") or "")
        out.append({
            "id": str(link or ((e.get("id") or {}).get("label") or ""))[:120],
            "source": "appstore",
            "platform": "ios",
            "platform_id": str(app_id),
            "date": _day((e.get("updated") or {}).get("label") or ""),
            "rating": int(rating),
            "title": ((e.get("title") or {}).get("label") or "").strip(),
            "text": text,
            # RSS 的 id 字段本身就是该条评论的官方链接；不是 URL 时退回 App 评论页
            "url": link if link.startswith("http") else app_page,
            "url_type": "review" if link.startswith("http") else "app_reviews_page",
            "fetched_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        })
    if not out:
        return [], "RSS 无评论条目（可能该国区无评论）"
    return out[:PER_SOURCE_LIMIT], ""


def fetch_taptap(app_id: str) -> tuple[list[dict], str]:
    """TapTap 公开 Web 接口 —— 无稳定契约，取不到就留空。"""
    url = (f"https://www.taptap.cn/webapiv2/review/v1/by-app"
           f"?app_id={app_id}&from=0&limit={PER_SOURCE_LIMIT}&sort=default")
    try:
        data = json.loads(_get(url))
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"

    items = ((data.get("data") or {}).get("list")
             or (data.get("data") or {}).get("review_list") or [])
    out = []
    for it in items:
        rv = it.get("review") or it
        text = ((rv.get("contents") or {}).get("text")
                or rv.get("text") or "").strip()
        score = rv.get("score") or rv.get("rating")
        if not text or not score:
            continue
        rid = str(rv.get("id") or "")
        out.append({
            "id": rid,
            "source": "taptap",
            "platform": "taptap",
            "platform_id": str(app_id),
            "date": _day(str(rv.get("created_time") or rv.get("created_at") or "")),
            "rating": int(score),
            "title": "",
            "text": text,
            "url": (f"https://www.taptap.cn/app/{app_id}/review"
                    + (f"/{rid}" if rid else "")),
            "url_type": "review" if rid else "app_reviews_page",
            "fetched_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        })
    if not out:
        return [], "接口未返回可解析的评论（TapTap 无公开稳定接口，本次留空）"
    return out[:PER_SOURCE_LIMIT], ""


def fetch_googleplay(package: str) -> tuple[list[dict], str]:
    """Google Play 应用页 —— 评论已改为动态加载，通常取不到。"""
    url = (f"https://play.google.com/store/apps/details"
           f"?id={package}&hl=zh_CN&gl=CN")
    try:
        html = _get(url)
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"
    if "AF_initDataCallback" not in html:
        return [], "页面结构不含评论数据"
    return [], "Google Play 评论改为动态加载，静态页取不到（本次留空）"


FETCHERS = {"appstore": fetch_appstore, "taptap": fetch_taptap,
            "googleplay": fetch_googleplay}


# --------------------------------------------------------------------------
# 选游戏 + 匹配平台 ID
# --------------------------------------------------------------------------
def _all_platform_rows(snap: dict) -> dict:
    """{归一化名: {"row": 行, "plats": set}} —— 覆盖快照里的全部平台。

    候选池只看微信/抖音/TapTap，但评论要落到 App Store / Google Play 的 ID 上，
    所以取 ID 时必须回到**全平台**原始快照找。
    """
    out: dict[str, dict] = {}
    for pk, p in (snap.get("platforms") or {}).items():
        for b in p.get("boards") or []:
            for r in b.get("rows") or []:
                n = r.get("name")
                if not n:
                    continue
                key = CL.norm_name(n)
                e = out.setdefault(key, {"row": r, "plats": set(), "ids": {}})
                e["plats"].add(pk)
                # ID 必须跟着平台走：微信/抖音的 app_id 是小游戏 ID，
                # 当成 App Store 的 id 去查会查到完全无关的应用。
                if pk == "ios" and r.get("app_id"):
                    e["ids"].setdefault("ios", str(r["app_id"]))
                if pk == "android" and r.get("package"):
                    e["ids"].setdefault("android", str(r["package"]))
    return out


def resolve_ids(name: str, detail: dict, rows: dict) -> dict:
    """这款游戏在哪些平台有可核对的 ID：{platform: id}。

    ID 只有两个来源，都是「同一款游戏」的强约束：
      1. 产品档案 identity.platform_app_ids（建档时核对过的）；
      2. 当期快照里同名行的 app_id / package（榜单自带的应用标识）。
    两边都没有 = 不猜、不按名字搜索，评论直接留空。
    """
    ids: dict[str, str] = {}
    for plat, v in ((detail.get("identity") or {}).get("platform_app_ids") or {}).items():
        if v:
            ids[str(plat)] = str(v)

    for plat, v in ((rows.get(CL.norm_name(name)) or {}).get("ids") or {}).items():
        ids.setdefault(plat, v)
    return {k: v for k, v in ids.items() if v}


def pick_games(ctx: S.WeeklyContext, limit: int, rows: dict,
               index: dict) -> list[dict]:
    """按 story 优先级挑出本期要采评论的几款。"""
    cache: dict = {}
    out = []
    for c in S.detect_candidates(ctx):
        detail = CL.detail_of(c["name"], index, cache)
        play = CL.play_of(detail)
        has_play = bool(play) and play != "暂无产品档案"
        ids = resolve_ids(c["name"], detail, rows)
        # 排序与 hotlist 完全一致：采的就是本期要展开的那 1–2 款，
        # 不因为「有没有商店 ID」改变「谁值得说」。
        score = S.priority(c, has_reviews=False, has_play=has_play)
        out.append({**c, "detail": detail, "ids": ids, "score": score,
                    "archive_name": (detail.get("name") or "")})
    out.sort(key=lambda x: (-x["score"], x["cur_rank"]))
    return out[:limit]


# --------------------------------------------------------------------------
def collect(games: list[dict], week_end: str,
            only: list[str] | None = None) -> list[dict]:
    only = only or list(FETCHERS)
    result = []
    for g in games:
        entry = {
            "name": g["name"],
            "aliases": [g["archive_name"]] if g["archive_name"]
                       and CL.norm_name(g["archive_name"]) != CL.norm_name(g["name"]) else [],
            "matched": bool(g["ids"]),
            "platform_ids": g["ids"],
            "sources": [],
            "reviews": [],
            "notes": [] if g["ids"] else ["没有可核对的平台 ID，评论留空"],
        }
        for source in only:
            plat = SOURCE_PLATFORM[source]
            pid = g["ids"].get(plat)
            if not pid:
                entry["sources"].append({
                    "source": source, "platform": plat, "ok": False,
                    "fetched": 0, "kept": 0, "error": "无该平台 ID"})
                continue
            rows, err = FETCHERS[source](pid)
            kept = _filter_recent(rows, week_end)
            entry["sources"].append({
                "source": source, "platform": plat, "ok": bool(rows),
                "fetched": len(rows), "kept": len(kept), "error": err})
            entry["reviews"].extend(kept)
        result.append(entry)
    return result


def _filter_recent(rows: list[dict], as_of: str) -> list[dict]:
    end = datetime.strptime(as_of, "%Y-%m-%d")
    out, seen = [], set()
    for r in rows:
        d = r.get("date") or ""
        try:
            age = (end - datetime.strptime(d, "%Y-%m-%d")).days
        except Exception:  # noqa: BLE001
            continue
        if age < 0 or age > MAX_AGE_DAYS:
            continue
        rid = str(r.get("id") or "")
        if rid and rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="玩家评论采集（只采 1–2 款）")
    ap.add_argument("--report", default=None, help="周报 JSON（默认最新一期）")
    ap.add_argument("--max-games", type=int, default=2, help="最多采几款（默认 2）")
    ap.add_argument("--only", default="", help="只跑某些来源，逗号分隔")
    ap.add_argument("--out", default=None, help="输出路径（默认 data/reviews/<week_end>.json）")
    ap.add_argument("--dry-run", action="store_true", help="只打印会采谁，不发请求")
    args = ap.parse_args()

    report = Path(args.report) if args.report else _latest_report()
    data = json.loads(report.read_text(encoding="utf-8"))
    meta = data.get("meta") or {}
    week_end = meta.get("week_end") or ""
    if not week_end:
        print("周报缺少 meta.week_end", file=sys.stderr)
        return 2

    hist = A.load_history()
    ctx = S.WeeklyContext(hist, week_end, meta.get("baseline_date"))
    snap = ctx.cur_snap
    rows = _all_platform_rows(snap)
    index = CL.load_index()
    games = pick_games(ctx, max(1, args.max_games), rows, index)

    print(f"周报 {report.name}（{ctx.base_date} → {week_end}）")
    print(f"候选 {len(S.detect_candidates(ctx))} 款，本期采评论的：")
    for g in games:
        ids = "、".join(f"{k}={v}" for k, v in g["ids"].items()) or "无平台 ID（评论将留空）"
        print(f"  · {g['name']}　事件 {','.join(g['events'])}　{ids}")

    if args.dry_run:
        print("（dry-run，未抓取）")
        return 0

    only = [s.strip() for s in args.only.split(",") if s.strip()] or None
    entries = collect(games, week_end, only)

    blob = {
        "date": week_end,
        "collected_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "period": {"week_end": week_end, "baseline_date": ctx.base_date},
        "max_age_days": MAX_AGE_DAYS,
        "min_valid": RV.MIN_VALID,
        "sources": list(only or FETCHERS),
        "games": entries,
        "policy": ("只采本期展开的 1–2 款；只保留最近 30 天；"
                   "有效评论 < %d 条不生成玩家反馈；微信/抖音无公开可验证评论来源，留空。"
                   % RV.MIN_VALID),
    }
    for e in entries:
        e["summary"] = RV.summarize(e, week_end)

    out = Path(args.out) if args.out else RV.path_for(week_end, OUT_DIR)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    ok_n = sum(1 for e in entries if e["summary"]["enough"])
    print(f"\n已写出 {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    for e in entries:
        brief = "、".join(f"{s['source']}:{s['kept']}条" + (f"（{s['error']}）"
                                                          if s["error"] else "")
                         for s in e["sources"])
        print(f"  · {e['name']}　{brief}")
        if e["summary"]["sentence"]:
            print(f"    {e['summary']['sentence']}")
    print(f"可生成玩家反馈：{ok_n}/{len(entries)} 款"
          f"（阈值 {RV.MIN_VALID} 条；不足则消息里不写玩家态度）")

    # 全部为空是常态（小游戏多无 App Store / GP 条目），用 warning 提示但不失败
    if not any(e["reviews"] for e in entries):
        print("::warning::本期没有采到可核对的评论，群消息不写玩家反馈（不阻断推送）")
    return 0


def _latest_report() -> Path:
    import report as R  # noqa: PLC0415

    files = sorted(R.REPORT_DIR.glob("weekly-*.json"))
    if not files:
        raise SystemExit("reports/ 下没有周报 JSON，请先跑 report.py")
    return files[-1]


if __name__ == "__main__":
    sys.exit(main())
