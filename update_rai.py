# -*- coding: utf-8 -*-
"""
update_rai.py
--------------
Run this locally whenever you want to refresh the Risk Appetite Index
(schedule it weekly with cron / Task Scheduler for a "self-updating" setup):

    pip install yfinance openpyxl pandas numpy
    python update_rai.py

What it does, in order:
  1. Downloads ~5.2 years of WEEKLY closes for the RAI basket from Yahoo Finance,
     writes them into RAI_CreditSuisse_Style.xlsx (Raw_Prices tab), and computes
     the weekly RAI into rai_dashboard_data_weekly.json.
  2. Downloads ~5.2 years of DAILY closes and computes a separate daily-resolution
     RAI (65-trading-day rolling window, the daily equivalent of 13 weeks) into
     rai_dashboard_data_daily.json. This one is dashboard-only (not written to Excel,
     since a 1,300-row daily formula grid would make the workbook unwieldy).
  3. Upload whichever JSON(s) you want into the RAI Dashboard page - it has a
     주간/일간 (weekly/daily) toggle and keeps both loaded at once.

Keep this script, the .xlsx, and the dashboard .html in the same folder.
"""
import datetime
import json
import os

import numpy as np
import openpyxl
import pandas as pd
import yfinance as yf

WORKBOOK = "RAI_CreditSuisse_Style.xlsx"
JSON_OUT_WEEKLY = "rai_dashboard_data_weekly.json"
JSON_OUT_DAILY = "rai_dashboard_data_daily.json"
JSON_PRICES_WEEKLY = "rai_asset_prices_weekly.json"
JSON_PRICES_DAILY = "rai_asset_prices_daily.json"
N_WEEKS = 270
N_DAYS = 1300          # ~5.2 years of trading days
WINDOW_WEEKS = 13      # ~1 quarter
WINDOW_DAYS = 65       # 13 weeks x 5 trading days = same ~1 quarter, daily-resolution
RF_TICKER = "^IRX"

ASSETS = [
    ("SPX", "^GSPC", "S&P 500", "Risky-Equity"),
    ("NDX", "^NDX", "Nasdaq 100", "Risky-Equity"),
    ("RUT", "^RUT", "Russell 2000", "Risky-Equity"),
    ("FTSE", "^FTSE", "FTSE 100", "Risky-Equity"),
    ("DAX", "^GDAXI", "DAX", "Risky-Equity"),
    ("ESTX50", "^STOXX50E", "Euro Stoxx 50", "Risky-Equity"),
    ("N225", "^N225", "Nikkei 225", "Risky-Equity"),
    ("HSI", "^HSI", "Hang Seng", "Risky-Equity"),
    ("KOSPI", "^KS11", "KOSPI", "Risky-Equity"),
    ("EEM", "EEM", "MSCI EM ETF", "Risky-Equity"),
    ("HYG", "HYG", "US High Yield Bond ETF", "Risky-Credit"),
    ("EMB", "EMB", "EM USD Bond ETF", "Risky-Credit"),
    ("BKLN", "BKLN", "Senior Bank Loan ETF", "Risky-Credit"),
    ("WTI", "CL=F", "WTI Crude Oil", "Risky-Commodity"),
    ("COPPER", "HG=F", "Copper Futures", "Risky-Commodity"),
    ("AUDUSD", "AUDUSD=X", "AUD/USD", "Risky-FX"),
    ("NZDUSD", "NZDUSD=X", "NZD/USD", "Risky-FX"),
    ("BTC", "BTC-USD", "Bitcoin", "Risky-Alt"),
    ("IEF", "IEF", "US 7-10Y Treasury ETF", "Safe-Haven"),
    ("TLT", "TLT", "US 20Y+ Treasury ETF", "Safe-Haven"),
    ("GOLD", "GC=F", "Gold Futures", "Safe-Haven"),
    ("JPY", "JPY=X", "USD/JPY", "Safe-Haven"),
    ("CHF", "CHF=X", "USD/CHF", "Safe-Haven"),
    ("LQD", "LQD", "US IG Corporate Bond ETF", "Safe-Haven"),
]
INVERT = {"JPY", "CHF"}  # safe-haven direction: return = -(fx % change)
ALL_TICKERS = [RF_TICKER] + [t for _, t, _, _ in ASSETS]


def fetch_history(tickers, interval, periods, lookback_extra):
    """interval: '1wk' or '1d'. periods: how many rows to keep after fetch.
    Returns (close_df, raw) - close_df is Close-only (used for RAI calcs),
    raw is the full per-ticker OHLC frame (used for the candlestick export)."""
    if interval == "1wk":
        start = datetime.date.today() - datetime.timedelta(weeks=periods + lookback_extra)
    else:
        start = datetime.date.today() - datetime.timedelta(days=int((periods + lookback_extra) * 1.6))
    print(f"Downloading {len(tickers)} tickers ({interval}) from {start} ...")
    raw = yf.download(tickers, start=start, interval=interval,
                       auto_adjust=True, group_by="ticker", progress=False)
    closes = {}
    for t in tickers:
        try:
            s = raw[t]["Close"] if len(tickers) > 1 else raw["Close"]
            closes[t] = s.dropna()
        except Exception as e:
            print(f"  ! failed for {t}: {e}")
    df = pd.DataFrame(closes).ffill().dropna(how="all")
    return df.tail(periods), raw


def write_excel(df):
    wb = openpyxl.load_workbook(WORKBOOK)
    ws = wb["Raw_Prices"]
    for row in ws.iter_rows(min_row=4, max_row=ws.max_row):
        for cell in row:
            cell.value = None
    start_row = 4
    for i, (date, vals) in enumerate(df.iterrows()):
        r = start_row + i
        ws.cell(row=r, column=1, value=date.date()).number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=2, value=float(vals.get(RF_TICKER)) if pd.notna(vals.get(RF_TICKER)) else None)
        for k, (code, tkr, _, _) in enumerate(ASSETS):
            v = vals.get(tkr)
            ws.cell(row=r, column=3 + k, value=float(v) if pd.notna(v) else None)
    wb.save(WORKBOOK)
    print(f"Excel updated: {WORKBOOK} ({len(df)} weekly rows).")


def compute_rai(df, window, periods_per_year, freq_label, window_label):
    rf_per = df[RF_TICKER] / 100 / periods_per_year
    rets = pd.DataFrame(index=df.index)
    for code, tkr, _, _ in ASSETS:
        if code in INVERT:
            rets[code] = -(df[tkr] / df[tkr].shift(1) - 1)
        else:
            rets[code] = df[tkr] / df[tkr].shift(1) - 1

    codes = [c for c, _, _, _ in ASSETS]
    exc_ann = pd.DataFrame(index=df.index, columns=codes, dtype=float)
    vol_ann = pd.DataFrame(index=df.index, columns=codes, dtype=float)
    for code in codes:
        exc = rets[code] - rf_per
        exc_ann[code] = exc.rolling(window).mean() * periods_per_year
        vol_ann[code] = rets[code].rolling(window).std() * np.sqrt(periods_per_year)

    slopes = []
    for dt in df.index:
        y = exc_ann.loc[dt].values.astype(float)
        x = vol_ann.loc[dt].values.astype(float)
        mask = ~(np.isnan(x) | np.isnan(y))
        if mask.sum() < 5:
            slopes.append(np.nan)
            continue
        xv, yv = x[mask], y[mask]
        xbar, ybar = xv.mean(), yv.mean()
        denom = np.sum((xv - xbar) ** 2)
        slope = np.sum((xv - xbar) * (yv - ybar)) / denom if denom != 0 else np.nan
        slopes.append(slope)
    slope_series = pd.Series(slopes, index=df.index)

    valid = slope_series.dropna()
    mu, sigma = valid.mean(), valid.std()
    rai = (slope_series - mu) / sigma

    def regime(z):
        if pd.isna(z):
            return None
        if z > 1:
            return "Risk-Seeking"
        if z < -1:
            return "Risk-Aversion"
        return "Neutral"

    out_rows = []
    for dt in df.index:
        z = rai.loc[dt]
        if pd.isna(z):
            continue
        out_rows.append({
            "date": dt.date().isoformat(),
            "slope": round(float(slope_series.loc[dt]), 5),
            "rai": round(float(z), 3),
            "regime": regime(z),
        })

    return {
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "indexType": "RAI",
        "frequency": freq_label,
        "windowLabel": window_label,
        "basketSize": len(ASSETS),
        "assets": [{"code": c, "name": n, "class": cl} for c, _, n, cl in ASSETS],
        "series": out_rows,
    }


def export_asset_prices(raw, freq_label, max_points):
    """Per-asset OHLC candle history + latest close/change, for the dashboard's
    clickable basket panel (shows current values, opens a candlestick chart)."""
    multi = isinstance(raw.columns, pd.MultiIndex)
    assets_out = []
    for code, tkr, name, cls in ASSETS:
        try:
            sub = raw[tkr] if multi else raw
            sub = sub[["Open", "High", "Low", "Close"]].dropna()
        except Exception:
            continue
        if sub.empty:
            continue
        sub = sub.tail(max_points)
        series = [{
            "date": dt.date().isoformat(),
            "open": round(float(r["Open"]), 4),
            "high": round(float(r["High"]), 4),
            "low": round(float(r["Low"]), 4),
            "close": round(float(r["Close"]), 4),
        } for dt, r in sub.iterrows()]
        latest = series[-1]["close"]
        prev = series[-2]["close"] if len(series) > 1 else latest
        change_pct = ((latest / prev) - 1) * 100 if prev else 0.0
        assets_out.append({
            "code": code, "name": name, "class": cls,
            "latest": round(latest, 4),
            "latestDate": series[-1]["date"],
            "changePct": round(change_pct, 2),
            "series": series,
        })
    return {
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "indexType": "ASSET_PRICES",
        "frequency": freq_label,
        "assets": assets_out,
    }


def main():
    # ---- weekly (drives the Excel model too) ----
    df_w, raw_w = fetch_history(ALL_TICKERS, "1wk", N_WEEKS, lookback_extra=8)
    if df_w.empty:
        print("No weekly data downloaded - check your internet connection / tickers.")
        return
    if os.path.exists(WORKBOOK):
        write_excel(df_w)
    else:
        print(f"({WORKBOOK} not found here - skipping Excel update, JSON-only mode)")
    payload_w = compute_rai(df_w, WINDOW_WEEKS, 52, "weekly", f"{WINDOW_WEEKS}-week")
    with open(JSON_OUT_WEEKLY, "w") as f:
        json.dump(payload_w, f, indent=2)
    print(f"Excel + weekly dashboard data written: {JSON_OUT_WEEKLY} ({len(payload_w['series'])} points).")

    prices_w = export_asset_prices(raw_w, "weekly", N_WEEKS)
    with open(JSON_PRICES_WEEKLY, "w") as f:
        json.dump(prices_w, f, indent=2)
    print(f"Weekly asset price data written: {JSON_PRICES_WEEKLY} ({len(prices_w['assets'])} assets).")

    # ---- daily (dashboard only - not written into the Excel model) ----
    df_d, raw_d = fetch_history(ALL_TICKERS, "1d", N_DAYS, lookback_extra=20)
    if df_d.empty:
        print("No daily data downloaded - skipping daily dashboard file.")
    else:
        payload_d = compute_rai(df_d, WINDOW_DAYS, 252, "daily", f"{WINDOW_DAYS}-day")
        with open(JSON_OUT_DAILY, "w") as f:
            json.dump(payload_d, f, indent=2)
        print(f"Daily dashboard data written: {JSON_OUT_DAILY} ({len(payload_d['series'])} points).")

        prices_d = export_asset_prices(raw_d, "daily", N_DAYS)
        with open(JSON_PRICES_DAILY, "w") as f:
            json.dump(prices_d, f, indent=2)
        print(f"Daily asset price data written: {JSON_PRICES_DAILY} ({len(prices_d['assets'])} assets).")

    print("Upload the rai_dashboard_data_*.json and rai_asset_prices_*.json files")
    print("into the RAI Dashboard page (or just use run_dashboard.bat) to refresh it.")


if __name__ == "__main__":
    main()
