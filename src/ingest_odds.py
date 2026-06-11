"""Build the market data files from the round-2 odds research
(data/raw/research_round2.json, key 'odds-refresh').

Codified aggregation (matches the committed round-2 artifacts exactly):
  decimal_odds        = median across all books quoting the team
  decimal_odds_sharp  = mean across the sharp/low-margin set
                        (Pinnacle, Betfair Exchange, Polymarket, Kalshi)

Outputs: data/odds.csv, data/odds_books.csv

This is the single producer of odds.csv — src/ingest.py deliberately does
NOT write odds (it would clobber this newer, sharper snapshot with the
round-1 board).

Usage:  python3 -m src.ingest_odds
"""
from __future__ import annotations

import json
import os
import statistics

import pandas as pd

from .load_data import DATA, canon

SHARP = {"Pinnacle", "Betfair Exchange", "Polymarket", "Kalshi"}
RAW = os.path.join(DATA, "raw", "research_round2.json")


def main() -> None:
    with open(RAW) as f:
        o = json.load(f)["odds-refresh"]
    rows, book_rows = [], []
    for t in o["odds"]:
        team = canon(t["team"])
        books = t.get("books") or []
        all_odds = [b["decimal_odds"] for b in books]
        sharp = [b["decimal_odds"] for b in books if b["book"] in SHARP]
        rows.append({
            "team": team,
            "decimal_odds": round(statistics.median(all_odds), 2) if all_odds
                            else t["decimal_odds"],
            "decimal_odds_sharp": round(sum(sharp) / len(sharp), 2) if sharp else None,
            "n_books": len(books), "n_sharp": len(sharp),
            "as_of": o["as_of_date"],
        })
        for b in books:
            book_rows.append({"team": team, "book": b["book"],
                              "decimal_odds": b["decimal_odds"]})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA, "odds.csv"), index=False)
    pd.DataFrame(book_rows).to_csv(os.path.join(DATA, "odds_books.csv"), index=False)
    missing_sharp = df["decimal_odds_sharp"].isna().sum()
    print(f"odds.csv: {len(df)} teams (as of {o['as_of_date']}); "
          f"odds_books.csv: {len(book_rows)} rows; "
          f"teams without sharp quotes: {missing_sharp}")


if __name__ == "__main__":
    main()
