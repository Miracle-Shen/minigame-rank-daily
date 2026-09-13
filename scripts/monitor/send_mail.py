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

用法：
    python scripts/monitor/send_mail.py --check                # 测连通性 + 登录
    python scripts/monitor/send_mail.py --check --send-test    # 再给自己发一封测试信
    python scripts/monitor/send_mail.py --dry-run              # 生成 .eml 到本地，不发送
    python scripts/monitor/send_mail.py --latest               # 发送最新一期周报
    python scripts/monitor/send_mail.py --report reports/weekly-2026-09-14.json
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
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
            f"  群机器人  {'已配置' if self.webhook else '未配置'}"
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


def push_wecom(cfg: Config, data: dict, plain: str) -> None:
    """可选：同步推一条 markdown 摘要到企业微信群机器人。"""
    if not cfg.webhook:
        return
    import urllib.request

    m = data.get("meta", {})
    head = f"**{cfg.prefix}** · {m.get('baseline_date')} ~ {m.get('week_end')}\n"
    body = "\n".join(plain.splitlines()[:24])
    payload = json.dumps({"msgtype": "markdown",
                          "markdown": {"content": head + body}},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(cfg.webhook, data=payload,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"  群机器人推送：{r.status} {r.read().decode(errors='replace')[:120]}")
    except Exception as e:  # noqa: BLE001
        print(f"  群机器人推送失败（不影响邮件）：{type(e).__name__}: {e}")


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="周报邮件投递")
    ap.add_argument("--report", default=None, help="指定 weekly-*.json（默认最新一期）")
    ap.add_argument("--latest", action="store_true", help="取 reports/ 下最新一期")
    ap.add_argument("--to", default=None, help="本次收件人（覆盖 MAIL_TO，逗号分隔）")
    ap.add_argument("--check", action="store_true", help="只测连通性与登录，不发信")
    ap.add_argument("--send-test", action="store_true", help="配合 --check，登录成功后发测试信")
    ap.add_argument("--dry-run", action="store_true", help="生成 .eml 存本地，不真正发送")
    ap.add_argument("--outdir", default=None, help="dry-run 的 .eml 输出目录")
    args = ap.parse_args()

    cfg = Config()
    if args.to:
        cfg.to = split_addrs(args.to)

    print("邮件配置：")
    print(cfg.describe())

    if args.check:
        lack = cfg.missing()
        if lack:
            print(f"\n缺少环境变量：{', '.join(lack)}（--check 需要能登录才能验证）")
            return EXIT_BADCONFIG
        try:
            s = smtp_login(cfg)
            print(f"\n登录成功：{cfg.host}:{cfg.port} 用户 {cfg.user}")
            s.quit()
        except Exception as e:  # noqa: BLE001
            print(f"\n登录失败：{type(e).__name__}: {e}")
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
        return EXIT_OK

    report = Path(args.report) if args.report else latest_report()
    if not report.is_absolute():
        report = (ROOT / report).resolve()
    data, html_p, md_p = load_bundle(report)

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
        print(f"\n缺少配置：{', '.join(lack) or 'MAIL_TO'}，跳过发信。")
        print("请在仓库 Settings → Secrets and variables → Actions 中补齐。")
        return EXIT_BADCONFIG

    try:
        send(cfg, msg)
    except Exception as e:  # noqa: BLE001
        print(f"\n发送失败：{type(e).__name__}: {e}")
        return EXIT_SENDFAIL

    print(f"\n周报已发送：{period} → {', '.join(cfg.to)}"
          + (f"（抄送 {', '.join(cfg.cc)}）" if cfg.cc else ""))
    push_wecom(cfg, data, plain)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
