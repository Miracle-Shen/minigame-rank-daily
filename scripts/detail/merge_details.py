#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把调研产物合并成正式详情页数据。

数据流：

    _worklist.json     今日在榜 + 历史表现 + 已有档案线索   （机器生成）
    _media.json        官方图标/实机截图/官方描述/包体       （机器生成）
    _staging/*.json    调研子智能体产出的 tech/play/clone    （人工/模型产出）
            ↓ merge_details.py
    <slug>.json        单款完整详情（站点按需加载）
    index.json         清单索引（站点列表用，含徽章字段）

**幂等**：目标文件已存在且 collected=true 的，默认跳过；要重跑用 --force
或 --only 指定游戏名。这是「已收集过就复用」的落地位置。

用法：
    python scripts/detail/merge_details.py                    # 合并全部暂存
    python scripts/detail/merge_details.py --force            # 覆盖已有
    python scripts/detail/merge_details.py --only 羊了个羊：星球,抓大鹅
    python scripts/detail/merge_details.py --rebuild-index    # 只重建索引
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from schema import (DIM_KEYS, SCHEMA_VERSION, canon_engine,  # noqa: E402
                    fill_derived, norm_name, slug, validate)

DETAIL_DIR = ROOT / "data" / "detail"
STAGING_DIR = DETAIL_DIR / "_staging"
WORKLIST = DETAIL_DIR / "_worklist.json"
MEDIA = DETAIL_DIR / "_media.json"
INDEX = DETAIL_DIR / "index.json"
BEIJING = timezone(timedelta(hours=8))


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 解析失败 {p}: {e}")
        return default


def load_staging() -> list[dict]:
    recs = []
    if not STAGING_DIR.exists():
        return recs
    for fp in sorted(STAGING_DIR.glob("*.json")):
        # 下划线开头是流水线中间文件（_batch_*_input.json 等），不是调研产物
        if fp.name.startswith("_"):
            continue
        data = load_json(fp)
        if data is None:
            continue
        items = data.get("games") if isinstance(data, dict) else data
        if not isinstance(items, list):
            print(f"[warn] {fp.name} 结构不是数组，跳过")
            continue
        for it in items:
            if isinstance(it, dict) and it.get("name"):
                it.setdefault("_from", fp.name)
                recs.append(it)
    return recs


def merge_media(rec: dict, media: dict) -> None:
    """把官方媒体并进记录；已由调研方给出的 shots 保留优先。"""
    if not media or not media.get("matched"):
        rec.setdefault("media", media or {"matched": False})
        return
    rec["media"] = media
    shots = rec.get("shots") or []
    seen = {s.get("url") for s in shots}
    if media.get("icon") and media["icon"] not in seen:
        shots.insert(0, {"url": media["icon"], "kind": "icon",
                         "source": f"App Store（{media.get('country', '')}）",
                         "caption": "官方图标"})
    for i, u in enumerate(media.get("screenshots") or [], 1):
        if u in seen:
            continue
        shots.append({"url": u, "kind": "screenshot",
                      "source": f"App Store（{media.get('country', '')}）",
                      "caption": f"官方实机截图 {i}"})
    rec["shots"] = shots


def build_record(staged: dict, wl_entry: dict, media: dict,
                 today: str) -> dict:
    rec = {
        "schema_version": SCHEMA_VERSION,
        "name": staged.get("name") or wl_entry.get("name"),
        "updated_at": staged.get("updated_at") or today,
        "collected": True,
        "generated_by": staged.get("generated_by") or "agent",
        "identity": {
            "publisher": wl_entry.get("publisher") or "",
            "category_l1": wl_entry.get("category_l1") or "",
            "category_l2": wl_entry.get("category_l2") or "",
            "boards": wl_entry.get("boards") or [],
            "ranks": wl_entry.get("ranks") or {},
            "platform_app_ids": wl_entry.get("platform_app_ids") or {},
            "first_seen": wl_entry.get("first_seen") or "",
            "appearances": wl_entry.get("appearances") or 0,
            "best_rank": wl_entry.get("best_rank"),
            "profile_value": wl_entry.get("profile_value") or "",
            "profile_abandoned": bool(wl_entry.get("profile_abandoned")),
            "profile_abandon_reason": wl_entry.get("profile_abandon_reason") or "",
            "seed": wl_entry.get("seed") or {},
        },
        "tech": staged.get("tech") or {},
        "play": staged.get("play") or {},
        "clone": staged.get("clone") or {},
        "shots": staged.get("shots") or [],
        "evidence": staged.get("evidence") or [],
        "sources": staged.get("sources") or [],
        "notes": staged.get("notes") or "",
        "issues": staged.get("issues") or [],
    }
    # 技术栏若调研方没给全 8 维，用同批次中位数兜底不合适 —— 留空并记问题，
    # 由校验拦下，避免用假数据把成本档位拉平。
    merge_media(rec, media)
    return fill_derived(rec)


def write_index(records: dict[str, dict], wl_entries: dict[str, dict]) -> dict:
    """注意：wl_entries 传进来的是**已按 norm_name 索引**的清单表。"""
    """索引给站点列表用：只放轻量字段，单款详情按需再取。

    **键一律用 norm_name**。榜单名（Supabase/快照）与档案名可能只差一个
    全角冒号或不换行空格，用原始名做键会产生「已建档」与「未建档」两条幽灵记录。
    站点侧 game-detail.js 用同一套归一化规则查表，两边对齐。
    """
    # 先把清单条目按归一化名归并，便于取展示名与榜单信息
    wl_by_norm: dict[str, dict] = {}
    for e in wl_entries.values():
        wl_by_norm.setdefault(norm_name(e.get("name")), e)

    games: dict[str, dict] = {}
    for name, rec in records.items():
        tech = rec.get("tech") or {}
        clone = rec.get("clone") or {}
        media = rec.get("media") or {}
        ident = rec.get("identity") or {}
        key = norm_name(name)
        entries = wl_by_norm.get(key) or {}
        eng = canon_engine(tech.get("engine") or "", tech.get("engine_evidence") or "")
        games[key] = {
            "name": entries.get("name") or name,
            # slug 必须等于**磁盘上真实文件名**：历史文件的命名规则与当前
            # slug() 未必一致（旧版会把日文假名剥掉），拿现算值会让站点 404。
            "slug": (rec.get("_slug") or rec.get("slug") or slug(name)),
            "updated_at": rec.get("updated_at") or "",
            "icon": media.get("icon") or entries.get("icon_url") or "",
            "cost_level": tech.get("cost_level") or "",
            "cost_score": tech.get("cost_score") or 0,
            # engine 保留原始串（可追溯），engine_primary 给界面当标签用
            "engine": tech.get("engine") or "",
            "engine_primary": eng["primary"],
            "engine_inferred": eng["inferred"],
            "dimension": tech.get("dimension") or "",
            "verdict": clone.get("verdict") or "",
            "boards": ident.get("boards") or entries.get("boards") or [],
            "best_rank": ident.get("best_rank") or entries.get("best_rank"),
            "has_shots": bool(rec.get("shots")),
            "shot_count": len(rec.get("shots") or []),
            "play_brief": (rec.get("play") or {}).get("core_loop", "")[:120],
            "collected": True,
        }
    # 尚未建档的也进索引，标记未收集，方便站点显示覆盖率
    for key, e in wl_by_norm.items():
        if key in games:
            continue
        games[key] = {
            "name": e.get("name") or key,
            "slug": e.get("slug"), "updated_at": "", "collected": False,
            "icon": e.get("icon_url") or "",
            "boards": e.get("boards") or [],
            "best_rank": e.get("best_rank"),
            "engine": "", "engine_primary": "", "engine_inferred": False,
            "dimension": "", "verdict": "", "cost_level": "",
            "cost_score": 0, "has_shots": False, "shot_count": 0,
            "play_brief": "",        }
    return {
        "updated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "total": len(games),
        "collected": sum(1 for g in games.values() if g["collected"]),
        "games": games,
    }


def load_all_records(prefer: set[str] | None = None
                     ) -> tuple[dict[str, dict], list[str]]:
    """读盘上所有正式档案，按归一化名去重。

    历史原因会留下同游戏的重复文件：早期版本用调研方给的原始名建文件
    （全角「：」「！」），后来引入 NFKC 归一化后按规范名又写了一份，两次的
    文件名哈希不同，于是并存。

    这里**不改动磁盘**，只在索引层面择优：`prefer` 传的是工作清单里的规范名
    集合，命中者胜出（保证站点按榜单名查得到）。不能拿「文件名 == 自己名字
    算出的 slug」当判据 —— 每份文件的 slug 本来就由它自己的名字算出，恒为真。
    """
    best: dict[str, dict] = {}
    conflicts: list[str] = []
    for p in sorted(DETAIL_DIR.glob("*.json")):
        if p.name.startswith("_") or p.name == "index.json":
            continue
        rec = load_json(p)
        if not rec or not str(rec.get("name") or "").strip():
            # 历史上 slug 生成有缺陷时写出过 None.json 之类的无名文件，跳过
            if p.name != "index.json":
                pass
            continue
        key = norm_name(rec["name"])
        rec["_file"] = p.name
        rec["_slug"] = p.stem
        # 分档优先级，高者胜：
        #   2 = 名字是清单规范名 且 文件名自洽（filename == slug(name)）—— 最可信
        #   1 = 名字是清单规范名（早期 slug() 写出的旧文件名，可能与 slug 字段不一致）
        #   0 = 都不是（调研方原始名的历史残留）
        in_prefer = bool(prefer and rec.get("name") in prefer)
        self_named = (p.name == f"{slug(rec['name'])}.json")
        rec["_tier"] = (2 if (in_prefer and self_named)
                        else 1 if in_prefer else 0)
        if key not in best:
            best[key] = rec
            continue
        old = best[key]
        if rec["_tier"] > old["_tier"] or (
                rec["_tier"] == old["_tier"] and rec["_file"] > old["_file"]):
            conflicts.append(f"{old['_file']}（保留 {rec['_file']}）")
            best[key] = rec
        else:
            conflicts.append(f"{rec['_file']}（保留 {old['_file']}）")
    return best, conflicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="覆盖已收集的详情")
    ap.add_argument("--only", default="", help="逗号分隔的游戏名，只处理这些")
    ap.add_argument("--rebuild-index", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有 warning 也判不通过")
    args = ap.parse_args()

    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    wl = load_json(WORKLIST, {}) or {}
    entries_wl = wl.get("entries") or []
    wl_map = {e["name"]: e for e in entries_wl}
    # 归一化回退表：榜单名里混着 NBSP 等不可见字符，子智能体也会把长名截短，
    # 精确匹配会漏配并留下「有档案但清单显示未建档」的幽灵条目。
    wl_by_norm = {norm_name(e["name"]): e for e in entries_wl}
    media_map = (load_json(MEDIA, {}) or {}).get("games") or {}

    only = {s.strip() for s in args.only.split(",") if s.strip()}

    if args.rebuild_index:
        prefer = {e["name"] for e in entries_wl}
        records, conflicts = load_all_records(prefer)
        idx = write_index(records, wl_by_norm)
        if conflicts:
            print(f"[warn] {len(conflicts)} 份重复档案已按规范名择优（未删文件）：")
            for c in conflicts[:12]:
                print("   -", c)
        INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        print(f"索引重建：{idx['collected']}/{idx['total']} 已收集")
        return 0

    staged = load_staging()
    if not staged:
        print(f"没有暂存产物（{STAGING_DIR.relative_to(ROOT)}/*.json）")
        return 1
    if only:
        staged = [s for s in staged if s["name"] in only]

    print(f"待合并 {len(staged)} 条")
    ok = skipped = bad = 0
    problems: list[str] = []

    for s in staged:
        name = s["name"]
        # 文件名现算，不用清单里的 slug —— 清单可能由旧版 slug() 生成
        target = DETAIL_DIR / f"{slug(name)}.json"
        if target.exists() and not args.force and not only:
            old = load_json(target, {}) or {}
            if old.get("collected"):
                skipped += 1
                print(f"  跳过（已有档案）{name}")
                continue

        passed, msgs = validate(s, strict=args.strict)
        errs = [m for m in msgs if not m.startswith("[warn]")]
        warns = [m[7:] for m in msgs if m.startswith("[warn]")]
        if not passed:
            bad += 1
            problems.append(f"{name}: {'; '.join(errs)}")
            print(f"  不合格 {name}: {'; '.join(errs)}")
            continue

        wl_entry = (wl_map.get(name) or wl_by_norm.get(norm_name(name))
                    or {"name": name, "slug": s.get("slug")})
        # 以清单里的规范名为准（修正 NBSP、截短名），文件名随之
        canonical = wl_entry.get("name") or name
        s = dict(s, name=canonical, slug=wl_entry.get("slug") or s.get("slug"))
        rec = build_record(s, wl_entry,
                           media_map.get(canonical) or media_map.get(name) or {}, today)
        if warns:
            rec["issues"] = warns
        DETAIL_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        ok += 1
        extra = "（有提示）" if warns else ""
        print(f"  写入 {target.name}{extra}")

    # 重建索引：读盘上所有详情（按归一化名去重），保证与文件一致
    records, conflicts = load_all_records({e["name"] for e in entries_wl})
    idx = write_index(records, wl_by_norm)
    INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=1),
                     encoding="utf-8")

    print(f"\n写入 {ok}｜跳过 {skipped}｜不合格 {bad}")
    print(f"索引：{idx['collected']}/{idx['total']} 已收集"
          f" -> {INDEX.relative_to(ROOT)}")
    if problems:
        print("\n需要修的问题：")
        for p in problems[:20]:
            print("  -", p)
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
