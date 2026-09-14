#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把待建档游戏切成批次，给调研子智能体准备输入文件。

每批写一个 `data/detail/_staging/_batch_<N>_input.json`，里面已经带齐
榜单表现、已有档案线索、官方媒体（图标/截图/官方描述/包体/开发商），
子智能体只要读它 + 读 RESEARCH_SPEC.md，就能产出 `batch_<N>.json`。

用法：
    python scripts/detail/make_batches.py --size 15
    python scripts/detail/make_batches.py --size 12 --only-uncollected
    python scripts/detail/make_batches.py --size 12 --names "抓大鹅,躺平发育"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DETAIL_DIR = ROOT / "data" / "detail"
STAGING = DETAIL_DIR / "_staging"
WORKLIST = DETAIL_DIR / "_worklist.json"
MEDIA = DETAIL_DIR / "_media.json"


def load(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=15, help="每批游戏数")
    ap.add_argument("--limit", type=int, default=0, help="总处理上限")
    ap.add_argument("--names", default="", help="只处理这些名字（逗号分隔）")
    ap.add_argument("--include-done", action="store_true",
                    help="连已建档的一起重做")
    args = ap.parse_args()

    wl = load(WORKLIST) or {}
    entries = wl.get("entries") or []
    if not entries:
        raise SystemExit("先跑 build_worklist.py")
    media = (load(MEDIA) or {}).get("games") or {}

    names = [s.strip() for s in args.names.split(",") if s.strip()]
    if names:
        picked = [e for e in entries if e["name"] in names]
        missing = [n for n in names if n not in {e["name"] for e in entries}]
        if missing:
            print(f"[warn] {len(missing)} 款不在当前工作清单（可能已掉榜）："
                  + "、".join(missing))
    else:
        picked = entries if args.include_done else [
            e for e in entries if e.get("status") != "done"]
    if args.limit:
        picked = picked[:args.limit]

    STAGING.mkdir(parents=True, exist_ok=True)
    for old in STAGING.glob("_batch_*_input.json"):
        old.unlink()

    batches = [picked[i:i + args.size] for i in range(0, len(picked), args.size)]
    for i, chunk in enumerate(batches, 1):
        out = {
            "batch": i,
            "total_batches": len(batches),
            "spec": "scripts/detail/RESEARCH_SPEC.md",
            "output": f"data/detail/_staging/batch_{i}.json",
            "games": [{
                "name": e["name"],
                "boards": e.get("boards") or [],
                "ranks": e.get("ranks") or {},
                "category_l1": e.get("category_l1") or "",
                "category_l2": e.get("category_l2") or "",
                "publisher": e.get("publisher") or "",
                "first_seen": e.get("first_seen") or "",
                "appearances": e.get("appearances") or 0,
                "best_rank": e.get("best_rank"),
                "profile_value": e.get("profile_value") or "",
                "seed": e.get("seed") or {},
                "media": media.get(e["name"]) or {"matched": False},
            } for e in chunk],
        }
        (STAGING / f"_batch_{i}_input.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    with_media = sum(1 for e in picked if (media.get(e["name"]) or {}).get("matched"))
    print(f"待建档 {len(picked)} 款 -> {len(batches)} 批（每批 ≤{args.size}）")
    print(f"  其中 {with_media} 款有官方媒体材料")
    for i, chunk in enumerate(batches, 1):
        names_preview = "、".join(e["name"] for e in chunk[:4])
        print(f"  第 {i} 批（{len(chunk)} 款）：{names_preview}…")
    print(f"\n输入目录：{STAGING.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
