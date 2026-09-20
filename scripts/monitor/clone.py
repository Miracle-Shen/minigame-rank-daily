#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""值得复刻清单：把周报榜内游戏与产品档案里的「复刻结论」关联起来。

数据来源是两个已经存在的产物，本模块不抓取、不调用模型：

    data/daily/*.json     榜内游戏名 / 名次（analyze.py 已经读好，这里只接收入参）
    data/detail/*.json    产品档案，其中的 clone 分区给出复刻结论

## 结论档位（口径来自 scripts/detail/RESEARCH_SPEC.md，不要在这里改语义）

    ┌ 一线（值得立项做） ──────────────────────────────────────┐
    │ 换肤      机制照搬就成立，瓶颈在题材/美术 —— 换皮即差异化，成本最低 │
    │ 变种创意  核心机制可取，但必须改一处结构 —— 照抄会撞车           │
    └──────────────────────────────────────────────────────┘
    ┌ 二线（练手 / 填充 / 局部升级） ─────────────────────────────┐
    │ 微创新    整体已成熟，只值得细节升级 —— 不建议独立立项          │
    │ 原样复刻  无壁垒的玩法原型 —— 适合练手或填充位，不建议当主力      │
    └──────────────────────────────────────────────────────┘
    不建议      有 IP/版权风险、独家资源、重资本，或玩法已被头部锁死 → 不入选

一句话理由取档案的 `clone.rationale` 首句，不是模板文案 —— 同一档位的两款游戏
理由是不同的，这正是领导要看的「为什么是它」。
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DETAIL_DIR = ROOT / "data" / "detail"
INDEX_PATH = DETAIL_DIR / "index.json"

# 档位：tier 0/1 = 一线，2/3 = 二线，9 = 不入选
# note 给报告正文（完整口径），short 给群消息（一行放得下）
VERDICT_META = {
    "换肤":     {"tier": 0, "band": "一线", "label": "换皮即用",
                 "short": "成本最低，换题材即差异化",
                 "note": "机制照搬就成立，瓶颈在题材/美术；换题材即差异化，成本最低"},
    "变种创意": {"tier": 0, "band": "一线", "label": "需改一处",
                 "short": "机制可取，需改一处结构",
                 "note": "核心机制可取，但必须改一处结构，照抄会撞车"},
    "微创新":   {"tier": 1, "band": "二线", "label": "细节升级",
                 "short": "只值得细节升级",
                 "note": "整体已成熟，只值得做细节体验升级，不建议独立立项"},
    "原样复刻": {"tier": 1, "band": "二线", "label": "练手填充",
                 "short": "适合练手或填充位",
                 "note": "无壁垒的玩法原型，适合练手或填充位，不建议当主力"},
    "不建议":   {"tier": 9, "band": "不推荐", "label": "不建议",
                 "short": "不入选",
                 "note": "有 IP/版权风险、依赖独家资源、需重资本，或玩法已被头部锁死"},
}
EXCLUDED = "不建议"

# 输出顺序 = 复刻力度由强到弱；每档默认列几条（按成本档 + 榜内名次排序）
GROUP_ORDER = ["换肤", "变种创意", "微创新", "原样复刻"]
GROUP_LIMIT = {"换肤": 5, "变种创意": 3, "微创新": 3, "原样复刻": 2}

COST_ORDER = {"极低": 0, "低": 1, "中": 2, "高": 3, "极高": 4}

REASON_MAX = 44     # 一句话理由的截断长度
HOW_MAX = 36        # 差异化动作的截断长度


def norm_name(s) -> str:
    """与产品档案管线同一套名称归一化（NFKC + 去空白），否则会漏匹配。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    for ch in ("\u00a0", "\u3000", "\u200b", "\ufeff"):
        s = s.replace(ch, "")
    return "".join(s.split()).lower()


def _first_sentence(text: str) -> str:
    text = re.sub(r"\s+", "", str(text or ""))
    if not text:
        return ""
    parts = re.split(r"[。；;!！?？]", text, maxsplit=1)
    head = parts[0] or text
    if len(head) > REASON_MAX:
        head = head[:REASON_MAX] + "…"
    return head


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", "", str(text or ""))
    return text if len(text) <= n else text[:n] + "…"


def load_index(path: Path = INDEX_PATH) -> dict:
    """返回 {归一化游戏名: 索引条目}；索引不存在时返回空字典（不抛错）。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for name, entry in (data.get("games") or {}).items():
        out[norm_name(name)] = entry
        if entry.get("name"):
            out[norm_name(entry["name"])] = entry
    return out


def load_clone(slug: str, cache: dict) -> dict:
    """按 slug 读档案的 clone 分区（带缓存）；读不到就返回空字典。"""
    if slug in cache:
        return cache[slug]
    clone = {}
    p = DETAIL_DIR / f"{slug}.json"
    if p.exists():
        try:
            clone = json.loads(p.read_text(encoding="utf-8")).get("clone") or {}
        except Exception:
            clone = {}
    cache[slug] = clone
    return clone


def _status(name: str, new_names: set, riser_map: dict) -> str:
    if name in new_names:
        return "新晋"
    if name in riser_map:
        d = riser_map[name]
        return f"上升 {d:+d}" if d else "在榜"
    return "在榜"


def build_clone_list(rows: list[dict], board_label: str,
                     new_names: set | None = None,
                     riser_map: dict | None = None,
                     limits: dict | None = None,
                     index: dict | None = None) -> dict:
    """从榜内行里挑出「值得复刻」的游戏，按复刻结论档位分组。

    rows        榜内行（需要 name / rank，可选 publisher）；行里带 `_board`
                时用来标注该游戏来自哪个榜，典型用法是三榜并集去重后传入
    board_label 候选池的描述，用于展示与说明口径
    new_names   本周新晋游戏名集合（用于标注状态）
    riser_map   {游戏名: 名次变化}（用于标注状态）
    limits      各档位展示条数上限，默认取 GROUP_LIMIT
    """
    new_names = new_names or set()
    riser_map = riser_map or {}
    limits = {**GROUP_LIMIT, **(limits or {})}
    index = load_index() if index is None else index

    buckets: dict[str, list] = {v: [] for v in GROUP_ORDER}
    excluded, missing, unmatched = 0, [], []
    cache: dict = {}

    for r in rows:
        name = r.get("name")
        if not name:
            continue
        entry = index.get(norm_name(name))
        if not entry:
            missing.append(name)
            continue
        clone = load_clone(entry.get("slug") or "", cache)
        verdict = clone.get("verdict") or entry.get("verdict") or ""
        meta = VERDICT_META.get(verdict)
        if not meta:
            unmatched.append(name)
            continue
        if verdict == EXCLUDED:
            excluded += 1
            continue

        buckets[verdict].append({
            "name": entry.get("name") or name,
            "rank": r.get("rank"),
            "board": r.get("_board") or board_label,
            "publisher": r.get("publisher") or "",
            "verdict": verdict,
            "tier": meta["tier"],
            "band": meta["band"],
            "label": meta["label"],
            "verdict_note": meta["note"],
            "cost_level": entry.get("cost_level") or "",
            "cost_score": entry.get("cost_score"),
            "effort": _clip(clone.get("effort") or "", 40),
            "reason": _first_sentence(clone.get("rationale") or ""),
            "how": _clip((clone.get("suggestions") or [""])[0], HOW_MAX),
            "status": _status(name, new_names, riser_map),
            "icon": entry.get("icon") or "",
            "slug": entry.get("slug") or "",
        })

    def key(x):
        return (COST_ORDER.get(x["cost_level"], 9), x["rank"] or 999)

    groups, picked, candidates = [], 0, 0
    for verdict in GROUP_ORDER:
        items = sorted(buckets[verdict], key=key)
        candidates += len(items)
        if not items:
            continue
        meta = VERDICT_META[verdict]
        groups.append({
            "verdict": verdict,
            "band": meta["band"],
            "tier": meta["tier"],
            "label": meta["label"],
            "short": meta["short"],
            "note": meta["note"],
            "items": items[:limits.get(verdict, 3)],
            "total": len(items),
        })
        picked += min(len(items), limits.get(verdict, 3))

    return {
        "board": board_label,
        "pool": len(rows),
        "matched": len(rows) - len(missing),
        "groups": groups,
        "picked": picked,
        "candidates": candidates,
        "excluded": excluded,
        "missing": missing,
        "unmatched": unmatched,
        "generated_from": "data/detail/index.json",
    }


def merge_pool(boards: list[dict], top_n: int = 20) -> list[dict]:
    """三榜并集去重：同一款游戏取最好的名次，并记录它来自哪个榜。

    只看畅销榜会漏掉只在人气榜 / 畅玩榜上跑的产品（例如靠玩法创新而非
    商业化冲榜的新品），所以候选池按三榜并集取。
    """
    best: dict = {}
    for b in boards:
        label = b.get("board") or ""
        for r in (b.get("rows") or [])[:top_n]:
            name = r.get("name")
            if not name:
                continue
            rank = r.get("rank") or 999
            cur = best.get(name)
            if cur is None or rank < (cur.get("rank") or 999):
                best[name] = {"name": name, "rank": rank,
                              "publisher": r.get("publisher") or "",
                              "_board": label}
    return sorted(best.values(), key=lambda x: x["rank"])


def collect_status(result: dict) -> tuple[set, dict]:
    """跨三榜汇总「新晋」与「上升」状态，用于给清单里的游戏打标。"""
    new_names, risers = set(), {}
    for b in result.get("boards") or []:
        for x in b.get("new_entrants") or []:
            if x.get("name"):
                new_names.add(x["name"])
        for x in b.get("risers") or []:
            if x.get("name"):
                risers[x["name"]] = x.get("delta") or 0
    return new_names, risers


def attach(result: dict, limits: dict | None = None,
           board_label: str = "微信三榜（畅销/人气/畅玩）TOP20 并集") -> dict:
    """给周报结果挂上 `clone` 清单，并回填 meta 里的口径字段。

    候选池取三榜并集：只看畅销榜会漏掉只在人气榜 / 畅玩榜上跑的产品。
    """
    pool = merge_pool(result.get("boards") or [])
    new_names, risers = collect_status(result)
    res = build_clone_list(pool, board_label, new_names, risers, limits=limits)
    result["clone"] = res
    meta = result.setdefault("meta", {})
    meta["clone_board"] = board_label
    meta["clone_pool_size"] = res["pool"]
    meta["clone_index_size"] = len(load_index())
    return res


def main() -> int:
    """自检：直接跑一份当前三榜并集的清单。"""
    import sys
    sys.path.insert(0, str(HERE))
    import analyze as A  # noqa: E402

    result = A.analyze()
    res = attach(result)
    print(f"{res['board']}：候选 {res['pool']} 款，有档案 {res['matched']} 款，"
          f"入选 {res['candidates']} 款，结论不建议 {res['excluded']} 款，"
          f"无档案 {len(res['missing'])} 款")
    for g in res["groups"]:
        print(f"\n【{g['band']}】{g['verdict']}（{g['label']}）"
              f"共 {g['total']} 款，展示 {len(g['items'])} 款")
        print(f"    口径：{g['note']}")
        for x in g["items"]:
            print(f"  #{x['rank']:<3} {x['name']}　成本 {x['cost_level']}"
                  f"　{x['board']}　{x['status']}")
            print(f"        {x['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
