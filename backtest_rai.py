# -*- coding: utf-8 -*-
"""
backtest_rai.py
-----------------
Answers: "Does buying when RAI signals fear actually pay off, and did those
signals line up with real market drawdowns?" - using data you already have
locally (no internet needed if rai_dashboard_data_weekly.json and
rai_asset_prices_weekly.json are already in this folder from update_rai.py).

Run:
    pip install pandas numpy
    python backtest_rai.py

IMPORTANT METHODOLOGY NOTE
---------------------------
The RAI value shown in the dashboard is z-scored using the FULL sample
(including weeks that hadn't happened yet at any given historical date).
That's fine for a dashboard reading "today", but it's look-ahead bias if
you backtest it - at any past date, you could not have known the future
mean/stdev used to normalize that day's reading.

This script recomputes a REALTIME z-score instead: at each week t, the
z-score uses ONLY the slope history up to and including week t (an
"expanding window"). That's the version that actually reflects what an
investor could have seen and acted on in real time. Both versions are
reported side by side so you can see how much they differ.

Outputs:
  backtest_episodes.csv   - every detected fear episode, its start date,
                             the market drawdown into that point, and
                             forward returns at each holding horizon.
  backtest_summary.csv    - one row per (threshold, horizon): hit rate,
                             average/median forward return, vs. the
                             unconditional (any-week) baseline.
Also prints a readable summary table to the console.
"""
import json
import datetime
import numpy as np
import pandas as pd

RAI_JSON = "rai_dashboard_data_weekly.json"
PRICES_JSON = "rai_asset_prices_weekly.json"
RESULTS_JSON = "backtest_results.json"
BENCHMARK_CODE = "SPX"

MIN_WARMUP_WEEKS = 104          # need >=2 years of slope history before trusting the realtime z-score
THRESHOLDS = [-1.0, -1.5, -2.0, -2.5]
HORIZONS_WEEKS = [4, 12, 26, 52]
DRAWDOWN_LOOKBACK_WEEKS = 52     # "peak" = highest close in the trailing N weeks


def load_rai():
    with open(RAI_JSON) as f:
        payload = json.load(f)
    df = pd.DataFrame(payload["series"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def load_benchmark_close():
    with open(PRICES_JSON) as f:
        payload = json.load(f)
    asset = next((a for a in payload["assets"] if a["code"] == BENCHMARK_CODE), None)
    if asset is None:
        raise SystemExit(f"'{BENCHMARK_CODE}' not found in {PRICES_JSON} - check the asset code.")
    s = pd.DataFrame(asset["series"])
    s["date"] = pd.to_datetime(s["date"])
    s = s.sort_values("date").reset_index(drop=True)
    return s[["date", "close"]]


def realtime_zscore(slope):
    """Expanding-window z-score: at index i, uses only slope[0..i]."""
    n = len(slope)
    z = np.full(n, np.nan)
    running_sum = 0.0
    running_sq = 0.0
    for i in range(n):
        v = slope[i]
        running_sum += v
        running_sq += v * v
        count = i + 1
        if count < MIN_WARMUP_WEEKS:
            continue
        mean = running_sum / count
        var = running_sq / count - mean * mean
        std = np.sqrt(max(var, 1e-12))
        z[i] = (v - mean) / std if std > 0 else np.nan
    return z


def find_episodes(dates, z, threshold):
    """Group consecutive weeks with z < threshold into episodes; the episode's
    signal date is the FIRST week it crossed below threshold (that's the
    actionable moment, not every week it stays below)."""
    episodes = []
    in_episode = False
    for i, v in enumerate(z):
        below = (not np.isnan(v)) and v < threshold
        if below and not in_episode:
            episodes.append(i)
            in_episode = True
        elif not below:
            in_episode = False
    return episodes


def forward_return(closes, i, horizon):
    if i + horizon >= len(closes):
        return np.nan
    return (closes[i + horizon] / closes[i] - 1) * 100


def trailing_drawdown(closes, i, lookback):
    start = max(0, i - lookback)
    peak = np.max(closes[start:i+1]) if i >= start else closes[i]
    return (closes[i] / peak - 1) * 100


def main():
    rai_df = load_rai()
    bench_df = load_benchmark_close()

    merged = pd.merge(rai_df[["date", "slope", "rai"]], bench_df, on="date", how="inner")
    merged = merged.sort_values("date").reset_index(drop=True)
    if len(merged) < MIN_WARMUP_WEEKS + 10:
        raise SystemExit("Not enough overlapping weekly history to backtest yet - run update_rai.py "
                          "for longer to build up more data, or lower MIN_WARMUP_WEEKS.")

    merged["rai_realtime"] = realtime_zscore(merged["slope"].values)
    closes = merged["close"].values
    dates = merged["date"].values

    print(f"Loaded {len(merged)} overlapping weekly observations "
          f"({pd.Timestamp(dates[0]).date()} -> {pd.Timestamp(dates[-1]).date()}).")
    print(f"Realtime z-score usable from week {MIN_WARMUP_WEEKS} onward "
          f"({pd.Timestamp(dates[MIN_WARMUP_WEEKS]).date() if len(dates) > MIN_WARMUP_WEEKS else 'n/a'}).\n")

    # ---- unconditional baseline: average forward return for ANY week ----
    baseline = {}
    for h in HORIZONS_WEEKS:
        rets = [forward_return(closes, i, h) for i in range(len(closes))]
        rets = [r for r in rets if not np.isnan(r)]
        baseline[h] = {
            "avg": np.mean(rets), "median": np.median(rets),
            "hit_rate": np.mean([r > 0 for r in rets]) * 100,
        }

    episode_rows = []
    summary_rows = []

    for thr in THRESHOLDS:
        ep_idxs = find_episodes(dates, merged["rai_realtime"].values, thr)
        for i in ep_idxs:
            row = {
                "threshold": thr,
                "date": pd.Timestamp(dates[i]).date().isoformat(),
                "rai_realtime": round(merged["rai_realtime"].values[i], 2),
                "rai_full_sample_lookahead": round(merged["rai"].values[i], 2),
                f"{BENCHMARK_CODE}_close": round(closes[i], 2),
                "drawdown_into_signal_pct": round(trailing_drawdown(closes, i, DRAWDOWN_LOOKBACK_WEEKS), 1),
            }
            for h in HORIZONS_WEEKS:
                row[f"fwd_{h}w_pct"] = round(forward_return(closes, i, h), 2) if not np.isnan(forward_return(closes, i, h)) else None
            episode_rows.append(row)

        for h in HORIZONS_WEEKS:
            rets = [forward_return(closes, i, h) for i in ep_idxs]
            rets = [r for r in rets if not np.isnan(r)]
            if rets:
                avg, med = np.mean(rets), np.median(rets)
                hit = np.mean([r > 0 for r in rets]) * 100
            else:
                avg = med = hit = np.nan
            summary_rows.append({
                "threshold": thr, "horizon_weeks": h, "n_episodes": len(ep_idxs),
                "avg_fwd_return_pct": round(avg, 2) if rets else None,
                "median_fwd_return_pct": round(med, 2) if rets else None,
                "hit_rate_pct": round(hit, 1) if rets else None,
                "baseline_avg_fwd_return_pct": round(baseline[h]["avg"], 2),
                "baseline_hit_rate_pct": round(baseline[h]["hit_rate"], 1),
                "edge_vs_baseline_pct": round(avg - baseline[h]["avg"], 2) if rets else None,
            })

    ep_df = pd.DataFrame(episode_rows)
    sum_df = pd.DataFrame(summary_rows)
    ep_df.to_csv("backtest_episodes.csv", index=False)
    sum_df.to_csv("backtest_summary.csv", index=False)

    results_payload = {
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "indexType": "BACKTEST",
        "benchmark": BENCHMARK_CODE,
        "minWarmupWeeks": MIN_WARMUP_WEEKS,
        "sampleStart": pd.Timestamp(dates[0]).date().isoformat(),
        "sampleEnd": pd.Timestamp(dates[-1]).date().isoformat(),
        "horizons": HORIZONS_WEEKS,
        "thresholds": THRESHOLDS,
        "baseline": {str(h): {
            "avg": round(baseline[h]["avg"], 2),
            "median": round(baseline[h]["median"], 2),
            "hitRate": round(baseline[h]["hit_rate"], 1),
        } for h in HORIZONS_WEEKS},
        "summary": summary_rows,
        "episodes": episode_rows,
    }
    with open(RESULTS_JSON, "w") as f:
        json.dump(results_payload, f, indent=2, default=lambda x: None if (isinstance(x, float) and np.isnan(x)) else x)

    print("=" * 78)
    print("SUMMARY: contrarian buy-on-fear vs. unconditional baseline (SPX forward returns)")
    print("=" * 78)
    for thr in THRESHOLDS:
        sub = sum_df[sum_df["threshold"] == thr]
        n_ep = sub["n_episodes"].iloc[0] if len(sub) else 0
        print(f"\nThreshold RAI < {thr}   ({n_ep} distinct fear episodes)")
        if n_ep == 0:
            print("  (no episodes at this threshold in the available history)")
            continue
        print(f"  {'Horizon':<10}{'Avg fwd ret':>14}{'Median':>10}{'Hit rate':>11}{'Baseline avg':>15}{'Edge':>9}")
        for _, r in sub.iterrows():
            print(f"  {str(r['horizon_weeks'])+'w':<10}"
                  f"{r['avg_fwd_return_pct']:>13.2f}%"
                  f"{r['median_fwd_return_pct']:>9.2f}%"
                  f"{r['hit_rate_pct']:>10.1f}%"
                  f"{r['baseline_avg_fwd_return_pct']:>14.2f}%"
                  f"{r['edge_vs_baseline_pct']:>+8.2f}%")

    print(f"\nDetail on every episode written to backtest_episodes.csv")
    print(f"Full summary table written to backtest_summary.csv")
    print("\nColumns to look at in backtest_episodes.csv:")
    print("  drawdown_into_signal_pct - how far SPX had already fallen from its 52-week high")
    print("                             when the signal fired (confirms these are real fear/selloff periods)")
    print("  fwd_Nw_pct               - what SPX actually did over the following N weeks from that point")
    print("\nCaveat: 'edge_vs_baseline' compares against ALL weeks, not against other reasonable entry")
    print("rules - a positive edge is necessary but not sufficient evidence of real skill. Also note")
    print("distinct episodes are few in number (crises are rare by definition), so these stats have")
    print("wide uncertainty - treat as descriptive, not as a validated trading signal.")


if __name__ == "__main__":
    main()
