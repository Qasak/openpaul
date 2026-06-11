# 🐙 OpenPaul — 开源世界杯预测引擎

> 2010 年，章鱼保罗用触手预测世界杯；16 年后，OpenPaul 用 **100,000 次蒙特卡洛**接棒。
> 开源 · 可复现 · 开球前存证 · 赛后逐场公开核验

Elo 驱动 Dixon-Coles 进球模型 × 蒙特卡洛全赛事模拟 × 市场价值偏差分析。
全部数据、代码、参数入库；预测在揭幕战开球前经 RFC3161 可信时间戳锚定，赛后逐场 Brier 公开计分。

**📊 在线展示：** https://qasak.github.io/openpaul/ （3D 概率地形 · 夜景地球 · 中英双语）

## 核心结论（赛前快照，2026-06-11）

两层结论分开报告，不混为一谈：

| 层 | 问题 | 答案 |
|---|---|---|
| **绝对概率** | 谁最可能夺冠？ | **西班牙 20.8%**（敏感区间 16.2–22.7%）> 阿根廷 14.8% > 法国 10.1% |
| **价值偏差** | 谁被市场错误定价？ | **阿根廷 +6.5pp**（模型 14.8% vs 锐利盘隐含 8.3%，EV +69%）最被低估；法国/葡萄牙/英格兰被高估 |

## 方法论一页纸

```
eloratings.net 评级（48 队全覆盖，赛前最新）
  └─ 重放 49,400 场国际比赛（1872–2026）重算逐场赛前 Elo（vs 官方 corr 0.986）
       └─ Dixon-Coles 双泊松：λ = exp(a ± b·d)，(a,b,ρ) 在 6,794 场上 MLE 拟合
            ├─ 样本外验证（2025+ 共 1,309 场）：logloss 0.8325，平局率 21.7% vs 实测 22.5%
            └─ 实力不确定性 σ=75：在 2018/2022 两届世界杯上回测选定（非拍脑袋）
                 └─ 100,000 次全赛事蒙特卡洛（2026 新版同分规则 / Annex C 第三名分配 /
                    东道主主场加成 / 加时 λ×⅓ / 点球；已完赛场次锁定真实结果条件重模拟）
                      └─ 市场对照：8 书商单一时间窗，主基准 = 锐利盘共识
                         （Pinnacle/Betfair/Polymarket/Kalshi，overround 仅 6.1%，幂法去水）
```

模型经过多轮独立评审迭代后收敛。详细报告见 [REPORT.md](REPORT.md)，局限与已知近似如实列于 §8（含"Elo 高估南美"替代解释的实证检验）。

## 复现（固定种子，输出逐字节一致）

```bash
pip install numpy scipy pandas
python3 -m src.ingest && python3 -m src.ingest_odds
python3 -m src.elo_history && python3 -m src.fit && python3 -m src.backtest
python3 -m src.simulate --n 100000 --sigma 75
python3 -m src.simulate --n 100000 --sigma 0   --suffix _sigma0
python3 -m src.simulate --n 100000 --sigma 150 --suffix _sigma150
python3 -m src.market && python3 -m src.report round2 2026-06-11
python3 -m src.test_tournament   # 10 项测试
```

## 本地交互看板

```bash
python3 -m src.webapp        # → http://localhost:8765
```

概率图表 / 价值偏差 / 小组形势 / 72 场逐场预测；**录入真实比分一键触发锁定条件重算**（快速 2 万次 ≈30s / 完整 10 万次 ≈90s）并自动逐场 Brier 计分（vs 去水市场 1X2 基准）。

## 滚动更新流程（赛中）

1. 真实结果追加 `data/results.csv`（小组赛记比分；淘汰赛必填 `winner` 列，缺失会被校验拒绝）——或直接在本地看板录入
2. `python3 -m src.score` → 逐场 Brier/log-loss
3. `python3 -m src.simulate --n 100000 --sigma 75` → 条件重模拟
4. `python3 -m src.build_site` → 重新生成静态展示页 `docs/index.html`
5. 提交推送，展示页自动更新

每个比赛日开球前需抓取当日 1X2 赔率入库 `predictions/market_1x2_*.csv`（开球后无法补采）。

## 公开核验存证

- 72 场小组赛逐场胜平负概率于揭幕战（2026-06-11 19:00 UTC）开球前生成，并经 **freetsa.org RFC3161 可信时间戳做文件级锚定**（12:06 UTC）——锚定文件 SHA-512 摘要，与仓库历史无关，任何人可验证赛前预测未被赛后改动，凭证与验证方法见 [`provenance/`](provenance/README.md)
- 正式计分存档：`predictions/2026-06-11_round2_matches.csv`（声明见 [`predictions/README.md`](predictions/README.md)）
- 已完赛场次锁定真实结果条件重模拟，逐场 Brier / log-loss 公开计分

## 项目结构

```
src/        模型 / 拟合 / 回测 / 模拟 / 市场 / 计分 / 看板 / 静态站构建
data/       规范化数据 + raw/ 原始调研留痕（来源 URL 全保留）
docs/       静态展示页（GitHub Pages 直接服务）
dashboard/  本地交互看板前端
predictions/ 赛前预测存证 + 逐场市场基准
provenance/ RFC3161 文件级时间戳凭证
```

## 部署展示页

**GitHub Pages**：仓库 Settings → Pages → Source 选 `Deploy from a branch` → Branch `main` / 目录 `/docs` → 保存，约 1 分钟后访问 `https://<用户名>.github.io/<仓库名>/`。

**Cloudflare Pages**：Dashboard → Workers & Pages → Create → Pages → 连接 GitHub 仓库 → Build command 留空，Build output directory 填 `docs` → 部署。免费额度完全够用，国内访问通常更快。

## 免责声明

本项目为统计建模方法论演示，所有概率为模型输出，**非投注建议**。
