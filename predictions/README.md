# predictions/ 存证说明

## 正式计分存档

**`2026-06-11_round2_matches.csv` 为正式计分存档**（MLE 拟合参数版，72 场小组赛逐场胜平负概率 + xG）。
`2026-06-11_round1_matches.csv` 为 round_1 旧参数版，仅作模型对照保留，不参与计分。
两份文件均在揭幕战（2026-06-11 19:00 UTC）开球前生成，并经文件级 RFC3161 可信时间戳锚定（12:06 UTC，见 `provenance/`）。文件名中的 round1/round2 为内部参数版本号。

## 市场 1X2 基准（market_1x2_*.csv）

- 用途：逐场 Brier/log-loss 对照基准（`python3 -m src.score`），去水后比较。
- 书商为**零售盘单一报价**（FanDuel / bet365 / Sports Interaction / DraftKings 等，逐行记录于
  `book` 列）——非本项目冠军市场使用的锐利盘共识；解读模型 vs 市场结果时应计入此差异。
- `captured_utc` 为抓取时刻（约 2026-06-10 19:45–19:53），存在已披露的不确定性：
  部分底层报价来自 6 月 8–10 日的文章快照（可能滞后实盘 1–3 天）。
  **权威的赛前时间下界是文件级 RFC3161 锚定（2026-06-11 12:06 UTC）**，早于当日 19:00 UTC 揭幕战。
- `match` 列（官方场次号）非来自采集源（原始抓取见 `data/raw/market_1x2_capture_md1.json`，其中
  15/16 行 match=0），由 `data/schedule_group.csv` 的官方赛程在落盘时回填，已逐场与官方编号核对
  （FIFA 编号非严格按开球时间排序）。`src/score.py` 主联结键为场次号、队名对为回退。
- 后续每个比赛日开球前需续抓（下一批：2026-06-16 场次）——开球后无法补采。

## 滚动更新流程

1. 真实结果追加 `data/results.csv`（小组赛记比分；淘汰赛行必填 `winner`，缺失会被加载校验拒绝）。
2. `python3 -m src.score` —— 逐场计分写入 `data/score_log.csv`。
3. `python3 -m src.simulate --n 100000 --sigma 75` —— 锁定条件重模拟。
4. `python3 -m src.build_site` 刷新展示页后提交推送。

## 淘汰赛逐场存证账本（2026-07-02 起）

- `ko_forecasts.csv`：**append-only** 逐场晋级概率账本。每场淘汰赛在双方确定后、
  且数据源仍标记为未开赛（SCHEDULED）时追加一行，之后永不改写；git 提交时间即公开时间戳。
  错过赛前窗口的场次（生成时已开球）**永不补录**——完整性优先于覆盖率。
- `p1_advance` = team1 晋级概率（90 分钟 Dixon-Coles + 加时 1/3 强度 + 点球五五开的解析解，
  对 σ=75 实力扰动做 Gauss-Hermite 积分平均；`p1_advance_sigma0` 为无扰动点估计）。
  评级为赛前冻结 Elo，故已完赛场次的回算值与赛前值相同。
- 对阵生成：真实小组终榜 + 官方公布对阵（`data/schedule_ko.csv`，`src/build_schedule_ko.py`）。
  第三名槽位以官方公布为准——Annex-C 回溯近似在真实组合上与官方表存在两对互换（74/77、82/85），
  已按公布对阵校正并在模拟中锁定真实分配。
- 计分：`src/score.py` 对账本内已完赛场次算二分类 Brier（2·(p−o)²，两类和约定，
  与小组赛三结果 Brier 不同尺度），写 `data/score_log_ko.csv`。

### r4 滚动评级账本（Round 4 起，2026-07-02）

- `ko_forecasts_r4.csv`：与 v2 账本同规则的**第二条 append-only 账本**，唯一区别是评级来源——
  v2 用冻结的 2026-06-10 赛前 Elo（原封方法论），r4 用 `data/elo_current.csv` 滚动评级
  （eloratings 规则重放已完赛场次，2018/2022 两届回测验证 KO logloss −8%，REPORT §10）。
- 双轨并行的目的：**防"改口径洗记录"**——升级模型不抹掉旧口径，两轨对每场剩余比赛
  都在开球前入账，`src/score.py` 分别计分并在共同场次上输出头对头 Brier
  （data/score_log_ko.csv = v2，data/score_log_ko_r4.csv = r4）。
