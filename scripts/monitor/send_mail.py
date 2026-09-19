#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报邮件投递：把周报 HTML 作为正文，通过 SMTP 发给团队成员。

默认对接腾讯企业邮箱 / 企业微信邮箱：
    SMTP    smtp.exmail.qq.com : 465 (SSL)   —— 连接失败自动回落 :587 (STARTTLS)
    密码    必须是「客户端专用密码」（16 位），不是邮箱登录密码

需要配置的环境变量（GitHub Actions 里配成同名 Secrets）：
    MAIL_USER            发件邮箱，例如 miracleshen@tencent.com
    MAIL_PASS            客户端专用密码（16 位）
    MAIL_TO              收件人，多个用英文逗号分隔
    MAIL_CC              抄送（可选，逗号分隔）
    MAIL_HOST            默认 smtp.exmail.qq.com
    MAIL_PORT            默认 465
    MAIL_FROM            默认取 MAIL_USER
    MAIL_FROM_NAME       发件人显示名，默认「微信小游戏周报」
    MAIL_SUBJECT_PREFIX  主题前缀，默认「微信小游戏周报」
    WECOM_WEBHOOK        可选，企业微信群机器人 Webhook，配了会同时推一条摘要
    WECOM_MSG_TYPE       可选，markdown（默认）| markdown_v2
    WECOM_REPORT_URL     可选，群消息末尾附一个「查看图文周报」链接

用法：
    python scripts/monitor/send_mail.py --check                # 自检所有已配置通道
    python scripts/monitor/send_mail.py --check --send-test    # 再给自己发一封测试信
    python scripts/monitor/send_mail.py --webhook-only         # 只推群机器人，不发邮件
    python scripts/monitor/send_mail.py --dry-run              # 生成 .eml 到本地，不发送
    python scripts/monitor/send_mail.py --latest               # 发送最新一期周报
    python scripts/monitor/send_mail.py --report reports/weekly-2026-09-14.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import ssl
import subprocess
import sys
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPORT_DIR = ROOT / "reports"
MAIL_PREVIEW_DIR = ROOT / "_mail_preview"

DEFAULT_HOST = "smtp.exmail.qq.com"
DEFAULT_PORT = 465

EXIT_OK = 0
EXIT_BADCONFIG = 2
EXIT_SENDFAIL = 3


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def split_addrs(raw: str) -> list[str]:
    return [a.strip() for a in raw.replace("；", ",").replace(";", ",").split(",") if a.strip()]


class Config:
    def __init__(self) -> None:
        self.host = env("MAIL_HOST", DEFAULT_HOST)
        self.port = int(env("MAIL_PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
        self.user = env("MAIL_USER")
        self.password = env("MAIL_PASS").replace(" ", "")
        self.sender = env("MAIL_FROM", self.user) or self.user
        self.sender_name = env("MAIL_FROM_NAME", "微信小游戏周报")
        self.to = list(dict.fromkeys(split_addrs(env("MAIL_TO"))))
        self.cc = [a for a in dict.fromkeys(split_addrs(env("MAIL_CC"))) if a not in self.to]
        self.prefix = env("MAIL_SUBJECT_PREFIX", "微信小游戏周报")
        self.webhook = env("WECOM_WEBHOOK")
        self.msg_type = env("WECOM_MSG_TYPE", "markdown")
        self.report_url = env("WECOM_REPORT_URL")

    def missing(self) -> list[str]:
        lack = []
        if not self.user:
            lack.append("MAIL_USER")
        if not self.password:
            lack.append("MAIL_PASS")
        return lack

    def describe(self) -> str:
        return (
            f"  SMTP      {self.host}:{self.port}\n"
            f"  发件人    {self.sender_name} <{self.sender or '未配置'}>\n"
            f"  收件人    {', '.join(self.to) or '未配置'}\n"
            f"  抄送      {', '.join(self.cc) or '—'}\n"
            f"  专用密码  {'已配置（%d 位）' % len(self.password) if self.password else '未配置'}\n"
            f"  群机器人  {'已配置（%s）' % self.msg_type if self.webhook else '未配置'}"
        )


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


def load_bundle(report: Path) -> tuple[dict, Path | None, Path | None]:
    """返回 (结构化周报, html 路径, md 路径)。"""
    if not report.exists():
        raise FileNotFoundError(f"报告不存在：{report}")
    data = json.loads(report.read_text(encoding="utf-8"))
    html_p = report.with_suffix(".html")
    md_p = report.with_suffix(".md")
    return data, (html_p if html_p.exists() else None), (md_p if md_p.exists() else None)


HTML_WRAPPER = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:16px;background:#ffffff;-webkit-text-size-adjust:100%;">
{body}
</body>
</html>"""


def build_message(cfg: Config, data: dict, html: str, plain: str,
                  attachments: list[Path]) -> EmailMessage:
    m = data.get("meta", {})
    period = (f"{m.get('baseline_date')} ~ {m.get('week_end')}"
              if m.get("baseline_date") else str(m.get("week_end", "")))
    subject = f"{cfg.prefix} · {period}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((str(cfg.sender_name), cfg.sender))
    msg["To"] = ", ".join(cfg.to) if cfg.to else cfg.sender
    if cfg.cc:
        msg["Cc"] = ", ".join(cfg.cc)
    msg["Date"] = formatdate(localtime=True)
    msg["X-Mailer"] = "minigame-rank-daily/weekly"

    msg.set_content(plain or subject, subtype="plain", charset="utf-8")
    msg.add_alternative(HTML_WRAPPER.format(title=subject, body=html),
                        subtype="html", charset="utf-8")

    for p in attachments:
        if not p.exists():
            continue
        maintype, _, subtype = (p.suffix.lstrip(".").lower() or "octet-stream").partition("/")
        payload = p.read_bytes()
        if p.suffix.lower() == ".html":
            msg.add_attachment(payload, maintype="text", subtype="html",
                               filename=p.name)
        else:
            msg.add_attachment(payload, maintype="text", subtype="plain",
                               filename=p.name)
    return msg


# --------------------------------------------------------------------------
# 发送
# --------------------------------------------------------------------------
def _connect(cfg: Config) -> smtplib.SMTP:
    ctx = ssl.create_default_context()
    if cfg.port == 465:
        s = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=30, context=ctx)
    else:
        s = smtplib.SMTP(cfg.host, cfg.port, timeout=30)
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
    return s


def smtp_login(cfg: Config) -> smtplib.SMTP:
    """连接 + 登录；465 失败自动回落 587。"""
    try:
        s = _connect(cfg)
    except Exception as e:  # noqa: BLE001
        if cfg.port == 465:
            print(f"  :465 SSL 连接失败（{type(e).__name__}: {e}），回落 :587 STARTTLS")
            cfg.port = 587
            s = _connect(cfg)
        else:
            raise
    s.login(cfg.user, cfg.password)
    return s


def send(cfg: Config, msg: EmailMessage) -> None:
    recipients = list(dict.fromkeys(cfg.to + cfg.cc)) or [cfg.sender]
    s = smtp_login(cfg)
    try:
        s.send_message(msg, from_addr=cfg.sender, to_addrs=recipients)
    finally:
        try:
            s.quit()
        except Exception:  # noqa: BLE001
            pass


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
    45009: "接口调用超过限制（每个机器人 20 条/分钟）",
    40008: "消息内容超过 4096 字节",
    40001: "invalid credential，key 不正确",
}


def _summarize(data: dict) -> list[str]:
    """复用周报的「本周要点」，保证群消息与邮件同一套口径。"""
    try:
        import report as R  # 同目录，脚本以 scripts/monitor 为 cwd 运行

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

    note = "> 内容较长已截断，完整版见邮件"
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
    """拼一条适合群机器人渲染的摘要。

    刻意不用表格和列表 —— 机器人的 markdown(v1) 不支持这两者，
    用了会原样吐出一堆竖线和短横线。空行分段 + 引用块才是安全表达。
    """
    m = data.get("meta", {})
    p = data.get("primary", {})
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
        blocks.append("## 本周要点")
        blocks += [f"> {s}" for s in pts]
        blocks.append("")

    cs = p.get("category_structure", {})
    l1 = cs.get("l1", [])
    if l1:
        blocks.append("## 品类结构")
        blocks.append("　·　".join(f"{x['name']} {_pct(x['share'])}"
                                  for x in l1[:5]))
        blocks.append("")

    cc = p.get("concentration", {})
    hs = p.get("head_stability") or {}
    if cc or hs:
        blocks.append("## 头部格局")
        if cc:
            blocks.append(
                f"{cc.get('total', 0)} 席来自 {cc.get('distinct_publishers', 0)} 家发行商"
                f"（最大一家 {_pct(cc.get('top1_share'))}，TOP3 {_pct(cc.get('top3_share'))}）")
        if hs:
            blocks.append(c(f"TOP{hs.get('head_n')} 留存 "
                            f"{_pct(hs.get('retention_rate'))}",
                            C_ORANGE)
                          + f"　进：{'、'.join(hs.get('entered', [])[:3]) or '—'}"
                          + f"　出：{'、'.join(hs.get('exited', [])[:3]) or '—'}")
        blocks.append("")

    tail = c("口径：匿名接口每榜仅 TOP20，从榜外升入的计为「新进」而非「上升」。", C_GRAY)
    if report_url:
        tail += f"\n[查看图文周报]({report_url})"

    return _fit_bytes(blocks, tail)


def _pct(v) -> str:
    """55.0 → 55%，55.5 → 55.5%"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:.0f}%" if abs(f - round(f)) < 0.05 else f"{f:.1f}%"


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

    if content is None:
        url = report_url or cfg.report_url
        content = build_wecom_markdown(data or {}, cfg.prefix, url,
                                       v2=(msg_type == "markdown_v2"))
        print(f"  附报告链接：{url}" if url else "  未取到报告链接（非 git 仓库或没有 origin）")
    size = len(content.encode("utf-8"))
    if size > WECOM_MAX_BYTES:
        print(f"  内容 {size} 字节超上限，已自动截断到 {WECOM_MAX_BYTES}")
        content = content.encode("utf-8")[:WECOM_MAX_BYTES].decode("utf-8", "ignore")

    payload = json.dumps({"msgtype": msg_type, msg_type: {"content": content}},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg.webhook, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"  群机器人推送失败：{type(e).__name__}: {e}")
        return False

    try:
        ret = json.loads(body)
    except ValueError:
        print(f"  群机器人返回非 JSON：{body[:200]}")
        return False

    if ret.get("errcode") == 0:
        print(f"  群机器人推送成功（{size} 字节，{msg_type}）")
        return True
    code = ret.get("errcode")
    hint = WECOM_ERRHINT.get(code, "")
    print(f"  群机器人推送失败：errcode={code} errmsg={ret.get('errmsg')}"
          + (f"　→ {hint}" if hint else ""))
    return False


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="周报邮件投递")
    ap.add_argument("--report", default=None, help="指定 weekly-*.json（默认最新一期）")
    ap.add_argument("--latest", action="store_true", help="取 reports/ 下最新一期")
    ap.add_argument("--to", default=None, help="本次收件人（覆盖 MAIL_TO，逗号分隔）")
    ap.add_argument("--check", action="store_true", help="只测通道（SMTP 登录 + Webhook），不发正式内容")
    ap.add_argument("--send-test", action="store_true", help="配合 --check，登录成功后发测试信")
    ap.add_argument("--webhook-only", action="store_true", help="只推群机器人，不发邮件")
    ap.add_argument("--dry-run", action="store_true", help="生成 .eml 存本地，不真正发送")
    ap.add_argument("--outdir", default=None, help="dry-run 的 .eml 输出目录")
    args = ap.parse_args()

    cfg = Config()
    if args.to:
        cfg.to = split_addrs(args.to)

    print("投递配置：")
    print(cfg.describe())

    if args.check:
        ok = True
        lack = cfg.missing()
        if lack:
            print(f"\n跳过 SMTP 自检：缺少 {', '.join(lack)}")
        else:
            try:
                s = smtp_login(cfg)
                print(f"\nSMTP 登录成功：{cfg.host}:{cfg.port} 用户 {cfg.user}")
                s.quit()
            except Exception as e:  # noqa: BLE001
                print(f"\nSMTP 登录失败：{type(e).__name__}: {e}")
                print("排查：① 密码是否为 16 位客户端专用密码；② 是否已在邮箱【设置-收发信设置】"
                      "开启 IMAP/SMTP；③ 管理员是否在【协作-安全管理-客户端访问限制】放行该账号。")
                return EXIT_SENDFAIL
            if args.send_test:
                test = EmailMessage()
                test["Subject"] = f"[测试] {cfg.prefix} 发信通道正常"
                test["From"] = formataddr((str(cfg.sender_name), cfg.sender))
                test["To"] = ", ".join(cfg.to) if cfg.to else cfg.sender
                test["Date"] = formatdate(localtime=True)
                test.set_content("这是一封通道测试邮件，收到即表示 GitHub Actions → 企业微信邮箱 "
                                 "的 SMTP 链路可用。", charset="utf-8")
                try:
                    send(cfg, test)
                    print(f"测试信已发送给：{', '.join(cfg.to) or cfg.sender}")
                except Exception as e:  # noqa: BLE001
                    print(f"测试信发送失败：{type(e).__name__}: {e}")
                    return EXIT_SENDFAIL

        if cfg.webhook:
            print("\n群机器人自检：")
            ok = push_wecom(cfg, content=(
                f"# {cfg.prefix} · 通道自检\n"
                f"> 收到这条消息说明机器人 Webhook 可用，"
                f"后续每周一 09:00 会推送周报摘要。")) and ok
        elif lack:
            print("\n邮箱与群机器人都没配全，无可用通道。")
            ok = False
        return EXIT_OK if ok else EXIT_BADCONFIG

    report = Path(args.report) if args.report else latest_report()
    if not report.is_absolute():
        report = (ROOT / report).resolve()
    data, html_p, md_p = load_bundle(report)

    if args.webhook_only:
        if not cfg.webhook:
            print("\n未配置 WECOM_WEBHOOK，无法只推群机器人。")
            return EXIT_BADCONFIG
        print("\n只推群机器人（跳过邮件）：")
        return (EXIT_OK if push_wecom(cfg, data, report_url=cfg.report_url
                                      or github_blob_url(md_p or report))
                else EXIT_SENDFAIL)

    if not html_p:
        print(f"缺少 HTML 正文：{report.with_suffix('.html')}，请先跑 report.py")
        return EXIT_BADCONFIG

    html = html_p.read_text(encoding="utf-8")
    # 取 <body> 内容，避免嵌套完整 HTML 文档
    if "<body" in html:
        html = html.split("<body", 1)[1].split(">", 1)[1].rsplit("</body>", 1)[0]
    plain = md_p.read_text(encoding="utf-8") if md_p else report.stem

    attach = [p for p in (md_p, html_p) if p]
    msg = build_message(cfg, data, html, plain, attach)
    period = msg["Subject"].split("·", 1)[-1].strip()

    if args.dry_run:
        outdir = Path(args.outdir) if args.outdir else MAIL_PREVIEW_DIR
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / f"mail-{data['meta']['week_end']}.eml"
        out.write_bytes(bytes(msg))
        print(f"\n[dry-run] 已生成 {out.relative_to(ROOT)}（{out.stat().st_size} 字节）")
        print(f"  主题 {msg['Subject']}")
        print(f"  附件 {', '.join(p.name for p in attach)}")
        return EXIT_OK

    lack = cfg.missing()
    if lack or not cfg.to:
        print(f"\n跳过邮件：缺少 {', '.join(lack) or 'MAIL_TO'}。")
        if not cfg.webhook:
            print("也未配置 WECOM_WEBHOOK，无可用通道 —— 请在仓库 "
                  "Settings → Secrets and variables → Actions 中补齐。")
            return EXIT_BADCONFIG
        print("转为只推群机器人。")
    else:
        try:
            send(cfg, msg)
        except Exception as e:  # noqa: BLE001
            print(f"\n发送失败：{type(e).__name__}: {e}")
            return EXIT_SENDFAIL

        print(f"\n周报已发送：{period} → {', '.join(cfg.to)}"
              + (f"（抄送 {', '.join(cfg.cc)}）" if cfg.cc else ""))

    if cfg.webhook:
        push_wecom(cfg, data, report_url=cfg.report_url or github_blob_url(md_p or report))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
