#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信 / 抖音小游戏榜单品类归一化。

引力引擎自带的 `category` / `subcategory` 不能直接用于统计：

  - L2 是拼接串：`牌类棋牌传统棋牌`、`消除消除休闲`、`卡牌卡牌卡牌竞技`
    —— 同一品类被拆成十几个变体，占比会被稀释成噪音；
  - 抖音榜 `category` 返回占位值 `1`；
  - 同一游戏 L1 会漂移（历史数据里 175 个游戏出现过多个 L1）。

解析顺序（先命中先返回）：

    1. by_game_name  人工词典 —— 覆盖引力引擎标错 / 标「其他」的游戏
    2. l2_rules      L2 拼接串清洗 —— 按规则顺序子串匹配，具体标签在前、兜底在后
    3. learned       跨榜 / 跨期学习 —— 同一游戏在别的榜有正确品类时回填
    4. name_rule     游戏名关键词兜底 —— 抖音榜无品类字段时的主要出路
    5. l1_map        L1 字段直映 —— 以上都拿不到时用引力引擎自己的 L1
    6. 兜底          (其他, 待核)

第 3、4 步是抖音榜的关键：它的 `category` 返回占位值 `1`、`subcategory` 为空，
仅靠前两步会让抖音榜大半落到「其他」。因此先用跨榜学习（同一游戏在微信榜的
品类），再用游戏名关键词兜底。微信榜本身品类字段完整，前两步即可全覆盖。

**L1 一律由 L2 反推**（查 `l2_to_l1`），保证两者自洽。不直接用引力引擎的 L1，
因为它会漂移（同一游戏今天「休闲」、明天「动作」），直接用会污染品类结构结论。

规则与词典全部在 `category_map.json`，本模块只负责执行。
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

MAP_PATH = Path(__file__).resolve().parent / "category_map.json"

_cache: dict | None = None


def load_map(path: str | Path | None = None) -> dict:
    """加载归一化词典（进程内缓存）。"""
    global _cache
    if _cache is None or path is not None:
        _cache = json.loads(
            Path(path or MAP_PATH).read_text(encoding="utf-8"))
    return _cache


def clean_l2(subcategory: str | None, mapdata: dict) -> str | None:
    """从拼接串里提取规范 L2 标签。规则顺序即优先级，未命中返回 None。"""
    s = (subcategory or "").strip()
    if not s:
        return None
    low = s.lower()
    for rule in mapdata.get("l2_rules", []):
        for kw in rule.get("match", []):
            if kw.lower() in low:
                return rule["l2"]
    return None


def classify(name: str | None, category: str | None, subcategory: str | None,
             mapdata: dict | None = None,
             learned: dict | None = None) -> tuple[str, str, str]:
    """归一化单条记录。

    返回 (l1, l2, source)，source ∈ {dict, l2_rule, learned, l1_map, pending}，
    用于后续统计「未归类」比例（待核过多说明词典该补了）。
    """
    m = mapdata or load_map()

    # 1) 人工词典优先
    hit = (m.get("by_game_name") or {}).get(name or "")
    if hit:
        return hit["l1"], hit["l2"], "dict"

    # 2) 本行 L2 拼接串清洗 -> 由 L2 反推 L1（本行事实，最可靠）
    l2 = clean_l2(subcategory, m)
    if l2:
        return m.get("l2_to_l1", {}).get(l2, "其他"), l2, "l2_rule"

    # 3) 跨榜 / 跨期学习：同一游戏在别的榜已确定的品类
    if learned:
        got = learned.get(name or "")
        if got:
            return got[0], got[1], "learned"

    # 4) 退一步：从游戏名关键词推断。
    #    抖音榜完全不返回品类（category='1'），新游又没有历史可学，
    #    而小游戏名往往自带玩法关键词（「消个瓶子」「象棋达人」「跟我玩桌球」），
    #    这一步能救回相当一部分。误判风险由 name_rule 标记便于审计。
    l2 = clean_l2(name, m)
    if l2:
        return m.get("l2_to_l1", {}).get(l2, "其他"), l2, "name_rule"

    # 5) 退回引力引擎自己的 L1
    cat = (category or "").strip()
    l1 = (m.get("l1_map") or {}).get(cat)
    if l1:
        return l1, "其他", "l1_map"

    # 6) 兜底
    return "其他", "待核", "pending"


def build_learned(rows, mapdata: dict | None = None) -> dict:
    """从一批行学习 {游戏名: (l1, l2)}，只采纳 dict / l2_rule 两种高置信来源。

    rows 建议传全历史数据 —— 样本越多，抖音榜这类缺品类字段的榜回填率越高。
    同一游戏多票时取众数，避免个别脏行带偏。
    """
    votes: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        nm = r.get("name")
        if not nm:
            continue
        l1, l2, src = classify(nm, r.get("category"), r.get("subcategory"), mapdata)
        if src in ("dict", "l2_rule"):
            votes[nm][(l1, l2)] += 1
    return {n: c.most_common(1)[0][0] for n, c in votes.items()}


def classify_row(row: dict, mapdata: dict | None = None,
                 learned: dict | None = None) -> dict:
    """给一行榜单记录补上 l1 / l2 / category_src，返回新 dict（不改原对象）。"""
    l1, l2, src = classify(row.get("name"), row.get("category"),
                           row.get("subcategory"), mapdata, learned)
    out = dict(row)
    out["l1"] = l1
    out["l2"] = l2
    out["category_src"] = src
    return out


def stats(rows: list[dict], learned: dict | None = None) -> dict:
    """快速统计一组行的归一化质量，用于自检。"""
    l1c, l2c, srcc = Counter(), Counter(), Counter()
    for r in rows:
        n = classify_row(r, None, learned)
        l1c[n["l1"]] += 1
        l2c[n["l2"]] += 1
        srcc[n["category_src"]] += 1
    return {"l1": l1c, "l2": l2c, "source": srcc, "total": len(rows)}


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parents[2]
    dailies = sorted((root / "data" / "daily").glob("*.json"))
    if not dailies:
        print("data/daily 为空")
        sys.exit(1)

    def wx_dy_rows(snap: dict) -> list[dict]:
        out = []
        for pk in ("wx", "douyin"):
            plat = (snap.get("platforms") or {}).get(pk)
            for b in (plat or {}).get("boards", []):
                out.extend(b.get("rows") or [])
        return out

    # 全历史 -> 学习表（抖音榜缺品类字段，靠这一步回填）
    hist_rows = []
    for p in dailies:
        hist_rows.extend(wx_dy_rows(json.loads(p.read_text(encoding="utf-8"))))
    learned = build_learned(hist_rows)

    rows = wx_dy_rows(json.loads(dailies[-1].read_text(encoding="utf-8")))
    s = stats(rows, learned)
    print(f"快照={dailies[-1].stem}  微信/抖音行数={s['total']}  "
          f"学习表规模={len(learned)}")
    print("\nL1 分布：")
    for k, v in s["l1"].most_common():
        print(f"  {k:<8} {v:>4}  {v / max(s['total'], 1) * 100:5.1f}%")
    print("\nL2 分布：")
    for k, v in s["l2"].most_common(12):
        print(f"  {k:<10} {v:>4}")
    print("\n来源分布（pending 越多说明词典越该补）：")
    for k, v in s["source"].most_common():
        print(f"  {k:<9} {v:>4}")
