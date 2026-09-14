#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地预览站点：按 Pages 的布局把 site/ 与 data/ 拼到 _pages_preview/。

为什么需要它：站点用相对路径取数据（`data/detail/index.json`），
只有把 site/* 铺在根、data/ 放在同级，相对路径才对得上。
直接在 site/ 下开 http.server 会取不到数据。

用法：
    python scripts/detail/preview_site.py            # 只构建
    python scripts/detail/preview_site.py --serve    # 构建后起本地服务
    python scripts/detail/preview_site.py --serve --port 8899
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SITE = ROOT / "site"
DATA = ROOT / "data"
OUT = ROOT / "_pages_preview"


def build() -> None:
    """增量同步到 _pages_preview/。

    刻意**不做 rmtree**：一是沙箱/系统安全策略会把大目录递归删除判定为高危操作
    直接拦掉，二是增量复制更快，也避免误删正在被本地服务读的文件。
    代价是已下线的内容会残留，重新构建时手动清即可。
    """
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SITE, OUT, dirs_exist_ok=True)
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    for sub in ("daily", "diff", "base"):
        src = DATA / sub
        if src.exists():
            shutil.copytree(src, OUT / "data" / sub, dirs_exist_ok=True)
    # 详情页只拷正式档案，跳过 _worklist/_media/_icons/_staging 等中间产物
    src_detail = DATA / "detail"
    if src_detail.exists():
        dst = OUT / "data" / "detail"
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in src_detail.glob("*.json"):
            if f.name.startswith("_"):
                continue
            shutil.copy2(f, dst / f.name)
            n += 1
        print(f"详情档案同步 {n} 份")
    for f in ("latest.json", "history.jsonl"):
        if (DATA / f).exists():
            shutil.copy2(DATA / f, OUT / "data" / f)

    daily = sorted(p.stem for p in (OUT / "data" / "daily").glob("*.json"))
    diff = sorted(p.stem for p in (OUT / "data" / "diff").glob("*.json"))
    (OUT / "data" / "index.json").write_text(
        json.dumps({"daily": daily, "diff": diff}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    detail_idx = OUT / "data" / "detail" / "index.json"
    if detail_idx.exists():
        idx = json.loads(detail_idx.read_text(encoding="utf-8"))
        print(f"详情页：{idx.get('collected')}/{idx.get('total')} 已收集")
    else:
        print("[warn] 没有 data/detail/index.json，详情分区会整体隐藏")


def free_port(start: int = 8899) -> int:
    for p in range(start, start + 60):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    build()
    print(f"已构建 -> {OUT.relative_to(ROOT)}")
    if not args.serve:
        return 0

    port = args.port or free_port()
    print(f"\n预览地址：http://127.0.0.1:{port}/game.html")
    print("  详情页需要先有 data/detail/index.json（跑 build_worklist + merge_details）")
    print("  Ctrl+C 结束\n")
    try:
        subprocess.run([sys.executable, "-m", "http.server", str(port),
                        "--bind", "127.0.0.1", "--directory", str(OUT)],
                       check=False)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
