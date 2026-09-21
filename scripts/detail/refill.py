#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键补档：给「新上榜但还没档案」的游戏补齐产品档案。

把六步合成一条命令，并且**把刷新工单设为必经第一步**：

    1. build_worklist.py   刷新工单（**必经**）-> _worklist.json
    2. enrich_media.py     抓官方图标/截图/包体 -> _media.json
    3. make_batches.py     切成批次输入 -> _staging/_batch_N_input.json
    4. （子智能体调研）     读 RESEARCH_SPEC.md 写 -> _staging/batch_N.json  ← 无脚本
    5. merge_details.py    写回 <slug>.json + index.json
    6. verify.py           终检

## 为什么第一步必须是刷新工单

`_worklist.json` 是**快照**，不会自己跟着榜单更新。不刷新就切批，切出来的是上一次
的名单 —— 曾因此在 09-21 对不上当天新上榜的 3 款（模拟城市：我是市长、渔力全开、
切开一切3D）。所以本脚本自己调 `build_worklist.py`，不给「跳过」的开关。

## 为什么第 4 步会停下来

那一步要现读规范、现做调研，没有脚本可代替。本脚本用一个「两段式」绕开它：

    第一段：refill.py                 刷新工单 -> 抓素材 -> 切批 -> 打印派单说明（退出码 3）
    第 4 步：（派子智能体调研，人工/模型）
    第二段：refill.py 或 --finish      合并 -> 终检 -> 报告补了谁

**第二段再跑一次同一条命令就行**：脚本会先刷新工单（必经），然后发现「上次备的批次
产物已经齐了」，就直接接力到合并，**不会重新切一批**（重切会因编号 +1 而把刚写好的
产物晾在一边）。

## 用法

    python scripts/detail/refill.py                  # 一键（就绪则一条命令跑完）
    python scripts/detail/refill.py --status         # 只看缺口，不动任何文件
    python scripts/detail/refill.py --prepare-only   # 只备料，不自动续跑
    python scripts/detail/refill.py --finish         # 明确表示「调研已写完」，直接合并 + 校验

常用开关：

    --scope wx|all    补哪些。wx（默认）= 周报口径「微信三榜 TOP20 并集」；
                      all = 工单里全部待建档（含抖音/iOS/安卓/TapTap）
    --names "A,B"     点名只补这几款（在 scope 之内再筛）
    --size 6          每批几款（子智能体的粒度，默认 6）
    --source today|recent7|all
                      刷新工单的取数范围，默认 today
    --no-icons        刷新工单时不抓图标（离线可用；图标仍可由 enrich_media 补）
    --force-media     忽略 _media.json 缓存重查（默认增量，只补没查过的）
    --check-targets   只报告上次备料的目标是否已全部落库（读 _refill_state.json）

退出码：0 = 完成 / 3 = 等调研产物 / 1 = 出错。

> `--status` 与 `--check-targets` 是只读的，**不刷新工单**；其余入口一律先刷新。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

# 本脚本会 fork 子脚本共享同一个 stdout。若输出被管道接走（`| tail`、CI 日志收集），
# Python 默认按块缓冲，本脚本自己的 banner 会攒到最后才吐，跟子进程输出交错错位 ——
# 看起来像「步骤顺序乱了」。改成行缓冲，并在每次 fork 前显式 flush。
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001
    pass

from schema import norm_name  # noqa: E402

DETAIL_DIR = ROOT / "data" / "detail"
STAGING = DETAIL_DIR / "_staging"
WORKLIST = DETAIL_DIR / "_worklist.json"
INDEX = DETAIL_DIR / "index.json"
STATE = DETAIL_DIR / "_refill_state.json"
BEIJING = timezone(timedelta(hours=8))

EXIT_OK, EXIT_ERROR, EXIT_NEED_RESEARCH = 0, 1, 3

BAR = "─" * 64


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def step(n: int, total: int, text: str) -> None:
    print(f"\n{BAR}\n[{n}/{total}] {text}\n{BAR}")


def run_script(name: str, args: list[str], *, capture: bool = False):
    """在仓库根目录跑同目录下的脚本。

    默认直通 stdout（进度条/警告都让用户看见）；capture=True 时返回
    (rc, 输出文本)，给需要判读结果的 verify.py 用。
    """
    cmd = [sys.executable, str(HERE / name), *args]
    print(f"  $ python scripts/detail/{name} {' '.join(args)}".rstrip())
    sys.stdout.flush()          # 先把自己的话吐干净，否则会被子进程输出插队
    if capture:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out = (r.stdout or "") + (r.stderr or "")
        print(out.rstrip())
        return r.returncode, out
    r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode, ""


def norm_set(names) -> set[str]:
    return {norm_name(n) for n in names if n}


def dated() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------
# 缺口计算
# --------------------------------------------------------------------------
def wx_pool_names() -> set[str]:
    """周报口径的候选池（微信三榜 TOP20 并集）归一化名字。

    直接复用周报自己的 `clone.merge_pool`（`scripts/monitor/`），
    保证这里的「缺口」和周报里那句「无档案 N 款」是**同一套口径**。
    导入/分析失败时返回空集合，调用方回落到「工单里带 wx/ 榜的条目」。
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "monitor"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            import analyze as A  # noqa: E402
            import clone as C  # noqa: E402
            res = A.analyze()
            pool = C.merge_pool(res.get("boards") or [])
        return norm_set(p["name"] for p in pool)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 复用周报口径失败（{type(e).__name__}: {e}），"
              f"回落为「工单里带 wx/ 榜的条目」")
        return set()


def archived_keys() -> set[str]:
    """**真正已落库**的游戏名字（归一化）：索引里有键，且 `<slug>.json` 真在磁盘上。

    不能只看索引键：`merge_details.py` 会给工单里的待建档游戏也建索引行
    （collected 取假、文件不写），键在、文件不在 —— 拿键当「已建档」会把
    占位行误判成补齐成功。这里与 `verify.py` 的「文件缺失」判定保持同一口径。
    """
    idx = load_json(INDEX, {}) or {}
    out: set[str] = set()
    for key, meta in (idx.get("games") or {}).items():
        if (DETAIL_DIR / f"{meta.get('slug', '')}.json").exists():
            out.add(norm_name(key))
    return out


def worklist_entries() -> list[dict]:
    wl = load_json(WORKLIST, {}) or {}
    return wl.get("entries") or []


def worklist_pending(entries: list[dict]) -> list[dict]:
    """工单里 status != done 的条目。"""
    return [e for e in entries if e.get("status") != "done"]


def in_wx_scope(entry: dict, pool: set[str]) -> bool:
    if pool:
        return norm_name(entry["name"]) in pool
    return any(str(b).startswith("wx/") for b in (entry.get("boards") or []))


def pick_targets(entries: list[dict], scope: str, names: list[str],
                 pool: set[str]) -> tuple[list[dict], list[str]]:
    """从工单待建档条目里筛出本次目标，返回 (目标条目, 被点名但没找到的名字)。"""
    pending = worklist_pending(entries)
    if scope == "wx":
        pending = [e for e in pending if in_wx_scope(e, pool)]
    if names:
        want = norm_set(names)
        picked = [e for e in pending if norm_name(e["name"]) in want]
        found = {norm_name(e["name"]) for e in picked}
        return picked, [n for n in names if norm_name(n) not in found]
    return pending, []


# --------------------------------------------------------------------------
# 批次编号：续着已有产物走，避免盖掉上一轮
# --------------------------------------------------------------------------
BATCH_OUT_RE = re.compile(r"^batch_(\d+)\.json$")
BATCH_IN_RE = re.compile(r"^_batch_(\d+)_input\.json$")


def next_batch_start() -> int:
    nums = []
    for fp in STAGING.glob("*.json"):
        m = BATCH_OUT_RE.match(fp.name) or BATCH_IN_RE.match(fp.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


# --------------------------------------------------------------------------
# 状态文件（两个阶段之间传递「本次补了谁」）
# --------------------------------------------------------------------------
def save_state(targets: list[dict], batch_names: list[list[str]],
               first: int, size: int, scope: str) -> None:
    batches = [{
        "batch": first + i,
        "input": f"data/detail/_staging/_batch_{first + i}_input.json",
        "output": f"data/detail/_staging/batch_{first + i}.json",
        "names": names,
    } for i, names in enumerate(batch_names)]
    STATE.write_text(json.dumps({
        "prepared_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "scope": scope,
        "size": size,
        "targets": [e["name"] for e in targets],
        "batches": batches,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def read_state() -> dict:
    return load_json(STATE, {}) or {}


def batch_ready(batch: dict) -> tuple[bool, str]:
    """批次产物是否就绪：文件在 + 名字齐（缺谁一并返回）。"""
    fp = ROOT / batch["output"]
    if not fp.exists():
        return False, "文件不存在"
    data = load_json(fp)
    items = data.get("games") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return False, "结构不是数组"
    got = norm_set(i.get("name") for i in items if isinstance(i, dict))
    lost = [n for n in batch["names"] if norm_name(n) not in got]
    if lost:
        return False, f"少 {len(lost)} 款：{'、'.join(lost[:3])}"
    return True, ""


def all_ready(state: dict) -> tuple[bool, list[dict]]:
    batches = state.get("batches") or []
    if not batches:
        return False, []
    todo = []
    for b in batches:
        ok, why = batch_ready(b)
        if not ok:
            todo.append({**b, "why": why})
    return (not todo), todo


# --------------------------------------------------------------------------
# 阶段一：备料（拆成三小步，好让「第二次跑」能只复用成果、不重复切批）
# --------------------------------------------------------------------------
def refresh_worklist(args) -> int:
    """第 1 步，**必经**：`_worklist.json` 是快照，不刷新就会按上一次的名单切批。"""
    step(1, 3, "刷新工单（必经第一步：_worklist.json 是快照，不刷新会按老名单切批）")
    wl_args = ["--source", args.source]
    if args.no_icons:
        wl_args.append("--no-icons")
    rc, _ = run_script("build_worklist.py", wl_args)
    if rc != 0:
        print("[error] 刷新工单失败，后面步骤不再执行")
        return EXIT_ERROR
    if not worklist_entries():
        print("[error] 刷新后工单为空")
        return EXIT_ERROR
    return EXIT_OK


def fetch_media(args) -> None:
    """第 2 步：抓官方图标/截图/包体（增量，跨次合并缓存）。"""
    step(2, 3, "抓官方图标 / 实机截图 / 包体（增量，只查没缓存的）")
    rc, _ = run_script("enrich_media.py",
                       ["--force"] if args.force_media else [])
    if rc != 0:
        print("[warn] 抓素材非零退出，继续（可能只是部分款没命中）")


def cut_batches(args) -> int:
    """第 3 步：把本次目标切成批次输入文件，并落一份 state 供第二段接力。"""
    step(3, 3, "切批次输入文件（给调研子智能体）")
    entries = worklist_entries()

    pool = wx_pool_names() if args.scope == "wx" else set()
    targets, missing = pick_targets(entries, args.scope, args.names_list, pool)
    if missing:
        print(f"[warn] {len(missing)} 款被点名但不在本次范围"
              f"（已建档 / 已掉榜 / scope 之外）：" + "、".join(missing))

    pending_all = len(worklist_pending(entries))
    if not targets:
        print(f"\n✅ 没有缺口：工单待建档 {pending_all} 款，"
              f"本次口径（{args.scope}）内一款都不缺。")
        return EXIT_OK

    first = next_batch_start()
    from make_batches import write_batches  # noqa: E402  （HERE 已在模块顶部入 path）
    written = write_batches(targets, size=args.size, first=first)

    chunks = [targets[i:i + args.size] for i in range(0, len(targets), args.size)]
    save_state(targets, [[e["name"] for e in c] for c in chunks],
               first, args.size, args.scope)

    print(f"\n本次目标 {len(targets)} 款 -> {len(written)} 批"
          f"（编号 {first}–{first + len(written) - 1}）")
    for i, chunk in enumerate(chunks, first):
        print(f"  batch_{i}  " + "、".join(e["name"] for e in chunk))

    print(f"\n缺口总览：刷新后工单待建档 {pending_all} 款，"
          f"本次口径（{args.scope}）{len(targets)} 款已切批，"
          f"口径外还剩 {max(pending_all - len(targets), 0)} 款"
          f"（想一起补用 --scope all）")
    return EXIT_NEED_RESEARCH


# --------------------------------------------------------------------------
# 派单说明（第 4 步没有脚本，这一段是给人/给模型看的）
# --------------------------------------------------------------------------
def print_handoff(todo: list[dict]) -> None:
    print(f"\n{BAR}")
    print("还差一步：生成档案内容（这一步没有脚本，要派子智能体调研）")
    print(BAR)
    print("\n每个批次派一个子智能体（可并行），交给它这句话：\n")
    for b in todo:
        print(f"  读 scripts/detail/RESEARCH_SPEC.md，再读 {b['input']}，")
        print(f"  按里面的 games 逐款调研，把结果写成 {b['output']}（UTF-8 JSON 数组）。")
        print(f"      → 本批 {len(b['names'])} 款：{'、'.join(b['names'])}")
    print("\n写完回来跑这一条（会自动合并 + 终检）：\n")
    print("  python scripts/detail/refill.py --finish")
    print(f"\n退出码 3 = 在等调研产物，这是预期状态，不是报错。")


# --------------------------------------------------------------------------
# 阶段二：合并 + 校验
# --------------------------------------------------------------------------
COVERAGE_RE = re.compile(r"\[覆盖\] 工单缺失 (\d+)")
STRUCT_RE = re.compile(r"\[结构\] schema 不合格 (\d+) \| 8维缺失/越界 (\d+) \| 文件缺失或损坏 (\d+)")
CONTENT_RE = re.compile(r"\[内容\] 核心玩法空 (\d+) \| 爽点空 (\d+) \| 复刻结论空 (\d+)")


def classify_verify(rc: int, out: str, remaining: int) -> tuple[str, list[str]]:
    """判读 verify.py 的结果。

    补档是**按口径分批**做的，工单里口径外的款还没建，verify 必然报警 —— 而且是
    两处同时报：既算「文件缺失」（索引有行、文件没写），又算「覆盖缺失」。

    所以 **文件缺失 / 覆盖缺失这两个数，只要不超过「还没补的数量」，就是预期内的**，
    不能当成故障；schema / 8 维 / 内容为空才是真失败（它们只看已存在的文件）。

    返回 (verdict, 原因列表)：pass / waiting / fail。
    """
    if rc == 0:
        return "pass", []
    cov = COVERAGE_RE.search(out)
    struct = STRUCT_RE.search(out)
    cont = CONTENT_RE.search(out)

    hard: list[str] = []
    waiting: list[str] = []

    def note(label: str, n: int) -> None:
        if not n:
            return
        if n <= remaining:
            waiting.append(f"{label} {n} 条 = 还没补的那些，预期内")
        else:
            hard.append(f"{label} {n} 条，超出还没补的 {remaining} 款")

    if struct:
        schema_n, dim_n, file_n = (int(x) for x in struct.groups())
        if schema_n:
            hard.append(f"schema 不合格 {schema_n} 条")
        if dim_n:
            hard.append(f"8 维打分不全/越界 {dim_n} 条")
        note("文件缺失或损坏", file_n)
    if cont and any(int(x) for x in cont.groups()):
        hard.append(f"内容为空（核心玩法 {cont.group(1)} / 爽点 {cont.group(2)}"
                    f" / 复刻结论 {cont.group(3)}）")
    if cov:
        note("覆盖度缺失", int(cov.group(1)))

    if hard:
        return "fail", hard
    if waiting:
        return "waiting", waiting
    return "fail", ["verify 非零退出，且未能判读原因，请看上面原始输出"]


def finish(args) -> int:
    state = read_state()
    if not state:
        print("[error] 没有 _refill_state.json，先跑一次 python scripts/detail/refill.py")
        return EXIT_ERROR

    ready, todo = all_ready(state)
    if not ready and not args.force:
        print(f"调研产物还没齐（{len(todo)}/{len(state.get('batches') or [])} 批未就绪）：")
        for b in todo:
            print(f"  {b['output']}  ->  {b['why']}")
        print_handoff(todo)
        return EXIT_NEED_RESEARCH

    targets = state.get("targets") or []

    step(1, 3, "合并调研产物 -> data/detail/<slug>.json + index.json")
    rc, _ = run_script("merge_details.py", [])
    if rc != 0:
        print("[error] 合并失败")
        return EXIT_ERROR

    step(2, 3, "终检")
    rc, out = run_script("verify.py", [], capture=True)

    step(3, 3, "本次目标是否已落库")
    have = archived_keys()
    done = [n for n in targets if norm_name(n) in have]
    left = [n for n in targets if norm_name(n) not in have]

    remaining = sum(1 for e in worklist_pending(worklist_entries())
                    if norm_name(e["name"]) not in have)
    verdict, why = classify_verify(rc, out, remaining)

    bar = "=" * 64
    hint = " ✅"
    if remaining:
        hint = ("（跑 --scope all 可一起补齐）" if args.scope == "wx"
                else "（这批落库后重跑 refill.py 继续下一批）")
    print(f"\n{bar}\n补档结果（{dated()}）\n{bar}")
    print(f"本次目标        {len(targets)} 款")
    print(f"已落库          {len(done)} 款")
    print(f"未落库          {len(left)} 款" + (f"：{'、'.join(left)}" if left else ""))
    print(f"仍未补          {remaining} 款{hint}")
    print(f"verify.py       " + {"pass": "全部通过 ✅",
                              "waiting": "只卡在「还有款没补」，本次目标已合格 ⚠️",
                              "fail": "存在真问题 ❌"}[verdict])
    for w in why:
        print(f"                {w}")

    if left:
        print(f"{bar}\n❌ 有目标没落库。合并告警里找这几款：{'、'.join(left)}\n{bar}")
        return EXIT_ERROR
    if verdict == "fail":
        print(f"{bar}\n❌ verify 未通过，先修上面列的问题\n{bar}")
        return EXIT_ERROR

    print(f"{bar}\n✅ 本次 {len(done)} 款档案已补齐并入库\n{bar}")
    return EXIT_OK


def state_still_relevant(state: dict) -> bool:
    """上次那批目标里还有没落库的吗？全部已落库就没必要再走一遍合并。"""
    have = archived_keys()
    return any(norm_name(n) not in have for n in (state.get("targets") or []))


# --------------------------------------------------------------------------
# 只读：看现在缺什么
# --------------------------------------------------------------------------
def status() -> int:
    print(f"{BAR}\n当前缺口（只读，不刷新工单、不写任何文件）\n{BAR}")
    if not WORKLIST.exists():
        print("[error] 没有 _worklist.json，先跑 build_worklist.py")
        return EXIT_ERROR

    wl = load_json(WORKLIST, {}) or {}
    entries = wl.get("entries") or []
    have = archived_keys()
    pending = worklist_pending(entries)

    print(f"工单生成时间    {wl.get('generated_at')}（source={wl.get('source')}）")
    print(f"已真正落库      {len(have)} 款（索引有键且 <slug>.json 在磁盘上）")
    print(f"工单待建档      {len(pending)} 款")

    pool = wx_pool_names()
    wx_pending = [e for e in pending if in_wx_scope(e, pool)]
    print(f"\n周报口径（微信三榜 TOP20 并集，{len(pool) or '—'} 款）"
          f"里缺档案 {len(wx_pending)} 款：")
    for e in wx_pending:
        print(f"  #{e.get('best_rank') or '-':<4} {e['name']}"
              f"　{'/'.join(e.get('boards') or [])}")
    if not wx_pending:
        print("  （无缺口）")

    others = len(pending) - len(wx_pending)
    if others:
        print(f"\n口径外还有 {others} 款待建档（抖音/iOS/安卓/TapTap），"
              f"要一起补用 --scope all")
    print(f"\n提醒：工单是快照，上面这个缺口可能已经过时。"
          f"\n     真正补档时 refill.py 会先强制刷新一次工单。")
    return EXIT_OK


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="一键补档：刷新工单 -> 抓素材 -> 切批 ->（调研）-> 合并 -> 校验")
    ap.add_argument("--status", action="store_true", help="只看缺口，不动文件")
    ap.add_argument("--finish", action="store_true",
                    help="跳过备料与切批，直接合并 + 校验")
    ap.add_argument("--prepare-only", action="store_true", help="只备料，不自动续跑")
    ap.add_argument("--check-targets", action="store_true",
                    help="只报告上次备料的目标是否已落库")
    ap.add_argument("--scope", choices=["wx", "all"], default="wx",
                    help="wx=周报口径（默认）；all=工单内全部待建档")
    ap.add_argument("--names", default="", help="点名只补这几款（逗号分隔）")
    ap.add_argument("--size", type=int, default=6, help="每批几款（默认 6）")
    ap.add_argument("--source", choices=["today", "recent7", "all"], default="today",
                    help="刷新工单的取数范围，默认 today")
    ap.add_argument("--no-icons", action="store_true", help="刷新工单时不抓图标")
    ap.add_argument("--force-media", action="store_true", help="忽略媒体缓存重查")
    ap.add_argument("--force", action="store_true",
                    help="--finish 时即使调研产物不齐也强行合并")
    args = ap.parse_args()
    args.names_list = [s.strip() for s in args.names.split(",") if s.strip()]

    # 两个只读入口：不刷新工单
    if args.status:
        return status()
    if args.check_targets:
        state = read_state()
        if not state:
            print("[error] 没有 _refill_state.json，先跑一次 refill.py 备料")
            return EXIT_ERROR
        have = archived_keys()
        left = [n for n in (state.get("targets") or []) if norm_name(n) not in have]
        total = len(state.get("targets") or [])
        print(f"上次备料 {state.get('prepared_at')}：目标 {total} 款，"
              f"已落库 {total - len(left)} 款"
              + (f"，还差 {len(left)}：{'、'.join(left)}" if left else " ✅"))
        return EXIT_OK if not left else EXIT_NEED_RESEARCH

    if not list((ROOT / "data" / "daily").glob("*.json")):
        print("[error] data/daily 下没有快照，先跑一次抓取")
        return EXIT_ERROR

    # 第 1 步对两个入口都跑：刷新工单是本脚本的必经第一步
    if refresh_worklist(args) != EXIT_OK:
        return EXIT_ERROR

    if args.finish:
        return finish(args)

    # 上次备的料如果已经调研完了，就别再切一批新的 —— 直接接力到合并
    state = read_state()
    ready, _ = all_ready(state)
    if ready and state_still_relevant(state):
        print(f"\n上次备的 {len(state.get('batches') or [])} 批调研产物已就绪，"
              f"直接接力到合并（不重复切批）…")
        return finish(args)

    fetch_media(args)
    rc = cut_batches(args)
    if rc != EXIT_NEED_RESEARCH or args.prepare_only:
        return rc

    ready, todo = all_ready(read_state())
    if ready:
        print("\n检测到调研产物已就绪，自动续跑到合并 + 终检…")
        return finish(args)

    print_handoff(todo)
    return EXIT_NEED_RESEARCH


if __name__ == "__main__":
    raise SystemExit(main())
