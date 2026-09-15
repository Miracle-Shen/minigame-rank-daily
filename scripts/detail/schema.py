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

# --------------------------------------------------------------------------
# 结合业务的建议（biz）
# --------------------------------------------------------------------------
# 与 clone 的区别：clone 回答「要不要抄、抄多少钱」；biz 回答「不管抄不抄，
# 这款产品的机制/生态/发行背景能和我们手里的业务发生什么关系」。
#
# **内部落地是主体，按「424」法则写**（三层缺一不可，条数就是权重）：
#     4 条 why   为什么 —— 双视角：用户视角 2 条 + 业务视角 2 条
#     2 条 how   怎么做 —— 落到哪个模块、谁动、怎么验证（最精炼的一层）
#     4 条 gain  收益   —— 搬了之后拿到什么（效果/资产沉淀/避坑/复用范围）
# 对外机会、场景延展是**附属信息**，各封顶 2 条一句话，不做展开论证。
BIZ_INTERNAL_FITS = ["可复用", "需改造", "不建议搬"]
BIZ_OPPORTUNITY_FITS = ["推荐接触", "可观望", "不建议接触"]

# 424：(字段, 中文名, 目标条数)
BIZ_424 = [("why", "为什么", 4), ("how", "怎么做", 2), ("gain", "收益", 4)]
BIZ_424_MAX_SLACK = 1          # 超出目标 1 条内容忍，再多提示精简

# 「为什么」的 4 条不是四面八方的理由，而是**两个视角各 2 条**：
#   用户 —— 玩家为什么吃这套、我们的用户会不会接受把它搬过来
#   业务 —— 为什么是我们（贴合点/杠杆）、为什么是现在（时机/不做的代价）
# 只看业务侧会写出一份「对我们方便」的建议，却不回答用户买不买单；
# 只看用户侧会写出一份「玩家喜欢」的评审，却回答不了该不该投入。
BIZ_WHY_LENSES = ["用户", "业务"]
BIZ_WHY_PER_LENS = 2
BIZ_OPP_MAX_POINTS = 2         # 弱化：对外机会最多 2 条
BIZ_EXT_MIN_SCENES = 1         # 弱化：场景延展 1–2 个
BIZ_EXT_MAX_SCENES = 2
# 旧的「internal.points」三段式写法仍可读（历史档案），但会提示升级到 424
BIZ_LEGACY_POINTS = "points"

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


def why_item(x) -> tuple[str, str]:
    """把 `internal.why` 的一条归一化成 `(视角, 正文)`。

    新结构是 `{"lens": "用户"|"业务", "text": "..."}`；只写字符串是历史/偷懒写法，
    此时视角返回 `""`，渲染层会省掉视角标签 —— **不静默归到某一侧**，
    免得读者把一条业务理由误读成用户理由。
    """
    if isinstance(x, dict):
        return str(x.get("lens") or "").strip(), str(x.get("text") or "").strip()
    return "", str(x or "").strip()


def biz_why(internal) -> list[tuple[str, str]]:
    """取 `internal.why` 的归一化条目，丢掉空正文。校验与渲染共用同一套归一化。"""
    raw = (internal or {}).get("why") or [] if isinstance(internal, dict) else []
    return [p for p in (why_item(x) for x in raw) if p[1]]


# 子智能体写内容时半角单引号与中文引号混用（同一节里既有「」又有 ''），读起来很花。
# 判据是**引号不贴着 ASCII 词字符**：这样 `'糖果'`、`'A'` 会被转换，
# 而英文所有格（`Roblox's`、`King's`）因为引号前是字母，不会被误伤。
_ASCII_WORD = r"[0-9A-Za-z_]"
_ASCII_QUOTE_PAIR = re.compile(rf"(?<!{_ASCII_WORD})'([^']{{1,60}}?)'(?!{_ASCII_WORD})")


def polish_text(s) -> str:
    """统一中文语境里的排版细节。幂等，可反复跑。

    1. 成对半角单引号 -> `「」`（子智能体有的写 `'糖果'`，有的写 `「糖果」`）；
    2. 数字与汉字之间补空格（`72天在榜` -> `72 天在榜`、`第2` -> `第 2`）——
       中西文之间留空隙是中文排版惯例，也让 184 份档案读起来是一个口径。
       `2.2GB`、`5v5`、`1:1` 这类纯西文串不受影响（只处理数字与**汉字**相邻）。
    """
    out = _ASCII_QUOTE_PAIR.sub(lambda m: "「" + m.group(1) + "」", str(s or ""))
    out = re.sub(r"(\d)(?=[\u4e00-\u9fff])", r"\1 ", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff])(?=\d)", " ", out)
    return out


def polish_biz_text(biz: dict) -> dict:
    """就地清洗 biz 里所有面向读者的短文本（why / how / gain / points / scene / summary）。

    合并补丁时调用，让 184 份档案的标点口径一致；`--polish-biz` 可对历史档案补跑。
    """
    if not isinstance(biz, dict):
        return biz
    internal = biz.get("internal")
    if isinstance(internal, dict):
        why = []
        for x in internal.get("why") or []:
            if isinstance(x, dict):
                x = {**x, "text": polish_text(x.get("text"))}
            elif isinstance(x, str):
                x = polish_text(x)
            why.append(x)
        internal["why"] = why
        for k in ("how", "gain"):
            if isinstance(internal.get(k), list):
                internal[k] = [polish_text(x) for x in internal[k]]
    opp = biz.get("opportunity")
    if isinstance(opp, dict) and isinstance(opp.get("points"), list):
        opp["points"] = [polish_text(x) for x in opp["points"]]
    ext = biz.get("extension")
    if isinstance(ext, dict) and isinstance(ext.get("scenes"), list):
        for sc in ext["scenes"]:
            if isinstance(sc, dict):
                sc["scene"] = polish_text(sc.get("scene"))
                sc["how"] = polish_text(sc.get("how"))
    if isinstance(biz.get("summary"), str):
        biz["summary"] = polish_text(biz["summary"])
    return biz


def blank_biz() -> dict:
    """「结合业务的建议」的空骨架（合并器补位用）。"""
    return {
        # 主体：424（why 的每条是 {lens, text}，用户/业务各 2 条）
        "internal": {"fit": "", "why": [], "how": [], "gain": []},
        # 附属：封顶 2 条
        "opportunity": {"fit": "", "points": []},
        "extension": {"scenes": []},
        "summary": "",
    }


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
        # 结合业务的建议：与「复刻建议」并行的第二个决策视角。
        # 三层各自给枚举结论 + 可执行要点，不写泛泛而谈的「值得关注」。
        "biz": blank_biz(),
        "shots": [],
        "evidence": [],
        "sources": [],
    }


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def _nonempty(v) -> bool:
    return bool(str(v or "").strip())


def validate_biz(rec: dict, require: bool = False) -> tuple[list[str], list[str]]:
    """校验「结合业务的建议」分区，返回 (errs, warns)。

    结构口径：内部落地是主体，按 **424** 校验（why 4 / how 2 / gain 4）；
    对外机会与场景延展已弱化，超过 2 条即提示压缩。条数问题一律记 warning，
    只有枚举非法才算 error —— 内容多少是质量问题，不该拦住入库。

    `require=False`（默认）时缺失只记 warning —— 历史档案是「复刻建议」时代的
    产物，没有这一节，不该被判定为不合格；`require=True` 用于回填完成后的终检。
    """
    errs: list[str] = []
    warns: list[str] = []
    biz = rec.get("biz")
    if not isinstance(biz, dict) or not any(
        isinstance(biz.get(k), dict) for k in ("internal", "opportunity", "extension")
    ):
        (errs if require else warns).append("biz 分区缺失（结合业务的建议）")
        return errs, warns

    # ---- ① 内部落地（主体，424）----
    internal = biz.get("internal")
    if not isinstance(internal, dict):
        (errs if require else warns).append("biz.internal 缺失")
    else:
        fit = str(internal.get("fit") or "").strip()
        if fit and fit not in BIZ_INTERNAL_FITS:
            errs.append(f"biz.internal.fit 必须是 {BIZ_INTERNAL_FITS} 之一，当前={fit!r}")
        elif not fit:
            (errs if require else warns).append("biz.internal.fit 为空")

        blocks = {k: [x for x in (internal.get(k) or []) if _nonempty(x)]
                  for k, _, _ in BIZ_424}
        # why 换成归一化条目计数：{lens,text} 与旧字符串都算，但空正文不算
        blocks["why"] = biz_why(internal)
        legacy = [x for x in (internal.get(BIZ_LEGACY_POINTS) or []) if _nonempty(x)]
        if not any(blocks.values()) and legacy:
            # 历史档案：三段式 points。能读能渲染，但不算 424
            warns.append(f"biz.internal 仍是旧结构（points {len(legacy)} 条），"
                         "应升级为 424 的 why/how/gain")
        else:
            for key, label, target in BIZ_424:
                items = blocks[key]
                if not items:
                    (errs if require else warns).append(
                        f"biz.internal.{key}（{label}）为空（424 要求 {target} 条）")
                elif len(items) < target:
                    warns.append(f"biz.internal.{key}（{label}）{len(items)} 条，"
                                 f"424 要求 {target} 条")
                elif len(items) > target + BIZ_424_MAX_SLACK:
                    warns.append(f"biz.internal.{key}（{label}）{len(items)} 条，"
                                 f"424 建议 {target} 条，请精简")
            # why 的 4 条必须是「用户 2 + 业务 2」：视角非法才算错，配比失衡只提示
            lens_list = [l for l, _ in blocks["why"]]
            bad_lens = sorted({l for l in lens_list if l and l not in BIZ_WHY_LENSES})
            if bad_lens:
                errs.append(f"biz.internal.why 的 lens 必须是 {BIZ_WHY_LENSES} 之一，"
                            f"当前={bad_lens}")
            plain = sum(1 for l in lens_list if not l)
            if plain:
                # 一条就够：没标视角时再报「用户 0 条 / 业务 0 条」只是噪声
                warns.append(f"biz.internal.why 有 {plain} 条未标视角（纯字符串），"
                             "应写成 {lens, text}，lens 取「用户」或「业务」")
            else:
                for lens in BIZ_WHY_LENSES:
                    n = sum(1 for l in lens_list if l == lens)
                    if len(blocks["why"]) and n != BIZ_WHY_PER_LENS:
                        warns.append(f"biz.internal.why「{lens}」视角 {n} 条，"
                                     f"424 要求用户/业务各 {BIZ_WHY_PER_LENS} 条")

    # ---- ② 对外机会（弱化：≤2 条一句式）----
    opp = biz.get("opportunity")
    if not isinstance(opp, dict):
        (errs if require else warns).append("biz.opportunity 缺失")
    else:
        fit = str(opp.get("fit") or "").strip()
        if fit and fit not in BIZ_OPPORTUNITY_FITS:
            errs.append(f"biz.opportunity.fit 必须是 {BIZ_OPPORTUNITY_FITS} 之一，当前={fit!r}")
        elif not fit:
            (errs if require else warns).append("biz.opportunity.fit 为空")
        pts = [p for p in (opp.get("points") or []) if _nonempty(p)]
        if not pts:
            (errs if require else warns).append("biz.opportunity.points 为空")
        elif len(pts) > BIZ_OPP_MAX_POINTS:
            warns.append(f"biz.opportunity.points {len(pts)} 条，对外机会已弱化，"
                         f"请压到 ≤{BIZ_OPP_MAX_POINTS} 条一句式")

    # ---- ③ 场景延展（弱化：1–2 个）----
    ext = biz.get("extension")
    if not isinstance(ext, dict):
        (errs if require else warns).append("biz.extension 缺失")
    else:
        scenes = ext.get("scenes") or []
        bad = [s for s in scenes
               if not isinstance(s, dict) or not _nonempty(s.get("scene"))
               or not _nonempty(s.get("how"))]
        if bad:
            warns.append(f"biz.extension.scenes 有 {len(bad)} 项缺 scene/how")
        good = [s for s in scenes if isinstance(s, dict) and _nonempty(s.get("scene"))]
        if not good:
            (errs if require else warns).append("biz.extension.scenes 为空")
        elif len(good) > BIZ_EXT_MAX_SCENES:
            warns.append(f"biz.extension.scenes {len(good)} 个，场景延展已弱化，"
                         f"请压到 ≤{BIZ_EXT_MAX_SCENES} 个")
    return errs, warns


def validate(rec: dict, strict: bool = False,
             require_biz: bool = False) -> tuple[bool, list[str]]:
    """返回 (是否合格, 问题列表)。

    合格线（硬性）：有 name/slug；tech 的 engine+dimension 至少填一个、
    8 个维度分数全部在 1..5；play.core_loop 非空、highlights 至少 1 条；
    clone.verdict 合法。其余缺失只记 warning。

    `require_biz=True` 时把「结合业务的建议」分区也纳入硬性要求
    （回填完成后的终检用；回填期间保持 False，历史档案不至于全判不合格）。
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

    biz_errs, biz_warns = validate_biz(rec, require=require_biz)
    errs.extend(biz_errs)
    warns.extend(biz_warns)

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
