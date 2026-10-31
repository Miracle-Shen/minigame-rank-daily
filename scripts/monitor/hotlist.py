#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群消息 · 本周榜单里「值得单独说的 1–2 款」。

## 为什么换掉了原来的固定版式

原版固定输出「微信前三 / 抖音前三 / 全平台 TOP1」。问题不在信息少，而在
**没变化也占版面**：TOP3 与上周一模一样时仍要占 9 行，读者读完的结论是
「本周没事」，却付出了扫 30 行的成本。TOP3 三个月不动，它就是噪音。

现在的规则是：先由 story.py 找出**事件**（新进榜 / 首进 TOP10 / ±5 位 /
连续两周同向 / 新增平台），再决定说多少：

  * 最多展开 2 款：游戏是什么怎么玩 → 本周发生了什么 → 玩家具体在说什么；
  * 其余只有名次数据的，合并成一句；
  * 一款都没命中事件 → 只发一句话，不凑字数。

## 文案硬约束（audit() 逐条自检）

  * 不写「值得关注 / 后续可期 / 释放信号 / 建议持续观察」这类空话；
  * 没有 ≥10 条近期评论，就不出现「玩家认为 / 口碑不错」；
  * 结论只在数据支持时给：连续两周上涨才说「热度仍在上升」，
    多平台同时前进才说「多个平台同时升温」，单次名次变化只描述事实；
  * 正文控制在 350 个汉字内（企业微信手机端一屏）。

口径与 analyze.py / clone.py / story.py 共用，不在这里另起炉灶。

命令行：
    python scripts/monitor/hotlist.py                        # 最新一期预览
    python scripts/monitor/hotlist.py --log                  # 附带筛选诊断
    python scripts/monitor/hotlist.py --strict               # 自检不过就退出 1
    python scripts/monitor/hotlist.py --out reports/push-2026-10-04.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analyze as A  # noqa: E402
import classify as C  # noqa: E402
import clone as CL  # noqa: E402
import reviews as RV  # noqa: E402
import story as S  # noqa: E402

ROOT = HERE.parents[1]
LATEST_PATH = ROOT / "data" / "latest.json"

HAN_LIMIT = 350          # 正文字数预算（按汉字计，标点/数字不计）
NO_CHANGE = "本周榜单变化不大，没有需要单独提醒的新情况。"

# 明令禁止的空泛表述：出现即判自检不通过
BANNED = ("值得关注", "后续可期", "释放信号", "建议持续观察",
          "口碑不错", "玩家认为", "值得期待")
# 没有评论时，这些词一句都不许出现（否则就是在编玩家态度）
ATTITUDE_WORDS = ("玩家", "评论", "口碑", "好评", "差评")

_HAN = re.compile(r"[\u4e00-\u9fff]")
_SENT_END = re.compile(r"[。！？!?]")


def han_count(text: str) -> int:
    return len(_HAN.findall(text or ""))


# --------------------------------------------------------------------------
# 平台 / 榜名
# --------------------------------------------------------------------------
def _plat_label(ctx: S.WeeklyContext, pk: str) -> str:
    p = (ctx.cur_snap.get("platforms") or {}).get(pk) or {}
    return p.get("label") or S.PLATFORM_SHORT.get(pk, pk)


def _lead_plat(c: dict) -> str:
    """名次最好的平台 —— 讲故事时以它为主语。"""
    return min(c["plats"], key=lambda pk: c["plats_rank"].get(pk, 999))


def _where(ctx, c) -> str:
    pk = _lead_plat(c)
    boards = (c["boards"].get(pk) or [])
    return f"{_plat_label(ctx, pk)}{boards[0] if boards else ''}"


# --------------------------------------------------------------------------
# 文案片段
# --------------------------------------------------------------------------
def _short_event(ctx, c: dict) -> str:
    """一句话的「发生了什么」，给首句用。"""
    ev = c["events"]
    if "new_top20" in ev:
        return f"新进{_where(ctx, c)}第{c['cur_rank']}"
    if c["delta"]:
        head = "从第%d升到第%d" % (c["base_rank"], c["cur_rank"]) \
            if c["delta"] > 0 else "从第%d降到第%d" % (c["base_rank"], c["cur_rank"])
        if "streak2" in ev:
            head += "，连续第二周" + ("上涨" if c["delta"] > 0 else "下跌")
        return head
    if "new_platform" in ev:
        return "新增上榜平台"
    if "first_top10" in ev:
        return f"首次进入 TOP10（第{c['cur_rank']}）"
    return "名次有变动"


def _happened(ctx, c: dict) -> str:
    """完整版「本周发生了什么」，给正文用。"""
    segs = []
    if "new_top20" in c["events"]:
        segs.append(f"上周不在榜，本周新进{_where(ctx, c)}第{c['cur_rank']}")
    elif "streak2" in c["events"]:
        # 连续两周同向时不再重复「↑5 位」—— 三段名次链本身就是变化量
        chain = "→".join(str(x) for x in (c["base2_rank"], c["base_rank"],
                                          c["cur_rank"]) if x is not None)
        segs.append(f"连续第二周{'上涨' if c['streak'] == 'up' else '下跌'}"
                    f"（{chain}）")
    elif c["delta"]:
        segs.append(f"从第{c['base_rank']}{'升到' if c['delta'] > 0 else '降到'}"
                    f"第{c['cur_rank']}（{'↑' if c['delta'] > 0 else '↓'}"
                    f"{abs(c['delta'])} 位）")
    if "first_top10" in c["events"] and "new_top20" not in c["events"]:
        segs.append("首次进入 TOP10")
    if c["new_plats"]:
        names = "、".join(_plat_label(ctx, pk) for pk in c["new_plats"])
        segs.append(f"本周新增出现在{names}")
    if not segs:
        segs.append("名次有变动")

    text = "；".join(segs)

    # 结论只在数据支持时给：连续两周上涨 → 热度仍在上升；多平台同向 → 同时升温
    if c["streak"] == "up":
        return text + "，热度仍在上升。"
    if len(c["up_plats"]) >= 2:
        return text + f"，{len(c['up_plats'])} 个平台同时升温。"
    if len(c["down_plats"]) >= 2:
        return text + f"，{len(c['down_plats'])} 个平台同时下滑。"
    return text + "。"


def _brief(ctx, compressed: list[dict]) -> str:
    """其余只有名次数据的：一句话收口，不展开。"""
    moved = [c for c in compressed if c["delta"]]
    moved.sort(key=lambda c: -abs(c["delta"]))
    segs = []
    for c in moved[:2]:
        segs.append(f"《{c['name']}》从第{c['base_rank']}"
                    f"{'升到' if c['delta'] > 0 else '降到'}第{c['cur_rank']}")
    rest = len(compressed) - len(segs)
    if not segs:
        news = [c for c in compressed if "new_top20" in c["events"]]
        if news:
            names = "、".join(f"《{c['name']}》" for c in news[:2])
            more = f"等 {len(news)} 款" if len(news) > 2 else ""
            return f"{names}{more}新进榜，榜单其余部分变化不大。"
        if not compressed:
            return ""
        return f"{len(compressed)} 款只是小幅变动，没有需要单说的。"
    text = "、".join(segs)
    if rest > 0:
        text += f"，另有 {rest} 款只是新进榜或小幅变动"
    return text + "。"


def _headline(ctx, stories: list[dict], new_count: int) -> str:
    """消息第一句：本周最明显的那件事。"""
    if not stories:
        return ""      # 由 _brief / NO_CHANGE 兜底
    top = stories[0]
    short = _short_event(ctx, top)
    tail = f"另有 {new_count} 款新进榜。" if new_count >= 2 else ""
    if len(stories) > 1:
        return (f"本周最明显的是《{top['name']}》：{short}；"
                f"另一款值得单独说的是《{stories[1]['name']}》。{tail}")
    return f"本周最明显的是《{top['name']}》：{short}。{tail}"


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------
def _type_and_play(name: str, row: dict, index: dict, cache: dict,
                   mapdata, learned) -> tuple[str, str, bool]:
    row = C.classify_row(row, mapdata, learned) if row else {}
    det = CL.detail_of(name, index, cache)
    typ = CL.type_of(row.get("l1") or "", row.get("l2") or "", det)
    play = CL.play_of(det)
    has_play = bool(play) and play != "暂无产品档案"
    return typ, (play if has_play else ""), has_play


def _collect(ctx: S.WeeklyContext, cands: list[dict], rv_blob: dict,
             as_of: str, index: dict, cache: dict, mapdata, learned) -> list[dict]:
    """给每个候选补上类型 / 玩法 / 评论摘要 / 优先级。"""
    out = []
    for c in cands:
        typ, play, has_play = _type_and_play(
            c["name"], c.get("row") or {}, index, cache, mapdata, learned)
        entry = RV.game_entry(rv_blob, c["name"]) if rv_blob else None
        summ = RV.summarize(entry, as_of) if entry else None
        feedback = (summ or {}).get("sentence") or ""
        n_valid = (summ or {}).get("n") or 0
        has_reviews = bool((summ or {}).get("enough"))
        ready, why = S.story_ready(c, has_reviews=has_reviews, has_play=has_play)
        out.append({
            **c,
            "type": typ,
            "play": play,
            "has_play": has_play,
            "feedback": feedback,
            "feedback_n": n_valid,
            "feedback_used": list((summ or {}).get("used") or []),
            "has_reviews": has_reviews,
            "ready": ready,
            "why": why,
            "score": S.priority(c, has_reviews=has_reviews, has_play=has_play),
        })
    out.sort(key=lambda x: (-x["score"], x["cur_rank"]))
    return out


def build_card(result: dict, hist: list[tuple[str, dict]] | None = None,
               v2: bool = False, site_url: str = "",
               review_blob: dict | None = None) -> dict:
    """产出群消息文本 + 结构化数据 + 自检所需的诊断信息。

    `v2` 只为兼容旧调用（send_wecom 会传）保留：新文案改用 ↑/↓ 表达涨跌，
    不再输出 <font> 标签，所以这个开关现在不影响结果。
    """
    meta = result.get("meta") or {}
    hist = hist if hist is not None else A.load_history()
    if not hist:
        raise SystemExit("data/daily 为空，无法生成群消息")

    cur_date = meta.get("week_end") or hist[-1][0]
    ctx = S.WeeklyContext(hist, cur_date, meta.get("baseline_date"))
    as_of = ctx.cur_date
    if not ctx.base_snap:
        # 历史快照不足一轮：不假装「本周没变化」，明确说暂不判断
        return _insufficient(meta, as_of, site_url)

    # 品类归一化与产品档案：与图文周报同一套
    learned = C.build_learned(A.collect_rows(hist, A.DEFAULT_BOARDS))
    mapdata = C.load_map() if hasattr(C, "load_map") else None
    index = CL.load_index()
    cache: dict = {}

    if review_blob is None:
        review_blob = RV.load(as_of) or {}

    cands = S.detect_candidates(ctx)
    enriched = _collect(ctx, cands, review_blob, as_of, index, cache,
                        mapdata, learned)

    stories = [c for c in enriched if c["ready"]][:S.EXPAND_MAX]
    expanded = {c["name"] for c in stories}
    compressed = [c for c in enriched if c["name"] not in expanded]
    new_count = sum(1 for c in enriched if "new_top20" in c["events"])

    head = _headline(ctx, stories, new_count)
    brief = _brief(ctx, compressed)

    data = {
        "meta": {
            "period": _period(ctx.base_date or meta.get("baseline_date"), as_of),
            "week_end": as_of,
            "baseline_date": ctx.base_date,
            "site_url": site_url,
        },
        "headline": head,
        "stories": [{
            "name": c["name"],
            "type": c["type"],
            "play": c["play"],
            "happened": _happened(ctx, c),
            "feedback": c["feedback"],
            "feedback_n": c["feedback_n"],
            "feedback_used": c["feedback_used"],
            "events": c["events"],
            "why": c["why"],
            "cur_rank": c["cur_rank"],
            "base_rank": c["base_rank"],
            "delta": c["delta"],
        } for c in stories],
        "brief": brief,
        "no_change": bool(not stories and not brief),
        "diagnostics": {
            "candidates": [{
                "name": c["name"], "events": c["events"], "delta": c["delta"],
                "cur_rank": c["cur_rank"], "score": c["score"],
                "ready": c["ready"], "why": c["why"],
                "reviews": c["feedback_n"],
            } for c in enriched],
            "expanded": [c["name"] for c in stories],
            "compressed": [c["name"] for c in compressed],
            "stable_top": S.stable_top(ctx, enriched),
            "dropped_layout": ["微信前三", "抖音前三", "全平台 TOP1（固定版式已取消）"],
        },
    }

    data["text"] = _render(data)
    data["audit"] = audit(data)
    return data


def _insufficient(meta: dict, as_of: str, site_url: str) -> dict:
    """没有可比基准时的最小产出：一句说明 + 自检，不发任何判断。"""
    text = (f"# 游戏周热榜　{_period(meta.get('baseline_date'), as_of)}\n\n"
            "本周缺少可比基准（基准日在历史快照里找不到），暂不判断变化。")
    if site_url:
        text += f"\n\n[数据主页]({site_url})"
    data = {"meta": {"period": _period(meta.get("baseline_date"), as_of), "week_end": as_of,
                     "baseline_date": None, "site_url": site_url},
            "headline": "", "stories": [], "brief": "", "no_change": True,
            "diagnostics": {"candidates": [], "expanded": [], "compressed": [],
                            "stable_top": [], "dropped_layout": []},
            "text": text}
    data["audit"] = audit(data)
    return data


def _period(baseline: str | None, week_end: str) -> str:
    b, e = baseline, week_end
    fmt = "%m.%d"
    try:
        return (f"{datetime.strptime(b, '%Y-%m-%d').strftime(fmt)}"
                f"–{datetime.strptime(e, '%Y-%m-%d').strftime(fmt)}")
    except Exception:  # noqa: BLE001
        return str(e or "")


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def _render(data: dict) -> str:
    """按预算逐级收缩：先砍第二款的玩家段，再砍第二款，最后砍「其余」。"""
    for level in range(5):
        text = _render_once(data, level)
        if han_count(text) <= HAN_LIMIT:
            return text
    return text


def _render_once(data: dict, level: int) -> str:
    """两种版式：

    有可展开内容 → 首句 + 每款三行 + 「其余」一句
    只有名次数据 → 正文就是那一句（不再加首句，避免为凑结构造一句空话）
    """
    m = data["meta"]
    L = [f"# 游戏周热榜　{m['period']}", ""]

    stories = data["stories"]
    if level >= 2:
        stories = stories[:1]
    brief = data["brief"]

    if not stories:
        # 无展开：整条消息就一句，没有事件时用固定句式
        L.append(brief or NO_CHANGE)
        L.append("")
    else:
        L.append(data["headline"])
        L.append("")
        for i, s in enumerate(stories, 1):
            L.append(f"**{i}. {s['name']}**（{s['type']}）")
            if s["play"]:
                L.append(f"　怎么玩：{s['play']}")
            L.append(f"　本周：{s['happened']}")
            if s["feedback"] and not (level >= 1 and i > 1):
                L.append(f"　玩家：{s['feedback']}")
            L.append("")
        if brief and level < 4:
            if level >= 3:
                brief = "其余名次变动不再逐款列出。"
            L.append(f"其余：{brief}")
            L.append("")

    if m.get("site_url"):
        L.append(f"[数据主页]({m['site_url']})")
    return "\n".join(L).strip()


# --------------------------------------------------------------------------
# 自检：把验收标准写成可执行的检查
# --------------------------------------------------------------------------
def audit(data: dict, limit: int = HAN_LIMIT) -> list[dict]:
    text = data.get("text") or ""
    stories = data.get("stories") or []
    checks = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("TOP3 未变化时不重复列出",
        "前三" not in text and "TOP1）" not in text,
        "固定版式已取消，正文无「前三」段落")
    add(f"每期最多展开 {S.EXPAND_MAX} 款", len(stories) <= S.EXPAND_MAX,
        f"实际展开 {len(stories)} 款")
    add(f"正文 ≤ {limit} 汉字", han_count(text) <= limit,
        f"实际 {han_count(text)} 汉字")

    bad = [w for w in BANNED if w in text]
    add("无空泛表述", not bad, "命中：" + "、".join(bad) if bad else "未命中禁用词")

    for s in stories:
        add(f"《{s['name']}》有实据可展开",
            bool(s["play"] or s["feedback"] or s["events"]),
            "、".join(s["why"]) or "无")

    fed = [s for s in stories if s["feedback"]]
    tracked = bool(fed) and all(s["feedback_used"] for s in fed)
    add("玩家反馈可追溯到原始评论", tracked or not fed,
        "每段反馈都带评论 ID" if fed and tracked
        else ("存在没有来源的反馈" if fed else "本期无玩家反馈，无需追溯"))

    if not any(s["feedback"] for s in stories):
        hit = [w for w in ATTITUDE_WORDS if w in text]
        add("无评论时不写玩家态度", not hit,
            "命中：" + "、".join(hit) if hit else "全文未出现玩家态度表述")

    brief = data.get("brief") or ""
    add("只有名次数据的部分 ≤ 2 句", len(_SENT_END.findall(brief)) <= 2,
        f"实际 {len(_SENT_END.findall(brief))} 句")

    add("无需提醒时只发一句话",
        (not data.get("no_change")) or text.count("。") == 1,
        "本周无变化，正文仅一句" if data.get("no_change") else "本周有变化，正常展开")
    return checks


def audit_ok(checks: list[dict]) -> bool:
    return all(c["ok"] for c in checks)


# --------------------------------------------------------------------------
def _log_line(card: dict) -> list[str]:
    """Actions 日志四问：候选多少 / 谁有可靠评论 / 谁被压缩 / 删了哪些稳定内容。"""
    d = card["diagnostics"]
    cands = d["candidates"]
    L = [f"候选游戏 {len(cands)} 款（命中事件即入池）"]

    if d["expanded"]:
        for c in cands:
            if c["name"] in d["expanded"]:
                L.append(f"  展开 · {c['name']}　事件 {','.join(c['events'])}"
                         f"　名次 #{c['cur_rank']}　{('、'.join(c['why']))}")
    else:
        L.append("  展开 · 无（本期没有可展开的内容）")

    reviewed = [c for c in cands if c["reviews"] >= RV.MIN_VALID]
    L.append("获得可靠评论：" + (
        "、".join(f"{c['name']}({c['reviews']}条)" for c in reviewed)
        if reviewed else f"无（<{RV.MIN_VALID} 条，不写玩家反馈）"))

    packed = [c["name"] for c in cands if c["name"] not in d["expanded"]]
    shown = "、".join(packed[:10])
    more = f" 等 {len(packed)} 款" if len(packed) > 10 else ""
    L.append(f"只有名次数据被压缩：{shown + more if packed else '无'}")

    stable = [f"{x['label']} {'、'.join(x['names'])}"
              for x in d["stable_top"] if x["names"]]
    L.append("未列出的稳定榜单内容：" + ("；".join(stable) if stable else "无"))
    L.append("删除的固定版式：" + "、".join(d["dropped_layout"]))
    return L


def _pick_report() -> Path:
    from report import REPORT_DIR  # noqa: PLC0415

    files = sorted(REPORT_DIR.glob("weekly-*.json"))
    if not files:
        raise SystemExit("reports/ 下没有周报 JSON，请先跑 report.py")
    return files[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description="群消息 · 本周值得说的 1–2 款")
    ap.add_argument("--report", default=None, help="周报 JSON（默认最新一期）")
    ap.add_argument("--latest", action="store_true", help="用 data/latest.json 现算一期")
    ap.add_argument("--reviews", default=None,
                    help="指定评论文件 data/reviews/YYYY-MM-DD.json（默认按周期自动取）")
    ap.add_argument("--v2", action="store_true", help="去掉 <font> 颜色标签")
    ap.add_argument("--site-url", default="", help="数据主页地址")
    ap.add_argument("--out", default=None, help="写出到文件（推送预览）")
    ap.add_argument("--json", action="store_true", help="附结构化数据")
    ap.add_argument("--log", action="store_true", help="打印筛选诊断（Actions 日志用）")
    ap.add_argument("--strict", action="store_true", help="自检不通过时退出码 1")
    args = ap.parse_args()

    if args.latest:
        result = A.analyze()
        CL.attach(result)
    else:
        path = Path(args.report) if args.report else _pick_report()
        result = json.loads(path.read_text(encoding="utf-8"))
        print(f"# 取自 {path.name}", file=sys.stderr)

    blob = None
    if args.reviews:
        blob = json.loads(Path(args.reviews).read_text(encoding="utf-8"))

    site = args.site_url or (result.get("meta") or {}).get("site_url") or ""
    card = build_card(result, v2=args.v2, site_url=site, review_blob=blob)
    print(card["text"])
    print(f"\n--- {han_count(card['text'])} 汉字 / {len(card['text'].encode('utf-8'))} "
          f"字节（上限 {HAN_LIMIT} 汉字 / 4096 字节）---", file=sys.stderr)

    if args.out:
        Path(args.out).write_text(card["text"] + "\n", encoding="utf-8")
        side = Path(args.out).with_suffix(".json")
        side.write_text(json.dumps({k: v for k, v in card.items() if k != "text"},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出 {args.out}（诊断见 {side.name}）", file=sys.stderr)

    if args.log:
        print("\n[内容筛选]", file=sys.stderr)
        for line in _log_line(card):
            print(line, file=sys.stderr)
        print("\n[自检]", file=sys.stderr)
        for c in card["audit"]:
            print(f"  {'✓' if c['ok'] else '✗'} {c['check']}（{c['detail']}）",
                  file=sys.stderr)
            if not c["ok"]:
                print(f"::warning::文案自检未通过：{c['check']}（{c['detail']}）")

    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))

    if args.strict and not audit_ok(card["audit"]):
        print("自检未通过", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
