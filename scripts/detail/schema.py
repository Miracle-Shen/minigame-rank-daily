#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏详情页的字段规范（单一事实来源）。

站点渲染、合并器校验、调研产物都以此为准。改字段先改这里。

## 评分口径（重要，别反着填）

`tech.dims[].score` 是**实现难度**，不是「性价比」：

    1 = 几乎白送（有现成模板/素材，改配置即可）
    2 = 容易（少量适配）
    3 = 中等（需要真实工作量，但有清晰参照）
    4 = 偏难（需要专项投入，如手感调优、物理、联网）
    5 = 很难（需要从零构建或大量量产，是这类项目的主要风险点）

`tech.cost_score` = 各维度均值，`tech.cost_level` 由均值分段得到，
分段线见 COST_LEVELS。**分数越低 = 复刻越便宜。**
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------
# 成本维度
# --------------------------------------------------------------------------
COST_DIMS: list[tuple[str, str, str]] = [
    ("engine", "引擎与框架", "有无成熟模板可套；H5/小游戏基建是否现成"),
    ("art", "美术素材量", "需要量产多少套图；能否用素材库或程序化生成"),
    ("anim", "动画与特效", "骨骼/序列帧/粒子，动作表现是否吃重"),
    ("physics", "物理与手感", "碰撞、刚体、拖拽吸附、手感调优难度"),
    ("logic", "玩法逻辑与关卡", "核心循环代码量；关卡是否需要逐关手工设计"),
    ("data", "数值与养成体系", "成长线、经济系统、留存钩子的复杂度"),
    ("server", "服务端与联网", "是否需要账号、排行榜、对战、存档同步"),
    ("audio", "音频", "音效与BGM的定制程度"),
]
DIM_KEYS = [k for k, _, _ in COST_DIMS]
DIM_LABELS = {k: lbl for k, lbl, _ in COST_DIMS}
DIM_DESC = {k: d for k, _, d in COST_DIMS}

# 均值 -> 成本档位
COST_LEVELS: list[tuple[float, str]] = [
    (1.8, "极低"),
    (2.6, "低"),
    (3.4, "中"),
    (4.2, "高"),
    (9.9, "极高"),
]

DIMENSIONS = ["2D", "2.5D", "3D", "2D+3D混合"]

# --------------------------------------------------------------------------
# 引擎归一化
# --------------------------------------------------------------------------
# 调研产物里引擎写法五花八门（「Unity（推断）」「Cocos Creator (推断)」
# 「Unity / Cocos（inferred，休闲排序游戏常见选择）」…），直接当标签用没法看。
# 这里按**特征串 -> 规范名**归一到可读家族。顺序敏感：越具体的写法放越前，
# 否则「Unity 团结引擎」会先被 "Unity" 吃掉。
ENGINE_PATTERNS: list[tuple[str, str]] = [
    ("团结引擎", "Unity 团结引擎"),
    ("基岩", "基岩引擎"),
    ("Roblox", "Roblox Studio"),
    ("VSO", "Playrix VSO"),
    ("Valhalla", "自研 Valhalla"),
    ("乐道", "自研 Valhalla"),
    ("PopCap", "自研 PopCap"),
    ("Titan", "自研 Titan"),
    ("Rainbow", "自研 彩虹"),
    ("彩虹引擎", "自研 彩虹"),
    ("光子", "自研（光子）"),
    ("Frostbite", "Frostbite"),
    ("Unreal Engine 5", "Unreal 5"),
    ("Unreal 5", "Unreal 5"),
    ("UE5", "Unreal 5"),
    ("UE 5", "Unreal 5"),
    ("虚幻引擎5", "Unreal 5"),
    ("Unreal Engine 4", "Unreal 4"),
    ("Unreal 4", "Unreal 4"),
    ("UE4", "Unreal 4"),
    ("UE 4", "Unreal 4"),
    ("虚幻引擎4", "Unreal 4"),
    ("Unreal Engine", "Unreal"),
    ("虚幻引擎", "Unreal"),
    ("Unreal", "Unreal"),
    ("LayaAir", "LayaAir"),
    ("Laya", "LayaAir"),
    ("Cocos Creator", "Cocos Creator"),
    ("Cocos3D", "Cocos Creator"),
    ("Cocos 2D", "Cocos2d-x"),
    ("Cocos2d-x", "Cocos2d-x"),
    ("Cocos", "Cocos Creator"),
    ("Unity", "Unity"),
    ("原生", "原生"),
    ("Native", "原生"),
    ("HTML5", "Web/H5"),
    ("Html5", "Web/H5"),
    ("H5", "Web/H5"),
    ("Web", "Web/H5"),
    ("自研", "自研"),
    ("定制", "自研"),
]

# 出现这些词说明引擎判断只是推测，不是官方/实锤
_ENGINE_INFER_RE = re.compile(r"推断|推测|inferred|未确认|未见|可能|存疑|猜测|冲突|矛盾", re.I)

# 多引擎候选时的**固定展示顺序**。不按原始串里的先后（否则同一组
# 「Unity + Cocos」会随文案写成「Unity / Cocos」或「Cocos / Unity」两种标签，
# 统计时被拆成两组），统一按本表排序，保证同组合恒定。
FAMILY_ORDER = [
    "Unity", "Unity 团结引擎", "Cocos Creator", "Cocos2d-x", "LayaAir",
    "Unreal", "Unreal 4", "Unreal 5", "Unreal 4/5",
    "Roblox Studio", "基岩引擎", "Frostbite",
    "Web/H5", "原生", "自研",
]
_FAMILY_RANK = {f: i for i, f in enumerate(FAMILY_ORDER)}


def canon_engine(raw: str, evidence: str = "") -> dict:
    """引擎原始串 -> {primary, families, inferred, raw}。

    `evidence` 优先于文本嗅探：记录里已标 `engine_evidence=confirmed` 就认它，
    文本里的「推断」字样只作兜底（历史产物常忘了填）。
    """
    s = norm_name(raw)
    if not s:
        return {"primary": "", "families": [], "inferred": False, "raw": ""}
    low = s.lower()
    fams: list[str] = []
    for pat, canon in ENGINE_PATTERNS:
        if pat.lower() in low and canon not in fams:
            fams.append(canon)
    # 归并冗余：
    # 1) Cocos Creator 是 Cocos2d-x 的继任者，同时命中时只留前者
    if "Cocos Creator" in fams and "Cocos2d-x" in fams:
        fams.remove("Cocos2d-x")
    # 2) Unity 团结引擎是 Unity 的国内定制分支，命中它就不必再挂裸 Unity
    if "Unity 团结引擎" in fams and "Unity" in fams:
        fams.remove("Unity")
    # 3) 已经命中具名引擎时，光秃秃的「自研」是废话（Roblox Studio / 自研 → Roblox Studio）
    if "自研" in fams and [f for f in fams if f != "自研"]:
        fams.remove("自研")
    # 4) Unreal 的版本写法（Unreal 5 / Unreal 4 / Unreal）合并成一个标签
    unreal = [f for f in fams if f.startswith("Unreal")]
    if len(unreal) > 1:
        merged = "Unreal 4/5" if {"Unreal 4", "Unreal 5"} <= set(unreal) else unreal[0]
        for f in unreal:
            fams.remove(f)
        fams.append(merged)
    # 5) 固定顺序，保证同组合标签恒定
    fams.sort(key=lambda f: _FAMILY_RANK.get(f, 999))
    inferred = (str(evidence or "").strip().lower() != "confirmed") or bool(_ENGINE_INFER_RE.search(s))
    primary = " / ".join(fams[:2]) if fams else s[:24]
    return {"primary": primary, "families": fams, "inferred": inferred, "raw": raw}


# 复刻建议结论
CLONE_VERDICTS = ["换肤", "变种创意", "微创新", "原样复刻", "不建议"]

# 证据强度
EVIDENCE_LEVELS = ["confirmed", "inferred"]


def cost_level(score: float) -> str:
    for hi, label in COST_LEVELS:
        if score <= hi:
            return label
    return "极高"


def norm_name(name: str) -> str:
    """游戏名归一化，用于**跨来源匹配**（榜单/底库/已有档案/商店/调研产物）。

    榜单里混着各种不可见字符，最典型的是 `Block\\xa0Blast！` 里的不换行空格
    （U+00A0）——肉眼与普通空格无异，但字符串比对会直接失配，导致档案对不上号。
    这里统一：NFKC（全角→半角、兼容字符）→ 各种 Unicode 空白折叠成单个普通空格。
    """
    s = unicodedata.normalize("NFKC", str(name or ""))
    s = s.replace("\u00a0", " ").replace("\u3000", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", s).strip()


def slug(name: str, maxlen: int = 40) -> str:
    """游戏名 -> 安全文件名。保留各语种文字与数字，其余折叠成下划线。

    **用 \\w 而不是手写 CJK 区间**：手写 `[\\u4e00-\\u9fff]` 只覆盖汉字，
    会把日文假名整个丢掉 —— 「ワクワク電車ライフ」会被压成「電車」，
    「ちいかわぽけっと」直接变空。`\\w` 在 unicode 模式下覆盖汉字、假名、
    谚文、字母与数字，正是我们要的。

    末尾附 6 位内容哈希，避免「删掉标点后重名」和超长名截断后撞名。
    """
    base = re.sub(r"[^\w]+", "_", str(name), flags=re.UNICODE).strip("_")
    if len(base) > maxlen:
        base = base[:maxlen]
    h = hashlib.md5(str(name).encode("utf-8")).hexdigest()[:6]
    return f"{base}_{h}" if base else f"g_{h}"


def blank_record(name: str) -> dict:
    """调研产物骨架。子智能体按这个填，不要加没定义的顶层键。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "slug": slug(name),
        "updated_at": "",
        "collected": False,
        "tech": {
            "engine": "",
            "engine_evidence": "inferred",
            "dimension": "",
            "art_style": "",
            "dims": {k: 0 for k in DIM_KEYS},
            "cost_score": 0,
            "cost_level": "",
            "asset_notes": "",
            "notes": "",
        },
        "play": {
            "core_loop": "",
            "highlights": [],
            "innovations": [],
            "target_user": "",
            "monetization": "",
            "lifecycle": "",
        },
        "clone": {
            "verdict": "",
            "rationale": "",
            "suggestions": [],
            "risks": [],
            "effort": "",
        },
        "shots": [],
        "evidence": [],
        "sources": [],
    }


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def _nonempty(v) -> bool:
    return bool(str(v or "").strip())


def validate(rec: dict, strict: bool = False) -> tuple[bool, list[str]]:
    """返回 (是否合格, 问题列表)。

    合格线（硬性）：有 name/slug；tech 的 engine+dimension 至少填一个、
    8 个维度分数全部在 1..5；play.core_loop 非空、highlights 至少 1 条；
    clone.verdict 合法。其余缺失只记 warning。
    """
    errs: list[str] = []
    warns: list[str] = []

    if not isinstance(rec, dict):
        return False, ["记录不是对象"]
    if not _nonempty(rec.get("name")):
        errs.append("缺 name")

    tech = rec.get("tech") or {}
    if not _nonempty(tech.get("engine")) and not _nonempty(tech.get("dimension")):
        errs.append("tech.engine 与 tech.dimension 都为空")
    if _nonempty(tech.get("dimension")) and tech.get("dimension") not in DIMENSIONS:
        warns.append(f"tech.dimension 取值非常规：{tech.get('dimension')}")

    dims = tech.get("dims") or {}
    for k in DIM_KEYS:
        v = dims.get(k)
        if not isinstance(v, (int, float)) or not (1 <= v <= 5):
            errs.append(f"tech.dims.{k} 必须是 1..5 的数字，当前={v!r}")

    play = rec.get("play") or {}
    if not _nonempty(play.get("core_loop")):
        errs.append("play.core_loop 为空")
    if not (play.get("highlights") or []):
        errs.append("play.highlights 至少 1 条")
    if not (play.get("innovations") or []):
        warns.append("play.innovations 为空（可接受，但最好写明「无」）")

    clone = rec.get("clone") or {}
    verdict = str(clone.get("verdict") or "").strip()
    if verdict not in CLONE_VERDICTS:
        errs.append(f"clone.verdict 必须是 {CLONE_VERDICTS} 之一，当前={verdict!r}")
    if not _nonempty(clone.get("rationale")):
        warns.append("clone.rationale 为空")
    if not (clone.get("suggestions") or []):
        warns.append("clone.suggestions 为空")

    if not (rec.get("sources") or []):
        warns.append("sources 为空（未留出处）")

    return (not errs), errs + [f"[warn] {w}" for w in warns]


def fill_derived(rec: dict) -> dict:
    """补齐可计算的字段：cost_score / cost_level / slug。"""
    tech = rec.setdefault("tech", {})
    dims = tech.get("dims") or {}
    vals = [dims.get(k) for k in DIM_KEYS]
    if all(isinstance(v, (int, float)) and v > 0 for v in vals):
        avg = round(sum(vals) / len(vals), 2)
        tech["cost_score"] = avg
        tech["cost_level"] = cost_level(avg)
    rec["slug"] = rec.get("slug") or slug(rec.get("name", ""))
    return rec
