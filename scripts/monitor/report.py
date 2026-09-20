#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报渲染：把 analyze.py 的结果输出成 Markdown / HTML。

版式是**领导视角**的两段式结论（本周趋势 / 值得复刻），明细全部后置为附录：

    ┌ 结论速览 ────────────────────────────────────────────┐
    │ 3-4 条判断，只回答「往哪走」和「抄哪个」                      │
    ├ 一、本周趋势 ─────────────────────────────────────────┤
    │ 头部格局 / 品类走向（近 4 周）/ 本周新变量                    │
    ├ 二、值得复刻 ─────────────────────────────────────────┤
    │ 按复刻结论档位分组（换肤 / 变种创意 / 微创新 / 原样复刻）      │
    ├ 附录 A-D ────────────────────────────────────────────┤
    │ 大盘明细 / 异动明细 / 结构稳定性 / 三榜速览（给执行同学）      │
    └──────────────────────────────────────────────────────┘

HTML 用全内联样式 + 表格布局，刻意不依赖外部 CSS/JS —— 直接粘进邮件正文、
或在 GitHub Pages / 任何浏览器里打开都能正常显示。

配色遵循中文习惯：上涨/上升用红色，下跌/下降用绿色。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analyze as A  # noqa: E402
import clone as CL  # noqa: E402

ROOT = HERE.parents[1]
REPORT_DIR = ROOT / "reports"

# 涨红跌绿（中文习惯）
C_UP = "#c0392b"
C_DOWN = "#1e8e3e"
C_NEW = "#e67e22"
C_MUTED = "#6b7280"
C_LINE = "#e5e7eb"
C_HEAD = "#1f2937"
C_LINK = "#2f6fed"


# --------------------------------------------------------------------------
# 站内链接（周报里必须先给出主页的完整地址）
# --------------------------------------------------------------------------
# 周报会被投递到企业微信群 / 邮件里，读者从群里点进来之后要能一键回到主页看
# 实时数据，所以这里的地址一律是**绝对地址**——相对路径在 GitHub blob 页和
# 邮件客户端里都会失效。
#
# 主页地址优先取环境变量 REPORT_SITE_URL，其次从 git origin 现场推导
# GitHub Pages 地址（仓库换了也不用改代码），最后回落到默认地址。
FALLBACK_SITE_URL = "https://miracle-shen.github.io/minigame-rank-daily/"


def site_base() -> str:
    """站点根地址，形如 https://<owner>.github.io/<repo>/。"""
    for name in ("REPORT_SITE_URL", "SITE_URL"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v.rstrip("/") + "/"
    try:
        remote = subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        remote = ""
    mm = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", remote)
    if mm:
        return (f"https://{mm.group('owner').lower()}.github.io/"
                f"{mm.group('repo')}/")
    return FALLBACK_SITE_URL


def site_links(base: str | None = None) -> dict:
    """周报里用到的站内完整链接。"""
    b = ((base or site_base()).strip()).rstrip("/") + "/"
    return {
        "home": b,                        # 主页 = 榜单仪表盘
        "games": f"{b}game.html",         # 产品档案
        "publishers": f"{b}publishers.html",  # 新厂商冒泡
    }


def game_url(base: str, name: str) -> str:
    """某款游戏在产品档案里的直达地址。"""
    b = (base or "").rstrip("/") + "/"
    return f"{b}game.html?name={quote(str(name or ''), safe='')}"


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _fmt_delta(d: int) -> str:
    return f"+{d}" if d > 0 else str(d)


# --------------------------------------------------------------------------
# 结论速览（领导视角：往哪走 + 抄哪个）
# --------------------------------------------------------------------------
def _head_sentence(p: dict) -> str | None:
    hs = p.get("head_stability") or {}
    if not hs:
        return None
    rate = hs["retention_rate"]
    judge = ("头部高度固化，新进者短期难撬动" if rate >= 80 else
             "头部仍在轮动，存在切入窗口" if rate <= 60 else
             "头部相对稳定")
    return (f"{judge} —— TOP{hs['head_n']} 留存 {rate}%，"
            f"换血 {hs['turnover']} 席")


def _trend_sentence(p: dict) -> str | None:
    """品类走向一句：谁在涨、谁在退（相比于数周前）。"""
    t = p.get("category_trend") or {}
    items = t.get("items") or []
    if items:
        up, down = items[0], min(items, key=lambda i: i["delta"])
        if up["delta"] > 0:
            seg = (f"{up['name']}接棒 —— 占比 {up['share']}%"
                   f"（近 {t['weeks']} 周 {up['delta']:+.0f}pp）")
            if down["delta"] < 0:
                seg += (f"，{down['name']} {down['share']}%"
                        f"（{down['delta']:+.0f}pp）让出份额")
            return seg
    cs = p.get("category_structure") or {}
    l1 = cs.get("l1") or []
    if l1:
        top3 = "、".join(f"{x['name']} {x['share']}%" for x in l1[:3])
        return f"品类以{l1[0]['name']}为主（{l1[0]['share']}%）；前三为 {top3}"
    return None


def _clone_sentence(result: dict) -> str | None:
    """值得复刻一句：先给可立项的那几款，再给总数。"""
    c = result.get("clone") or {}
    groups = c.get("groups") or []
    if not groups:
        return None
    first = groups[0]
    names = "、".join(x["name"] for x in first["items"][:3])
    seg = (f"{first['verdict']} {first['total']} 款（{first['label']}，"
           f"成本最低）—— {names}")
    rest = [g for g in groups[1:] if g["tier"] == 0]
    if rest:
        g = rest[0]
        seg += (f"；{g['verdict']} {g['total']} 款（{g['label']}）"
                f"—— {'、'.join(x['name'] for x in g['items'][:2])}")
    return f"值得复刻 {c.get('candidates', 0)} 款：{seg}"


def build_summary(result: dict, with_clone: bool = True) -> list[str]:
    """提炼几条可直接读的结论：先趋势，后值得复刻。

    with_clone=False 时省掉「值得复刻」那条 —— 群消息里它自成一段，
    放在趋势里会重复。
    """
    p = result["primary"]
    m = result["meta"]
    out = []

    s = _head_sentence(p)
    if s:
        out.append(s)

    s = _trend_sentence(p)
    if s:
        out.append(s)

    ne, ri = p["new_entrants"], p["risers"]
    if ne or ri:
        seg = f"本周新变量：新晋 {len(ne)} 款"
        if ne:
            seg += f"（{'、'.join(x['name'] for x in ne[:3])}）"
        seg += f"、显著上升 {len(ri)} 款"
        if ri:
            seg += ("（" + "、".join(f"{x['name']}(+{x['delta']})"
                                     for x in ri[:3]) + "）")
        out.append(seg)

    if with_clone:
        s = _clone_sentence(result)
        if s:
            out.append(s)

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


def _sec_light(title: str, subtitle: str = "") -> str:
    """附录用的轻量小节标题（灰色，不抢正文的视线）。"""
    sub = (f'<span style="font-size:12px;color:{C_MUTED};">　{esc(subtitle)}</span>'
           if subtitle else "")
    return (f'<div style="font-size:13.5px;font-weight:600;color:{C_MUTED};'
            f'margin:16px 0 6px;">{esc(title)}{sub}</div>')


def _pp(v: float) -> str:
    """百分比点：10.0 → +10pp，-5.5 → -5.5pp。"""
    s = f"{v:+.1f}"
    return (s[:-2] if s.endswith(".0") else s) + "pp"


def _dir_cell(delta: int) -> str:
    color = C_UP if delta > 0 else C_DOWN
    arrow = "▲" if delta > 0 else "▼"
    return (f'<span style="color:{color};font-weight:600;">'
            f'{arrow} {abs(delta)}</span>')


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def _clone_groups_html(result: dict) -> list[str]:
    """「值得复刻」正文（HTML）：按结论档位分组。"""
    c = result.get("clone") or {}
    base = _base_of(result)
    parts: list[str] = []
    if not c.get("groups"):
        return [f'<div style="font-size:13px;color:{C_MUTED};">'
                f'本周未能生成复刻清单（产品档案缺失或候选池为空）。</div>']

    parts.append(
        f'<div style="font-size:12.5px;color:{C_MUTED};margin-bottom:10px;">'
        f'候选池：{esc(c.get("board", ""))} 共 {c.get("pool", 0)} 款。'
        f'其中 {c.get("matched", 0)} 款有产品档案，'
        f'{c.get("excluded", 0)} 款档案结论为「不建议」，已剔除。'
        f'<b>点游戏名可直达产品档案</b>。</div>')

    for g in c["groups"]:
        more = (f'　另有 {g["total"] - len(g["items"])} 款同档位未列出'
                if g["total"] > len(g["items"]) else "")
        tone = C_UP if g["tier"] == 0 else C_MUTED
        parts.append(
            f'<div style="margin:18px 0 4px;font-size:14px;font-weight:600;'
            f'color:{C_HEAD};">'
            f'<span style="color:{tone};">{esc(g["verdict"])}</span>'
            f'　{esc(g["label"])}'
            f'<span style="font-weight:400;font-size:12px;color:{C_MUTED};">'
            f'　{g["total"]} 款，列 {len(g["items"])} 款</span></div>')
        parts.append(
            f'<div style="font-size:12px;color:{C_MUTED};margin-bottom:8px;">'
            f'{esc(g["note"])}{more}</div>')
        rows = []
        for x in g["items"]:
            cell = esc(x["reason"] or "—")
            if x.get("how"):
                cell += (f'<br><span style="color:{C_MUTED};font-size:12px;">'
                         f'建议：{esc(x["how"])}</span>')
            rows.append([
                f'<a href="{esc(game_url(base, x["name"]))}" '
                f'style="color:{C_LINK};text-decoration:none;">'
                f'<b>{esc(x["name"])}</b></a>',
                f'#{x["rank"]}',
                esc(x["board"]),
                esc(x["cost_level"] or "—"),
                esc(x["status"]),
                cell,
            ])
        parts.append(_tbl(["游戏", "榜内", "榜", "成本", "状态", "为什么值得 / 怎么改"],
                          rows, ["left", "right", "left", "left", "left", "left"]))
        parts.append("")

    counts = {g["verdict"]: g["total"] for g in c["groups"]}
    parts.append('<div style="font-size:12.5px;font-weight:600;color:%s;'
                 'margin:22px 0 6px;">复刻结论口径（全量 5 档）</div>' % C_HEAD)
    vrows = [[esc(v), esc(CL.VERDICT_META[v]["note"]), str(counts.get(v, 0))]
             for v in CL.GROUP_ORDER]
    vrows.append([esc("不建议"), esc(CL.VERDICT_META["不建议"]["note"]),
                  f'{c.get("excluded", 0)}（不入选）'])
    parts.append(_tbl(["档位", "含义", "本次三榜"], vrows,
                      ["left", "left", "right"]))
    return parts


def render_html(result: dict) -> str:
    m = result["meta"]
    p = result["primary"]
    c = result.get("clone") or {}
    period = (f"{m['baseline_date']} → {m['week_end']}"
              if m.get("baseline_date") else m["week_end"])

    links = site_links(_base_of(result))

    def _nav(href: str, label: str, strong: bool = False) -> str:
        return (f'<a href="{esc(href)}" style="display:inline-block;'
                f'padding:4px 11px;margin:0 6px 6px 0;border-radius:999px;'
                f'border:1px solid {"%s" % C_LINK if strong else C_LINE};'
                f'background:{"%s" % C_LINK if strong else "#ffffff"};'
                f'color:{"#ffffff" if strong else C_HEAD};font-size:12.5px;'
                f'text-decoration:none;">{esc(label)}</a>')

    parts = [
        '<div style="max-width:760px;margin:0 auto;padding:4px 2px;'
        'font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\','
        '\'Microsoft YaHei\',sans-serif;color:%s;line-height:1.55;">' % C_HEAD,
        '<h1 style="font-size:20px;margin:0 0 4px;">微信小游戏周报</h1>',
        f'<div style="font-size:13px;color:{C_MUTED};margin-bottom:10px;">'
        f'{esc(period)}　·　数据源：引力引擎（匿名 TOP{A.TOP_N}）　·　'
        f'生成 {esc(m["generated_at"][:16].replace("T", " "))}</div>',
        # 主页入口：从群里/邮件里点进周报的人，要能一键回到仪表盘
        '<div style="margin-bottom:16px;">'
        + _nav(links["home"], "数据主页", strong=True)
        + _nav(links["games"], "产品档案")
        + _nav(links["publishers"], "新厂商冒泡")
        + f'<div style="font-size:12px;color:{C_MUTED};margin-top:4px;">'
          f'主页　<a href="{esc(links["home"])}" style="color:{C_LINK};">'
          f'{esc(links["home"])}</a></div>'
        + '</div>',
    ]

    # ---- 结论速览 ----
    parts.append('<div style="background:#f9fafb;border:1px solid %s;'
                 'border-radius:8px;padding:14px 16px;">' % C_LINE)
    parts.append(f'<div style="font-size:13px;font-weight:700;'
                 f'color:{C_HEAD};margin-bottom:8px;">结论速览</div>')
    parts.append('<ul style="margin:0;padding-left:18px;">')
    for s in build_summary(result):
        parts.append(f'<li style="font-size:13.5px;margin:5px 0;">{esc(s)}</li>')
    parts.append("</ul></div>")

    # ---- 一、本周趋势 ----
    parts.append(_sec("一、本周趋势"))

    hs = p.get("head_stability")
    parts.append(_sec_light("1.1 头部格局"))
    if hs:
        rate = hs["retention_rate"]
        judge = ("头部高度固化，新进者短期难撬动。" if rate >= 80 else
                 "头部仍在轮动，存在切入窗口。" if rate <= 60 else
                 "头部相对稳定。")
        parts.append(
            f'<div style="font-size:13px;margin-bottom:6px;">'
            f'TOP{hs["head_n"]} 留存 <b>{rate}%</b>'
            f'（{hs["retained"]}/{hs["head_n"]}），换血 {hs["turnover"]} 席 —— '
            f'{esc(judge)}</div>')
        for label, names in (("挤进头部", hs["entered"]), ("掉出头部", hs["exited"])):
            if names:
                parts.append(
                    f'<div style="font-size:12.5px;color:{C_MUTED};">'
                    f'{label}：{esc("、".join(names))}</div>')
    else:
        parts.append(f'<div style="font-size:13px;color:{C_MUTED};">'
                     f'本次没有可用的基准快照，无法给出留存率。</div>')

    t = p.get("category_trend")
    parts.append(_sec_light("1.2 品类走向",
                            (f"与 {t['baseline_date']}（{t['actual_days']} 天前）"
                             f"同榜对比" if t else "")))
    if t:
        def _delta_cell(v):
            if v > 0:
                return f'<span style="color:{C_UP};font-weight:600;">{_pp(v)}</span>'
            if v < 0:
                return f'<span style="color:{C_DOWN};font-weight:600;">{_pp(v)}</span>'
            return f'<span style="color:{C_MUTED};">0pp</span>'

        rows = [[esc(i["name"]), f'{i["share"]}%', f'{i["prev_share"]}%',
                 _delta_cell(i["delta"])] for i in t["items"]]
        for d in t.get("dropped") or []:
            rows.append([esc(d["name"] + "（本期已掉出）"), "—",
                         f'{d["prev_share"]}%', _delta_cell(d["delta"])])
        parts.append(_tbl(["品类(L1)", "本周占比", "参考期", "变化"], rows,
                          ["left", "right", "right", "right"]))
        up = t["items"][0] if t["items"] else None
        down = min(t["items"], key=lambda i: i["delta"]) if t["items"] else None
        if up and up["delta"] > 0:
            line = f'<b>{esc(up["name"])}</b> 上升最快（{_pp(up["delta"])}）'
            if down and down["delta"] < 0:
                line += (f'；<b>{esc(down["name"])}</b> 退得最多'
                         f'（{_pp(down["delta"])}）')
            parts.append(f'<div style="font-size:13px;margin-top:8px;">{line}。</div>')
    else:
        parts.append(f'<div style="font-size:13px;color:{C_MUTED};">'
                     f'历史快照不足以做多周对比，本周仅给出当期结构'
                     f'（见附录 A）。</div>')

    ne, ri = p["new_entrants"], p["risers"]
    parts.append(_sec_light("1.3 本周新变量"))
    parts.append('<ul style="margin:0;padding-left:18px;font-size:13px;">')
    parts.append(f'<li style="margin:4px 0;">新晋 <b>{len(ne)}</b> 款：'
                 + esc("、".join(f'{x["name"]}(#{x["rank"]})' for x in ne[:10])
                       or "—") + "</li>")
    parts.append(f'<li style="margin:4px 0;">显著上升 <b>{len(ri)}</b> 款：'
                 + esc("、".join(f'{x["name"]}(+{x["delta"]})' for x in ri[:10])
                       or "—") + "</li>")
    parts.append("</ul>")
    parts.append(f'<div style="font-size:12px;color:{C_MUTED};margin-top:6px;">'
                 f'新晋数量偏多说明榜在换血；名次上升只统计两期都在榜内的产品。'
                 f'</div>')

    wl, ws = p.get("waist_long") or [], p.get("waist_short") or []
    parts.append(_sec_light("1.4 结构稳定性"))
    if wl or ws:
        tail = ("腰部以短周期产品为主，注意区分买量冲榜。"
                if len(ws) > len(wl) else "腰部有不少长线产品。")
        parts.append(
            f'<div style="font-size:13px;">腰部（{A.HEAD_N + 1}-{A.TOP_N} 名）'
            f'连续在榜 ≥{A.WEEK_DAYS} 天的长线产品 <b>{len(wl)}</b> 款，'
            f'低于 {A.WEEK_DAYS} 天的 <b>{len(ws)}</b> 款 —— {esc(tail)}</div>')

    # ---- 二、值得复刻 ----
    parts.append(_sec("二、值得复刻"))
    parts += _clone_groups_html(result)

    # ---- 附录 ----
    parts.append(
        f'<div style="margin-top:26px;padding-top:10px;'
        f'border-top:1px dashed {C_LINE};font-size:12.5px;color:{C_MUTED};">'
        f'附录 · 明细（供执行同学查阅）—— 只看结论可到此为止。</div>')

    parts.append(_sec_light("附录 A · 大盘明细", "A.1 品类结构"))
    cs = p["category_structure"]
    rows = [[esc(x["name"]), f'<b>{x["share"]}%</b>', str(x["count"]),
             esc("、".join(x["examples"][:3]))] for x in cs["l1"]]
    parts.append(_tbl(["品类(L1)", "占比", "款数", "代表产品"], rows,
                      ["left", "right", "right", "left"]))
    l2 = [x for x in cs["l2"] if x["name"] not in ("其他", "待核")][:8]
    if l2:
        parts.append(_sec_light("A.2 细分玩法(L2) TOP8"))
        parts.append(_tbl(
            ["玩法(L2)", "占比", "款数"],
            [[esc(x["name"]), f'{x["share"]}%', str(x["count"])] for x in l2],
            ["left", "right", "right"]))
    cc = p["concentration"]
    parts.append(_sec_light("A.3 头部集中度"))
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

    parts.append(_sec_light("附录 B · 异动明细",
                            f"与 {p['baseline_date'] or '—'} 对比；"
                            f"仅统计榜内 TOP{A.TOP_N} 内的变化（B.1 新晋者）"))
    parts.append(_tbl(
        ["游戏", "当前名次", "品类", "发行商", "连续在榜"],
        [[f'<span style="color:{C_NEW};font-weight:600;">新</span> {esc(x["name"])}',
          f'#{x["rank"]}',
          esc(x["l1"]) + (f'·{esc(x["l2"])}' if x.get("l2") else ""),
          esc(x["publisher"] or "—"),
          f'{x["streak"]} 天'] for x in ne],
        ["left", "right", "left", "left", "right"]))
    parts.append(_sec_light("B.2 上升态势"))
    parts.append(_tbl(
        ["游戏", "名次变化", "当前", "品类", "发行商"],
        [[esc(x["name"]), _dir_cell(x["delta"]), f'#{x["rank"]}',
          esc(x["l1"]) + (f'·{esc(x["l2"])}' if x.get("l2") else ""),
          esc(x["publisher"] or "—")] for x in ri],
        ["left", "right", "right", "left", "left"]))
    if p["fallers"]:
        parts.append(_sec_light("B.3 下滑提示"))
        parts.append(_tbl(
            ["游戏", "名次变化", "当前", "品类"],
            [[esc(x["name"]), _dir_cell(x["delta"]), f'#{x["rank"]}',
              esc(x["l1"])] for x in p["fallers"]],
            ["left", "right", "right", "left"]))

    parts.append(_sec_light("附录 C · 结构稳定性", "C.1 头部稳定性"))
    if hs:
        parts.append(
            f'<div style="font-size:12.5px;margin-bottom:8px;">'
            f'TOP{hs["head_n"]} 留存 <b>{hs["retention_rate"]}%</b>'
            f'（{hs["retained"]}/{hs["head_n"]}），换血 {hs["turnover"]} 席。'
            + (f'　新进：{esc("、".join(hs["entered"]))}。' if hs["entered"] else "")
            + (f'　掉出：{esc("、".join(hs["exited"]))}。' if hs["exited"] else "")
            + '</div>')
    parts.append(_sec_light("C.2 腰部持续性"))
    parts.append(
        f'<div style="font-size:12.5px;color:{C_MUTED};margin-bottom:8px;">'
        f'连续在榜 ≥{A.WEEK_DAYS} 天的长线产品 {len(wl)} 款，'
        f'低于 {A.WEEK_DAYS} 天的 {len(ws)} 款。'
        f'长线=玩法留存支撑，短期=可能靠买量冲榜。</div>')
    parts.append(_tbl(
        ["游戏", "名次", "连续在榜", "品类", "发行商"],
        [[esc(x["name"]), f'#{x["rank"]}', f'<b>{x["streak"]} 天</b>',
          esc(x["l1"]) + (f'·{esc(x["l2"])}' if x.get("l2") else ""),
          esc(x["publisher"] or "—")] for x in (p["waist"] or [])[:12]],
        ["left", "right", "right", "left", "left"]))

    parts.append(_sec_light("附录 D · 三榜速览"))
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
        f'· 品类走向参考期：{esc(m.get("trend_baseline_date") or "—")}'
        f'（较本周往前 {m.get("trend_weeks")} 周）<br>'
        f'· 值得复刻的结论取自产品档案（data/detail），候选池为'
        f'{esc(m.get("clone_board") or "")}；档案未覆盖的 '
        f'{len(c.get("missing") or [])} 款本期无法给出结论<br>'
        f'· 历史快照 {m["snapshot_count"]} 份'
        f'（{esc(m["history_start"])} 起）；品类经行业口径归一化，'
        f'归一化词典覆盖 {m["learned_size"]} 款游戏<br>'
        f'· 品类口径：引力引擎原始字段存在标签拼接、占位值等问题，'
        f'报告中的品类为归一化结果（详见 scripts/monitor/category_map.json）'
        f'</div>')

    # ---- 继续查看（回主页） ----
    parts.append(
        f'<div style="margin-top:18px;padding:14px 16px;background:#f5f8ff;'
        f'border:1px solid #d6e4ff;border-radius:8px;font-size:13px;'
        f'line-height:1.8;">'
        f'<div style="font-weight:700;color:{C_HEAD};margin-bottom:6px;">'
        f'继续查看</div>'
        f'<div>· 主页（榜单 / 大盘实时数据）：'
        f'<a href="{esc(links["home"])}" style="color:{C_LINK};">'
        f'{esc(links["home"])}</a></div>'
        f'<div>· 产品档案（每款游戏的复刻结论与玩法说明）：'
        f'<a href="{esc(links["games"])}" style="color:{C_LINK};">'
        f'{esc(links["games"])}</a></div>'
        f'<div>· 新厂商冒泡：'
        f'<a href="{esc(links["publishers"])}" style="color:{C_LINK};">'
        f'{esc(links["publishers"])}</a></div>'
        f'</div>')
    parts.append("</div>")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------
def _base_of(result: dict) -> str:
    """报告里用的站点根地址（meta 里带上，没有就现场推导）。"""
    return (result.get("meta") or {}).get("site_url") or site_base()


def _clone_groups_md(result: dict) -> list[str]:
    """「值得复刻」正文：按结论档位分组，一档一张表。"""
    c = result.get("clone") or {}
    base = _base_of(result)
    L: list[str] = []
    if not c.get("groups"):
        return ["本周未能生成复刻清单（产品档案缺失或候选池为空）。"]

    L += [f"候选池：{c.get('board', '')} 共 {c.get('pool', 0)} 款。"
          f"其中 {c.get('matched', 0)} 款有产品档案，"
          f"{c.get('excluded', 0)} 款档案结论为「不建议」，已剔除。"
          f"**点游戏名可直达产品档案**。", ""]

    for g in c["groups"]:
        more = (f"，另有 {g['total'] - len(g['items'])} 款同档位未列出"
                if g["total"] > len(g["items"]) else "")
        L += [f"### {g['verdict']} —— {g['label']}"
              f"（{g['total']} 款，列 {len(g['items'])} 款）", "",
              f"> {g['note']}{more}", "",
              "| 游戏 | 榜内 | 榜 | 成本 | 状态 | 为什么值得 / 怎么改 |",
              "|---|---:|---|---|---|---|"]
        for x in g["items"]:
            cell = x["reason"] or "—"
            if x.get("how"):
                cell += f"<br>建议：{x['how']}"
            L += [f"| [{x['name']}]({game_url(base, x['name'])}) | "
                  f"#{x['rank']} | {x['board']} | "
                  f"{x['cost_level']} | {x['status']} | {cell} |"]
        L += [""]

    L += ["### 复刻结论口径（全量 5 档）", "",
          "| 档位 | 含义 | 本次三榜 |", "|---|---|---:|"]
    counts = {g["verdict"]: g["total"] for g in c["groups"]}
    for v in CL.GROUP_ORDER:
        meta = CL.VERDICT_META[v]
        L += [f"| {v} | {meta['note']} | {counts.get(v, 0)} |"]
    L += [f"| 不建议 | {CL.VERDICT_META['不建议']['note']} | "
          f"{c.get('excluded', 0)}（不入选） |", ""]
    return L


def render_markdown(result: dict) -> str:
    m, p = result["meta"], result["primary"]
    period = (f"{m['baseline_date']} → {m['week_end']}"
              if m.get("baseline_date") else m["week_end"])
    links = site_links(_base_of(result))
    L = [f"# 微信小游戏周报", "",
         f"**周期** {period}　|　**数据源** 引力引擎（匿名 TOP{A.TOP_N}）"
         f"　|　**生成** {m['generated_at'][:16].replace('T', ' ')}", "",
         f"**主页**　<{links['home']}>", "",
         f"> 站内直达：**[数据主页]({links['home']})**"
         f"　·　[产品档案]({links['games']})"
         f"　·　[新厂商冒泡]({links['publishers']})", "",
         "## 结论速览", ""]
    L += [f"- {s}" for s in build_summary(result)]

    # ---- 一、本周趋势 ----
    L += ["", "## 一、本周趋势", "", "### 1.1 头部格局", ""]
    hs = p.get("head_stability")
    if hs:
        rate = hs["retention_rate"]
        judge = ("头部高度固化，新进者短期难撬动。" if rate >= 80 else
                 "头部仍在轮动，存在切入窗口。" if rate <= 60 else
                 "头部相对稳定。")
        L += [f"TOP{hs['head_n']} 留存 **{rate}%**"
              f"（{hs['retained']}/{hs['head_n']}），换血 {hs['turnover']} 席 —— "
              + judge]
        if hs["entered"]:
            L += [f"- 挤进头部：{'、'.join(hs['entered'])}"]
        if hs["exited"]:
            L += [f"- 掉出头部：{'、'.join(hs['exited'])}"]
    else:
        L += ["本次没有可用的基准快照，无法给出留存率。"]

    t = p.get("category_trend")
    L += ["", "### 1.2 品类走向", ""]
    if t:
        L += [f"与 {t['baseline_date']}（{t['actual_days']} 天前）同榜占比对比，"
              f"单位为百分点：", "",
              "| 品类(L1) | 本周占比 | 参考期 | 变化 |", "|---|---:|---:|---:|"]
        L += [f"| {i['name']} | {i['share']}% | {i['prev_share']}% | "
              f"{_pp(i['delta'])} |" for i in t["items"]]
        for d in t.get("dropped") or []:
            L += [f"| {d['name']}（本期已掉出） | — | {d['prev_share']}% | "
                  f"{_pp(d['delta'])} |"]
        up = t["items"][0] if t["items"] else None
        down = min(t["items"], key=lambda i: i["delta"]) if t["items"] else None
        if up and up["delta"] > 0:
            line = f"**{up['name']}** 上升最快（{_pp(up['delta'])}）"
            if down and down["delta"] < 0:
                line += f"；**{down['name']}** 退得最多（{_pp(down['delta'])}）"
            L += ["", f"{line}。"]
    else:
        L += ["历史快照不足以做多周对比，本周仅给出当期结构（见附录 A）。"]

    ne, ri = p["new_entrants"], p["risers"]
    L += ["", "### 1.3 本周新变量", "",
          f"- 新晋 {len(ne)} 款："
          + ("、".join(f"{x['name']}(#{x['rank']})" for x in ne[:10]) or "—"),
          f"- 显著上升 {len(ri)} 款："
          + ("、".join(f"{x['name']}(+{x['delta']})" for x in ri[:10]) or "—")]
    if len(ne) > 10:
        L += [f"- 另有 {len(ne) - 10} 款新晋未列出"]
    L += ["", "> 新晋数量偏多说明榜在换血；名次上升只统计两期都在榜内的产品。"]
    L += ["", "### 1.4 结构稳定性", ""]
    wl, ws = p.get("waist_long") or [], p.get("waist_short") or []
    if wl or ws:
        L += [f"腰部（{A.HEAD_N + 1}-{A.TOP_N} 名）连续在榜 ≥{A.WEEK_DAYS} 天的"
              f"长线产品 {len(wl)} 款，低于 {A.WEEK_DAYS} 天的 {len(ws)} 款 —— "
              + ("腰部以短周期产品为主，注意区分买量冲榜。"
                 if len(ws) > len(wl) else "腰部有不少长线产品。")]

    # ---- 二、值得复刻 ----
    L += ["", "## 二、值得复刻", ""]
    L += _clone_groups_md(result)

    # ---- 附录 ----
    L += ["", "---", "",
          "## 附录 · 明细（供执行同学查阅）", "",
          "以下为大盘、异动与稳定性的完整明细，只看结论可到此为止。", ""]

    L += ["### 附录 A · 大盘明细", "", "#### A.1 品类结构", "",
          "| 品类(L1) | 占比 | 款数 | 代表产品 |", "|---|---:|---:|---|"]
    cs = p["category_structure"]
    L += [f'| {x["name"]} | {x["share"]}% | {x["count"]} | '
          f'{"、".join(x["examples"][:3])} |' for x in cs["l1"]]
    l2 = [x for x in cs["l2"] if x["name"] not in ("其他", "待核")][:8]
    if l2:
        L += ["", "#### A.2 细分玩法(L2) TOP8", "", "| 玩法 | 占比 | 款数 |",
              "|---|---:|---:|"]
        L += [f'| {x["name"]} | {x["share"]}% | {x["count"]} |' for x in l2]
    cc = p["concentration"]
    L += ["", "#### A.3 头部集中度", "",
          f"在榜 {cc['total']} 款来自 {cc['distinct_publishers']} 家发行商；"
          f"第一大占 {cc['top1_share']}%，前三合计 {cc['top3_share']}%。", "",
          "| 发行商 | 在榜款数 | 占比 |", "|---|---:|---:|"]
    L += [f'| {x["publisher"]} | {x["count"]} | {x["share"]}% |'
          for x in cc["top_publishers"]]

    L += ["", "### 附录 B · 异动明细", "",
          f"与 {p['baseline_date'] or '—'} 对比，仅统计榜内 TOP{A.TOP_N} 内变化。",
          "", "#### B.1 新晋者", "",
          "| 游戏 | 当前名次 | 品类 | 发行商 | 连续在榜 |",
          "|---|---:|---|---|---:|"]
    L += [f'| {x["name"]} | #{x["rank"]} | {x["l1"]}'
          f'{("·" + x["l2"]) if x.get("l2") else ""} | '
          f'{x["publisher"] or "—"} | {x["streak"]} 天 |'
          for x in ne]
    L += ["", "#### B.2 上升态势", "",
          "| 游戏 | 名次变化 | 当前 | 品类 | 发行商 |", "|---|---:|---:|---|---|"]
    L += [f'| {x["name"]} | {"+" if x["delta"] > 0 else ""}{x["delta"]} | '
          f'#{x["rank"]} | {x["l1"]} | {x["publisher"] or "—"} |' for x in ri]
    if p["fallers"]:
        L += ["", "#### B.3 下滑提示", "",
              "| 游戏 | 名次变化 | 当前 | 品类 |", "|---|---:|---:|---|"]
        L += [f'| {x["name"]} | {x["delta"]} | #{x["rank"]} | {x["l1"]} |'
              for x in p["fallers"]]

    L += ["", "### 附录 C · 结构稳定性", ""]
    if hs:
        L += ["#### C.1 头部稳定性", "",
              f"TOP{hs['head_n']} 留存 **{hs['retention_rate']}%**"
              f"（{hs['retained']}/{hs['head_n']}），换血 {hs['turnover']} 席。"]
        if hs["entered"]:
            L += [f"- 新进：{'、'.join(hs['entered'])}"]
        if hs["exited"]:
            L += [f"- 掉出：{'、'.join(hs['exited'])}"]
    L += ["", "#### C.2 腰部持续性", "",
          f"榜内 {A.HEAD_N + 1}-{A.TOP_N} 名：连续在榜 ≥{A.WEEK_DAYS} 天的"
          f"长线产品 {len(wl)} 款，低于 {A.WEEK_DAYS} 天的 {len(ws)} 款。", "",
          "| 游戏 | 名次 | 连续在榜 | 品类 | 发行商 |", "|---|---:|---:|---|---|"]
    L += [f'| {x["name"]} | #{x["rank"]} | {x["streak"]} 天 | {x["l1"]} | '
          f'{x["publisher"] or "—"} |' for x in (p["waist"] or [])[:12]]

    L += ["", "### 附录 D · 三榜速览", "",
          "| 榜单 | 新晋 | 上升 | 下降 | TOP10 留存 |",
          "|---|---:|---:|---:|---:|"]
    L += [f'| {b["board"]} | {len(b["new_entrants"])} | {len(b["risers"])} | '
          f'{len(b["fallers"])} | '
          f'{(b["head_stability"] or {}).get("retention_rate", "—")}% |'
          for b in result["boards"]]

    c = result.get("clone") or {}
    L += ["", "---", "", "**继续查看**", "",
          f"- 主页（榜单 / 大盘实时数据）：<{links['home']}>",
          f"- 产品档案（每款游戏的复刻结论与玩法说明）：<{links['games']}>",
          f"- 新厂商冒泡：<{links['publishers']}>",
          "", "**数据说明与口径**", "",
          f"- {m['data_limit_note']}",
          f"- 对比基准：{m['baseline_date'] or '—'} vs {m['week_end']}"
          f"（间隔 {m['baseline_gap_days']} 天）",
          f"- 品类走向参考期：{m.get('trend_baseline_date') or '—'}"
          f"（较本周往前 {m.get('trend_weeks')} 周）",
          f"- 值得复刻的结论取自产品档案（data/detail），"
          f"候选池为{m.get('clone_board') or '微信三榜 TOP20 并集'}；"
          f"档案未覆盖的 {len(c.get('missing') or [])} 款本期无法给出结论",
          f"- 历史快照 {m['snapshot_count']} 份（{m['history_start']} 起）",
          f"- 品类经行业口径归一化，词典覆盖 {m['learned_size']} 款游戏", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="周报渲染")
    ap.add_argument("--format", choices=["md", "html", "both"], default="both")
    ap.add_argument("--baseline", default=None, help="基准日期 YYYY-MM-DD")
    ap.add_argument("--outdir", default=None, help="输出目录（默认 reports/）")
    ap.add_argument("--no-clone", action="store_true",
                    help="跳过「值得复刻」清单（产品档案缺失时用）")
    ap.add_argument("--site-url", default=None,
                    help="站点主页地址（默认从 git origin 推导 GitHub Pages 地址）")
    args = ap.parse_args()

    result = A.analyze(baseline_date=args.baseline)
    result["meta"]["site_url"] = ((args.site_url or site_base()).strip()
                                 .rstrip("/") + "/")
    print(f"站内主页：{result['meta']['site_url']}")
    if args.no_clone:
        result["clone"] = {"groups": [], "excluded": 0, "missing": [],
                           "pool": 0, "matched": 0, "candidates": 0}
        result["meta"]["clone_board"] = "（本次跳过）"
    else:
        c = CL.attach(result)
        print(f"值得复刻：候选 {c['pool']} 款 → 入选 {c['candidates']} 款"
              f"（{len(c['groups'])} 个档位），"
              f"剔除不建议 {c['excluded']} 款，档案缺失 {len(c['missing'])} 款")

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
