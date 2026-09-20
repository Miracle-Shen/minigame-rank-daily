#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报投递 · 企业微信群机器人（Webhook）。

一条 Webhook URL 就能推消息，不需要任何密码 —— 适合放在 CI 里无人值守跑。

需要配置的环境变量（GitHub Actions 里配成同名 Secrets）：
    WECOM_WEBHOOK        群机器人 Webhook 地址（必填）
    WECOM_CHATID         可选，定向投递：只把消息发给这一个群。
                         同一个机器人被加进多个群时，不带它 = 每个群各收到一条；
                         带上它 = 只发指定群。群 ID 属于内部标识，不入库。
    WECOM_MSG_TYPE       可选，markdown（默认）| markdown_v2
    WECOM_REPORT_URL     可选，覆盖群消息末尾的「查看图文周报」链接；
                         默认从 git origin 现场推导当期报告地址
    REPORT_SITE_URL      可选，覆盖群消息末尾的「数据主页」链接；
                         默认从 git origin 推导 GitHub Pages 主页地址
    REPORT_PREFIX        可选，消息标题，默认「微信小游戏周报」

用法：
    python scripts/monitor/send_wecom.py --check               # 往群里发一条通道自检
    python scripts/monitor/send_wecom.py --dry-run             # 只打印摘要内容，不发送
    python scripts/monitor/send_wecom.py --latest              # 推送最新一期周报
    python scripts/monitor/send_wecom.py --latest --chatid wrkxxx   # 只推给指定群
    python scripts/monitor/send_wecom.py --report reports/weekly-2026-09-14.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPORT_DIR = ROOT / "reports"

EXIT_OK = 0
EXIT_BADCONFIG = 2
EXIT_SENDFAIL = 3


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def mask_chatid(value: str) -> str:
    """群 ID 是内部标识，日志里只留头尾，够对上号就行。"""
    if not value:
        return ""
    if len(value) <= 12:
        return "已指定"
    return f"{value[:6]}…{value[-4:]}"


class Config:
    def __init__(self) -> None:
        self.prefix = env("REPORT_PREFIX", "微信小游戏周报")
        self.webhook = env("WECOM_WEBHOOK")
        self.chatid = env("WECOM_CHATID")
        self.msg_type = env("WECOM_MSG_TYPE", "markdown")
        self.report_url = env("WECOM_REPORT_URL")
        self.allow_broadcast = env("WECOM_ALLOW_BROADCAST").lower() in (
            "1", "true", "yes", "on")

    def describe(self) -> str:
        if self.chatid:
            scope = f"仅 {mask_chatid(self.chatid)}（已锁定目标群）"
        elif self.allow_broadcast:
            scope = "机器人所在的全部群（WECOM_ALLOW_BROADCAST 已放行）"
        else:
            scope = "未指定目标群 —— 将拒绝发送"
        return (
            f"  消息标题  {self.prefix}\n"
            f"  群机器人  {'已配置（%s）' % self.msg_type if self.webhook else '未配置'}\n"
            f"  投递范围  {scope}"
        )


BROADCAST_GUARD_HINT = (
    "未指定目标群：不带 chatid 时企业微信会把消息发给「添加过这个机器人的所有群」，"
    "为避免漏进无关群，本脚本默认拒绝发送。\n"
    "  定向投递：设 WECOM_CHATID=<群 ID>（CI 里用同名 Secret），或加 --chatid <群 ID>。\n"
    "  确实要群发：设 WECOM_ALLOW_BROADCAST=1 显式放行。"
)


def check_scope(cfg: Config) -> bool:
    """目标群必须明确。放行返回 True。"""
    if cfg.chatid or cfg.allow_broadcast:
        return True
    print("  已拒绝发送 —— " + BROADCAST_GUARD_HINT)
    return False


# --------------------------------------------------------------------------
# 取报告
# --------------------------------------------------------------------------
def latest_report() -> Path:
    files = sorted(REPORT_DIR.glob("weekly-*.json"))
    if not files:
        raise FileNotFoundError(
            f"{REPORT_DIR} 下没有 weekly-*.json，请先运行 scripts/monitor/report.py"
        )
    return files[-1]


def github_blob_url(path: Path) -> str:
    """把本地文件路径换成 GitHub 上的可点链接；拿不到 origin 就返回空串。

    群里那条摘要末尾的「查看图文周报」靠它 —— 动态推导，所以每周都指向当期，
    不需要（也不应该）把一个会过期的地址写进 WECOM_REPORT_URL。
    """
    try:
        remote = subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""
    m = re.search(r"(?:git@|https://)github\.com[:/](?P<slug>[^/]+/[^/.]+)", remote)
    if not m:
        return ""
    try:
        rel = Path(path).resolve().relative_to(ROOT)
    except ValueError:
        return ""
    return f"https://github.com/{m.group('slug')}/blob/main/{rel.as_posix()}"


def load_bundle(report: Path) -> tuple[dict, Path | None]:
    """返回 (结构化周报, md 路径)。md 用于给群消息算「查看图文周报」链接。"""
    if not report.exists():
        raise FileNotFoundError(f"报告不存在：{report}")
    data = json.loads(report.read_text(encoding="utf-8"))
    md_p = report.with_suffix(".md")
    return data, (md_p if md_p.exists() else None)


# --------------------------------------------------------------------------
# 企业微信群机器人
# --------------------------------------------------------------------------
WECOM_MAX_BYTES = 4096
WECOM_HOST = "qyapi.weixin.qq.com"

# markdown 类型只认这 3 个内置颜色名
C_GREEN = "info"       # 绿
C_GRAY = "comment"     # 灰
C_ORANGE = "warning"   # 橙红

# 常见错误码 → 人话
WECOM_ERRHINT = {
    93000: "Webhook URL 无效，或机器人已被移除（key 变了要重取）",
    93001: "当前群聊不允许推送消息",
    93006: "chatid 不是合法的群 ID（或这个群没在机器人的投递范围内）",
    93008: "机器人不在该群里",
    45009: "接口调用超过限制（每个机器人 20 条/分钟）",
    40008: "消息内容超过 4096 字节",
    40001: "invalid credential，key 不正确",
}


def _summarize(data: dict) -> list[str]:
    """复用周报的趋势结论，保证群消息与图文周报同一套口径。

    只要趋势那几条 —— 「值得复刻」在群消息里自成一段，放这里会重复。
    """
    try:
        import report as R  # 同目录，脚本以 scripts/monitor 为 cwd 运行

        try:
            return R.build_summary(data, with_clone=False)
        except TypeError:
            # 老版 report.py 的 build_summary 只接一个参数
            return R.build_summary(data)
    except Exception as e:  # noqa: BLE001
        print(f"  提示：未能复用周报要点（{type(e).__name__}），改用精简摘要")
        p = data.get("primary", {})
        hs = p.get("head_stability") or {}
        out = []
        if hs:
            out.append(f"畅销榜 TOP{hs.get('head_n')} 留存 "
                       f"{hs.get('retention_rate')}%，换血 {hs.get('turnover')} 席")
        ne, ri = p.get("new_entrants", []), p.get("risers", [])
        if ne:
            out.append(f"新进 {len(ne)} 款：" + "、".join(x["name"] for x in ne[:4]))
        if ri:
            out.append(f"名次上升 {len(ri)} 款：" +
                       "、".join(f"{x['name']}(+{x['delta']})" for x in ri[:4]))
        return out


def _fit_bytes(blocks: list[str], tail: str = "",
               limit: int = WECOM_MAX_BYTES) -> str:
    """按 UTF-8 字节裁剪，保证不超机器人 4096 字节上限，且尽量保住尾注。"""
    def size(s: str) -> int:
        return len(s.encode("utf-8"))

    if size("\n".join(blocks + ([tail] if tail else []))) <= limit:
        return "\n".join(blocks + ([tail] if tail else []))

    note = "> 内容较长已截断，完整版见图文周报"
    reserved = size(tail) + size(note) + 2 if tail else size(note) + 1
    out, used = [], 0
    for b in blocks:
        if used + size(b) + 1 + reserved > limit:
            out.append(note)
            break
        out.append(b)
        used += size(b) + 1
    if tail:
        out.append(tail)
    return "\n".join(out)


def build_wecom_markdown(data: dict, prefix: str, report_url: str = "",
                         v2: bool = False) -> str:
    """拼一条适合群机器人渲染的摘要：本周趋势 + 值得复刻。

    刻意不用表格和列表 —— 机器人的 markdown(v1) 不支持这两者，
    用了会原样吐出一堆竖线和短横线。空行分段 + 引用块才是安全表达；
    游戏名单用全角空格缩进，视觉上接近二级列表。
    """
    m = data.get("meta", {})
    p = data.get("primary", {})
    cl = data.get("clone") or {}
    period = (f"{m.get('baseline_date')} ~ {m.get('week_end')}"
              if m.get("baseline_date") else str(m.get("week_end", "")))

    def c(text: str, color: str) -> str:
        return text if v2 else f'<font color="{color}">{text}</font>'

    blocks = [f"# {prefix}",
              c(f"{period}　·　{p.get('board', '畅销榜')}　·　"
                f"数据源 引力引擎（匿名 TOP20）", C_GRAY),
              ""]

    pts = _summarize(data)
    if pts:
        blocks.append("## 本周趋势")
        blocks += [f"> {s}" for s in pts]
        blocks.append("")

    groups = cl.get("groups") or []
    if groups:
        blocks.append(f"## 值得复刻（{cl.get('candidates', 0)} 款）")
        for g in groups:
            blocks.append(
                f"**{g['verdict']} {g['total']} 款**"
                f"　{c('· ' + (g.get('short') or g.get('label', '')), C_GRAY)}")
            items = g.get("items") or []
            if items:
                names = "、".join(
                    f"{x['name']}（{(x.get('board') or '')[:3]}#{x['rank']}"
                    f"·{x.get('cost_level') or '—'}）" for x in items[:3])
                if g["total"] > len(items[:3]):
                    names += f"，共 {g['total']} 款"
                blocks.append("　" + names)
            blocks.append("")
        blocks.append(c("判定取自产品档案（data/detail）的复刻结论；"
                        "结论为「不建议」的已剔除。", C_GRAY))
        blocks.append("")

    tail = c("口径：匿名接口每榜仅 TOP20，从榜外升入的计为「新进」而非「上升」。", C_GRAY)
    links = [f"[查看图文周报]({report_url})"] if report_url else []
    site = _site_home(m)
    if site:
        links.append(f"[数据主页]({site})")
    if links:
        tail += "\n" + "　|　".join(links)

    return _fit_bytes(blocks, tail)


def build_card_markdown(data: dict, prefix: str = "", report_url: str = "",
                        v2: bool = False) -> str:
    """群消息卡片：微信前三 / 抖音前三 / 全平台 TOP1（渲染逻辑见 hotlist.py）。

    和 `build_wecom_markdown` 的分工：

        build_wecom_markdown  周报摘要 —— 本周趋势 + 值得复刻分组，信息全、条目多
        build_card_markdown   周热榜卡片 —— 谁在榜 + 涨跌 + 抄不抄，一屏扫完

    两条通道并存，命令行 `--card` 切换。
    """
    import hotlist as H  # noqa: PLC0415

    meta = data.get("meta") or {}
    card = H.build_card(data, v2=v2, site_url=_site_home(meta),
                        report_url=report_url)
    text = card["text"]
    if prefix:
        head, _, rest = text.partition("\n")
        text = f"# {prefix} · {head.lstrip('# ').strip()}\n{rest}"
    size = len(text.encode("utf-8"))
    if size > WECOM_MAX_BYTES:
        print(f"  卡片 {size} 字节超上限，已截断到 {WECOM_MAX_BYTES}")
        text = text.encode("utf-8")[:WECOM_MAX_BYTES].decode("utf-8", "ignore")
    return text


def _site_home(meta: dict) -> str:
    """站点主页地址。报告里带 `site_url` 就直接用，否则现场推导。"""
    site = str(meta.get("site_url") or "").strip()
    if site:
        return site
    try:
        import report as R  # 同目录，脚本以 scripts/monitor 为 cwd 运行

        return R.site_base()
    except Exception:  # noqa: BLE001
        return ""


def push_wecom(cfg: Config, data: dict | None = None,
               content: str | None = None,
               report_url: str | None = None) -> bool:
    """推一条 markdown 到企业微信群机器人。返回是否成功。"""
    if not cfg.webhook:
        return False
    import urllib.error
    import urllib.request

    msg_type = cfg.msg_type if cfg.msg_type in ("markdown", "markdown_v2") else "markdown"
    if msg_type != cfg.msg_type:
        print(f"  WECOM_MSG_TYPE={cfg.msg_type} 不是有效值，按 markdown 发送")

    if WECOM_HOST not in cfg.webhook:
        print(f"  提示：Webhook 域名不是 {WECOM_HOST}，按测试地址处理")

    if cfg.chatid:
        print(f"  定向投递  只发 {mask_chatid(cfg.chatid)}（已带 chatid）")
    else:
        print("  投递范围  机器人所在的全部群（已显式放行群发）")

    if content is None:
        url = report_url or cfg.report_url
        content = build_wecom_markdown(data or {}, cfg.prefix, url,
                                       v2=(msg_type == "markdown_v2"))
        print(f"  附报告链接：{url}" if url else "  未取到报告链接（非 git 仓库或没有 origin）")
    size = len(content.encode("utf-8"))
    if size > WECOM_MAX_BYTES:
        print(f"  内容 {size} 字节超上限，已自动截断到 {WECOM_MAX_BYTES}")
        content = content.encode("utf-8")[:WECOM_MAX_BYTES].decode("utf-8", "ignore")

    body: dict = {"msgtype": msg_type, msg_type: {"content": content}}
    if cfg.chatid:
        # 定向投递：同一条消息只进这一个群。
        # 不带 chatid 时，企业微信会把消息发给「添加过这个机器人的所有内部群」。
        body["chatid"] = cfg.chatid
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg.webhook, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"  群机器人推送失败：{type(e).__name__}: {e}")
        print("  排查：① 域名是否为公网 qyapi.weixin.qq.com（内网地址 CI 连不上）；"
              "② Webhook 是否失效或机器人已被移除。")
        return False

    try:
        ret = json.loads(body)
    except ValueError:
        print(f"  群机器人返回非 JSON：{body[:200]}")
        return False

    if ret.get("errcode") == 0:
        where = f" → {mask_chatid(cfg.chatid)}" if cfg.chatid else "（全部群）"
        print(f"  群机器人推送成功（{size} 字节，{msg_type}）{where}")
        return True
    code = ret.get("errcode")
    hint = WECOM_ERRHINT.get(code, "")
    print(f"  群机器人推送失败：errcode={code} errmsg={ret.get('errmsg')}"
          + (f"　→ {hint}" if hint else ""))
    return False


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="周报投递 · 企业微信群机器人")
    ap.add_argument("--report", default=None, help="指定 weekly-*.json（默认最新一期）")
    ap.add_argument("--latest", action="store_true", help="取 reports/ 下最新一期")
    ap.add_argument("--check", action="store_true", help="往群里发一条通道自检，不发正式内容")
    ap.add_argument("--dry-run", action="store_true", help="只打印摘要内容，不发送")
    ap.add_argument("--card", action="store_true",
                    help="发「游戏周热榜」卡片（微信/抖音前三 + 全平台 TOP1），"
                         "而非默认的周报摘要")
    ap.add_argument("--prefix", default=None, help="覆盖消息标题（默认 REPORT_PREFIX）")
    ap.add_argument("--chatid", default=None,
                    help="只投递给指定群（群 ID），覆盖 WECOM_CHATID")
    args = ap.parse_args()

    cfg = Config()
    if args.prefix:
        cfg.prefix = args.prefix
    if args.chatid:
        cfg.chatid = args.chatid

    print("投递配置：")
    print(cfg.describe())

    if args.check:
        if not cfg.webhook:
            print("\n未配置 WECOM_WEBHOOK —— 到仓库 Settings → Secrets and variables → "
                  "Actions 里加一个（或本地 export 一下）。")
            return EXIT_BADCONFIG
        print("\n群机器人自检：")
        ok = push_wecom(cfg, content=(
            f"# {cfg.prefix} · 通道自检\n"
            f"> 收到这条消息说明机器人 Webhook 可用，"
            f"后续每周一 09:00 会推送周报摘要。"))
        return EXIT_OK if ok else EXIT_SENDFAIL

    report = Path(args.report) if args.report else latest_report()
    if not report.is_absolute():
        report = (ROOT / report).resolve()
    data, md_p = load_bundle(report)
    url = cfg.report_url or github_blob_url(md_p or report)
    style = "周热榜卡片" if args.card else "周报摘要"
    if args.card:
        try:
            # 卡片自带「游戏周热榜」标题，默认不再叠加前缀
            content = build_card_markdown(data, args.prefix or "", url,
                                          v2=(cfg.msg_type == "markdown_v2"))
        except Exception as e:  # noqa: BLE001
            # 卡片渲染失败（hotlist.py 缺失 / 报告字段不全等）→ 退回摘要。
            # 回落发生在「渲染阶段」、网络请求还没发出，所以不会重复投递；
            # 宁可变一次样式，也不让群消息整条静默。
            print(f"::warning::卡片渲染失败（{type(e).__name__}: {e}），"
                  f"本次回落为周报摘要")
            style = "周报摘要（卡片渲染失败，已回落）"
            content = build_wecom_markdown(data, cfg.prefix, url,
                                           v2=(cfg.msg_type == "markdown_v2"))
    else:
        content = build_wecom_markdown(data, cfg.prefix, url,
                                       v2=(cfg.msg_type == "markdown_v2"))

    print(f"\n推送周报：{report.name}　（样式：{style}）")
    print(f"  正文      {len(content.encode('utf-8'))} 字节"
          f"（上限 {WECOM_MAX_BYTES}，超了自动截断）")
    print(f"  报告链接  {url or '（未取到：非 git 仓库或没有 origin）'}")
    print(f"  投递范围  {('只发 ' + mask_chatid(cfg.chatid)) if cfg.chatid else '未指定目标群'}")

    if args.dry_run:
        print("-" * 60)
        print(content)
        print("-" * 60)
        print("（dry-run，未发送）")
        return EXIT_OK

    if not cfg.webhook:
        print("\n未配置 WECOM_WEBHOOK —— 到仓库 Settings → Secrets and variables → "
              "Actions 里加一个（或本地 export 一下）。")
        return EXIT_BADCONFIG

    if not check_scope(cfg):
        return EXIT_BADCONFIG

    print("\n群机器人推送：")
    return EXIT_OK if push_wecom(cfg, content=content) else EXIT_SENDFAIL


if __name__ == "__main__":
    sys.exit(main())
