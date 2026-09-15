#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为「结合业务的建议」（第 5 分区）回填准备批次输入。

和 `make_batches.py` 的区别：那个是给**首次建档**用的（输入来自当日榜单工单），
这个只服务**回填 biz** —— 输入是**磁盘上已有档案的事实摘要**，游戏集合取自
`index.json`（含掉榜留存的历史档案），而不是当日工单。

子智能体拿到的 digest 里已经带齐写 biz 需要的一切：榜单表现（在榜天数/最好名次）、
商店评分与评价数、包体、官方描述、玩法与爽点、复刻结论与工作量。
**写 biz 不需要额外检索**，但「用户动机」那两条必须落到这些真实数字上。

产出 `_staging/_biz_batch_<N>_input.json`（下划线开头，`load_staging()` 会跳过，
不会被当成调研产物）；子智能体对应写 `_staging/biz_batch_<N>.json`。

用法：
    python scripts/detail/make_biz_batches.py --size 8
    python scripts/detail/make_biz_batches.py --size 8 --limit 8     # 先跑一批试水
    python scripts/detail/make_biz_batches.py --names "抓大鹅,躺平发育"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DETAIL_DIR = ROOT / "data" / "detail"
STAGING = DETAIL_DIR / "_staging"

DESC_MAX = 500          # 官方描述截断长度：够写「用户动机」，又不至于把输入撑爆


def load(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def has_biz(rec: dict) -> bool:
    biz = rec.get("biz") or {}
    internal = biz.get("internal") or {}
    opp = biz.get("opportunity") or {}
    return bool(str(internal.get("fit") or "").strip()
                or str(opp.get("fit") or "").strip())


def trim(text, n: int = DESC_MAX) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[:n] + "…"


def digest(rec: dict, name: str) -> dict:
    """把一份档案压成写 biz 需要的事实摘要（不抄长句，只留可引用的硬信息）。"""
    ident = rec.get("identity") or {}
    media = rec.get("media") or {}
    tech = rec.get("tech") or {}
    play = rec.get("play") or {}
    clone = rec.get("clone") or {}
    size = media.get("file_size_bytes") or 0
    return {
        "name": name,
        "boards": ident.get("boards") or [],
        "first_seen": ident.get("first_seen") or "",
        "appearances": ident.get("appearances") or 0,
        "best_rank": ident.get("best_rank"),
        "category": f"{ident.get('category_l1') or ''}/{ident.get('category_l2') or ''}".strip("/"),
        "publisher": ident.get("publisher") or "",
        "store": {
            "rating": round(media["rating"], 2) if media.get("rating") else None,
            "rating_count": media.get("rating_count"),
            "size_mb": round(size / 1048576) if size else None,
            "release_date": media.get("release_date") or "",
            "seller": media.get("seller") or "",
        },
        "official_desc": trim(media.get("description")),
        "tech": {
            "engine": tech.get("engine") or "",
            "engine_confidence": tech.get("engine_evidence") or "",
            "dimension": tech.get("dimension") or "",
            "cost_level": tech.get("cost_level") or "",
            "notes": trim(tech.get("notes"), 200),
        },
        "play": {
            "core_loop": trim(play.get("core_loop"), 260),
            "highlights": play.get("highlights") or [],
            "innovations": play.get("innovations") or [],
            "target_user": play.get("target_user") or "",
            "monetization": play.get("monetization") or "",
            "lifecycle": play.get("lifecycle") or "",
        },
        "clone": {
            "verdict": clone.get("verdict") or "",
            "rationale": trim(clone.get("rationale"), 260),
            "effort": clone.get("effort") or "",
            "suggestions": clone.get("suggestions") or [],
            "risks": clone.get("risks") or [],
        },
    }


INSTRUCTIONS = [
    "先读 scripts/detail/RESEARCH_SPEC.md 的**第六节「结合业务的建议怎么写」**，再读本文件的 games。",
    "输出一个 JSON 数组，**每项只有 name 和 biz 两个键**（不要重抄 tech/play/clone，"
    "合并器据此把它当补丁打在已有档案上）。",
    "name 必须与输入里的 name **完全一致**（含全角冒号、® 等符号）。",
    "biz.internal 按 424 写：why 4 条、how 2 条、gain 4 条；"
    "why 的每条是 {\"lens\": \"用户\"｜\"业务\", \"text\": \"...\"}，**用户 2 条 + 业务 2 条**。",
    "用户视角两条：① 用户动机（打中哪根神经）+ ② 迁移接受度（我们的用户买不买单）；"
    "必须引用 digest 里的真实数字（在榜天数、最好名次、评分/评价数、包体）。",
    "业务视角两条：③ 贴合点 + 杠杆、④ 时机 / 不做的代价。",
    "fit = 不建议搬 时，用户视角转成「用户被原作锁在哪、我们的用户为什么不缺这套」。",
    "附属两层从简：opportunity.points ≤2 条、extension.scenes 1–2 个 {scene, how}，"
    "各一句话。",
    "summary 一句话 30 字内。",
    "**不要编造 digest 里没有的具体数字**（下载量、营收、DAU 等一律不写）；"
    "判断可以下，事实要有出处。",
    "写成 data/detail/_staging/<output 文件名>，UTF-8。",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=8, help="每批游戏数")
    ap.add_argument("--limit", type=int, default=0, help="总处理上限")
    ap.add_argument("--names", default="", help="只处理这些名字（逗号分隔）")
    ap.add_argument("--batch-start", type=int, default=1, help="批次编号起点")
    ap.add_argument("--keep-inputs", action="store_true",
                    help="保留已有 _biz_batch_*_input.json（默认先清掉）")
    args = ap.parse_args()

    idx = load(DETAIL_DIR / "index.json") or {}
    games = idx.get("games") or {}
    if not games:
        raise SystemExit("index.json 为空，先跑 merge_details.py")

    names = [s.strip() for s in args.names.split(",") if s.strip()]
    picked: list[tuple[str, dict]] = []
    for key, meta in games.items():
        path = DETAIL_DIR / f"{meta.get('slug','')}.json"
        rec = load(path)
        if not rec:
            continue
        name = rec.get("name") or key
        if names and name not in names:
            continue
        if not names and has_biz(rec):
            continue                      # 已有第 5 分区，回填自动跳过（幂等）
        picked.append((name, digest(rec, name)))

    missing = [n for n in names if n not in {p[0] for p in picked}]
    if missing:
        print(f"[warn] {len(missing)} 款没被选中（可能已回填或名字不匹配）："
              + "、".join(missing))
    if args.limit:
        picked = picked[:args.limit]
    if not picked:
        print("没有需要回填的游戏（全部已有 biz）")
        return 0

    STAGING.mkdir(parents=True, exist_ok=True)
    if not args.keep_inputs:
        for old in STAGING.glob("_biz_batch_*_input.json"):
            old.unlink()

    batches = [picked[i:i + args.size] for i in range(0, len(picked), args.size)]
    first = args.batch_start
    for i, chunk in enumerate(batches, first):
        out = {
            "batch": i,
            "total_batches": first + len(batches) - 1,
            "task": "回填第 5 分区「结合业务的建议」（biz 补丁）",
            "spec": "scripts/detail/RESEARCH_SPEC.md",
            "output": f"data/detail/_staging/biz_batch_{i}.json",
            "instructions": INSTRUCTIONS,
            "games": [d for _, d in chunk],
        }
        (STAGING / f"_biz_batch_{i}_input.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    with_desc = sum(1 for _, d in picked if d["official_desc"])
    with_store = sum(1 for _, d in picked if d["store"]["rating_count"])
    print(f"待回填 {len(picked)} 款 -> {len(batches)} 批（每批 ≤{args.size}），"
          f"编号 {first}–{first + len(batches) - 1}")
    print(f"  有官方描述 {with_desc} | 有商店评分数据 {with_store}")
    for i, chunk in enumerate(batches, first):
        print(f"  第 {i} 批（{len(chunk)} 款）："
              + "、".join(n for n, _ in chunk[:4]) + "…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
