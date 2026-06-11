# 赛前存证 · RFC3161 可信时间戳

本目录对 `predictions/` 下三份预测文件做了**文件级**可信时间戳锚定
（freetsa.org 签发，RFC3161 标准，仅文件哈希出库）：

| 文件 | 内容 | 锚定时间 (UTC) |
|---|---|---|
| `2026-06-11_round2_matches.csv` | **正式计分存档**：72 场小组赛逐场胜平负概率 + xG | 2026-06-11 12:06 |
| `2026-06-11_round1_matches.csv` | 早期参数版（仅模型对照保留，不参与计分） | 2026-06-11 12:06 |
| `market_1x2_2026-06-11_md1.csv` | 第一比赛日市场 1X2 基准（赛后无法补采） | 2026-06-11 12:06 |

揭幕战（墨西哥 vs 南非）于 **2026-06-11 19:00 UTC** 开球——所有锚定均早于开球约 7 小时。
锚定的是文件本身的 SHA-512 摘要，与 git 历史无关；任何人可独立验证文件在该时刻已存在且未被改动：

```bash
curl -sO https://freetsa.org/files/cacert.pem
openssl ts -verify -data ../predictions/2026-06-11_round2_matches.csv \
  -in file_2026-06-11_round2_matches.csv.tsr -CAfile cacert.pem
# 期望输出: Verification: OK
openssl ts -reply -in file_2026-06-11_round2_matches.csv.tsr -text | grep "Time stamp"
```

赛后核验：真实比分录入后运行 `python3 -m src.score`，对照这份不可改动的赛前预测逐场计算 Brier / log-loss。
