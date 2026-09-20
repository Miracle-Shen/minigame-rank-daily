#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群消息卡片 · 游戏周热榜（极简版）。

群里只回答三个问题，其余一概不写：

    1. 微信小游戏前三 —— 谁在涨、谁在跌、要不要抄
    2. 抖音小游戏前三 —— 同上
    3. 全部平台最热的一款 —— 谁 + 什么品类 + 本周一句话趋势

周报正文（reports/weekly-*.md|html）不归本模块管，两者互不影响：
本模块只产出一段可以贴进企业微信群的纯文本。

口径（与 analyze.py / clone.py 共用同一套，别在这里另起炉灶）：

  * 「前三」 = 该平台**全部榜并集**去重，同一款游戏取最好名次。
    只看畅销榜会漏掉只在人气榜 / 畅玩榜上跑的产品。
  * 「趋势」 = 本周快照 vs 基准快照（默认 7 天前）的**最好名次**变化。
  * 「品类」 = classify.py 归一化后的 L1（抖音榜缺品类字段，靠跨榜学习回填）。
  * 「复刻建议」 = data/detail 产品档案的 clone 结论，压成一句话。
  * 涨用红、跌用绿（中文习惯，与 report.py 一致）。

企业微信 markdown(v1) 不支持表格 / 列表 / 有序列表，所以编号写成「1.」纯文本、
条目符号用「·」、缩进用全角空格 —— 与 send_wecom.py 的既有约定一致。

命令行：
    python scripts/monitor/hotlist.py                  # 用 data/latest.json 预览
    python scripts/monitor/hotlist.py --v2             # 去掉 <font> 标签
    python scripts/monitor/hotlist.py --report reports/weekly-2026-09-19.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analyze as A  # noqa: E402
import classify as C  # noqa: E402
import clone as CL  # noqa: E402

ROOT = HERE.parents[1]
LATEST_PATH = ROOT / "data" / "latest.json"

# 卡片里各取前三的平台（顺序即展示顺序）
CARD_PLATFORMS = [("wx", "微信小游戏"), ("douyin", "抖音小游戏")]
PICK_N = 3

# 「全部平台最热」的候选范围：跨平台全覆盖，越多人抢越热
ALL_PLATFORMS = ["wx", "douyin", "taptap", "ios", "android"]
# 快照没给 label 时的兜底（正常走 snap["platforms"][pk]["label"]）
PLATFORM_LABEL = {"wx": "微信", "douyin": "抖音", "taptap": "TapTap",
                  "ios": "iOS", "android": "安卓"}

# markdown(v1) 只认这三个颜色名；v2 通道直接去标签
C_UP = "warning"     # 橙红 —— 涨
C_DOWN = "info"      # 绿   —— 跌
C_FLAT = "comment"   # 灰

BOOKMARK = "\u3000"  # 全角空格，v1 里当缩进用
ADVICE_MAX = 26      # 复刻建议里「怎么做」的截断长度
_CLAUSE = re.compile(r"[，,。；;]")


# --------------------------------------------------------------------------
# 快照读取
# --------------------------------------------------------------------------
def platform_rows(snap: dict, plat: str) -> list[dict]:
    """该平台**全部榜**的行，附上它来自哪个榜。"""
    p = (snap.get("platforms") or {}).get(plat) or {}
    out = []
    for b in p.get("boards") or []:
        for r in b.get("rows") or []:
            if not r.get("name"):
                continue
            rr = dict(r)
            rr["_board"] = b.get("label") or ""
            rr["_platform"] = plat
            out.append(rr)
    return out


def best_by_name(rows: list[dict]) -> dict:
    """同一款游戏取最好名次，并记下它上过哪些榜。

    返回 {游戏名: {"name","rank","boards","publisher","row"}}。
    """
    best: dict[str, dict] = {}
    for r in rows:
        n = r.get("name")
        rank = r.get("rank") or 999
        e = best.get(n)
        if e is None:
            best[n] = {"name": n, "rank": rank, "boards": [r.get("_board")],
                       "publisher": r.get("publisher") or "", "row": r}
        else:
            e["boards"].append(r.get("_board"))
            if rank < e["rank"]:
                e["rank"] = rank
                e["row"] = r
    return best


def snap_at_or_before(hist: list[tuple[str, dict]], target: str):
    """取 <= target 的最近一份快照，找不到返回 None。"""
    pick = None
    for d, s in hist:
        if d <= target:
            pick = (d, s)
        else:
            break
    return pick


def _plat_label(snap: dict, plat: str) -> str:
    """平台显示名：优先用快照里的 label，其次兜底表。"""
    p = (snap.get("platforms") or {}).get(plat) or {}
    return p.get("label") or PLATFORM_LABEL.get(plat, plat)


def base_date_of(hist: list[tuple[str, dict]], cur_date: str,
                 days: int = 7) -> str | None:
    tgt = (datetime.strptime(cur_date, "%Y-%m-%d") - timedelta(days=days)
           ).strftime("%Y-%m-%d")
    got = snap_at_or_before(hist, tgt)
    return got[0] if got else None


# --------------------------------------------------------------------------
# 名次索引（算趋势用）
# --------------------------------------------------------------------------
def build_rank_index(hist: list[tuple[str, dict]], plats: list[str]) -> dict:
    """{日期: {游戏名: 最好名次}} —— 在给定平台范围内算。"""
    idx = {}
    for d, snap in hist:
        rows: list[dict] = []
        for pk in plats:
            rows += platform_rows(snap, pk)
        idx[d] = {n: e["rank"] for n, e in best_by_name(rows).items()}
    return idx


def trend_of(name: str, cur_rank: int, base_ranks: dict | None) -> tuple[str, str]:
    """返回 (文案, 颜色名)。基准缺失或该游戏当时不在榜 → 新上榜。"""
    if base_ranks is None:
        return "—", C_FLAT
    prev = base_ranks.get(name)
    if prev is None:
        return "新上榜", C_UP
    d = prev - cur_rank          # 正数 = 名次前进
    if d > 0:
        return f"↑{d}位", C_UP
    if d < 0:
        return f"↓{-d}位", C_DOWN
    return "持平", C_FLAT


# --------------------------------------------------------------------------
# 复刻建议 / 品类
# --------------------------------------------------------------------------
def brief_action(text: str, n: int = ADVICE_MAX) -> str:
    """把档案里的差异化动作压成一句短话。

    档案的 suggestions[0] 常写成「做什么，为什么，还能怎样」的长句，
    群消息里只留最前面那个动作；第一个分句太短时再顺一句，但总长受限。
    """
    s = (text or "").strip()
    if not s:
        return ""
    parts = [p for p in _CLAUSE.split(s) if p]
    if not parts:
        return ""
    out = parts[0]
    if len(out) < 10 and len(parts) > 1:
        merged = f"{out}，{parts[1]}"
        if len(merged) <= n + 6:
            return merged          # 长度已验证，整句给完，不再截
    if len(out) > n:
        cut = out[:n]
        idx = max((cut.rfind(c) for c in "，,。；;、"), default=-1)
        out = cut[:idx] if idx >= n // 2 else cut[:n - 1] + "…"
    return out


def advice_of(name: str, index: dict, cache: dict) -> str:
    """复刻建议：直接给档案里的**确定动作**，不要档位占位词。

    档案里「换皮即用 / 需改一处 / 练手填充」这类档位标签本身不构成结论
    （「需改一处」到底改哪里没说），所以这里只取 `suggestions[0]` 那条动作，
    写成「保留 X 核心」「把题材换成 Y」这种能直接照着做的句子。
    """
    entry = index.get(CL.norm_name(name))
    if not entry:
        return "暂无产品档案，需人工判断"
    cl = CL.load_clone(entry.get("slug") or "", cache)
    verdict = cl.get("verdict") or entry.get("verdict") or ""
    if verdict == CL.EXCLUDED:
        return "不建议抄：IP / 头部已锁死"
    how = brief_action((cl.get("suggestions") or [""])[0] or "")
    if how:
        return how
    meta = CL.VERDICT_META.get(verdict)
    return meta["note"] if meta else "暂无复刻结论"


# --------------------------------------------------------------------------
# 各板块
# --------------------------------------------------------------------------
def picks_for_platform(snap: dict, plat: str, learned: dict, mapdata: dict,
                       base_ranks: dict | None, index: dict,
                       cache: dict, n: int = PICK_N) -> list[dict]:
    """该平台三榜并集 → 按最好名次取前 n，补品类 / 趋势 / 复刻建议。"""
    best = best_by_name(platform_rows(snap, plat))
    tops = sorted(best.values(), key=lambda x: x["rank"])[:n]
    out = []
    for t in tops:
        row = C.classify_row(t["row"], mapdata, learned)
        trend, color = trend_of(t["name"], t["rank"], base_ranks)
        out.append({
            "name": t["name"],
            "rank": t["rank"],
            "boards": t["boards"],
            "l1": row.get("l1") or "其他",
            "l2": row.get("l2") or "",
            "trend": trend,
            "trend_color": color,
            "advice": advice_of(t["name"], index, cache),
        })
    return out


def all_platform_top(snap: dict, learned: dict, mapdata: dict,
                     base_ranks: dict | None, top: int = 1) -> list[dict]:
    """全部平台合并排最热。

    排序键：**跨平台覆盖数** 优先（越多人抢越热）→ 最好名次 → 上榜次数。
    只按名次排会挤出一堆并列第一（每个榜都有个第一名），没有区分度。
    """
    agg: dict[str, dict] = {}
    for pk in ALL_PLATFORMS:
        for n, e in best_by_name(platform_rows(snap, pk)).items():
            cur = agg.get(n)
            if cur is None:
                agg[n] = {"name": n, "rank": e["rank"], "plats": [pk],
                          "boards": list(e["boards"]), "row": e["row"]}
            else:
                if pk not in cur["plats"]:
                    cur["plats"].append(pk)
                cur["boards"] += e["boards"]
                if e["rank"] < cur["rank"]:
                    cur["rank"] = e["rank"]
                    cur["row"] = e["row"]
    ranked = sorted(agg.values(),
                    key=lambda x: (-len(x["plats"]), x["rank"], -len(x["boards"])))
    out = []
    for t in ranked[:top]:
        row = C.classify_row(t["row"], mapdata, learned)
        trend, color = trend_of(t["name"], t["rank"], base_ranks)
        plats = t["plats"]
        note = "、".join(_plat_label(snap, p) for p in plats)
        out.append({
            "name": t["name"],
            "rank": t["rank"],
            "l1": row.get("l1") or "其他",
            "plats": plats,
            "plats_note": note,
            "boards": t["boards"],
            "trend": trend,
            "trend_color": color,
        })
    return out


def week_lines(result: dict) -> tuple[str, str]:
    """（本周变化, 保持不变）—— 两行都得说清「具体是什么」。

    变化行 = 品类涨退 + **新晋游戏的类型分布**（领导要知道新冒出来的是哪类玩法，
    只给「新晋 8 款」这个数字没有信息量）。
    未变行 = 占比没动的品类 + 头部续在榜席位数（榜单里没动的到底是哪部分）。
    """
    p = result.get("primary") or {}
    t = (p.get("category_trend") or {}).get("items") or []

    # ---- 变化 ----
    change = []
    if t:
        up = t[0]
        down = min(t, key=lambda i: i["delta"])
        # 说「占比 +10 个百分点」而不是「+10pp」：pp = percentage point（百分点），
        # 指占比本身挪了 10 个百分点（10% → 20%），不是「涨了 10%」。业务侧不看这个缩写。
        if up["delta"] > 0:
            change.append(f"{up['name']} 占比 {up['delta']:+.0f} 个百分点，接棒")
        if down["delta"] < 0:
            change.append(f"{down['name']} 占比 {down['delta']:+.0f} 个百分点，退坡")
    ne = p.get("new_entrants") or []
    if ne:
        c = Counter(x.get("l1") or "其他" for x in ne)
        top = " / ".join(f"{k} {v}" for k, v in c.most_common(3))
        change.append(f"新晋 {len(ne)} 款（{top}）")
    else:
        change.append("无新晋产品")

    # ---- 未变 ----
    steady = []
    flat = [i for i in t if abs(i["delta"]) < 0.5]
    if flat:
        steady.append("、".join(f"{i['name']} {i['share']:.0f}%" for i in flat[:3])
                      + " 占比未变")
    hs = p.get("head_stability") or {}
    if hs:
        steady.append(f"TOP{hs['head_n']} 中 {hs['retained']} 席继续在榜")
    if not steady:
        l1 = (p.get("category_structure") or {}).get("l1") or []
        if l1:
            steady.append(f"{l1[0]['name']} 仍为第一大品类（{l1[0]['share']:.0f}%）")

    return "；".join(change) if change else "本期样本不足，暂无法给出对比结论", \
        "；".join(steady)


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def _period(meta: dict) -> str:
    b, e = meta.get("baseline_date"), meta.get("week_end")
    if not (b and e):
        return str(e or "")
    fmt = "%m.%d"
    return (f"{datetime.strptime(b, '%Y-%m-%d').strftime(fmt)}"
            f"–{datetime.strptime(e, '%Y-%m-%d').strftime(fmt)}")


def build_card(result: dict, hist: list[tuple[str, dict]] | None = None,
               v2: bool = False, site_url: str = "") -> dict:
    """产出卡片文本 + 其结构化数据（便于自检与二次渲染）。"""
    meta = result.get("meta") or {}
    hist = hist if hist is not None else A.load_history()
    if not hist:
        raise SystemExit("data/daily 为空，无法生成卡片")

    cur_date = meta.get("week_end") or hist[-1][0]
    # 用 week_end 当天那份快照（而不是目录里最后一份，两者可能不是同一天）
    got = snap_at_or_before(hist, cur_date)
    cur_snap = got[1] if got else hist[-1][1]
    # 基准优先跟周报对齐；周报没给就自己往前推 7 天
    base_date = meta.get("baseline_date") or base_date_of(hist, cur_date)

    # 品类归一化：与周报同一套（全历史学习，抖音榜靠它回填）
    learned = C.build_learned(A.collect_rows(hist, A.DEFAULT_BOARDS))
    mapdata = C.load_map() if hasattr(C, "load_map") else None

    index = CL.load_index()
    cache: dict = {}

    per_platform_ranks = {
        pk: build_rank_index(hist, [pk]) for pk, _ in CARD_PLATFORMS
    }
    base_ranks_by_plat = {
        pk: (per_platform_ranks[pk].get(base_date) if base_date else None)
        for pk, _ in CARD_PLATFORMS
    }
    all_ranks = build_rank_index(hist, ALL_PLATFORMS)
    base_all_ranks = all_ranks.get(base_date) if base_date else None

    sections = []
    for pk, label in CARD_PLATFORMS:
        picks = picks_for_platform(cur_snap, pk, learned, mapdata,
                                   base_ranks_by_plat[pk], index, cache)
        sections.append({"platform": pk, "label": label, "picks": picks})

    top = all_platform_top(cur_snap, learned, mapdata, base_all_ranks, top=1)
    change, steady = week_lines(result)

    data = {
        "meta": {
            "period": _period(meta),
            "week_end": cur_date,
            "baseline_date": base_date,
            "site_url": site_url,
        },
        "sections": sections,
        "top1": top[0] if top else None,
        "week_change": change,
        "week_steady": steady,
    }
    data["text"] = render_text(data, v2=v2)
    return data


def _c(text: str, color: str, v2: bool) -> str:
    if v2 or not text:
        return text
    return f'<font color="{color}">{text}</font>'


def render_text(data: dict, v2: bool = False) -> str:
    """渲染成企业微信群机器人可直接发的文本（markdown v1 安全）。"""
    m = data["meta"]
    L = [f"# 游戏周热榜　{m['period']}", ""]

    for i, sec in enumerate(data["sections"], 1):
        L.append(f"**{i}. {sec['label']}（前三）**")
        L.append("")
        for g in sec["picks"]:
            head = (f"· {g['name']}-{g['l1']}-"
                    f"{_c(g['trend'], g['trend_color'], v2)}")
            L.append(head)
            L.append(f"{BOOKMARK}复刻建议：{g['advice']}")
            L.append("")
        if not sec["picks"]:
            L.append("· 本期无数据")
            L.append("")

    n = len(data["sections"]) + 1
    t = data.get("top1")
    L.append(f"**{n}. 🔥全部小游戏（TOP1）**")
    L.append("")
    if t:
        L.append(f"· {t['name']}（{t['l1']}）-"
                 f"{_c(t['trend'], t['trend_color'], v2)}")
        line = (f"{BOOKMARK}覆盖 {t['plats_note']}｜最好名次 #{t['rank']}")
        missed = [p for p in ALL_PLATFORMS if p not in t["plats"]]
        if missed:
            line += ("；" + "、".join(PLATFORM_LABEL.get(p, p) for p in missed)
                     + " 榜内未见")
        L.append(line)
    else:
        L.append("· 本期无数据")
    L.append("")
    L.append(f"{BOOKMARK}本周变化：{data['week_change']}")
    if data.get("week_steady"):
        L.append(f"{BOOKMARK}保持不变：{data['week_steady']}")
    L.append("")

    # 页脚只留数据主页：图文周报入口已下线（群里一屏看结论就够了）。
    if m.get("site_url"):
        L.append(f"[数据主页]({m['site_url']})")
    return "\n".join(L)


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="游戏周热榜 · 群消息卡片")
    ap.add_argument("--report", default=None,
                    help="周报 JSON（默认 reports/ 最新一期）")
    ap.add_argument("--latest", action="store_true", help="用 data/latest.json 做一期")
    ap.add_argument("--v2", action="store_true", help="去掉 <font> 颜色标签")
    ap.add_argument("--site-url", default="", help="数据主页地址")
    ap.add_argument("--out", default=None, help="写出到文件")
    ap.add_argument("--json", action="store_true", help="附结构化数据")
    args = ap.parse_args()

    if args.latest:
        result = A.analyze()
        CL.attach(result)
    else:
        path = Path(args.report) if args.report else _pick_report()
        result = json.loads(path.read_text(encoding="utf-8"))
        print(f"# 取自 {path.name}", file=sys.stderr)

    card = build_card(result, v2=args.v2, site_url=args.site_url)
    print(card["text"])
    size = len(card["text"].encode("utf-8"))
    print(f"\n--- {size} 字节 / 上限 4096 ---", file=sys.stderr)
    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(card["text"], encoding="utf-8")
        print(f"已写出 {args.out}", file=sys.stderr)
    return 0


def _pick_report() -> Path:
    from report import REPORT_DIR  # noqa: PLC0415

    files = sorted(REPORT_DIR.glob("weekly-*.json"))
    if not files:
        raise SystemExit("reports/ 下没有周报 JSON，请先跑 report.py")
    return files[-1]


if __name__ == "__main__":
    sys.exit(main())
