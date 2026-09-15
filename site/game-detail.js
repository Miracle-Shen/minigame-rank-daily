/* 游戏详情页渲染器
 * ---------------------------------------------------------------------------
 * 数据来源：data/detail/index.json（轻量索引）+ data/detail/<slug>.json（单款详情）
 * 由 scripts/detail/ 下的构建脚本生成，前端只读。
 *
 * 对外接口：
 *   GameDetail.render(name, box)   把详情渲染进 box；无档案时自动隐藏 box
 *   GameDetail.get(name)           取单款详情对象（带缓存）
 *   GameDetail.shots(name)         取该款的截图数组，供别处复用
 *   GameDetail.badge(name)         取索引里的徽章字段（成本档/复刻结论）
 */
(function () {
  "use strict";

  const INDEX_URL = "data/detail/index.json";
  const REC_DIR = "data/detail/";
  const ASSET_VERSION = (window.APP_CONFIG && window.APP_CONFIG.ASSET_VERSION) || "1";
  const assetUrl = (path) => path + "?v=" + encodeURIComponent(ASSET_VERSION);

  let indexPromise = null;
  let index = null;
  const recCache = {};

  const COST_DIMS = [
    ["engine", "引擎与框架"],
    ["art", "美术素材量"],
    ["anim", "动画与特效"],
    ["physics", "物理与手感"],
    ["logic", "玩法逻辑与关卡"],
    ["data", "数值与养成体系"],
    ["server", "服务端与联网"],
    ["audio", "音频"],
  ];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[m]));
  }

  /* 与 scripts/detail/schema.py 的 norm_name 保持同一套规则。
   * 榜单名里混着不换行空格（Block\u00a0Blast！）与全角冒号（羊了个羊：星球），
   * Supabase 里的名字与档案里的名字可能只差这些字符 —— 不归一化就会查不到档案。 */
  function normName(s) {
    return String(s == null ? "" : s)
      .normalize("NFKC")
      .replace(/[\u00a0\u3000]/g, " ")
      .replace(/\u200b/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  /* 引擎归一化：与 scripts/detail/schema.py 的 canon_engine 保持同一套规则。
   * 调研产物里引擎写法有 57 种（「Unity（推断）」「Cocos Creator (推断)」
   * 「Unity / Cocos（inferred，休闲排序游戏常见选择）」…），直接当标签没法看。
   * 索引里已带 engine_primary，这里的实现是**兜底**：索引是旧版或缺字段时仍能渲染。 */
  const ENGINE_PATTERNS = [
    ["团结引擎", "Unity 团结引擎"], ["基岩", "基岩引擎"], ["Roblox", "Roblox Studio"],
    ["VSO", "Playrix VSO"], ["Valhalla", "自研 Valhalla"], ["乐道", "自研 Valhalla"],
    ["PopCap", "自研 PopCap"], ["Titan", "自研 Titan"], ["Rainbow", "自研 彩虹"],
    ["彩虹引擎", "自研 彩虹"], ["光子", "自研（光子）"], ["Frostbite", "Frostbite"],
    ["Unreal Engine 5", "Unreal 5"], ["Unreal 5", "Unreal 5"], ["UE5", "Unreal 5"],
    ["UE 5", "Unreal 5"], ["虚幻引擎5", "Unreal 5"],
    ["Unreal Engine 4", "Unreal 4"], ["Unreal 4", "Unreal 4"], ["UE4", "Unreal 4"],
    ["UE 4", "Unreal 4"], ["虚幻引擎4", "Unreal 4"],
    ["Unreal Engine", "Unreal"], ["虚幻引擎", "Unreal"], ["Unreal", "Unreal"],
    ["LayaAir", "LayaAir"], ["Laya", "LayaAir"],
    ["Cocos Creator", "Cocos Creator"], ["Cocos3D", "Cocos Creator"],
    ["Cocos 2D", "Cocos2d-x"], ["Cocos2d-x", "Cocos2d-x"], ["Cocos", "Cocos Creator"],
    ["Unity", "Unity"], ["原生", "原生"], ["Native", "原生"],
    ["HTML5", "Web/H5"], ["Html5", "Web/H5"], ["H5", "Web/H5"], ["Web", "Web/H5"],
    ["自研", "自研"], ["定制", "自研"],
  ];
  const FAMILY_ORDER = ["Unity", "Unity 团结引擎", "Cocos Creator", "Cocos2d-x", "LayaAir",
    "Unreal", "Unreal 4", "Unreal 5", "Unreal 4/5", "Roblox Studio", "基岩引擎", "Frostbite",
    "Web/H5", "原生", "自研"];
  const INFER_RE = /推断|推测|inferred|未确认|未见|可能|存疑|猜测|冲突|矛盾/i;

  function canonEngine(raw, evidence) {
    const s = normName(raw);
    if (!s) return { primary: "", inferred: false, raw: "" };
    const low = s.toLowerCase();
    let fams = [];
    ENGINE_PATTERNS.forEach(([pat, canon]) => {
      if (low.indexOf(pat.toLowerCase()) >= 0 && fams.indexOf(canon) < 0) fams.push(canon);
    });
    if (fams.indexOf("Cocos Creator") >= 0 && fams.indexOf("Cocos2d-x") >= 0) fams = fams.filter((f) => f !== "Cocos2d-x");
    if (fams.indexOf("Unity 团结引擎") >= 0 && fams.indexOf("Unity") >= 0) fams = fams.filter((f) => f !== "Unity");
    if (fams.indexOf("自研") >= 0 && fams.some((f) => f !== "自研")) fams = fams.filter((f) => f !== "自研");
    const unreal = fams.filter((f) => f.indexOf("Unreal") === 0);
    if (unreal.length > 1) {
      const merged = (unreal.indexOf("Unreal 4") >= 0 && unreal.indexOf("Unreal 5") >= 0) ? "Unreal 4/5" : unreal[0];
      fams = fams.filter((f) => f.indexOf("Unreal") !== 0);
      fams.push(merged);
    }
    fams.sort((a, b) => {
      const ia = FAMILY_ORDER.indexOf(a), ib = FAMILY_ORDER.indexOf(b);
      return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
    });
    const inferred = String(evidence || "").toLowerCase() !== "confirmed" || INFER_RE.test(s);
    return { primary: fams.length ? fams.slice(0, 2).join(" / ") : s.slice(0, 24), inferred, raw };
  }

  function loadIndex(force) {
    if (index && !force) return Promise.resolve(index);
    if (indexPromise && !force) return indexPromise;
    indexPromise = fetch(assetUrl(INDEX_URL), { cache: "force-cache" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { index = d || { games: {} }; return index; })
      .catch(() => { index = { games: {} }; return index; });
    return indexPromise;
  }

  function entryFor(name) {
    return loadIndex().then((ix) => (ix.games || {})[normName(name)] || null);
  }

  function get(name) {
    if (recCache[name]) return Promise.resolve(recCache[name]);
    return entryFor(name).then((e) => {
      if (!e || !e.slug || !e.collected) return null;
      return fetch(assetUrl(REC_DIR + e.slug + ".json"), { cache: "force-cache" })
        .then((r) => (r.ok ? r.json() : null))
        .then((rec) => { if (rec) recCache[name] = rec; return rec; })
        .catch(() => null);
    });
  }

  function shots(name) {
    return get(name).then((rec) => (rec && rec.shots) || []);
  }

  function badge(name) {
    return entryFor(name).then((e) => {
      if (!e || !e.collected) return null;
      const eng = canonEngine(e.engine, e.engine_inferred === false ? "confirmed" : "");
      return {
        costLevel: e.cost_level || "",
        costScore: e.cost_score || 0,
        verdict: e.verdict || "",
        engine: e.engine || "",
        enginePrimary: e.engine_primary || eng.primary,
        engineInferred: typeof e.engine_inferred === "boolean" ? e.engine_inferred : eng.inferred,
        dimension: e.dimension || "",
      };
    });
  }

  // ---------- 成本档位配色：越低越绿 ----------
  const LV_CLASS = { "极低": "lv0", "低": "lv1", "中": "lv2", "高": "lv3", "极高": "lv4" };
  function lvClass(level) { return LV_CLASS[level] || "lv2"; }
  function barClass(score) {
    if (score <= 2) return "gd-bar-low";
    if (score <= 3) return "gd-bar-mid";
    return "gd-bar-high";
  }
  function mb(bytes) {
    const n = Number(bytes || 0);
    if (!n) return "";
    if (n >= 1073741824) return (n / 1073741824).toFixed(1) + " GB";
    return Math.round(n / 1048576) + " MB";
  }

  // ---------- 灯箱 ----------
  let lb = null;
  function lightbox(url) {
    if (!lb) {
      lb = document.createElement("div");
      lb.className = "gd-lb";
      lb.innerHTML = '<span class="gd-lb-x">×</span><img alt="" />';
      lb.addEventListener("click", () => lb.classList.remove("on"));
      document.body.appendChild(lb);
    }
    lb.querySelector("img").src = url;
    lb.classList.add("on");
  }

  // ---------- 各分区 ----------
  function secHead(n, title, extra) {
    return '<h3 class="gd-sec-h"><span class="gd-sec-n">' + n + "</span>" +
      esc(title) + (extra ? '<span class="gd-sec-x">' + esc(extra) + "</span>" : "") +
      "</h3>";
  }

  function summary(rec) {
    const tech = rec.tech || {}, clone = rec.clone || {};
    const m = rec.media || {};
    const chips = [];
    const eng = canonEngine(tech.engine, tech.engine_evidence);
    if (eng.primary) {
      const inf = eng.inferred ? '<i class="gd-inf" title="引擎判断为推断，未见官方确认">推断</i>' : "";
      chips.push('<span class="gd-chip" title="' + esc(eng.raw) + '"><span class="gd-k">引擎</span><b>' +
        esc(eng.primary) + "</b>" + inf + "</span>");
    }
    if (tech.dimension) chips.push('<span class="gd-chip"><span class="gd-k">画面</span><b>' + esc(tech.dimension) + "</b></span>");
    if (tech.cost_level) chips.push('<span class="gd-chip"><span class="gd-k">复刻成本</span><b>' + esc(tech.cost_level) + "</b><span class=\"gd-k\">" + esc(tech.cost_score || "") + "/5</span></span>");
    if (clone.verdict) chips.push('<span class="gd-chip"><span class="gd-k">建议</span><b>' + esc(clone.verdict) + "</b></span>");
    // effort 的取值本身已含单位（如「25–40人日（1前端+1美术+0.5策划）」），
    // 标签再写「人日」会变成「人日25–40人日」，故用「工作量」。
    // 摘要条只取**前面的人日区间**，完整写法（含人力配置）留在第 4 分区，
    // 否则同一个值会在页面上出现两遍、读起来像两个不同的结论。
    if (clone.effort) {
      const full = String(clone.effort);
      const hit = full.match(/^\s*([\d０-９]+\s*[–—~～\-]\s*[\d０-９]+|\d+\+?)\s*人日/);
      const brief = hit ? hit[0].trim() : "";
      if (brief && brief !== full) {
        chips.push('<span class="gd-chip" title="' + esc(full) + '"><span class="gd-k">工作量</span><b>' +
          esc(brief) + "</b></span>");
      }
    }
    const sz = mb(m.file_size_bytes);
    if (sz) chips.push('<span class="gd-chip"><span class="gd-k">包体</span><b>' + esc(sz) + "</b></span>");
    if (!chips.length) return "";
    return '<div class="gd-summary">' + chips.join("") + "</div>";
  }

  function techSection(rec) {
    const t = rec.tech || {};
    const m = rec.media || {};
    const facts = [];
    if (t.engine) {
      const eng = canonEngine(t.engine, t.engine_evidence);
      const ev = t.engine_evidence === "confirmed"
        ? '<span class="gd-flag ok">已证实</span>'
        : '<span class="gd-flag warn">推断</span>';
      // 归一后的家族名 + 推断标记；原始调研串若与之不同，作为小字保留，便于追溯
      const rawNote = eng.raw && normName(eng.raw) !== eng.primary
        ? '<small class="gd-eng-raw">原始判断：' + esc(eng.raw) + "</small>" : "";
      facts.push(["引擎", esc(eng.primary) + ev + rawNote]);
    }
    if (t.dimension) facts.push(["画面维度", esc(t.dimension)]);
    if (t.art_style) facts.push(["美术风格", esc(t.art_style)]);
    const sz = mb(m.file_size_bytes);
    if (sz) facts.push(["官方包体", esc(sz) + '<small> 量级参考</small>']);
    const dev = m.seller || (rec.identity || {}).publisher;
    if (dev) facts.push(["开发商", esc(dev)]);
    if (m.version) facts.push(["版本", esc(m.version) + (m.release_date ? '<small> ' + esc(m.release_date) + "</small>" : "")]);
    if (m.rating) facts.push(["商店评分", esc(Number(m.rating).toFixed(2)) + '<small> ' + esc(m.rating_count || 0) + " 评</small>"]);

    let h = secHead("1", "技术实现细节", "分数越低＝复刻越便宜");
    if (facts.length) {
      h += '<div class="gd-facts">' + facts.map(([k, v]) =>
        '<div class="gd-fact"><div class="gd-fk">' + esc(k) +
        '</div><div class="gd-fv">' + v + "</div></div>").join("") + "</div>";
    }
    const dims = t.dims || {}, notes = t.dim_notes || {};
    const bars = COST_DIMS.filter(([k]) => dims[k]).map(([k, label]) => {
      const v = dims[k];
      return '<div class="gd-dim"><span class="gd-dim-l">' + esc(label) +
        '</span><span class="gd-dim-t"><i class="gd-dim-b ' + barClass(v) +
        '" style="width:' + (v / 5 * 100) + '%"></i></span><span class="gd-dim-v">' +
        v + "/5</span></div>" +
        (notes[k] ? '<div class="gd-dim-note">' + esc(notes[k]) + "</div>" : "");
    }).join("");
    if (bars) h += '<div class="gd-dims">' + bars + "</div>";

    if (t.cost_score) {
      h += '<div class="gd-cost-total"><span class="gd-ct-n">' + esc(t.cost_score) +
        '</span><span class="gd-lv ' + lvClass(t.cost_level) + '">复刻成本 ' +
        esc(t.cost_level || "") + '</span><span class="gd-ct-l">八维均值（1 最易 · 5 最难）</span></div>';
    }
    if (t.asset_notes) h += '<div class="gd-note"><b>素材生成难易：</b>' + esc(t.asset_notes) + "</div>";
    if (t.notes) h += '<div class="gd-note">' + esc(t.notes) + "</div>";
    return '<div class="gd-sec">' + h + "</div>";
  }

  function playSection(rec) {
    const p = rec.play || {};
    let h = secHead("2", "核心玩法 · 爽点 · 创新点");
    if (p.core_loop) h += '<div class="gd-loop">' + esc(p.core_loop) + "</div>";
    const ul = (arr, cls) => (arr && arr.length)
      ? '<ul class="gd-ul">' + arr.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul>"
      : '<div class="gd-hint">—</div>';
    h += '<div class="gd-cols">' +
      '<div><div class="gd-list-h hl">爽点</div>' + ul(p.highlights) + "</div>" +
      '<div><div class="gd-list-h iv">创新点</div>' + ul(p.innovations) + "</div>" +
      "</div>";
    const meta = [];
    if (p.target_user) meta.push(["目标用户", p.target_user]);
    if (p.monetization) meta.push(["变现方式", p.monetization]);
    if (p.lifecycle) meta.push(["生命周期", p.lifecycle]);
    if (meta.length) {
      h += '<div class="gd-meta-row">' + meta.map(([k, v]) =>
        '<div class="gd-fact"><div class="gd-fk">' + esc(k) +
        '</div><div class="gd-fv" style="font-weight:500;font-size:12.5px">' +
        esc(v) + "</div></div>").join("") + "</div>";
    }
    return '<div class="gd-sec">' + h + "</div>";
  }

  function shotSection(rec) {
    const shots = rec.shots || [];
    const m = rec.media || {};
    let h = secHead("3", "游戏截图", shots.length ? shots.length + " 张" : "");
    if (!shots.length) {
      return '<div class="gd-sec">' + h +
        '<div class="gd-hint">暂无官方素材。可在上方「截图」区手动上传实机图。</div></div>';
    }
    h += '<div class="gd-shots">' + shots.map((s) => {
      const isIcon = s.kind === "icon";
      const tag = s.caption || (isIcon ? "图标" : "实机截图");
      return '<div class="gd-shot' + (isIcon ? " is-icon" : "") + '" data-u="' +
        esc(s.url) + '"><img loading="lazy" decoding="async" src="' + esc(s.url) + '" alt="' +
        esc(tag) + '" referrerpolicy="no-referrer" /><span class="gd-shot-tag">' +
        esc(isIcon ? "图标" : "实机") + "</span></div>";
    }).join("") + "</div>";
    const srcName = (shots[0] || {}).source;
    if (srcName) {
      h += '<div class="gd-hint" style="margin-top:8px">素材来源：' + esc(srcName) +
        (m.url ? ' · <a href="' + esc(m.url) + '" target="_blank" rel="noopener">商店页</a>' : "") + "</div>";
    }
    if (m.match_score && m.match_score < 0.9) {
      h += '<div class="gd-verify">⚠ 商店匹配名称为「' + esc(m.match_name || "") +
        "」（相似度 " + esc(m.match_score) + "），非完全同名，截图请人工确认后再引用。</div>";
    }
    return '<div class="gd-sec">' + h + "</div>";
  }

  function cloneSection(rec) {
    const c = rec.clone || {};
    let h = secHead("4", "复刻建议");
    h += '<div class="gd-verdict"><span class="gd-vd-badge gd-vd-' + esc(c.verdict || "") +
      '">' + esc(c.verdict || "未评估") + "</span>" +
      (c.effort ? '<span class="gd-chip"><span class="gd-k">预估</span><b>' + esc(c.effort) + "</b></span>" : "") +
      (c.rationale ? '<span class="gd-vd-r">' + esc(c.rationale) + "</span>" : "") + "</div>";
    const ul = (arr) => (arr && arr.length)
      ? '<ul class="gd-ul">' + arr.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul>"
      : '<div class="gd-hint">—</div>';
    if ((c.suggestions || []).length || (c.risks || []).length) {
      h += '<div class="gd-cols">' +
        '<div><div class="gd-list-h">落地建议</div>' + ul(c.suggestions) + "</div>" +
        '<div><div class="gd-list-h hl">风险提示</div>' + ul(c.risks) + "</div>" +
        "</div>";
    }
    return '<div class="gd-sec">' + h + "</div>";
  }

  function sourceSection(rec) {
    const ev = rec.evidence || [], src = rec.sources || [];
    if (!ev.length && !src.length) return "";
    let body = "";
    if (ev.length) {
      body += '<div class="gd-ev">' + ev.map((e) => {
        const lv = e.level === "confirmed"
          ? '<span class="gd-flag ok">已证实</span>'
          : '<span class="gd-flag warn">推断</span>';
        return '<div class="gd-ev-i">' + esc(e.claim || "") + lv + "</div>";
      }).join("") + "</div>";
    }
    if (src.length) {
      body += '<div class="gd-ev" style="margin-top:10px">' + src.map((u) =>
        '<div class="gd-ev-i"><span class="gd-ev-s">来源 </span><a href="' + esc(u) +
        '" target="_blank" rel="noopener">' + esc(String(u).slice(0, 110)) + "</a></div>"
      ).join("") + "</div>";
    }
    return '<details class="gd-src-wrap"><summary>数据来源与置信度（' +
      (ev.length + src.length) + " 项）</summary>" + body + "</details>";
  }

  function html(rec) {
    return summary(rec) + techSection(rec) + playSection(rec) +
      shotSection(rec) + cloneSection(rec) + sourceSection(rec);
  }

  function render(name, box) {
    if (!box) return Promise.resolve(false);
    box.innerHTML = '<div class="gd-loading">读取档案…</div>';
    return get(name).then((rec) => {
      if (!rec) {
        box.innerHTML = "";
        box.style.display = "none";
        return false;
      }
      box.style.display = "";
      box.innerHTML = html(rec);
      box.querySelectorAll(".gd-shot").forEach((el) => {
        el.addEventListener("click", () => lightbox(el.getAttribute("data-u")));
      });
      const firstShot = (rec.shots || []).find((s) => s.kind !== "icon");
      if (firstShot) box.setAttribute("data-first-shot", firstShot.url);
      return true;
    }).catch(() => {
      box.innerHTML = '<div class="gd-loading">档案读取失败</div>';
      return false;
    });
  }

  window.GameDetail = { render, get, shots, badge, loadIndex, normName, canonEngine };
})();
