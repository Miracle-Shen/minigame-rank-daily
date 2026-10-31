#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群消息的「本期到底该说哪几款」筛选层。

2026-10 重构：不再按固定位置取「微信前三 / 抖音前三 / 全平台 TOP1」——
固定版式的问题不是信息少，而是**没变化也要占版面**：TOP3 与上周一模一样时，
推送里仍然重复列三行，读者扫过去得到的结论是「本周没事」，却要读 30 行。

新口径是「先找事件，再决定说不说」：一款游戏进候选池，必须命中下面任一事件。

    new_top20     本周新进入某个平台的 TOP20
    first_top10   首次进入 TOP10（历史快照里从未进过）
    move5         周名次变化 ≥ 5 位（上升或下降）
    streak2       连续两周同方向变化（本周与上周同向，且两次都不足 5 位也算）
    new_platform  本周新增一个上榜平台（此前未在该平台出现）

本模块只做**数据判定**，不写一句文案：文案渲染在 hotlist.py，
评论摘要的接入在 reviews.py —— 三层各管一段，方便单独验证口径。

口径与 analyze.py 保持一致：
  * 名次 = 该平台**全部榜并集**里的最好名次（畅销/人气/畅玩三榜去重）。
  * 对比基准 = 周报的 baseline_date（默认 7 天前），连续两周再往前推 7 天。
  * 数据源匿名态每榜只有 TOP20，从榜外升入的表现为「新进」而不是「上升」。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# 候选池的平台范围：只算中文小游戏平台。
# iOS / Android 榜是「美区 / 日区 / 国区免费榜」，混进来一大半是英文日文 App，
# 与「小游戏复刻」这个主题无关，会把候选池从 30 款灌到 80 款（试过，正文全是噪音）。
# 需要跨平台验证时，微信 / 抖音 / TapTap 三个平台已经够用。
PLATFORMS = ["wx", "douyin", "taptap"]
PLATFORM_SHORT = {"wx": "微信", "douyin": "抖音", "taptap": "TapTap",
                  "ios": "iOS", "android": "安卓"}

TOP_N = 20            # 数据源上限，所有名次判定都在 TOP20 内进行
TOP_HEAD = 10         # 「首进 TOP10」的门槛
MOVE_MIN = 5          # 「明显名次变化」的门槛
STREAK_STEP_MIN = 2   # 连续两周同向：每一段至少 2 位，滤掉 1 位的抖动
EXPAND_MAX = 2        # 每期最多展开几款

# 事件码 → 中文短标签（日志/自检用，不直接进正文）
EVENT_LABEL = {
    "new_top20": "新进TOP20",
    "first_top10": "首进TOP10",
    "move5": "名次变动≥5",
    "streak2": "连续两周同向",
    "new_platform": "新增上榜平台",
}


# --------------------------------------------------------------------------
# 快照索引
# --------------------------------------------------------------------------
def _rows_of(snap: dict, plats: list[str]):
    """遍历指定平台的全部榜，产出 (平台, 榜名, 行)。"""
    for pk in plats:
        p = (snap.get("platforms") or {}).get(pk) or {}
        for b in p.get("boards") or []:
            label = b.get("label") or ""
            for r in b.get("rows") or []:
                if r.get("name"):
                    yield pk, label, r


def index_snapshot(snap: dict, plats: list[str] | None = None,
                   top_n: int = TOP_N) -> dict:
    """{游戏名: {rank, plats{平台:名次}, boards{平台:[榜名]}, row}}。

    rank 是跨平台合并后的最好名次；plats / boards 保留分平台信息，
    后面判断「新增一个上榜平台」和「多个平台同时升温」要用。
    """
    plats = plats or PLATFORMS
    idx: dict[str, dict] = {}
    for pk, label, r in _rows_of(snap, plats):
        rank = r.get("rank") or 999
        if rank > top_n:
            continue
        name = r["name"]
        e = idx.get(name)
        if e is None:
            e = idx[name] = {"rank": 999, "plats": {}, "boards": {}, "row": r}
        if rank < e["plats"].get(pk, 999):
            e["plats"][pk] = rank
        e.setdefault("boards", {}).setdefault(pk, [])
        if label and label not in e["boards"][pk]:
            e["boards"][pk].append(label)
        if rank < e["rank"]:
            e["rank"] = rank
            e["row"] = r
    return idx


def snapshot_at_or_before(hist: list[tuple[str, dict]], target: str):
    """<= target 的最近一份快照；没有就返回 None。"""
    pick = None
    for d, s in hist:
        if d <= target:
            pick = (d, s)
        else:
            break
    return pick


def _shift(date: str, days: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=days)
            ).strftime("%Y-%m-%d")


class WeeklyContext:
    """本周 / 上周 / 两周前 三份索引 + 「本周之前的历史最好名次」。

    prior_min 用来判断「首次进入 TOP10」—— 必须看过往全部快照，
    只看上周会漏掉「上周掉出去、这周回来」这种并不是首次的情况。
    """

    def __init__(self, hist: list[tuple[str, dict]], cur_date: str,
                 base_date: str | None = None, plats: list[str] | None = None):
        self.plats = plats or PLATFORMS
        self.cur_date = cur_date
        cur = snapshot_at_or_before(hist, cur_date)
        if not cur:
            raise SystemExit("没有可用快照，无法判定本周变化")
        self.cur_date, self.cur_snap = cur

        base_date = base_date or _shift(self.cur_date, 7)
        got = snapshot_at_or_before(hist, base_date)
        self.base_date = got[0] if got else None
        self.base_snap = got[1] if got else None

        prev2 = snapshot_at_or_before(hist, _shift(self.base_date, 7)) \
            if self.base_date else None
        self.base2_date = prev2[0] if prev2 else None
        self.base2_snap = prev2[1] if prev2 else None

        self.cur = index_snapshot(self.cur_snap, self.plats)
        self.base = index_snapshot(self.base_snap, self.plats) if self.base_snap else {}
        self.base2 = index_snapshot(self.base2_snap, self.plats) if self.base2_snap else {}
        self.prior_min = self._prior_min(hist)

    def _prior_min(self, hist) -> dict[str, int]:
        """本周（不含）之前，每款游戏到达过的最好名次。"""
        out: dict[str, int] = {}
        for d, snap in hist:
            if d >= self.cur_date:
                break
            for name, e in index_snapshot(snap, self.plats).items():
                if e["rank"] < out.get(name, 999):
                    out[name] = e["rank"]
        return out


# --------------------------------------------------------------------------
# 候选判定
# --------------------------------------------------------------------------
def _delta(cur: int | None, prev: int | None) -> int | None:
    """正 = 名次前进（数字变小）。任一端缺失返回 None。"""
    if cur is None or prev is None:
        return None
    return prev - cur


def detect_candidates(ctx: WeeklyContext) -> list[dict]:
    """本期全部候选，带上命中的事件与原始行（分类/档案在渲染层补）。"""
    out = []
    for name, e in ctx.cur.items():
        cur_rank = e["rank"]
        b = ctx.base.get(name) or {}
        p2 = ctx.base2.get(name) or {}
        base_rank = b.get("rank")
        base2_rank = p2.get("rank")

        d1 = _delta(cur_rank, base_rank)
        d2 = _delta(base_rank, base2_rank)

        events = []
        if base_rank is None:
            events.append("new_top20")
        if cur_rank <= TOP_HEAD and ctx.prior_min.get(name, 999) > TOP_HEAD:
            events.append("first_top10")
        if d1 is not None and abs(d1) >= MOVE_MIN:
            events.append("move5")

        streak = ""
        if (d1 and d2 and d1 * d2 > 0
                and abs(d1) >= STREAK_STEP_MIN and abs(d2) >= STREAK_STEP_MIN):
            events.append("streak2")
            streak = "up" if d1 > 0 else "down"

        # 新上榜平台：只在「上周就在榜」时才算 —— 全新游戏的所有平台都是新的，
        # 那不是「新增平台」，是「新进榜」，两个事件会重复叙述。
        cur_plats = set(e["plats"])
        base_plats = set(b.get("plats") or {})
        new_plats = sorted(cur_plats - base_plats) if base_rank is not None else []
        if new_plats:
            events.append("new_platform")

        if not events:
            continue

        # 「多个平台同时升温」：至少两个平台名次前进
        up_plats = [pk for pk, r in e["plats"].items()
                    if b.get("plats") and r < b["plats"].get(pk, 999)]
        down_plats = [pk for pk, r in e["plats"].items()
                      if b.get("plats") and r > b["plats"].get(pk, 999)]

        out.append({
            "name": name,
            "cur_rank": cur_rank,
            "base_rank": base_rank,
            "base2_rank": base2_rank,
            "delta": d1,
            "delta2": d2,
            "streak": streak,
            "events": events,
            "plats": sorted(e["plats"], key=PLATFORMS.index),
            "plats_rank": dict(e["plats"]),
            "boards": e["boards"],
            "new_plats": new_plats,
            "up_plats": up_plats,
            "down_plats": down_plats,
            "prior_best": ctx.prior_min.get(name),
            "row": e["row"],
        })
    return out


def priority(c: dict, has_reviews: bool = False, has_play: bool = False) -> int:
    """展开优先级：有实据的（评论/连续趋势/多平台）排前面。

    排序只影响「先展开谁」，不影响「能不能展开」——后者由 story_ready 判。
    """
    s = 0
    if has_reviews:
        s += 6
    if "streak2" in c["events"]:
        s += 4
    if "new_platform" in c["events"]:
        s += 3
    if "first_top10" in c["events"]:
        s += 2
    if has_play:
        s += 2
    if c["delta"] is not None:
        # 名次跨幅是唯一「一眼看得出多大」的实据，权重给高：
        # 18→3 这种 15 位的跳跃必须能压过连续两周各 2 位的缓慢趋势。
        s += min(abs(c["delta"]) // 2, 8)
    if "new_top20" in c["events"]:
        s += 1
    s += min(max(len(c["plats"]) - 1, 0), 2)   # 跨平台验证过更可信
    return s


def story_ready(c: dict, has_reviews: bool = False,
                has_play: bool = False) -> tuple[bool, list[str]]:
    """能不能展开成三行（是什么 / 本周 / 玩家）。

    准入条件（任一即可）：
      * 有可靠的近期玩家评论（≥10 条，由 reviews.py 判定后传进来）
      * 连续两周上涨或下跌
      * 本周新增上榜平台
      * 有产品档案，能说清具体玩法
    都不满足 = 只有名次数据 → 只能进「其余」那一句。
    """
    why = []
    if has_reviews:
        why.append("有可靠近期评论")
    if "streak2" in c["events"]:
        why.append("连续两周同向变化")
    if "new_platform" in c["events"]:
        why.append("本周新增上榜平台")
    if has_play:
        why.append("有产品档案与具体玩法")
    return bool(why), why


def stable_top(ctx: WeeklyContext, candidates: list[dict] | None = None,
               n: int = 3) -> list[dict]:
    """各平台前三里「名次没动、也没命中任何事件」的部分。

    这些正是固定版式里最该被删掉的内容：读者上次已经看过，这次没有任何新增。
    日志里列出来，是为了让「删了什么」有据可查。
    """
    cand_names = {c["name"] for c in (candidates or [])}
    out = []
    for pk in ctx.plats:
        best = {name: e["plats"][pk] for name, e in ctx.cur.items()
                if pk in e["plats"]}
        tops = sorted(best.items(), key=lambda kv: kv[1])[:n]
        stable = [name for name, rank in tops
                  if name not in cand_names
                  and (ctx.base.get(name) or {}).get("plats", {}).get(pk) == rank]
        out.append({"platform": pk, "label": PLATFORM_SHORT.get(pk, pk),
                    "names": stable, "stable": len(stable), "total": len(tops)})
    return out
