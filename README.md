# minigame-rank-daily

每日抓取 [引力引擎](https://rank.gravity-engine.com/) 的小游戏榜单（微信小游戏 + 抖音小游戏，共 6 个榜的日榜），叠加 TapTap 预约榜、**iOS App Store 美/国/日区游戏免费榜**（Apple 官方 iTunes RSS）与 **Android Google Play 美区免费游戏榜**（AppBrain），把数据 commit 进仓库，并通过 GitHub Pages 展示一个仪表盘，重点突出**每日新进游戏 / 新进发行商**。

在此基础上，仓库每周还会基于历史数据生成一份 **微信小游戏周报**（品类结构 / 头部集中度 / 新晋者 / 上升态势 / 头部稳定性 / 腰部持续性），输出 Markdown + HTML + JSON，落在 `reports/`。详见下方[「周报」](#周报game-market-monitor)。

> **抓取方式**：引力引擎两个平台的数据走的是站点公开接口（`scripts/scrape_gravity_http.py`，
> 纯 HTTP，不需要浏览器和登录态）。此前用 Playwright 渲染页面，实测会因 SPA 加载失败
> （`ERR_CONNECTION_CLOSED`）导致当天微信/抖音数据整块缺失，因此改为接口直连作为主路径，
> 浏览器渲染保留为兜底。

- 抓取：GitHub Actions 每日北京时间 10:30 触发（榜单 10:00 更新，留 30 分钟让后端稳定）
- 存储：每天一份 JSON 进 `data/daily/`，diff 进 `data/diff/`，cumulative base 进 `data/base/`，趋势进 `data/history.jsonl`
- 展示：纯静态页（`site/`）通过 GitHub Pages 发布

```
仓库结构
.
├── scripts/
│   ├── scrape_gravity_http.py 引力引擎公开接口抓取（纯 HTTP，主抓取路径）
│   ├── scrape_rank.py   浏览器渲染抓取/解析（兜底路径，与桌面端共用）
│   ├── scrape_taptap.py TapTap 预约榜（SSR JSON-LD，纯 stdlib）
│   ├── scrape_ios.py    iOS 美/国/日区游戏免费榜（Apple iTunes RSS，纯 stdlib）
│   ├── scrape_googleplay.py  Android 美区免费游戏榜（AppBrain SSR，纯 stdlib）
│   ├── ci_scrape.py     CI 抓取入口，写 daily/<日期>.json 等
│   ├── base.py          累积 base 库（历史所有游戏 / 发行商）
│   ├── ci_diff.py       基于 base 分类今日新进
│   └── monitor/         周报分析层（classify 品类归一化 / analyze 指标 / report 渲染）
├── data/
│   ├── daily/           历史快照（每天一份）
│   ├── diff/            每天的「新进」分类
│   ├── base/            累积 base：games.json + publishers.json
│   ├── latest.json      最新快照（前端默认加载）
│   ├── history.jsonl    每日条数趋势
│   └── index.json       由 Pages workflow 生成的可用日期列表
├── reports/             周报产出：weekly-<日期>.md / .html / .json
├── site/                Pages 站点
│   ├── index.html
│   ├── style.css
│   └── app.js
├── .github/workflows/
│   ├── daily.yml        定时抓取 + 写数据
│   ├── weekly.yml       每周一 09:00 出周报
│   └── pages.yml        发布站点
└── README.md
```

## 「新进榜」是怎么算出来的

这个项目维护一个**累积 base 库**（`data/base/games.json` + `publishers.json`），记录历史上所有抓到过的游戏 / 发行商，包括它们各自出现过的榜单和首次/末次出现的日期。

每天抓取后，把当日榜单和 base 对比，每条数据按"对**这个榜**而言"分两类：

| 类别 | 定义 |
| --- | --- |
| **新进榜** (new_to_board) | 这个游戏在**这个榜**的历史里从未出现过（不管它有没有出现在别的榜） |
| **回归** (returning) | 这个榜以前出现过、消失过、又回来（gap ≥ 2 天） |

发行商的"新进"独立计算：**首次出现在这个榜的发行商**。

> base 库内部还会区分"全新（任何榜都没见过）"和"首次入此榜（其他榜见过）"，但前端按"新进榜"统一展示——做单榜监控时这两者意义相同。要做跨榜分析的话可以直接读 `data/base/`。

base 库可以从 `data/daily/*.json` 完整重建（`base.py:rebuild_from_daily()`），所以即使 base 文件丢失也能恢复。每天 `ci_diff.py` 会先用历史 daily 重建一次 base 来保证准确性。


---

## 一、第一次部署

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

仓库 → Actions → 「Daily Rank Snapshot」 → Run workflow（手动触发一次，不用等 03:33）。

跑完后：
- `data/daily/<日期>.json` 会被 commit
- `data/latest.json` 同步更新
- 几分钟后 Pages 会重新部署，刷新页面就能看到数据

---

## 二、每天发生什么

```
03:33 北京时间 (= UTC 19:33 前一日)
  ├─ daily.yml 触发
  │   ├─ pip install playwright openpyxl
  │   ├─ playwright install chromium
  │   ├─ python scripts/ci_scrape.py
  │   │     输出 data/daily/YYYY-MM-DD.json
  │   │     更新 data/latest.json
  │   │     追加 data/history.jsonl
  │   ├─ python scripts/ci_diff.py
  │   │     输出 data/diff/YYYY-MM-DD.json
  │   └─ git commit & push (作者: github-actions[bot])
  │
  └─ data/ 变化触发 pages.yml
      └─ 站点重新构建并发布
```

---

---

## 周报（game-market-monitor）

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

---

## 三、本地开发

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

---

## 四、改抓取频率

编辑 `.github/workflows/daily.yml` 里的 `cron`：

```yaml
schedule:
  - cron: "30 2 * * *"   # 北京时间 10:30（默认）
```

cron 是 UTC，加 8 小时是北京时间。常用：
- 每天早上 10:30 北京 = `30 2 * * *`（默认，榜单 10:00 更新后 30 分钟）
- 每天早上 9:07 北京 = `7 1 * * *`
- 每 6 小时 = `0 */6 * * *`

---

## 五、用量与成本

| 资源 | 免费额度 | 实际用量 | 余量 |
| --- | --- | --- | --- |
| Actions | 2000 分钟/月（公开仓库无限制） | ~3 分钟/天 ≈ 90 分钟/月 | 充裕 |
| Pages | 公开仓库免费 | 无限制 | — |
| 仓库大小 | 软上限 1 GB | 每天 ~50 KB JSON ≈ 18 MB/年 | 50 年用不完 |

---

## 六、周报推送（邮件 / 企业微信群机器人）

周一 9:00 的 `weekly.yml` 生成报告后自动推送周报。**两个通道任配其一即可生效，也可以都开**：
只配 `WECOM_WEBHOOK` 就只推群，只配 `MAIL_*` 就只发邮件，都没配则跳过（只打 warning，不报错）。

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

1. 手机/桌面端企业微信，进入要收周报的**内部群**（群机器人不能发到外部群、微信用户群）；
2. 点右上角 `···` →【群机器人】→【添加机器人】→ 起个名字（如「榜单周报」）→ 添加；
3. 复制形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx` 的地址；
4. 存到仓库 Secret `WECOM_WEBHOOK`。

**群消息长什么样**：标题 + 本周要点（引用块）+ 品类结构 + 头部格局 + 口径说明，
末尾可附 `[查看图文周报](链接)`。**刻意不用表格和列表**——机器人的 markdown(v1) 不支持
这两者，用了会原样吐出一堆竖线和短横线；正文按 **4096 字节**上限自动截断并保住尾注。

**已知限制（来自官方文档）**

| 限制 | 说明 |
| --- | --- |
| 内容上限 | markdown 4096 字节（脚本已自动截断保护） |
| 频率上限 | 每个机器人 **20 条/分钟**（周报每周 1 条，无压力） |
| 不支持表格/列表/分割线 | 因此摘要改用空行分段 + 引用块 + `<font color>` 三色 |
| 不支持 @所有人 | markdown 类型只能 `@` 单个成员且需 userid；要 @全体得改用文本消息的 `mentioned_list` |
| key 即凭据 | Webhook 泄露 = 任何人可往群里发消息，只放 GitHub Secret，别写进代码或日志 |
| 换了 key 会 93000 | 机器人被移除或 key 重置时，返回 `errcode=93000`，需重取地址 |

需要表格/分割线可以切到 `WECOM_MSG_TYPE=markdown_v2`，但它**不支持字体颜色**，且要求客户端
版本 ≥ 4.1.36（安卓 ≥ 4.1.38），低版本会整条退化成纯文本——面向多人时慎用。

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
| `WECOM_REPORT_URL` | B | — | 群消息末尾附「查看图文周报」链接 |

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

---

## 七、常见问题

**Q：Actions 跑失败说找不到登录态？**
A：检查 Secret `GRAVITY_AUTH` 是否填了完整 JSON，注意复制时不要丢了首尾的 `{` `}`。失败也不阻塞 —— Action 会回落到匿名模式，只是 Top 数变少。

**Q：Pages 一直显示「尚无 latest.json」？**
A：等第一次 `daily.yml` 跑完。或者手动触发一次。

**Q：站点访问空白 / 中文乱码？**
A：刷新一下（CDN 可能没即时刷新）。如果持续，看浏览器 Console 错误信息。

**Q：能不能多平台抓 Apple Store / TapTap？**
A：已支持。TapTap 预约榜 + iOS 美/国/日区游戏免费榜 + Android 美区免费游戏榜每天随主快照一起抓取（`scrape_taptap.py` / `scrape_ios.py` / `scrape_googleplay.py`）。iOS 榜单来自 Apple 官方 iTunes RSS（`itunes.apple.com/{cc}/rss/topfreeapplications/genre=6014/limit=100/json`），免登录免密钥；Android 来自 AppBrain（`appbrain.com/stats/google-play-rankings/top_free/game/us`，SSR 免登录，注意免费限流）。两者都不含排名涨跌箭头、只提供当前榜单。扩展更多国家/榜单：改对应 `scrape_*.py` 的配置 + `site/app.js` 的 `BOARD_LABELS`。引力引擎微信/抖音的选择器逻辑见 `scrape_rank.py`。

**Q：周报没收到 / 群里没消息？**
A：打开 Actions 里那次 run，看 `Send weekly report` 步骤日志。四种情况：① 日志出现
`未配置任何投递通道` 的 warning —— Secrets 没填全；② `SMTP 登录失败` —— 回到第六节
方式 A 的三步开通流程（专用密码 / IMAP-SMTP 开关 / 管理员客户端访问范围）；
③ `errcode=93000` —— 群机器人 Webhook 失效或 key 被重置，重取地址；
④ 日志显示已发送 —— 邮件查收件方垃圾箱或公司邮件网关。想单独验证通道，
手动跑 `Mail Channel Test` 工作流。
