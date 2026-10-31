#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""玩家评论的读取与摘要（给群消息「玩家」那一行用）。

设计上有三条硬规矩，都是为了防止「编出口碑」：

  1. **没有足够评论就不说话**。有效评论 < 10 条时 `summarize()` 返回空句子，
     渲染层自然不出现「玩家认为 / 口碑不错」这类表述。
  2. **只用最近 30 天**，并且只看逐条评论，不碰商店总评分 ——
     总评分是历史累计，拿它推断「本周玩家反馈」属于张冠李戴。
  3. **摘要可追溯**。`used` 保留参与统计的评论 ID，`sample` 带原文，
     人工核对时能顺着 `url` 回到原始评论。

摘要是**规则统计**，不是模型生成：按主题词典给每条评论打标，
正评（≥4 星）数正面主题、负评（≤2 星）数负面主题，取各自最高频的一条。
主题命中不足（默认 < 2 条）就当没有集中反馈 —— 宁可少写一句，也不概括。

数据文件：data/reviews/YYYY-MM-DD.json（由 collect_reviews.py 产出）
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from clone import norm_name  # noqa: E402  名称归一化必须与档案管线同一套

ROOT = HERE.parents[1]
REVIEW_DIR = ROOT / "data" / "reviews"

MIN_VALID = 10        # 有效评论少于此数，不生成玩家反馈
MAX_AGE_DAYS = 30     # 只看最近 30 天
MIN_TEXT_LEN = 4      # 太短的「好玩」「垃圾」不足以支撑主题统计

# 主题词典：kind=pos/neg 表示这个主题天然带方向（只在对应极性里计），
# kind=any 表示正负评都可能提到（画面、音效这类）。
TOPICS = [
    ("经营与扩建的成就感", "pos", ["扩建", "开店", "经营", "建造", "布置", "装修",
                                   "种田", "养殖", "收集", "解锁", "升级店铺"]),
    ("消除过关的爽感", "pos", ["消除", "通关", "过关", "连击", "爽", "上头",
                               "停不下来", "解压"]),
    ("玩法有趣", "pos", ["好玩", "有意思", "耐玩", "创意", "新颖", "丰富"]),
    ("画面与美术", "any", ["画面", "画风", "美术", "建模", "立绘", "特效", "界面"]),
    ("音效与配乐", "any", ["音乐", "音效", "配乐", "BGM"]),
    ("广告过多", "neg", ["广告", "看广告", "强制广告"]),
    ("等待时间长", "neg", ["等待", "等太久", "太慢", "进度慢", "卡关", "太肝",
                           "肝", "刷很久"]),
    ("氪金压力", "neg", ["氪金", "充值", "付费", "抽卡", "礼包", "逼氪", "太贵"]),
    ("闪退卡顿", "neg", ["闪退", "卡顿", "卡死", "进不去", "掉线", "崩溃",
                         "bug", "BUG", "卡住"]),
    ("内容重复单调", "neg", ["重复", "单调", "无聊", "没意思", "换皮", "内容少"]),
    ("客服与运营", "any", ["客服", "反馈", "投诉", "活动", "更新"]),
]

MIN_TOPIC_HITS = 2    # 单个主题至少被几条评论提到才算「集中」


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------
def path_for(date: str, root: Path = REVIEW_DIR) -> Path:
    return Path(root) / f"{date}.json"


def load(date: str | None = None, root: Path = REVIEW_DIR) -> dict:
    """读某天的评论文件；date 为空取目录里最新一份。读不到返回空结构。"""
    root = Path(root)
    p = path_for(date, root) if date else _latest(root)
    if not p or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _latest(root: Path) -> Path | None:
    files = sorted(root.glob("*.json")) if root.exists() else []
    return files[-1] if files else None


def game_entry(blob: dict, name: str) -> dict | None:
    """按归一化名（含别名）取一款游戏的评论块；匹配不上返回 None。

    「匹配不上就不用」是硬规矩：把 A 的评论贴到 B 头上，比没有评论更糟。
    """
    key = norm_name(name)
    for g in blob.get("games") or []:
        names = [g.get("name")] + list(g.get("aliases") or [])
        if any(norm_name(n) == key for n in names if n):
            return g
    return None


# --------------------------------------------------------------------------
# 过滤与统计
# --------------------------------------------------------------------------
def _parse_date(s: str):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def valid_reviews(entry: dict | None, as_of: str,
                  max_age: int = MAX_AGE_DAYS) -> list[dict]:
    """最近 N 天内、有正文、评分合法的评论（按 id 去重）。"""
    if not entry:
        return []
    end = _parse_date(as_of)
    if not end:
        return []
    out, seen = [], set()
    for r in entry.get("reviews") or []:
        text = str(r.get("text") or "").strip()
        if len(re.sub(r"\s+", "", text)) < MIN_TEXT_LEN:
            continue
        rid = str(r.get("id") or "") or f"{r.get('source')}:{text[:12]}"
        if rid in seen:
            continue
        d = _parse_date(r.get("date") or "")
        if not d:
            continue
        age = (end - d).days
        if age < 0 or age > max_age:
            continue
        try:
            rating = int(r.get("rating"))
        except Exception:  # noqa: BLE001
            continue
        if not 1 <= rating <= 5:
            continue
        seen.add(rid)
        out.append({**r, "id": rid, "rating": rating})
    out.sort(key=lambda x: str(x.get("date")), reverse=True)
    return out


def _topics_of(text: str) -> list[tuple[str, str]]:
    hit = []
    for label, kind, words in TOPICS:
        if any(w in text for w in words):
            hit.append((label, kind))
    return hit


def summarize(entry: dict | None, as_of: str,
              min_valid: int = MIN_VALID) -> dict:
    """一条（最多一句）玩家反馈。样本不足时 sentence 为空串。"""
    rows = valid_reviews(entry, as_of)
    n = len(rows)
    empty = {"n": n, "enough": False, "sentence": "", "positive": None,
             "negative": None, "used": [], "sample": []}
    if n < min_valid:
        return empty

    pos, neg = Counter(), Counter()
    order = {label: i for i, (label, _k, _w) in enumerate(TOPICS)}
    for r in rows:
        bucket = "pos" if r["rating"] >= 4 else ("neg" if r["rating"] <= 2 else "")
        if not bucket:
            continue
        for label, kind in _topics_of(r["text"]):
            if kind in (bucket, "any"):
                (pos if bucket == "pos" else neg)[label] += 1

    need = max(MIN_TOPIC_HITS, round(n * 0.15))
    best_pos = _pick(pos, order, need)
    best_neg = _pick(neg, order, need)

    if not best_pos and not best_neg:
        # 评论够多但没形成集中主题 —— 不概括、不硬凑。
        return {**empty, "enough": True, "used": [r["id"] for r in rows]}

    head = f"最近采集的 {n} 条评论里"
    if best_pos and best_neg:
        sent = (f"{head}，提到最多的是{best_pos[0]}（{best_pos[1]} 条）；"
                f"主要不满是{best_neg[0]}（{best_neg[1]} 条）。")
    elif best_pos:
        sent = (f"{head}，提到最多的是{best_pos[0]}（{best_pos[1]} 条），"
                f"没有集中的负面反馈。")
    else:
        sent = (f"{head}，主要不满是{best_neg[0]}（{best_neg[1]} 条），"
                f"正面反馈不集中。")

    return {
        "n": n,
        "enough": True,
        "sentence": sent,
        "positive": {"topic": best_pos[0], "count": best_pos[1]} if best_pos else None,
        "negative": {"topic": best_neg[0], "count": best_neg[1]} if best_neg else None,
        "used": [r["id"] for r in rows],
        "sample": [{k: r.get(k) for k in
                    ("id", "source", "date", "rating", "text", "url")}
                   for r in rows[:5]],
    }


def _pick(counter: Counter, order: dict, need: int):
    if not counter:
        return None
    label, count = sorted(counter.items(),
                          key=lambda kv: (-kv[1], order.get(kv[0], 99)))[0]
    return (label, count) if count >= need else None


# --------------------------------------------------------------------------
def main() -> int:
    """自检：打印某天评论文件的有效条数与摘要。"""
    import argparse

    ap = argparse.ArgumentParser(description="评论摘要自检")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（默认最新一份）")
    a = ap.parse_args()
    blob = load(a.date)
    if not blob:
        print("没有评论文件（data/reviews/ 为空）：本周玩家反馈一律留空。")
        return 0
    as_of = blob.get("date") or a.date
    print(f"评论文件 {as_of}：{len(blob.get('games') or [])} 款游戏")
    for g in blob.get("games") or []:
        s = summarize(g, as_of)
        print(f"\n· {g.get('name')}：有效 {s['n']} 条"
              f"{'（够生成反馈）' if s['enough'] else f'（不足 {MIN_VALID} 条，不生成反馈）'}")
        if s["sentence"]:
            print(f"  {s['sentence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
