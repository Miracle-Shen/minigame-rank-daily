#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成详情页工作清单：data/detail/_worklist.json

做四件事（都不重跑已有成果）：

1. 从最新 daily 快照取「今日在榜」游戏及其平台/榜单/名次；
2. 从 data/base/games.json 取历史表现（首次上榜、上榜天数、最佳名次）；
3. 从 Supabase game_profiles 取已有档案（价值评级、玩法关键词、标签）
   —— 这些是**线索**，不是成品，会作为 seed 喂给调研；
4. 调引力引擎接口拿官方 icon_url（顺带补全品类副类）。

最后按 `data/detail/<slug>.json` 是否已存在判断 status：done 的直接跳过。

用法：
    python scripts/detail/build_worklist.py                  # 今日在榜
    python scripts/detail/build_worklist.py --source recent7 # 近 7 天在榜
    python scripts/detail/build_worklist.py --all-history    # 全历史
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))

from schema import norm_name, slug  # noqa: E402

DAILY_DIR = ROOT / "data" / "daily"
BASE_DIR = ROOT / "data" / "base"
DETAIL_DIR = ROOT / "data" / "detail"
WORKLIST = DETAIL_DIR / "_worklist.json"
ICON_CACHE = DETAIL_DIR / "_icons.json"

BEIJING = timezone(timedelta(hours=8))


# --------------------------------------------------------------------------
# Supabase（读已有档案，只读）
# --------------------------------------------------------------------------
def load_supabase_profiles() -> dict[str, dict]:
    cfg = (ROOT / "site" / "config.js").read_text(encoding="utf-8")
    url = re.search(r'SUPABASE_URL:\s*"([^"]+)"', cfg)
    key = re.search(r'SUPABASE_KEY:\s*"([^"]+)"', cfg)
    if not (url and key):
        return {}
    endpoint = (f"{url.group(1)}/rest/v1/game_profiles"
                "?select=game_name,developer,gameplay_desc,tags,notes,"
                "value,abandoned,abandon_reason,favorite,updated_at&limit=5000")
    req = urllib.request.Request(endpoint, headers={
        "apikey": key.group(1), "Authorization": f"Bearer {key.group(1)}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 读 Supabase 档案失败，按空处理：{type(e).__name__}: {e}")
        return {}
    return {norm_name(r["game_name"]): r for r in rows if r.get("game_name")}


# --------------------------------------------------------------------------
# 官方图标（引力引擎接口直出）
# --------------------------------------------------------------------------
def fetch_icon_map(log=print) -> dict[str, dict]:
    """返回 {游戏名: {icon_url, main_type, sub_type, app_ids:{...}}}。"""
    if ICON_CACHE.exists():
        try:
            cached = json.loads(ICON_CACHE.read_text(encoding="utf-8"))
            if cached.get("games"):
                log(f"[icon] 命中缓存 {len(cached['games'])} 条")
                return cached["games"]
        except Exception:  # noqa: BLE001
            pass

    import scrape_gravity_http as G

    out: dict[str, dict] = {}
    jobs = []
    for plat, spec in G.BOARDS.items():
        for rtype, label in spec["types"]:
            jobs.append((plat, spec["genre"], rtype, label))

    for plat, genre, rtype, label in jobs:
        try:
            data = G.fetch_page(rtype, genre, 1)
        except Exception as e:  # noqa: BLE001
            log(f"[icon] {plat}/{label} 拉取失败：{type(e).__name__}: {e}")
            continue
        rows = data.get("list") or []
        for row in rows:
            info = row.get("app_info") or {}
            name = norm_name(info.get("app_name"))
            if not name:
                continue
            rec = out.setdefault(name, {"icon_url": "", "main_type": "",
                                        "sub_type": "", "app_ids": {}})
            if info.get("icon_url") and not rec["icon_url"]:
                rec["icon_url"] = info["icon_url"]
            if info.get("game_type_main_name"):
                rec["main_type"] = info["game_type_main_name"]
            if info.get("game_type_sub_name"):
                rec["sub_type"] = info["game_type_sub_name"]
            if info.get("mini_app_id"):
                rec["app_ids"][plat] = info["mini_app_id"]
        log(f"[icon] {plat}/{label}: {len(rows)} 行")

    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    ICON_CACHE.write_text(json.dumps(
        {"updated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
         "games": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"[icon] 共 {len(out)} 款，已写 {ICON_CACHE.relative_to(ROOT)}")
    return out


# --------------------------------------------------------------------------
# 快照 / 底库
# --------------------------------------------------------------------------
def collect_from_snapshots(days: int | None) -> dict[str, dict]:
    """{游戏名: {boards:[...], ranks:{board:rank}, category, subcategory,
                slogan, publisher, app_ids:{}}}"""
    files = sorted(DAILY_DIR.glob("*.json"))
    if not files:
        raise SystemExit("data/daily 下没有快照，先跑一次抓取")
    files = files[-days:] if days else files

    out: dict[str, dict] = {}
    for fp in files:
        try:
            snap = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for plat, pv in (snap.get("platforms") or {}).items():
            for bd in pv.get("boards") or []:
                board_key = f"{plat}/{bd.get('label')}"
                for row in bd.get("rows") or []:
                    name = norm_name(row.get("name"))
                    if not name:
                        continue
                    rec = out.setdefault(name, {
                        "boards": [], "ranks": {}, "category": "",
                        "subcategory": "", "slogan": "", "publisher": "",
                        "app_ids": {}, "seen_dates": [],
                    })
                    if board_key not in rec["boards"]:
                        rec["boards"].append(board_key)
                    rec["ranks"][board_key] = row.get("rank")
                    if snap.get("date_beijing"):
                        rec["seen_dates"].append(snap["date_beijing"])
                    for src, dst in (("category", "category"),
                                     ("subcategory", "subcategory"),
                                     ("slogan", "slogan"),
                                     ("publisher", "publisher")):
                        if not rec[dst] and row.get(src):
                            rec[dst] = row[src]
                    if row.get("app_id"):
                        rec["app_ids"].setdefault(plat, str(row["app_id"]))
    return out


def load_base() -> dict:
    p = BASE_DIR / "games.json"
    if not p.exists():
        return {}
    raw = (json.loads(p.read_text(encoding="utf-8")) or {}).get("games") or {}
    return {norm_name(k): v for k, v in raw.items()}


def existing_details() -> dict[str, dict]:
    """已收集的详情 -> {游戏名: {slug, updated_at, collected}}"""
    out = {}
    for fp in DETAIL_DIR.glob("*.json"):
        if fp.name.startswith("_"):
            continue
        try:
            rec = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if rec.get("name"):
            out[norm_name(rec["name"])] = rec
    return out


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="today",
                    choices=["today", "recent7", "all"])
    ap.add_argument("--all-history", action="store_true",
                    help="等价于 --source all")
    ap.add_argument("--no-icons", action="store_true", help="跳过图标抓取")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    src = "all" if args.all_history else args.source
    days = {"today": 1, "recent7": 7, "all": None}[src]

    print(f"=== 生成工作清单（范围={src}）===")
    games = collect_from_snapshots(days)
    print(f"榜单覆盖游戏：{len(games)} 款")

    base = load_base()
    profiles = load_supabase_profiles()
    print(f"底库 games.json：{len(base)} 款 | Supabase 已有档案：{len(profiles)} 份")

    icons = {} if args.no_icons else fetch_icon_map()

    done = existing_details()
    print(f"已收集详情（将跳过）：{len(done)} 款")

    entries = []
    for name, info in games.items():
        b = base.get(name) or {}
        prof = profiles.get(name) or {}
        icon = icons.get(name) or {}
        hist = b.get("board_history") or {}

        best_rank = min([v.get("best_rank") for v in hist.values()
                         if v.get("best_rank")], default=None)
        appearances = max([v.get("appearances") or 0 for v in hist.values()],
                          default=0)
        l2 = ""
        if icon.get("sub_type"):
            l2 = icon["sub_type"]
        elif info.get("subcategory"):
            l2 = info["subcategory"]

        has = name in done
        entry = {
            "name": name,
            "slug": slug(name),
            "status": "done" if has else "pending",
            "icon_url": icon.get("icon_url") or "",
            "boards": sorted(info["boards"]),
            "ranks": info["ranks"],
            "platform_app_ids": {**info["app_ids"], **(icon.get("app_ids") or {})},
            "publisher": info.get("publisher") or (
                (b.get("publishers") or [""])[0] if b.get("publishers") else ""),
            "category_l1": icon.get("main_type") or info.get("category") or "",
            "category_l2": l2,
            "first_seen": b.get("first_seen_anywhere") or "",
            "appearances": appearances,
            "best_rank": best_rank,
            "seen_days": len(set(info.get("seen_dates") or [])),
            "profile_value": prof.get("value") or "",
            "profile_abandoned": bool(prof.get("abandoned")),
            "profile_abandon_reason": prof.get("abandon_reason") or "",
            "seed": {
                "gameplay_desc": prof.get("gameplay_desc") or "",
                "tags": prof.get("tags") or [],
                "notes": prof.get("notes") or "",
                "developer": prof.get("developer") or "",
            },
        }
        entries.append(entry)

    entries.sort(key=lambda e: (
        0 if e["status"] == "pending" else 1,
        -len(e["boards"]),
        e["best_rank"] if e["best_rank"] else 999,
    ))

    payload = {
        "generated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "source": src,
        "total": len(entries),
        "pending": sum(1 for e in entries if e["status"] == "pending"),
        "done": sum(1 for e in entries if e["status"] == "done"),
        "with_icon": sum(1 for e in entries if e["icon_url"]),
        "entries": entries,
    }
    out = Path(args.out) if args.out else WORKLIST
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    print(f"\n工作清单 -> {out.relative_to(ROOT)}")
    print(f"  合计 {payload['total']} 款｜待建档 {payload['pending']}"
          f"｜已完成 {payload['done']}｜有图标 {payload['with_icon']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
