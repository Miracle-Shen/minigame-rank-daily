#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""详情页数据终检：index / 记录 / 媒体 / 覆盖度 一次过。

用法:
    python3 scripts/detail/verify.py            # 全量校验，非零退出码代表有问题
    python3 scripts/detail/verify.py --sample 3 # 额外打印若干条记录摘要
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import schema  # noqa: E402
from schema import norm_name  # noqa: E402

DETAIL = ROOT / "data" / "detail"
REQUIRED_DIMS = list(schema.DIM_KEYS)


def load(p: pathlib.Path):
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--require-biz", action="store_true",
                    help="把「结合业务的建议」分区也当硬性要求（回填完成后用）")
    args = ap.parse_args()

    problems: list[str] = []
    warns: list[str] = []

    idx = load(DETAIL / "index.json")
    games = idx["games"]
    wl = load(DETAIL / "_worklist.json")
    entries = wl["entries"]
    media = load(DETAIL / "_media.json")["games"] if (DETAIL / "_media.json").exists() else {}

    print("=" * 62)
    print(f"索引 total={idx['total']} collected={idx['collected']} games={len(games)}")
    print(f"工单 total={wl['total']} done={wl['done']} pending={wl['pending']} with_icon={wl['with_icon']}")
    print(f"媒体匹配 {len(media)}/{len(entries)}")
    print("=" * 62)

    # 1) 覆盖度：工单里的每个游戏都必须在索引里
    wl_by_norm = {norm_name(e["name"]): e["name"] for e in entries}
    missing = [wl_by_norm[k] for k in wl_by_norm if k not in games]
    extras = [games[k].get("name") for k in games if k not in wl_by_norm]
    if missing:
        problems.append(f"工单有 {len(missing)} 个游戏不在索引中: {missing[:10]}")
    print(f"[覆盖] 工单缺失 {len(missing)} | 索引多出(留存旧档) {len(extras)}")
    if extras:
        warns.append(f"索引含 {len(extras)} 个非当日榜单留存条目: {extras[:6]}")

    # 2) 每条记录的 schema + 8 维打分 + 磁盘文件
    schema_bad, dim_bad, file_bad, media_bad = [], [], [], []
    for key, meta in games.items():
        slug = meta.get("slug") or ""
        path = DETAIL / f"{slug}.json"
        if not path.exists():
            file_bad.append((key, slug))
            continue
        try:
            rec = load(path)
        except Exception as exc:  # noqa: BLE001
            file_bad.append((key, f"{slug}: {exc}"))
            continue

        ok, issues = schema.validate(rec, strict=False, require_biz=args.require_biz)
        if not ok:
            hard = [i for i in issues if not i.startswith("[warn]")]
            soft = [i for i in issues if i.startswith("[warn]")]
            schema_bad.append((key, hard[:3]))
            for s in soft:
                warns.append(f"{key}: {s}")

        tech = rec.get("tech") or {}
        dims = tech.get("dims") or {}
        if len(dims) != 8 or set(dims) != set(REQUIRED_DIMS) or not all(
            isinstance(v, int) and 1 <= v <= 5 for v in dims.values()
        ):
            dim_bad.append((key, dims))

        if norm_name(rec.get("name", "")) != key:
            warns.append(f"记录名与索引键不一致: {rec.get('name')!r} -> {key!r}")

        # 媒体弱匹配必须显式降级，不能冒充官方素材
        m = rec.get("media") or {}
        if m.get("matched") is False and rec.get("shots"):
            media_bad.append(key)

    print(f"[结构] schema 不合格 {len(schema_bad)} | 8维缺失/越界 {len(dim_bad)} | 文件缺失或损坏 {len(file_bad)}")
    if schema_bad:
        problems.append(f"schema 不合格 {len(schema_bad)} 条: {schema_bad[:5]}")
    if dim_bad:
        problems.append(f"8 维打分不全 {len(dim_bad)} 条: {dim_bad[:5]}")
    if file_bad:
        problems.append(f"文件缺失/损坏 {len(file_bad)} 条: {file_bad[:5]}")
    if media_bad:
        warns.append(f"{len(media_bad)} 条弱匹配仍带截图: {media_bad[:5]}")

    # 3) 内容质量：各板块是否有实质内容
    empty_core, empty_hl, empty_clone = [], [], []
    biz_missing, biz_thin, biz_legacy, biz_lens_bad = [], [], [], []
    for key, meta in games.items():
        path = DETAIL / f"{meta.get('slug','')}.json"
        if not path.exists():
            continue
        rec = load(path)
        play = rec.get("play") or {}
        clone = rec.get("clone") or {}
        if not str(play.get("core_loop") or "").strip():
            empty_core.append(key)
        if not (play.get("highlights") or []):
            empty_hl.append(key)
        if not str(clone.get("verdict") or "").strip():
            empty_clone.append(key)

        # 业务结合分区：缺失 / 写了但没写透（内部落地 424 不齐），分开统计
        biz = rec.get("biz") or {}
        internal = biz.get("internal") or {}
        opp = biz.get("opportunity") or {}
        ext = biz.get("extension") or {}
        filled = bool(str(internal.get("fit") or "").strip()
                      or str(opp.get("fit") or "").strip())
        if not filled:
            biz_missing.append(key)
            continue
        # 主体按 424 卡：why 4（用户 2 + 业务 2）/ how 2 / gain 4；旧结构单独识别为待升级
        why_items = schema.biz_why(internal)
        blocks = {k: [x for x in (internal.get(k) or []) if str(x).strip()]
                  for k, _, _ in schema.BIZ_424}
        blocks["why"] = why_items
        legacy = [x for x in (internal.get(schema.BIZ_LEGACY_POINTS) or [])
                  if str(x).strip()]
        if not any(blocks.values()) and legacy:
            biz_legacy.append(key)
        elif any(len(blocks[k]) < target for k, _, target in schema.BIZ_424):
            biz_thin.append(key)
        # why 的双视角配比：只在已按 424 写的地方检查
        elif [l for l, _ in why_items].count("用户") != schema.BIZ_WHY_PER_LENS \
                or [l for l, _ in why_items].count("业务") != schema.BIZ_WHY_PER_LENS:
            biz_lens_bad.append(key)

    if empty_core:
        problems.append(f"核心玩法为空 {len(empty_core)}: {empty_core[:5]}")
    if empty_hl:
        problems.append(f"爽点为空 {len(empty_hl)}: {empty_hl[:5]}")
    if empty_clone:
        problems.append(f"复刻结论为空 {len(empty_clone)}: {empty_clone[:5]}")
    if biz_missing:
        msg = f"业务结合分区未回填 {len(biz_missing)}/{len(games)}: {biz_missing[:5]}"
        (problems if args.require_biz else warns).append(msg)
    if biz_thin:
        warns.append(f"业务结合分区 424 不齐 {len(biz_thin)}: {biz_thin[:5]}")
    if biz_legacy:
        warns.append(f"业务结合分区仍是旧结构（points，待升级 424）{len(biz_legacy)}: "
                     f"{biz_legacy[:5]}")
    if biz_lens_bad:
        warns.append(f"业务结合 why 的用户/业务配比不是 2+2 {len(biz_lens_bad)}: "
                     f"{biz_lens_bad[:5]}")
    print(f"[内容] 核心玩法空 {len(empty_core)} | 爽点空 {len(empty_hl)} | 复刻结论空 {len(empty_clone)}")
    print(f"[内容] 业务结合 未回填 {len(biz_missing)} | 424 不齐 {len(biz_thin)}"
          f" | 旧结构 {len(biz_legacy)} | why 视角失衡 {len(biz_lens_bad)}"
          f" | 已回填 {len(games) - len(biz_missing)}/{len(games)}")

    # 4) 统计分布
    verdict = collections.Counter(str((load(DETAIL / f"{m.get('slug','')}.json").get("clone") or {}).get("verdict") or "-")
                                 for m in games.values() if (DETAIL / f"{m.get('slug','')}.json").exists())
    level = collections.Counter(str((load(DETAIL / f"{m.get('slug','')}.json").get("tech") or {}).get("cost_level") or "-")
                               for m in games.values() if (DETAIL / f"{m.get('slug','')}.json").exists())
    engine = collections.Counter(str((load(DETAIL / f"{m.get('slug','')}.json").get("tech") or {}).get("engine") or "-")
                                for m in games.values() if (DETAIL / f"{m.get('slug','')}.json").exists())
    shots = 0
    conf = 0
    for m in games.values():
        p = DETAIL / f"{m.get('slug','')}.json"
        if not p.exists():
            continue
        r = load(p)
        if r.get("shots"):
            shots += 1
        if (r.get("tech") or {}).get("engine_evidence") == "confirmed":
            conf += 1
    print("\n[分布] 复刻结论:", dict(verdict))
    print("[分布] 成本等级:", dict(level))
    print("[分布] 引擎:", engine.most_common())
    print(f"[分布] 有截图 {shots}/{len(games)} | 引擎证据确凿 {conf}/{len(games)}")

    def _biz_fit(field: str) -> collections.Counter:
        c: collections.Counter = collections.Counter()
        for m in games.values():
            p = DETAIL / f"{m.get('slug','')}.json"
            if not p.exists():
                continue
            b = (load(p).get("biz") or {}).get(field) or {}
            val = str(b.get("fit") or "").strip()
            if val:
                c[val] += 1
        return c

    print("[分布] 业务结合·内部落地:", dict(_biz_fit("internal")) or "（未回填）")
    print("[分布] 业务结合·对外机会:", dict(_biz_fit("opportunity")) or "（未回填）")

    if args.sample:
        print("\n[样例]")
        for m in list(games.values())[: args.sample]:
            p = DETAIL / f"{m.get('slug','')}.json"
            if not p.exists():
                continue
            r = load(p)
            t = r.get("tech") or {}
            print(f"  - {r['name']}: {t.get('engine')}/{t.get('dimension')} 成本{t.get('cost_score')}({t.get('cost_level')}) "
                  f"| {(r.get('clone') or {}).get('verdict')} | 截图{len(r.get('shots') or [])}")

    print("\n" + "=" * 62)
    if warns:
        print(f"[warn] 共 {len(warns)} 条，示例：")
        for w in warns[:8]:
            print("   -", w)
    if problems:
        for p in problems:
            print("[FAIL]", p)
        print(f"结论：不合格 {len(problems)} 项")
        return 1
    print("结论：全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
