#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信小游戏周报分析层（game-market-monitor）。

输入：仓库里已有的 JSON，不引入数据库、不重新抓取
    data/daily/*.json   历史快照（每天一份）
    data/diff/*.json    每日新进分类（ci_diff.py 产出）

输出六个板块，对应三类立项参考价值：

    ┌ 大盘格局 ────────────────────────────────────────────┐
    │ category_structure  品类结构    在做什么品类 —— 立项方向      │
    │ category_trend      品类走向    占比在涨还是退 —— 方向变化    │
    │ concentration       头部集中度  榜被谁占据 —— 挤不挤得进去    │
    └──────────────────────────────────────────────────┘
    ┌ 异动信号 ────────────────────────────────────────────┐
    │ new_entrants        新晋者      本周新进榜 —— 新变量          │
    │ risers              上升态势    名次显著上升 —— 正在起量的玩法  │
    └──────────────────────────────────────────────────┘
    ┌ 结构稳定性 ──────────────────────────────────────────┐
    │ head_stability      头部稳定性  TOP10 留存/换血 —— 是否固化   │
    │ waist_persistence   腰部持续性  11-20 名连续在榜 —— 长线 or 虚火│
    └──────────────────────────────────────────────────┘

面向领导的两段结论（趋势 / 值得复刻）在 `report.py` 里组装，其中「值得复刻」
的数据来自产品档案 `data/detail/` 的 `clone.verdict`，由 `clone.py` 负责关联。

## 口径与已知限制

- 对比基准：默认「最新快照 vs 7 天前快照」（周环比），历史不足时取最早一份并
  在 `meta.baseline_gap_days` 里如实标注实际间隔，不假装是 7 天。
- **匿名数据源限制**：每榜只有 TOP20。一个游戏从第 25 名升到第 15 名，
  在数据上表现为「新进 TOP20」而不是「上升 10 位」——这不是 bug，是数据边界。
  因此 `risers` 只统计**两周都在榜内**的排名变化，`new_entrants` 则包含这类
  「从榜外挤进来」的情况，报告里会显式说明。
- 「腰部」按榜内名次定义（11-20 名），而不是全榜的 30-100 名，原因同上。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import classify as C  # noqa: E402

ROOT = HERE.parents[1]
DAILY_DIR = ROOT / "data" / "daily"

BEIJING_TZ = timezone(timedelta(hours=8))

# 分析范围：微信小游戏三榜（领导关注的核心）；抖音榜品类字段不可用，仅作异动参考
DEFAULT_BOARDS = [
    ("wx", "人气榜"),
    ("wx", "畅销榜"),
    ("wx", "畅玩榜"),
]
PRIMARY_BOARD = ("wx", "畅销榜")   # 报告重点展示的榜

WEEK_DAYS = 7      # 周环比基准
TOP_N = 20         # 匿名数据源每榜行数上限；对比时两端都按此截断以保证口径一致
HEAD_N = 10        # 头部 = 榜内前 N 名
RISE_MIN = 3       # 名次上升 >= N 位才算「上升态势」
TREND_WEEKS = 4    # 品类走向：与 N 周前对比，看占比是在涨还是在退
TREND_MIN_SHARE = 5.0   # 趋势里至少保留占比 >= 该值的品类
TREND_MIN_DELTA = 3.0   # 或占比变化 >= 该百分点（绝对值）


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------
def load_history(daily_dir: Path = DAILY_DIR) -> list[tuple[str, dict]]:
    """返回 [(日期, 快照), ...]，按日期升序。"""
    out = []
    for p in sorted(daily_dir.glob("*.json")):
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append((snap.get("date_beijing") or p.stem, snap))
    out.sort(key=lambda x: x[0])
    return out


def get_board(snap: dict, plat: str, label: str) -> list[dict] | None:
    p = (snap.get("platforms") or {}).get(plat)
    if not p:
        return None
    for b in p.get("boards") or []:
        if b.get("label") == label:
            return b.get("rows") or []
    return None


def collect_rows(hist: list[tuple[str, dict]], boards=None) -> list[dict]:
    """汇总指定榜单在全部历史里的行（用于构建品类学习表）。"""
    boards = boards or DEFAULT_BOARDS
    rows = []
    for _date, snap in hist:
        for plat, label in boards:
            rows.extend(get_board(snap, plat, label) or [])
    return rows


def build_presence(hist, boards=None) -> dict:
    """{(plat,label): {日期: {游戏名, ...}}}，用于算连续在榜天数。"""
    boards = boards or DEFAULT_BOARDS
    pres: dict = {}
    for date, snap in hist:
        for plat, label in boards:
            rows = get_board(snap, plat, label)
            if rows is None:
                continue
            pres.setdefault((plat, label), {})[date] = {
                r.get("name") for r in rows if r.get("name")}
    return pres


def streak_days(presence: dict, plat: str, label: str, name: str) -> int:
    """从最新日期往回数，该游戏连续在榜的天数。"""
    by_date = presence.get((plat, label)) or {}
    n = 0
    for date in sorted(by_date, reverse=True):
        if name in by_date[date]:
            n += 1
        else:
            break
    return n


# --------------------------------------------------------------------------
# 指标
# --------------------------------------------------------------------------
def category_structure(rows: list[dict], learned: dict) -> dict:
    """品类结构：L1 / L2 占比 + 每个品类的代表产品。"""
    l1c, l2c = Counter(), Counter()
    examples: dict[str, list[str]] = {}
    pending = 0
    for r in rows:
        n = C.classify_row(r, None, learned)
        l1c[n["l1"]] += 1
        l2c[n["l2"]] += 1
        if n["category_src"] in ("pending",):
            pending += 1
        examples.setdefault(n["l1"], [])
        if len(examples[n["l1"]]) < 3 and r.get("name"):
            examples[n["l1"]].append(r["name"])
    total = max(len(rows), 1)
    return {
        "total": len(rows),
        "unclassified": pending,
        "l1": [{"name": k, "count": v, "share": round(v / total * 100, 1),
                "examples": examples.get(k, [])}
               for k, v in l1c.most_common()],
        "l2": [{"name": k, "count": v, "share": round(v / total * 100, 1)}
               for k, v in l2c.most_common()],
    }


def concentration(rows: list[dict]) -> dict:
    """头部集中度：发行商在榜产品数 + 生存周期跨度。"""
    pubs = Counter(r.get("publisher") or "(未标注)" for r in rows)
    total = max(len(rows), 1)
    top = [{"publisher": k, "count": v, "share": round(v / total * 100, 1)}
           for k, v in pubs.most_common(6)]
    # 单一发行商占比过高 = 大盘被垄断，新进者空间小
    return {
        "total": len(rows),
        "distinct_publishers": len(pubs),
        "top_publishers": top,
        "top1_share": top[0]["share"] if top else 0.0,
        "top3_share": round(sum(t["share"] for t in top[:3]), 1),
    }


def snapshot_near(hist: list[tuple[str, dict]], target_date: str):
    """取 <= target_date 的最近一份快照；历史不够早时退回最早一份。"""
    cands = [(d, s) for d, s in hist if d <= target_date]
    if cands:
        return cands[-1]
    return hist[0] if hist else (None, None)


def category_trend(hist, plat: str, label: str, learned: dict,
                   cur_rows: list[dict], cur_date: str,
                   weeks: int = TREND_WEEKS) -> dict | None:
    """品类走向：当前 L1 占比 vs 数周前同榜 L1 占比（单位：百分点）。

    只给当期占比看不出趋势 —— 领导要的是「这个品类在涨还是在退」，
    所以两端用同一套归一化词典、同样截断到 TOP_N，保证是可比的。
    """
    target = (datetime.strptime(cur_date, "%Y-%m-%d")
              - timedelta(days=weeks * 7)).strftime("%Y-%m-%d")
    base_date, base_snap = snapshot_near(hist, target)
    if not base_date or base_date >= cur_date or not base_snap:
        return None
    base_rows = (get_board(base_snap, plat, label) or [])[:TOP_N]
    if not base_rows:
        return None

    cur = category_structure(cur_rows, learned)
    old_map = {x["name"]: x["share"]
               for x in category_structure(base_rows, learned)["l1"]}

    items, seen = [], set()
    for x in cur["l1"]:
        seen.add(x["name"])
        prev = old_map.get(x["name"], 0.0)
        items.append({
            "name": x["name"], "count": x["count"], "share": x["share"],
            "prev_share": prev, "delta": round(x["share"] - prev, 1),
            "is_new": x["name"] not in old_map,
        })
    # 当期已消失、但此前有分量的品类（变化为负，代表赛道在退）
    dropped = [{"name": k, "count": 0, "share": 0.0, "prev_share": v,
                "delta": round(-v, 1), "is_new": False}
               for k, v in old_map.items()
               if k not in seen and v >= TREND_MIN_SHARE]

    items = [i for i in items
             if i["share"] >= TREND_MIN_SHARE
             or abs(i["delta"]) >= TREND_MIN_DELTA]
    items.sort(key=lambda i: (-i["delta"], -i["share"]))
    return {
        "baseline_date": base_date,
        "weeks": weeks,
        "actual_days": (datetime.strptime(cur_date, "%Y-%m-%d")
                        - datetime.strptime(base_date, "%Y-%m-%d")).days,
        "items": items,
        "dropped": dropped,
    }


def board_analysis(hist, presence, plat: str, label: str, learned: dict,
                   baseline_date: str | None = None) -> dict:
    """单榜周环比分析。"""
    cur_date, cur_snap = hist[-1]
    # 两端都截断到 TOP_N：历史里有登录态抓的 TOP100 快照，若直接与当前匿名
    # TOP20 对比，一个「78 名 -> 2 名」会算出 +76 的假升幅。截断对齐后，
    # 这类情况统一表现为「新进 TOP20」，与 meta 里的口径说明一致。
    cur_rows = (get_board(cur_snap, plat, label) or [])[:TOP_N]

    # 基准快照：优先用指定日期，否则取 <= cur_date - WEEK_DAYS 的最近一份
    base_date, base_rows = None, []
    if baseline_date:
        for d, s in hist:
            if d == baseline_date:
                base_rows = (get_board(s, plat, label) or [])[:TOP_N]
                base_date = d
    if base_date is None:
        target = (datetime.strptime(cur_date, "%Y-%m-%d")
                  - timedelta(days=WEEK_DAYS)).strftime("%Y-%m-%d")
        cands = [(d, s) for d, s in hist if d < cur_date and d <= target]
        if not cands:
            cands = [(d, s) for d, s in hist if d < cur_date]
        if cands:
            base_date, base_snap = cands[-1]
            base_rows = (get_board(base_snap, plat, label) or [])[:TOP_N]

    cur = {r["name"]: r for r in cur_rows if r.get("name")}
    base = {r["name"]: r for r in base_rows if r.get("name")}

    # --- 新晋者：本期在榜、基准期不在榜 ---
    new_entrants = []
    for nm, r in cur.items():
        if nm not in base:
            n = C.classify_row(r, None, learned)
            new_entrants.append({
                "name": nm, "rank": r.get("rank"),
                "publisher": r.get("publisher") or "",
                "l1": n["l1"], "l2": n["l2"],
                "streak": streak_days(presence, plat, label, nm),
            })
    new_entrants.sort(key=lambda x: x["rank"] or 999)

    # --- 上升 / 下降：两期都在榜内，名次变化的绝对值 >= RISE_MIN ---
    risers, fallers = [], []
    for nm, r in cur.items():
        b = base.get(nm)
        if not b or b.get("rank") is None or r.get("rank") is None:
            continue
        delta = b["rank"] - r["rank"]     # 正 = 名次前进
        if delta >= RISE_MIN:
            n = C.classify_row(r, None, learned)
            risers.append({"name": nm, "rank": r["rank"], "prev_rank": b["rank"],
                           "delta": delta, "l1": n["l1"], "l2": n["l2"],
                           "publisher": r.get("publisher") or ""})
        elif delta <= -RISE_MIN:
            n = C.classify_row(r, None, learned)
            fallers.append({"name": nm, "rank": r["rank"], "prev_rank": b["rank"],
                            "delta": delta, "l1": n["l1"], "l2": n["l2"],
                            "publisher": r.get("publisher") or ""})
    risers.sort(key=lambda x: -x["delta"])
    fallers.sort(key=lambda x: x["delta"])

    # --- 头部稳定性：TOP10 留存率 / 换血率 ---
    cur_top = {r["name"] for r in cur_rows[:HEAD_N] if r.get("name")}
    base_top = {r["name"] for r in base_rows[:HEAD_N] if r.get("name")}
    retained = cur_top & base_top
    stability = None
    if base_top:
        rate = len(retained) / max(len(cur_top), 1)
        stability = {
            "head_n": HEAD_N,
            "retained": len(retained),
            "turnover": len(cur_top - base_top),
            "retention_rate": round(rate * 100, 1),
            "turnover_rate": round((1 - rate) * 100, 1),
            "entered": sorted(cur_top - base_top, key=lambda nm: cur[nm]["rank"]),
            "exited": sorted(base_top - cur_top),
        }

    # --- 腰部持续性：11-20 名的连续在榜天数 ---
    waist = []
    for r in cur_rows:
        if (r.get("rank") or 0) <= HEAD_N or not r.get("name"):
            continue
        n = C.classify_row(r, None, learned)
        waist.append({"name": r["name"], "rank": r["rank"],
                      "streak": streak_days(presence, plat, label, r["name"]),
                      "l1": n["l1"], "l2": n["l2"],
                      "publisher": r.get("publisher") or ""})
    waist_long = [w for w in waist if w["streak"] >= WEEK_DAYS]
    waist_short = [w for w in waist if w["streak"] < WEEK_DAYS]

    return {
        "platform": plat,
        "board": label,
        "date": cur_date,
        "baseline_date": base_date,
        "rows": cur_rows,
        "total": len(cur_rows),
        "new_entrants": new_entrants,
        "risers": risers,
        "fallers": fallers,
        "head_stability": stability,
        "waist": sorted(waist, key=lambda x: -x["streak"]),
        "waist_long": waist_long,
        "waist_short": waist_short,
        "category_structure": category_structure(cur_rows, learned),
        "category_trend": category_trend(hist, plat, label, learned,
                                         cur_rows, cur_date),
        "concentration": concentration(cur_rows),
    }


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def analyze(daily_dir: Path = DAILY_DIR, boards=None,
            baseline_date: str | None = None) -> dict:
    boards = boards or DEFAULT_BOARDS
    hist = load_history(daily_dir)
    if not hist:
        raise SystemExit("data/daily 为空，无数据可分析")

    cur_date = hist[-1][0]
    learned = C.build_learned(collect_rows(hist, boards))
    presence = build_presence(hist, boards)

    per_board = [board_analysis(hist, presence, plat, label, learned, baseline_date)
                 for plat, label in boards]

    # 实际基准间隔（如实标注，避免把「最早可用」说成「7 天前」）
    gap = None
    for b in per_board:
        if b["baseline_date"]:
            gap = (datetime.strptime(b["date"], "%Y-%m-%d")
                   - datetime.strptime(b["baseline_date"], "%Y-%m-%d")).days
            break

    primary = next((b for b in per_board
                    if (b["platform"], b["board"]) == PRIMARY_BOARD), per_board[0])

    return {
        "meta": {
            "generated_at": datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
            "week_end": cur_date,
            "baseline_date": primary.get("baseline_date"),
            "baseline_gap_days": gap,
            "snapshot_count": len(hist),
            "history_start": hist[0][0],
            "learned_size": len(learned),
            "trend_weeks": TREND_WEEKS,
            "trend_baseline_date": (primary.get("category_trend") or {}
                                    ).get("baseline_date"),
            "primary_board": f"{PRIMARY_BOARD[1]}",
            "boards": [f"{p}/{l}" for p, l in boards],
            "data_limit_note": (
                "数据源匿名态每榜仅 TOP20：从榜外（21名以后）升入的会被计为"
                "「新进」而非「上升」；「腰部」指榜内 11-20 名。"),
        },
        "boards": per_board,
        "primary": primary,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="微信小游戏周报分析层")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--baseline", default=None,
                    help="指定基准日期 YYYY-MM-DD（默认自动取 7 天前）")
    ap.add_argument("--out", default=None, help="把 JSON 写入指定路径")
    args = ap.parse_args()

    result = analyze(baseline_date=args.baseline)

    if args.out:
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出 {args.out}")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    m = result["meta"]
    print(f"周报分析  {m['week_end']}  vs  基准 {m['baseline_date']}"
          f"（间隔 {m['baseline_gap_days']} 天，历史 {m['snapshot_count']} 份快照）")
    for b in result["boards"]:
        hs = b["head_stability"] or {}
        print(f"\n===== {b['board']}（{b['total']} 条）=====")
        print(f"  新晋 {len(b['new_entrants'])} | 上升 {len(b['risers'])} "
              f"| 下降 {len(b['fallers'])} | "
              f"TOP{hs.get('head_n')} 留存 {hs.get('retention_rate')}%")
        if b["new_entrants"]:
            print("  新晋: " + ", ".join(
                f"{x['name']}(#{x['rank']})" for x in b["new_entrants"][:6]))
        if b["risers"]:
            print("  上升: " + ", ".join(
                f"{x['name']} +{x['delta']}" for x in b["risers"][:6]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
