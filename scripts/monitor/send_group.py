#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报投递 · 企业微信群（走本机 wecom-cli，由机器人在群里发消息）。

和 `send_wecom.py` 里「方式 A：群机器人 Webhook」的区别：

    ┌──────────────┬─────────────────────────┬──────────────────────────┐
    │              │ Webhook（方式 A）        │ 本脚本（机器人直发）      │
    ├──────────────┼─────────────────────────┼──────────────────────────┤
    │ 凭据         │ 群机器人 Webhook URL      │ 群会话 ID + 本机授权      │
    │ 跑在哪       │ GitHub Actions 里就行     │ 只能本机（CI 拿不到授权） │
    │ 触发         │ weekly.yml 自动           │ WorkBuddy automation 定时 │
    └──────────────┴─────────────────────────┴──────────────────────────┘

wecom-cli 的授权凭据存在本机，GitHub runner 访问不到，所以这条通道必须由
WorkBuddy 侧的定时任务驱动，不能在 CI 里跑。

用法：
    python scripts/monitor/send_group.py --list                 # 列可发送的会话
    python scripts/monitor/send_group.py --latest --dry-run     # 只看内容，不发
    python scripts/monitor/send_group.py --latest               # 发最新一期周报
    python scripts/monitor/send_group.py --report reports/weekly-2026-09-14.json
    python scripts/monitor/send_group.py --latest --text-only   # 只发纯文本（降级通道）

群会话 ID 的来源优先级：--group-id > 环境变量 WECOM_GROUP_ID。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from send_wecom import (  # noqa: E402
    build_wecom_markdown, github_blob_url, latest_report, load_bundle,
)

CLI = "wecom-cli"
# 群消息里不放内部 ID；这两个是「给用户看」的兜底文案
DEFAULT_PREFIX = "微信小游戏周报"

EXIT_OK = 0
EXIT_BADCONFIG = 2
EXIT_SENDFAIL = 3


# --------------------------------------------------------------------------
# wecom-cli 调用
# --------------------------------------------------------------------------
def cli_path() -> str:
    p = shutil.which(CLI)
    if not p:
        raise SystemExit(
            f"找不到 {CLI}。这条通道依赖 WorkBuddy 企业微信连接器，"
            "请先安装（npm install -g @wecom/cli）并完成授权。"
        )
    return p


def run_cli(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(
        [cli_path(), *args], capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, proc.stdout, proc.stderr


def list_sessions() -> list[dict]:
    rc, out, err = run_cli(["message", "aibot", "sessions", "list"])
    if rc != 0:
        raise SystemExit(f"{CLI} 取会话列表失败：{(err or out)[:300]}")
    try:
        data = json.loads(out)
    except ValueError:
        raise SystemExit(f"{CLI} 返回非 JSON，无法解析会话列表：{out[:300]}")
    return data.get("sessions") or []


def send_markdown(chat_id: str, content: str) -> tuple[bool, str]:
    payload = json.dumps(
        {"chat_id": chat_id, "msg_type": "markdown", "markdown": {"content": content}},
        ensure_ascii=False,
    )
    rc, out, err = run_cli(["message", "aibot", "send", "--json", payload])
    return rc == 0, (out or err).strip()


def send_text(chat_id: str, content: str) -> tuple[bool, str]:
    """降级通道：admin 侧的 message.send 只支持 text，最长 2048 字符。"""
    payload = json.dumps(
        {"chat_id": chat_id, "msg_type": "text", "text": {"content": content[:2048]}},
        ensure_ascii=False,
    )
    rc, out, err = run_cli(["message", "send", "--json", payload])
    return rc == 0, (out or err).strip()


# --------------------------------------------------------------------------
# 内容
# --------------------------------------------------------------------------
def build_content(data: dict, prefix: str, report_url: str) -> str:
    """复用 send_wecom 的群摘要（空行分段 + 引用块，不用表格/列表）。"""
    return build_wecom_markdown(data, prefix, report_url)


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="周报投递 · 企业微信群（机器人直发）")
    ap.add_argument("--report", default=None, help="指定 weekly-*.json（默认最新一期）")
    ap.add_argument("--latest", action="store_true", help="取 reports/ 下最新一期")
    ap.add_argument("--group-id", default=None,
                    help="目标群会话 ID（默认取环境变量 WECOM_GROUP_ID）")
    ap.add_argument("--list", action="store_true", help="列出当前可发送消息的会话")
    ap.add_argument("--dry-run", action="store_true", help="只打印内容，不发送")
    ap.add_argument("--text-only", action="store_true",
                    help="用降级通道发纯文本（markdown 通道不可用时）")
    ap.add_argument("--prefix", default=os.environ.get("REPORT_PREFIX", DEFAULT_PREFIX))
    a = ap.parse_args()

    if a.list:
        sessions = list_sessions()
        if not sessions:
            print("当前没有可发送消息的最近会话。")
            return EXIT_OK
        print(f"可发送的会话共 {len(sessions)} 个：")
        for i, s in enumerate(sessions, 1):
            kind = "群聊" if s.get("chat_type") == "group" else "单聊"
            print(f"  {i}. {s.get('chat_name')}（{kind}，最后消息 {s.get('last_msg_time')}）")
        return EXIT_OK

    report = Path(a.report) if a.report else latest_report()
    data, md_p = load_bundle(report)
    # 链接指向 .md —— GitHub 上会渲染成可读页面；.json 点开是一屏原始 JSON
    url = github_blob_url(md_p or report)
    content = build_content(data, a.prefix, url)

    m = data.get("meta", {})
    title = f"{m.get('baseline_date')} ~ {m.get('week_end')}" if m.get("baseline_date") else report.stem
    print(f"报告：{report.name}（{title}）")
    print(f"正文：{len(content.encode('utf-8'))} 字节{'，附链接 ' + url if url else ''}")

    if a.dry_run:
        print("-" * 60)
        print(content)
        print("-" * 60)
        print("（dry-run，未发送）")
        return EXIT_OK

    group_id = a.group_id or os.environ.get("WECOM_GROUP_ID", "")
    if not group_id:
        print("没有指定目标群：用 --group-id 或环境变量 WECOM_GROUP_ID 给一个。")
        print("不知道群会话 ID 时，先跑 --list 看当前可发送的会话。")
        return EXIT_BADCONFIG

    ok, detail = (send_text(group_id, content) if a.text_only
                  else send_markdown(group_id, content))
    if ok:
        print(f"已推送到群（{'纯文本' if a.text_only else 'markdown'}）。")
        return EXIT_OK
    print(f"推送失败：{detail[:500]}")
    if not a.text_only:
        print("提示：markdown 通道失败时可用 --text-only 走纯文本降级通道。")
    return EXIT_SENDFAIL


if __name__ == "__main__":
    sys.exit(main())
