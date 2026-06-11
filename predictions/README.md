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
