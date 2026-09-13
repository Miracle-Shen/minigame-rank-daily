#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报渲染：把 analyze.py 的结果输出成 Markdown / HTML。

HTML 用全内联样式 + 表格布局，刻意不依赖外部 CSS/JS —— 直接粘进邮件正文、
或在 GitHub Pages / 任何浏览器里打开都能正常显示。

配色遵循中文习惯：上涨/上升用红色，下跌/下降用绿色。
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analyze as A  # noqa: E402

ROOT = HERE.parents[1]
REPORT_DIR = ROOT / "reports"

# 涨红跌绿（中文习惯）
C_UP = "#c0392b"
C_DOWN = "#1e8e3e"
C_NEW = "#e67e22"
C_MUTED = "#6b7280"
C_LINE = "#e5e7eb"
C_HEAD = "#1f2937"


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _fmt_delta(d: int) -> str:
    return f"+{d}" if d > 0 else str(d)


# --------------------------------------------------------------------------
# 摘要
# --------------------------------------------------------------------------
def build_summary(result: dict) -> list[str]:
    """从分析结果提炼几条可直接读的结论。"""
    p = result["primary"]
    m = result["meta"]
    out = []

    hs = p.get("head_stability") or {}
    if hs:
        out.append(
            f"畅销榜 TOP{hs['head_n']} 留存 {hs['retention_rate']}%，"
            f"换血 {hs['turnover']} 席"
            + ("—— 头部高度固化，新进者短期难撬动"
               if hs["retention_rate"] >= 80 else
               "—— 头部仍在轮动，存在切入窗口"
               if hs["retention_rate"] <= 60 else
               "—— 头部相对稳定"))

    cs = p["category_structure"]
    if cs["l1"]:
        top = cs["l1"][0]
        top3 = "、".join(f"{x['name']} {x['share']}%" for x in cs["l1"][:3])
        out.append(f"品类以{top['name']}为主（{top['share']}%）；前三为 {top3}")

    ne, ri = p["new_entrants"], p["risers"]
    if ne:
        out.append(f"畅销榜新进 {len(ne)} 款：" +
                   "、".join(x["name"] for x in ne[:4]))
    if ri:
        out.append(f"名次显著上升 {len(ri)} 款：" +
                   "、".join(f"{x['name']}(+{x['delta']})" for x in ri[:4]))

    wl, ws = p.get("waist_long", []), p.get("waist_short", [])
    if wl or ws:
        out.append(f"腰部（{A.HEAD_N + 1}-{A.TOP_N} 名）"
                   f"连续在榜 ≥{A.WEEK_DAYS} 天的长线产品 {len(wl)} 款，"
                   f"新进/短期 {len(ws)} 款"
                   + ("—— 腰部以短周期产品为主，注意区分买量冲榜"
                      if len(ws) > len(wl) else "—— 腰部有不少长线产品"))

    if m.get("baseline_gap_days") and m["baseline_gap_days"] != A.WEEK_DAYS:
        out.append(f"⚠️ 本次实际对比间隔为 {m['baseline_gap_days']} 天"
                   f"（历史数据所限），不是标准 7 天")
    return out


# --------------------------------------------------------------------------
# 表格片段
# --------------------------------------------------------------------------
def _tbl(headers: list[str], rows: list[list[str]], aligns=None) -> str:
    aligns = aligns or ["left"] * len(headers)
    th = "".join(
        f'<th style="padding:8px 10px;border-bottom:2px solid {C_LINE};'
        f'text-align:{aligns[i]};font-size:12px;color:{C_MUTED};'
        f'font-weight:600;white-space:nowrap;">{esc(h)}</th>'
        for i, h in enumerate(headers))
    body = ""
    for r in rows:
        tds = "".join(
            f'<td style="padding:8px 10px;border-bottom:1px solid {C_LINE};'
            f'text-align:{aligns[i]};font-size:13px;color:{C_HEAD};">{c}</td>'
            for i, c in enumerate(r))
        body += f"<tr>{tds}</tr>"
    if not rows:
        body = (f'<tr><td colspan="{len(headers)}" style="padding:12px;'
                f'font-size:13px;color:{C_MUTED};">本周无</td></tr>')
    return (f'<table style="width:100%;border-collapse:collapse;'
            f'font-family:inherit;">'
            f'<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>')


def _sec(title: str, subtitle: str = "") -> str:
    sub = (f'<div style="font-size:12px;color:{C_MUTED};margin-top:2px;">'
           f'{esc(subtitle)}</div>' if subtitle else "")
    return (f'<h2 style="font-size:16px;color:{C_HEAD};margin:26px 0 4px;'
            f'padding-left:9px;border-left:3px solid {C_HEAD};">{esc(title)}</h2>{sub}')


def _dir_cell(delta: int) -> str:
    color = C_UP if delta > 0 else C_DOWN
    arrow = "▲" if delta > 0 else "▼"
    return (f'<span style="color:{color};font-weight:600;">'
            f'{arrow} {abs(delta)}</span>')


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def render_html(result: dict) -> str:
    m = result["meta"]
    p = result["primary"]
    period = (f"{m['baseline_date']} → {m['week_end']}"
              if m.get("baseline_date") else m["week_end"])

    parts = [
        '<div style="max-width:760px;margin:0 auto;padding:4px 2px;'
        'font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\','
        '\'Microsoft YaHei\',sans-serif;color:%s;line-height:1.55;">' % C_HEAD,
        f'<h1 style="font-size:20px;margin:0 0 4px;">微信小游戏周报</h1>',
        f'<div style="font-size:13px;color:{C_MUTED};margin-bottom:16px;">'
        f'{esc(period)}　·　数据源：引力引擎（匿名 TOP{A.TOP_N}）　·　'
        f'生成 {esc(m["generated_at"][:16].replace("T", " "))}</div>',
    ]

    # ---- 摘要 ----
    parts.append('<div style="background:#f9fafb;border:1px solid %s;'
                 'border-radius:8px;padding:14px 16px;">' % C_LINE)
    parts.append(f'<div style="font-size:13px;font-weight:700;'
                 f'color:{C_HEAD};margin-bottom:8px;">本周要点</div>')
    parts.append('<ul style="margin:0;padding-left:18px;">')
    for s in build_summary(result):
        parts.append(f'<li style="font-size:13.5px;margin:5px 0;">{esc(s)}</li>')
    parts.append("</ul></div>")

    # ---- 一、大盘格局 ----
    parts.append(_sec("一、大盘格局", f"基准：{p['board']}（微信小游戏）"))

    cs = p["category_structure"]
    parts.append('<div style="font-size:13px;font-weight:600;margin:14px 0 6px;">'
                 '1.1 品类结构</div>')
    rows = [[esc(x["name"]),
             f'<b>{x["share"]}%</b>',
             str(x["count"]),
             esc("、".join(x["examples"][:3]))] for x in cs["l1"]]
    parts.append(_tbl(["品类(L1)", "占比", "款数", "代表产品"], rows,
                      ["left", "right", "right", "left"]))

    l2 = [x for x in cs["l2"] if x["name"] not in ("其他", "待核")][:8]
    if l2:
        parts.append('<div style="font-size:13px;font-weight:600;'
                     'margin:16px 0 6px;">1.2 细分玩法(L2) TOP8</div>')
        parts.append(_tbl(
            ["玩法(L2)", "占比", "款数"],
            [[esc(x["name"]), f'{x["share"]}%', str(x["count"])] for x in l2],
            ["left", "right", "right"]))

    cc = p["concentration"]
    parts.append('<div style="font-size:13px;font-weight:600;'
                 'margin:16px 0 6px;">1.3 头部集中度</div>')
    parts.append(
        f'<div style="font-size:12.5px;color:{C_MUTED};margin-bottom:8px;">'
        f'在榜 {cc["total"]} 款来自 {cc["distinct_publishers"]} 家发行商；'
        f'第一大发行商占 {cc["top1_share"]}%，前三合计 {cc["top3_share"]}%。'
        f'</div>')
    parts.append(_tbl(
        ["发行商", "在榜款数", "占比"],
        [[esc(x["publisher"]), str(x["count"]), f'{x["share"]}%']
         for x in cc["top_publishers"]],
        ["left", "right", "right"]))

    # ---- 二、异动信号 ----
    parts.append(_sec("二、异动信号",
                      f"与 {p['baseline_date'] or '—'} 对比；仅统计榜内 "
                      f"TOP{A.TOP_N} 内的变化"))

    parts.append('<div style="font-size:13px;font-weight:600;'
                 'margin:14px 0 6px;">2.1 新晋者'
                 f'<span style="font-weight:400;color:{C_MUTED};font-size:12px;">'
                 f'　含从榜外升入的产品</span></div>')
    parts.append(_tbl(
        ["游戏", "当前名次", "品类", "发行商", "连续在榜"],
        [[f'<span style="color:{C_NEW};font-weight:600;">新</span> {esc(x["name"])}',
          f'#{x["rank"]}',
          esc(x["l1"]) + (f'·{esc(x["l2"])}' if x.get("l2") else ""),
          esc(x["publisher"] or "—"),
          f'{x["streak"]} 天'] for x in p["new_entrants"]],
        ["left", "right", "left", "left", "right"]))

    parts.append('<div style="font-size:13px;font-weight:600;'
                 'margin:16px 0 6px;">2.2 上升态势</div>')
    parts.append(_tbl(
        ["游戏", "名次变化", "当前", "品类", "发行商"],
        [[esc(x["name"]),
          _dir_cell(x["delta"]),
          f'#{x["rank"]}',
          esc(x["l1"]) + (f'·{esc(x["l2"])}' if x.get("l2") else ""),
          esc(x["publisher"] or "—")] for x in p["risers"]],
        ["left", "right", "right", "left", "left"]))

    if p["fallers"]:
        parts.append('<div style="font-size:13px;font-weight:600;'
                     'margin:16px 0 6px;">2.3 下滑提示</div>')
        parts.append(_tbl(
            ["游戏", "名次变化", "当前", "品类"],
            [[esc(x["name"]), _dir_cell(x["delta"]), f'#{x["rank"]}',
              esc(x["l1"])] for x in p["fallers"]],
            ["left", "right", "right", "left"]))

    # ---- 三、结构稳定性 ----
    parts.append(_sec("三、结构稳定性"))
    hs = p.get("head_stability")
    if hs:
        parts.append('<div style="font-size:13px;font-weight:600;'
                     'margin:14px 0 6px;">3.1 头部稳定性</div>')
        parts.append(
            f'<div style="font-size:12.5px;margin-bottom:8px;">'
            f'TOP{hs["head_n"]} 留存 <b>{hs["retention_rate"]}%</b>'
            f'（{hs["retained"]}/{hs["head_n"]}），换血 {hs["turnover"]} 席。'
            + (f'　新进：{esc("、".join(hs["entered"]))}。' if hs["entered"] else "")
            + (f'　掉出：{esc("、".join(hs["exited"]))}。' if hs["exited"] else "")
            + '</div>')

    parts.append('<div style="font-size:13px;font-weight:600;'
                 'margin:16px 0 6px;">3.2 腰部持续性'
                 f'<span style="font-weight:400;color:{C_MUTED};font-size:12px;">'
                 f'　榜内 {A.HEAD_N + 1}-{A.TOP_N} 名</span></div>')
    parts.append(
        f'<div style="font-size:12.5px;color:{C_MUTED};margin-bottom:8px;">'
        f'连续在榜 ≥{A.WEEK_DAYS} 天的长线产品 {len(p["waist_long"])} 款，'
        f'低于 {A.WEEK_DAYS} 天的 {len(p["waist_short"])} 款。'
        f'长线=玩法留存支撑，短期=可能靠买量冲榜。</div>')
    parts.append(_tbl(
        ["游戏", "名次", "连续在榜", "品类", "发行商"],
        [[esc(x["name"]), f'#{x["rank"]}',
          f'<b>{x["streak"]} 天</b>',
          esc(x["l1"]) + (f'·{esc(x["l2"])}' if x.get("l2") else ""),
          esc(x["publisher"] or "—")] for x in p["waist"][:12]],
        ["left", "right", "right", "left", "left"]))

    # ---- 四、三榜速览 ----
    parts.append(_sec("四、三榜速览"))
    parts.append(_tbl(
        ["榜单", "新晋", "上升", "下降", "TOP10 留存"],
        [[esc(b["board"]), str(len(b["new_entrants"])), str(len(b["risers"])),
          str(len(b["fallers"])),
          f'{(b["head_stability"] or {}).get("retention_rate", "—")}%']
         for b in result["boards"]],
        ["left", "right", "right", "right", "right"]))

    # ---- 口径说明 ----
    parts.append(
        f'<div style="margin-top:24px;padding:12px 14px;background:#f9fafb;'
        f'border-radius:8px;border:1px solid {C_LINE};font-size:12px;'
        f'color:{C_MUTED};line-height:1.6;">'
        f'<b>数据说明与口径</b><br>'
        f'· {esc(m["data_limit_note"])}<br>'
        f'· 对比基准：{esc(m["baseline_date"] or "—")} vs '
        f'{esc(m["week_end"])}（间隔 {m["baseline_gap_days"]} 天）<br>'
        f'· 历史快照 {m["snapshot_count"]} 份'
        f'（{esc(m["history_start"])} 起）；品类经行业口径归一化，'
        f'归一化词典覆盖 {m["learned_size"]} 款游戏<br>'
        f'· 品类口径：引力引擎原始字段存在标签拼接、占位值等问题，'
        f'报告中的品类为归一化结果（详见 scripts/monitor/category_map.json）'
        f'</div>')
    parts.append("</div>")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------
def render_markdown(result: dict) -> str:
    m, p = result["meta"], result["primary"]
    period = (f"{m['baseline_date']} → {m['week_end']}"
              if m.get("baseline_date") else m["week_end"])
    L = [f"# 微信小游戏周报", "",
         f"**周期** {period}　|　**数据源** 引力引擎（匿名 TOP{A.TOP_N}）"
         f"　|　**生成** {m['generated_at'][:16].replace('T', ' ')}", "",
         "## 本周要点", ""]
    L += [f"- {s}" for s in build_summary(result)]
    L += ["", "## 一、大盘格局", ""]

    cs = p["category_structure"]
    L += ["### 1.1 品类结构", "", "| 品类(L1) | 占比 | 款数 | 代表产品 |",
          "|---|---:|---:|---|"]
    L += [f'| {x["name"]} | {x["share"]}% | {x["count"]} | '
          f'{"、".join(x["examples"][:3])} |' for x in cs["l1"]]
    l2 = [x for x in cs["l2"] if x["name"] not in ("其他", "待核")][:8]
    if l2:
        L += ["", "### 1.2 细分玩法(L2) TOP8", "", "| 玩法 | 占比 | 款数 |",
              "|---|---:|---:|"]
        L += [f'| {x["name"]} | {x["share"]}% | {x["count"]} |' for x in l2]

    cc = p["concentration"]
    L += ["", "### 1.3 头部集中度", "",
          f"在榜 {cc['total']} 款来自 {cc['distinct_publishers']} 家发行商；"
          f"第一大占 {cc['top1_share']}%，前三合计 {cc['top3_share']}%。", "",
          "| 发行商 | 在榜款数 | 占比 |", "|---|---:|---:|"]
    L += [f'| {x["publisher"]} | {x["count"]} | {x["share"]}% |'
          for x in cc["top_publishers"]]

    L += ["", "## 二、异动信号", "",
          f"与 {p['baseline_date'] or '—'} 对比，仅统计榜内 TOP{A.TOP_N} 内变化。",
          "", "### 2.1 新晋者", "",
          "| 游戏 | 当前名次 | 品类 | 发行商 | 连续在榜 |",
          "|---|---:|---|---|---:|"]
    L += [f'| {x["name"]} | #{x["rank"]} | {x["l1"]}'
          f'{("·" + x["l2"]) if x.get("l2") else ""} | '
          f'{x["publisher"] or "—"} | {x["streak"]} 天 |'
          for x in p["new_entrants"]]

    L += ["", "### 2.2 上升态势", "",
          "| 游戏 | 名次变化 | 当前 | 品类 | 发行商 |", "|---|---:|---:|---|---|"]
    L += [f'| {x["name"]} | {"+" if x["delta"] > 0 else ""}{x["delta"]} | '
          f'#{x["rank"]} | {x["l1"]} | {x["publisher"] or "—"} |'
          for x in p["risers"]]

    if p["fallers"]:
        L += ["", "### 2.3 下滑提示", "",
              "| 游戏 | 名次变化 | 当前 | 品类 |", "|---|---:|---:|---|"]
        L += [f'| {x["name"]} | {x["delta"]} | #{x["rank"]} | {x["l1"]} |'
              for x in p["fallers"]]

    L += ["", "## 三、结构稳定性", ""]
    hs = p.get("head_stability")
    if hs:
        L += ["### 3.1 头部稳定性", "",
              f"TOP{hs['head_n']} 留存 **{hs['retention_rate']}%**"
              f"（{hs['retained']}/{hs['head_n']}），换血 {hs['turnover']} 席。"]
        if hs["entered"]:
            L += [f"- 新进：{'、'.join(hs['entered'])}"]
        if hs["exited"]:
            L += [f"- 掉出：{'、'.join(hs['exited'])}"]
    L += ["", "### 3.2 腰部持续性", "",
          f"榜内 {A.HEAD_N + 1}-{A.TOP_N} 名：连续在榜 ≥{A.WEEK_DAYS} 天的"
          f"长线产品 {len(p['waist_long'])} 款，低于 {A.WEEK_DAYS} 天的 "
          f"{len(p['waist_short'])} 款。", "",
          "| 游戏 | 名次 | 连续在榜 | 品类 | 发行商 |", "|---|---:|---:|---|---|"]
    L += [f'| {x["name"]} | #{x["rank"]} | {x["streak"]} 天 | {x["l1"]} | '
          f'{x["publisher"] or "—"} |' for x in p["waist"][:12]]

    L += ["", "## 四、三榜速览", "",
          "| 榜单 | 新晋 | 上升 | 下降 | TOP10 留存 |",
          "|---|---:|---:|---:|---:|"]
    L += [f'| {b["board"]} | {len(b["new_entrants"])} | {len(b["risers"])} | '
          f'{len(b["fallers"])} | '
          f'{(b["head_stability"] or {}).get("retention_rate", "—")}% |'
          for b in result["boards"]]

    L += ["", "---", "", "**数据说明与口径**", "",
          f"- {m['data_limit_note']}",
          f"- 对比基准：{m['baseline_date'] or '—'} vs {m['week_end']}"
          f"（间隔 {m['baseline_gap_days']} 天）",
          f"- 历史快照 {m['snapshot_count']} 份（{m['history_start']} 起）",
          f"- 品类经行业口径归一化，词典覆盖 {m['learned_size']} 款游戏", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="周报渲染")
    ap.add_argument("--format", choices=["md", "html", "both"], default="both")
    ap.add_argument("--baseline", default=None, help="基准日期 YYYY-MM-DD")
    ap.add_argument("--outdir", default=None, help="输出目录（默认 reports/）")
    args = ap.parse_args()

    result = A.analyze(baseline_date=args.baseline)
    outdir = Path(args.outdir) if args.outdir else REPORT_DIR
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"weekly-{result['meta']['week_end']}"
    written = []

    if args.format in ("md", "both"):
        p = outdir / f"{stem}.md"
        p.write_text(render_markdown(result), encoding="utf-8")
        written.append(p)
    if args.format in ("html", "both"):
        p = outdir / f"{stem}.html"
        p.write_text(render_html(result), encoding="utf-8")
        written.append(p)

    # 同时落一份给下游（邮件/站点）直接消费的结构化数据
    p = outdir / f"{stem}.json"
    p.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    written.append(p)

    for p in written:
        print(f"写出 {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
