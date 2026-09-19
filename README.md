<p align="center">
  <img src="docs/assets/homepage.png" alt="新游监控系统 · 首页（微信小游戏 · 畅销榜）" width="100%">
</p>

<p align="center">
  <a href="https://miracle-shen.github.io/minigame-rank-daily/">[在线仪表盘]</a> •
  <a href="#weekly">[周报]</a> •
  <a href="#detail">[产品档案]</a> •
  <a href="#quick-start">[快速开始]</a> •
  <a href="#push">[推送通道]</a> •
  <a href="https://github.com/Miracle-Shen/minigame-rank-daily">[源码]</a>
</p>

**minigame-rank-daily** 每天北京时间 10:30 自动抓取微信小游戏 / 抖音小游戏的 6 个榜单，叠加 TapTap 预约榜、**iOS 美 / 国 / 日区**与 **Android 美区**免费榜，与累积 base 库比对算出每条榜单的「新进榜 / 回归 / 新发行商」，发布成一面纯静态仪表盘；每周一 09:00 再产出一份微信小游戏周报，自动推送到邮箱 / 企业微信群。

<table>
  <tr>
    <th>微信小游戏</th>
    <th>抖音小游戏</th>
    <th>TapTap</th>
    <th>iOS App Store</th>
    <th>Android</th>
  </tr>
  <tr>
    <td align="center">畅销榜<br>畅玩榜<br>人气榜</td>
    <td align="center">畅销榜<br>热门榜<br>新游榜</td>
    <td align="center">预约榜</td>
    <td align="center">美区免费榜<br>国区免费榜<br>日区免费榜</td>
    <td align="center">美区免费榜</td>
  </tr>
</table>

> **抓取方式**：引力引擎两个平台的数据走的是站点公开接口（`scripts/scrape_gravity_http.py`，
> 纯 HTTP，不需要浏览器和登录态）。此前用 Playwright 渲染页面，实测会因 SPA 加载失败
> （`ERR_CONNECTION_CLOSED`）导致当天微信 / 抖音数据整块缺失，因此改为接口直连作为主路径，
> 浏览器渲染保留为兜底。

- **抓取**：GitHub Actions 每日北京时间 10:30 触发（榜单 10:00 更新，留 30 分钟让后端稳定）
- **存储**：每天一份 JSON 进 `data/daily/`，diff 进 `data/diff/`，cumulative base 进 `data/base/`，趋势进 `data/history.jsonl`
- **展示**：纯静态页（`site/`）通过 GitHub Pages 发布

## 📢 更新记录

- **2026.09.16** — 产品档案新增第 5 分区「结合业务的建议」：**424 法则**（为什么 / 怎么做 / 收益），其中「为什么」按**用户视角 2 条 + 业务视角 2 条**双视角写；184 款全量回填。
- **2026.09.15** — 产品档案上线：当日各榜 TOP20 并集 **152/152** 全部建档（索引 184 条），含技术实现 / 核心玩法 / 官方截图 / 复刻建议四分区，并提供逐款详情页。
- **2026.09.14** — 抓取主路径改为引力引擎公开接口（纯 HTTP，不再依赖浏览器渲染）；接入周报分析层与**邮件 / 企业微信群机器人双通道**推送，产出首份周报 `reports/weekly-2026-09-14.*`。

## 🗂️ 仓库结构

```
.
├── scripts/
│   ├── scrape_gravity_http.py 引力引擎公开接口抓取（纯 HTTP，主抓取路径）
│   ├── scrape_rank.py    浏览器渲染抓取 / 解析（兜底路径，与桌面端共用）
│   ├── scrape_taptap.py  TapTap 预约榜（SSR JSON-LD，纯 stdlib）
│   ├── scrape_ios.py     iOS 美 / 国 / 日区游戏免费榜（Apple iTunes RSS，纯 stdlib）
│   ├── scrape_googleplay.py  Android 美区免费游戏榜（AppBrain SSR，纯 stdlib）
│   ├── ci_scrape.py      CI 抓取入口，写 daily/<日期>.json 等
│   ├── base.py           累积 base 库（历史所有游戏 / 发行商）
│   ├── ci_diff.py        基于 base 分类今日新进
│   ├── ci_sync_supabase.py  快照同步到 Supabase（仪表盘的数据源）
│   ├── detail/           产品档案管线（见「产品档案」一节）
│   └── monitor/          周报分析层（classify 品类归一化 / analyze 指标 / report 渲染 / send_mail 邮件与 Webhook / send_group 机器人直发群）
├── data/
│   ├── daily/            历史快照（每天一份）
│   ├── diff/             每天的「新进」分类
│   ├── base/             累积 base：games.json + publishers.json
│   ├── detail/           产品档案：index.json + <slug>.json（中间产物不上线）
│   ├── latest.json       最新快照（前端默认加载）
│   ├── history.jsonl     每日条数趋势
│   └── index.json        由 Pages workflow 生成的可用日期列表
├── reports/              周报产出：weekly-<日期>.md / .html / .json
├── site/                 Pages 站点
│   ├── index.html        仪表盘（新进榜 / 当前榜单全貌）
│   ├── publishers.html   新厂商冒泡
│   ├── game.html         产品档案列表 + 详情页
│   ├── style.css
│   └── app.js · publishers.js · game-detail.js · game-profile.js · sb.js · config.js
├── docs/assets/          README 使用的截图
├── supabase/             数据库迁移与 Edge Function（save-profile）
├── .github/workflows/
│   ├── daily.yml         定时抓取 + 写数据
│   ├── weekly.yml        每周一 09:00 出周报
│   ├── mail-test.yml     投递通道自检
│   └── pages.yml         发布站点
└── README.md
```

<a name="quick-start"></a>

## 🚀 快速开始（第一次部署）

### 1. 把这个项目推到你自己的 GitHub

```bash
cd minigame-rank-daily
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的用户名>/minigame-rank-daily.git
git push -u origin main
```

仓库设为 **Public**（公开），否则 Pages 免费额度会受限、Actions 配额也会减半。

### 2. 准备登录态（可选，默认不需要）

**当前配置走匿名公开接口，不需要配置任何 Secret。** 引力引擎的公开接口在未登录
状态下稳定返回每榜 TOP20，且不依赖会过期的凭证 —— 对长期无人值守的定时任务是
最稳的路径。

如果将来确实需要 TOP100（例如要做全量腰部分析），再配置登录态：

1. 在本机登录 `rank.gravity-engine.com`，导出 Playwright 的 storage_state 存为 `rank_auth.json`
2. 到 GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret
3. Name 填 `GRAVITY_AUTH`，Value 粘贴上一步 JSON 的全文

> **登录态有效期**：通常几天到几周，失效后 Action 自动回落到匿名 TOP20。
> 这份维护成本对无人值守任务来说往往不划算，因此默认不启用。

### 3. 启用 GitHub Pages

仓库 → Settings → Pages：

- Source 选 **GitHub Actions**

第一次推送后，`pages.yml` 会自动构建并发布。访问：

```
https://<你的用户名>.github.io/minigame-rank-daily/
```

### 4. 第一次试跑

仓库 → Actions → 「Daily Rank Snapshot」 → Run workflow（手动触发一次，不用等 10:30）。

跑完后：

- `data/daily/<日期>.json` 会被 commit
- `data/latest.json` 同步更新
- 几分钟后 Pages 会重新部署，刷新页面就能看到数据

## ⏰ 每天发生什么

```
10:30 北京时间 (= UTC 02:30)   ┐
11:30 北京时间 (= UTC 03:30)   ┘ 双触发，防 GitHub schedule 偶发静默跳过
  ├─ daily.yml 触发
  │   ├─ pip install playwright openpyxl pycryptodome
  │   ├─ playwright install chromium
  │   ├─ python scripts/ci_scrape.py
  │   │     输出 data/daily/YYYY-MM-DD.json
  │   │     更新 data/latest.json
  │   │     追加 data/history.jsonl
  │   ├─ python scripts/ci_diff.py
  │   │     输出 data/diff/YYYY-MM-DD.json
  │   ├─ python scripts/ci_sync_supabase.py   （未配置 Supabase 时自动跳过）
  │   └─ git commit & push (作者: github-actions[bot])
  │
  └─ data/ 变化触发 pages.yml
      └─ 站点重新构建并发布
```

> 双触发是刻意的：第一次成功后第二次抓到的数据一样，`git commit` 会直接跳过，无副作用。

## 🧮 「新进榜」是怎么算出来的

这个项目维护一个**累积 base 库**（`data/base/games.json` + `publishers.json`），记录历史上所有抓到过的游戏 / 发行商，包括它们各自出现过的榜单和首次 / 末次出现的日期。

每天抓取后，把当日榜单和 base 对比，每条数据按“对**这个榜**而言”分两类：

| 类别 | 定义 |
| --- | --- |
| **新进榜** (new_to_board) | 这个游戏在**这个榜**的历史里从未出现过（不管它有没有出现在别的榜） |
| **回归** (returning) | 这个榜以前出现过、消失过、又回来（gap ≥ 2 天） |

发行商的“新进”独立计算：**首次出现在这个榜的发行商**。

> base 库内部还会区分“全新（任何榜都没见过）”和“首次入此榜（其他榜见过）”，但前端按“新进榜”统一展示——做单榜监控时这两者意义相同。要做跨榜分析的话可以直接读 `data/base/`。

base 库可以从 `data/daily/*.json` 完整重建（`base.py:rebuild_from_daily()`），所以即使 base 文件丢失也能恢复。每天 `ci_diff.py` 会先用历史 daily 重建一次 base 来保证准确性。

<a name="detail"></a>

## 🖼️ 产品档案（`scripts/detail/`）

站点第二块内容是**产品档案**：给每款在榜游戏建一份详情页，回答“这款游戏技术上怎么做的、值不值得抄、能不能和我们手里的业务发生关系”。

**覆盖口径**：**当日各榜 TOP20 的并集**（约 150 款/天），不是站点列表里的全部历史产品
（Supabase `games` 有 3200+ 款，列表里大量行显示「未建档」是预期）。

**五个分区**：

1. 技术实现细节 · 2. 核心玩法 · 爽点 · 创新点 · 3. 游戏截图 · 4. 复刻建议 · 5. **结合业务的建议**

第 5 分区与「复刻建议」是**并行**的两个决策视角：`clone` 答「要不要抄」，`biz` 答「不管抄不抄，能和我们手里的业务发生什么关系」。**一主两附**：

| 键 | 权重 | 结构 | 结论枚举 |
| --- | --- | --- | --- |
| `biz.internal` | **主体** | `why` 4 条 · `how` 2 条 · `gain` 4 条（**424 法则**：为什么 / 怎么做 / 收益） | 可复用 / 需改造 / 不建议搬 |
| `biz.opportunity` | 附属 | `points` ≤2 条一句式 | 推荐接触 / 可观望 / 不建议接触 |
| `biz.extension` | 附属 | `scenes` 1–2 个 `{scene, how}` | 无枚举 |

其中 `why` 的 4 条是**用户视角 2 条 + 业务视角 2 条**（写 `{"lens": "用户"|"业务", "text": "..."}`）——
只写业务侧会变成“对我们顺手”的建议、不回答用户买不买单；只写用户侧又回答不了该不该投入。
页面把视角渲染成小标签（用户 = 天蓝、业务 = 紫）。

口径约束：**通用口径**，不绑定具体业务线，写具体产品时用「若自有产品线含 XX 品类」条件句。

### 日常增量流程（幂等，已有档案自动跳过）

```bash
python scripts/detail/build_worklist.py      # 对齐当日榜单 -> _worklist.json
python scripts/detail/enrich_media.py        # iTunes 图标 / 实机截图 / 包体（跨次合并缓存）
python scripts/detail/make_batches.py --size 6
# 派并行子智能体：各读 RESEARCH_SPEC.md + _batch_N_input.json，写 batch_N.json
python scripts/detail/merge_details.py       # -> <slug>.json + index.json
python scripts/detail/verify.py              # 终检：覆盖 / schema / 8 维 / 内容 / 分布
python scripts/detail/preview_site.py        # 本地预览（增量复制到 _pages_preview）
```

### 只回填第 5 分区

已有档案要补 `biz`、又不想重抄已定稿的四分区时，走补丁通道：

```bash
python scripts/detail/make_biz_batches.py --size 13   # 从已有档案抽事实摘要生成批次
# 派子智能体：各读 RESEARCH_SPEC 第六节 + _biz_batch_N_input.json，写 _staging/biz_batch_N.json
#             （只含 name + biz 两个键；带 biz 且无 tech/play/clone 即被识别为补丁）
python scripts/detail/merge_details.py --biz-only     # 只打 biz 补丁，不动已定稿分区
python scripts/detail/merge_details.py --polish-biz   # 标点排版统一（幂等）+ 重建索引
python scripts/detail/verify.py --require-biz         # 硬门禁：要求全部档案都有 biz
```

### 管线里踩过的坑

- **名称归一化必须两侧同一套**：`schema.norm_name`（NFKC + 去 NBSP / 全角空格 / 零宽 + 合并空白）。
  榜单名与档案名常只差一个全角冒号「：」或 NBSP，不归一会产生「已建档」与「未建档」两条幽灵记录。
- **`slug()` 不能用 `[^0-9A-Za-z\u4e00-\u9fff]`**：会把日文假名剥掉（`ワクワク電車ライフ` → `電車`）。
- **索引的 `slug` 必须取磁盘真实文件名**，不能拿当前规则重算 —— 历史文件命名规则不同，重算会让站点 404。
- **`_staging/` 里下划线开头的文件是中间产物**，必须跳过，否则会把 `_batch_N_input.json` 当调研成果合并进去。
- 改了 `site/` 下的 js 必须 **bump `game.html` 里的 `?v=` 版本号**，否则浏览器吃缓存看到旧 JS。

<a name="weekly"></a>

## 📊 周报（game-market-monitor）

每周一北京时间 09:00，`weekly.yml` 基于仓库里的历史快照生成一份微信小游戏周报。

**六个板块**，对应三类立项参考价值：

| 板块 | 说明 | 立项价值 |
| --- | --- | --- |
| 品类结构 | L1/L2 占比 + 代表产品 | 大盘在做什么品类 —— 立项方向 |
| 头部集中度 | 发行商在榜产品数 / 占比 | 榜被谁占据，新进者有没有空间 |
| 新晋者 | 本期在榜、基准期不在榜 | 新变量、正在冒头的产品 |
| 上升态势 | 两周都在榜内、名次前进 ≥3 位 | 正在起量的题材 / 玩法 |
| 头部稳定性 | TOP10 留存率 / 换血率 | 大盘是否固化，还挤不挤得进去 |
| 腰部持续性 | 11-20 名连续在榜天数 | 区分长线产品与买量冲榜 |

产出落在 `reports/weekly-<日期>.{md,html,json}`。HTML 用全内联样式，可直接作为
邮件正文粘贴发送。

### 品类口径为什么需要归一化

引力引擎自带的 `category` / `subcategory` 不能直接用于统计：

- L2 是拼接串（`牌类棋牌传统棋牌`、`消除消除休闲`、`卡牌卡牌卡牌竞技`），
  同一品类被拆成十几个变体，占比会被稀释成噪音；
- 抖音榜 `category` 返回占位值 `1`；
- 同一游戏 L1 跨期漂移（历史数据里 175 个游戏出现过多个 L1）。

`scripts/monitor/classify.py` 按「人工词典 → L2 清洗 → 跨榜学习 → 游戏名兜底 → L1 直映」
逐层归一，并把 L1 统一由 L2 反推以保证自洽。规则与词典都在
`scripts/monitor/category_map.json`，新增游戏时优先补 `by_game_name`。

### 数据口径限制（重要）

匿名接口每榜只有 TOP20，因此：

- 一个游戏从第 25 名升到第 15 名，在数据上表现为「新进 TOP20」而非「上升 10 位」；
- 「腰部」指榜内 11-20 名，不是全榜的 30-100 名；
- 对比时两端都截断到 TOP20，避免与历史 TOP100 快照混比算出虚高涨幅。

以上三条会写进每份周报末尾的「数据说明与口径」。

### 手动跑一份周报

```bash
python scripts/monitor/report.py --format both
python scripts/monitor/report.py --baseline 2026-09-01   # 指定基准日期
python scripts/monitor/analyze.py                        # 只出指标摘要
```

<a name="push"></a>

## 📬 周报推送（邮件 / 企业微信群机器人）

周一 9:00 的 `weekly.yml` 生成报告后自动推送周报。**A / B 两个通道任配其一即可生效，也可以都开**：
只配 `WECOM_WEBHOOK` 就只推群，只配 `MAIL_*` 就只发邮件，都没配则跳过（只打 warning，不报错）。
**方式 C 是另一条独立通道**，不走 CI、也不需要 Webhook，见下。

### 方式 A：邮箱（企业微信邮箱 / 腾讯企业邮）

**服务器参数**（企业微信邮箱 = 腾讯企业邮）

| 项 | 值 |
| --- | --- |
| SMTP | `smtp.exmail.qq.com` · 端口 `465` · SSL（失败自动回落 `587` STARTTLS） |
| 备用 SMTP | `hwsmtp.exmail.qq.com` |
| 密码 | **16 位客户端专用密码**，不是邮箱登录密码 |

**三步开通 SMTP（缺一不可）**

1. **管理员**：企业微信管理后台 →【协作】→【安全管理】→【客户端访问限制】→
   修改 Exchange/IMAP/SMTP 服务范围 → 勾选发信账号；
2. **用户**：网页版邮箱 `exmail.qq.com/login`（扫码登录）→【设置】→【收发信设置】→
   勾选「开启 IMAP/SMTP 服务」→ 保存；
3. **用户**：【设置】→【邮箱绑定】→ 开启「安全登录」→【生成新密码】→ 复制 16 位专用密码。

**注意**：GitHub 托管 runner 是动态公网 IP。若公司邮箱后台配了 IP 白名单，登录会失败——
这种情况改用方式 B，或把 `MAIL_HOST` 换成内网可达的自建 SMTP。

### 方式 B：企业微信群机器人（推荐先跑通这个）

**不需要任何密码**，一个 Webhook URL 即可，5 分钟能验完。

**取 Webhook 地址**

1. 手机 / 桌面端企业微信，进入要收周报的**内部群**（群机器人不能发到外部群、微信用户群）；
2. 点右上角 `···` →【群机器人】→【添加机器人】→ 起个名字（如「榜单周报」）→ 添加；
3. 复制形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx` 的地址；
4. 存到仓库 Secret `WECOM_WEBHOOK`。

**群消息长什么样**：标题 + 本周要点（引用块）+ 品类结构 + 头部格局 + 口径说明，
末尾自动附 `[查看图文周报](链接)`。**刻意不用表格和列表**——机器人的 markdown(v1) 不支持
这两者，用了会原样吐出一堆竖线和短横线；正文按 **4096 字节**上限自动截断并保住尾注。

末尾那个链接**不需要配置**：从 `git remote get-url origin` 现场推导，指向当期
`reports/weekly-*.md`（GitHub 上直接可读）。只有想钉死成固定地址时才需要 `WECOM_REPORT_URL`。

**不要给请求体加 `chatid`**：`key` 本身已经绑定了群，官方文档的请求体只有
`msgtype` + 内容体两个字段，`chatid` 是 `appchat/send`（应用群聊）那套接口的字段，
在消息推送这里不生效。

**已知限制（来自官方文档）**

| 限制 | 说明 |
| --- | --- |
| 内容上限 | markdown 4096 字节（脚本已自动截断保护） |
| 频率上限 | 每个机器人 **20 条/分钟**（周报每周 1 条，无压力） |
| 不支持表格 / 列表 / 分割线 | 因此摘要改用空行分段 + 引用块 + `<font color>` 三色 |
| 不支持 @所有人 | markdown 类型只能 `@` 单个成员且需 userid；要 @全体得改用文本消息的 `mentioned_list` |
| key 即凭据 | Webhook 泄露 = 任何人可往群里发消息，只放 GitHub Secret，别写进代码或日志 |
| 换了 key 会 93000 | 机器人被移除或 key 重置时，返回 `errcode=93000`，需重取地址 |

需要表格 / 分割线可以切到 `WECOM_MSG_TYPE=markdown_v2`，但它**不支持字体颜色**，且要求客户端
版本 ≥ 4.1.36（安卓 ≥ 4.1.38），低版本会整条退化成纯文本——面向多人时慎用。

### 方式 C：企业微信群（机器人直发，走本机 `wecom-cli`）

不申请 Webhook，直接让已经在群里的机器人把消息发进群 —— 只需要**群会话 ID**。
摘要内容与方式 B 完全同一套口径（复用 `build_wecom_markdown`），空行分段 + 引用块，不含表格 / 列表。

```bash
python scripts/monitor/send_group.py --list                  # 看当前能发消息的会话
python scripts/monitor/send_group.py --latest --dry-run      # 只看摘要内容，不发送
WECOM_GROUP_ID=<群会话ID> python scripts/monitor/send_group.py --latest
```

**前置条件（关键）**：目标群必须**和机器人有过对话** —— 群里任一成员 `@机器人` 发一条消息，
该群才会进入机器人的「最近会话」，之后才能被推送。否则接口直接拒绝：

```
853008 当前会话不是机器人的最近会话，暂不支持发送消息。需要成员向机器人对话过，机器人即可发送。
```

**为什么这条通道不在 CI 里跑**：授权凭据在本机，GitHub runner 拿不到。
所以它由 **WorkBuddy 定时任务**驱动 —— 每周一 10:00 同步仓库 → 取最新一期周报 → 推送。
（`weekly.yml` 周一 09:00 / 09:30 出报告，10:00 留出落库与 Pages 部署的时间。）

| | 方式 B（Webhook） | 方式 C（机器人直发） |
| --- | --- | --- |
| 凭据 | 群机器人 Webhook URL | 群会话 ID + 本机授权 |
| 跑在哪 | GitHub Actions 里就行 | 只能本机（依赖 `wecom-cli` 授权） |
| 触发 | `weekly.yml` 自动 | WorkBuddy 定时任务 |
| 额外依赖 | 需在企业微信后台建群机器人 | 群需先和机器人对过话 |

- 降级通道 `--text-only` 走管理端 `message.send`，需企业开通该工具；未开通会报
  `853006 this tool is not available for your corporation`，此时用默认的 markdown 通道即可。
- 群会话 ID 属于内部标识：不要写进仓库、issue 或日志，用 `WECOM_GROUP_ID` 环境变量传入。

**配置 Secrets**（仓库 Settings → Secrets and variables → Actions）

| Secret | 通道 | 必填 | 说明 |
| --- | --- | --- | --- |
| `WECOM_WEBHOOK` | B | 二选一 | 群机器人 Webhook 地址 |
| `MAIL_USER` | A | 二选一 | 发件邮箱，如 `miracleshen@tencent.com` |
| `MAIL_PASS` | A | 同上 | 16 位客户端专用密码 |
| `MAIL_TO` | A | 同上 | 收件人，逗号分隔 |
| `MAIL_CC` | A | — | 抄送，逗号分隔 |
| `MAIL_HOST` | A | — | 默认 `smtp.exmail.qq.com` |
| `MAIL_PORT` | A | — | 默认 `465` |
| `MAIL_FROM_NAME` | A | — | 发件人显示名，默认「微信小游戏周报」 |
| `WECOM_MSG_TYPE` | B | — | 默认 `markdown`，可改 `markdown_v2` |
| `WECOM_REPORT_URL` | B | — | 仅用于覆盖末尾链接；不配则自动推导当期报告地址 |

**先自检再等周一**：手动触发 `Mail Channel Test` 工作流（Actions → 左侧选它 → Run workflow），
它会 `--check` 所有**已配置**的通道：SMTP 连接 + 登录，以及往群里发一条自检消息。

**本地调试**

```bash
# 邮箱通道
export MAIL_USER=... MAIL_PASS=... MAIL_TO=...
# 群机器人通道（不需要邮箱凭据）
export WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx

python scripts/monitor/send_mail.py --check --send-test   # 自检所有已配置通道
python scripts/monitor/send_mail.py --webhook-only        # 只推群，不发邮件
python scripts/monitor/send_mail.py --dry-run             # 落 _mail_preview/mail-<日期>.eml，不发信
python scripts/monitor/send_mail.py --latest              # 发最新一期
python scripts/monitor/send_mail.py --latest --to a@x.com # 临时改收件人
```

邮件为 `multipart/mixed`：正文是 `text/plain`（周报 Markdown）+ `text/html`（全内联样式，
手机上直接可读），并附上 `.md` / `.html` 两个文件便于转发。两个通道都没配时
`weekly.yml` 会跳过推送并给出 warning，不影响报告生成与提交。

## 🖥️ 本地开发

```bash
# 装依赖：pycryptodome 供 HTTP 抓取解密响应；playwright 仅在浏览器兜底时需要
pip install pycryptodome
pip install playwright openpyxl && python -m playwright install chromium

# 跑一次抓取（匿名 Top 20，走公开接口）
python scripts/ci_scrape.py

# 计算 diff（需要至少两天数据）
python scripts/ci_diff.py

# 生成周报
python scripts/monitor/report.py --format both

# 起一个本地静态服务器看页面
python -m http.server 8000 --directory _pages_preview
```

要在本地预览 Pages，需要把 `site/*` 和 `data/` 拼成 `_pages_preview/`。最简单做法是仿造 `pages.yml` 里的脚本片段：

```bash
mkdir -p _pages_preview/data
cp -r site/* _pages_preview/
cp -r data/daily _pages_preview/data/ 2>/dev/null
cp -r data/diff _pages_preview/data/ 2>/dev/null
cp data/latest.json _pages_preview/data/ 2>/dev/null
cp data/history.jsonl _pages_preview/data/ 2>/dev/null
python -m http.server 8000 --directory _pages_preview
```

打开 http://localhost:8000

## ⚙️ 改抓取频率

编辑 `.github/workflows/daily.yml` 里的 `cron`：

```yaml
schedule:
  - cron: "30 2 * * *"   # 北京时间 10:30（默认）
  - cron: "30 3 * * *"   # 北京时间 11:30（兜底补跑）
```

cron 是 UTC，加 8 小时是北京时间。常用：

- 每天早上 10:30 北京 = `30 2 * * *`（默认，榜单 10:00 更新后 30 分钟）
- 每天早上 9:07 北京 = `7 1 * * *`
- 每 6 小时 = `0 */6 * * *`

## 💰 用量与成本

| 资源 | 免费额度 | 实际用量 | 余量 |
| --- | --- | --- | --- |
| Actions | 2000 分钟/月（公开仓库无限制） | ~3 分钟/天 ≈ 90 分钟/月 | 充裕 |
| Pages | 公开仓库免费 | 无限制 | — |
| 仓库大小 | 软上限 1 GB | 每天 ~50 KB JSON ≈ 18 MB/年 | 50 年用不完 |

## ❓ 常见问题

**Q：Actions 跑失败说找不到登录态？**
A：检查 Secret `GRAVITY_AUTH` 是否填了完整 JSON，注意复制时不要丢了首尾的 `{` `}`。失败也不阻塞 —— Action 会回落到匿名模式，只是 Top 数变少。

**Q：Pages 一直显示「尚无 latest.json」？**
A：等第一次 `daily.yml` 跑完。或者手动触发一次。

**Q：站点访问空白 / 中文乱码？**
A：刷新一下（CDN 可能没即时刷新）。如果持续，看浏览器 Console 错误信息。

**Q：产品档案里很多游戏显示「未建档」？**
A：这是预期。详情档案按约定只覆盖**当日各榜 TOP20 的并集**（约 150 款/天），而列表由 Supabase 里的全部历史产品（3200+ 款）驱动。

**Q：能不能多平台抓 Apple Store / TapTap？**
A：已支持。TapTap 预约榜 + iOS 美 / 国 / 日区游戏免费榜 + Android 美区免费游戏榜每天随主快照一起抓取（`scrape_taptap.py` / `scrape_ios.py` / `scrape_googleplay.py`）。iOS 榜单来自 Apple 官方 iTunes RSS（`itunes.apple.com/{cc}/rss/topfreeapplications/genre=6014/limit=100/json`），免登录免密钥；Android 来自 AppBrain（`appbrain.com/stats/google-play-rankings/top_free/game/us`，SSR 免登录，注意免费限流）。两者都不含排名涨跌箭头、只提供当前榜单。扩展更多国家 / 榜单：改对应 `scrape_*.py` 的配置 + `site/app.js` 的 `BOARD_LABELS`。引力引擎微信 / 抖音的选择器逻辑见 `scrape_rank.py`。

**Q：周报没收到 / 群里没消息？**
A：先确认走的是哪条通道。**方式 A / B**：打开 Actions 里那次 run，看 `Send weekly report` 步骤日志。
四种情况：① 日志出现`未配置任何投递通道` 的 warning —— Secrets 没填全；② `SMTP 登录失败` —— 回到「周报推送」
方式 A 的三步开通流程（专用密码 / IMAP-SMTP 开关 / 管理员客户端访问范围）；
③ `errcode=93000` —— 群机器人 Webhook 失效或 key 被重置，重取地址；
④ 日志显示已发送 —— 邮件查收件方垃圾箱或公司邮件网关。想单独验证通道，
手动跑 `Mail Channel Test` 工作流。

**方式 C** 不看 Actions 日志（它根本不走 CI），在本机单独验：

```bash
python scripts/monitor/send_group.py --list        # 目标群在不在「可发送的会话」里
python scripts/monitor/send_group.py --latest      # 直接推一条看看
```

常见两类：`853008` = 目标群还没和机器人对过话（让群里的人 `@机器人` 发一条）；
`--list` 里看不到目标群，说明该群不在机器人的最近会话里，同上。
