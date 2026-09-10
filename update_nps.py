# -*- coding: utf-8 -*-
"""
update_nps.py
--------------
Replicates the logic in NPS_위기인식지수_시뮬레이터.xlsx (국민연금 위기인식지수
시뮬레이터 - 역산 추정 모델, 공식 비공개) using REAL historical market data instead
of a single manually-typed scenario, so it becomes a live time series you can chart.

Run locally:
    pip install yfinance pandas numpy
    python update_nps.py

Data sources (all free, no API key):
  - VIX             : Yahoo Finance ^VIX
  - GSCI YoY        : Yahoo Finance ^SPGSCI (12-month / 52-week % change)
  - EMBI 스프레드    : FRED BAMLEMCBPIOAS (ICE BofA EM Corporate Plus OAS, bp)
                        -- public proxy for the (non-public) JPM EMBI+ sovereign
                        spread; same order of magnitude and historical range.
  - HY 스프레드      : FRED BAMLH0A0HYM2 (ICE BofA US High Yield OAS, bp)
  - FX 변동성        : realized volatility of the ICE US Dollar Index (DXY),
                        21-trading-day rolling, annualized (%) -- a realized-vol
                        proxy for the (non-public) implied FX vol index the
                        original file likely used.

The percentile lookup tables and the 3 model formulas (weighted percentile /
z-score logistic / tail-risk enhanced) below are copied EXACTLY from the
uploaded workbook's TABLES sheet and formulas.

Output: nps_dashboard_data_daily.json and nps_dashboard_data_weekly.json,
in the same folder, ready to be picked up by rai_dashboard.html.
"""
import datetime
import json
import math

import numpy as np
import pandas as pd
import yfinance as yf

N_DAYS_FETCH = 1650      # extra history for the 252-day GSCI YoY warmup
GSCI_YOY_WINDOW = 252
FXVOL_WINDOW = 21

JSON_OUT_DAILY = "nps_dashboard_data_daily.json"
JSON_OUT_WEEKLY = "nps_dashboard_data_weekly.json"

# ---- percentile lookup tables (verbatim from the uploaded file's TABLES sheet) ----
VIX_X = [9, 12, 14, 16, 17, 18, 20, 22, 24, 26, 28, 30, 32, 33, 35, 40, 50, 82]
VIX_Y = [2, 6, 13, 25, 33, 40, 52, 62, 70, 76, 81, 85, 88, 89, 92, 96, 98, 100]
EMBI_X = [150, 200, 230, 255, 270, 285, 300, 315, 330, 355, 390, 430, 480, 550, 650, 800]
EMBI_Y = [5, 14, 24, 32, 40, 46, 53, 60, 65, 71, 77, 83, 88, 93, 97, 100]
GSCI_X = [-50, -30, -20, -10, -5, 0, 3, 5, 8, 12, 18, 25, 35, 50]
GSCI_Y = [3, 8, 12, 20, 28, 38, 46, 53, 62, 70, 78, 85, 91, 96]
HY_X = [240, 280, 310, 340, 370, 395, 420, 445, 470, 510, 560, 620, 700, 900]
HY_Y = [5, 12, 22, 33, 44, 53, 60, 66, 72, 78, 84, 89, 94, 99]
FX_X = [3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 18, 22]
FX_Y = [8, 16, 26, 37, 47, 57, 65, 72, 80, 88, 94, 99]


def percentile(value, X, Y):
    if pd.isna(value):
        return np.nan
    if value <= X[0]:
        return float(Y[0])
    if value >= X[-1]:
        return float(Y[-1])
    idx = 0
    for i in range(len(X) - 1):
        if X[i] <= value < X[i + 1]:
            idx = i
            break
    x0, x1, y0, y1 = X[idx], X[idx + 1], Y[idx], Y[idx + 1]
    return y0 + (value - x0) / (x1 - x0) * (y1 - y0)


def stage_label(score):
    if pd.isna(score):
        return None
    if score >= 80:
        return "위기심각"
    if score >= 60:
        return "위기발단"
    if score >= 40:
        return "주의"
    return "정상"


def indicator_status(pct):
    if pd.isna(pct):
        return None
    if pct >= 80:
        return "위험"
    if pct >= 60:
        return "경계"
    if pct >= 40:
        return "주의"
    return "안정"


def fetch_fred(series_id):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        df = pd.read_csv(url, na_values=".")
        df.columns = ["date", series_id]
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")[series_id].astype(float)
        return df
    except Exception as e:
        print(f"  ! FRED fetch failed for {series_id}: {e}")
        return pd.Series(dtype=float)


def fetch_yahoo_close(ticker, start):
    try:
        d = yf.download(ticker, start=start, interval="1d", auto_adjust=True, progress=False)
        if d.empty:
            return pd.Series(dtype=float)
        return d["Close"].iloc[:, 0] if hasattr(d["Close"], "iloc") and d["Close"].ndim > 1 else d["Close"]
    except Exception as e:
        print(f"  ! Yahoo fetch failed for {ticker}: {e}")
        return pd.Series(dtype=float)


def main():
    start = datetime.date.today() - datetime.timedelta(days=int(N_DAYS_FETCH * 1.6))
    print(f"Downloading NPS crisis-index inputs from {start} ...")

    vix = fetch_yahoo_close("^VIX", start)
    gsci = fetch_yahoo_close("^SPGSCI", start)
    dxy = fetch_yahoo_close("DX-Y.NYB", start)
    if dxy.empty:
        dxy = fetch_yahoo_close("DX=F", start)

    embi_bp = fetch_fred("BAMLEMCBPIOAS") * 100   # % -> bp
    hy_bp = fetch_fred("BAMLH0A0HYM2") * 100       # % -> bp

    if vix.empty or gsci.empty or dxy.empty or embi_bp.empty or hy_bp.empty:
        print("One or more sources returned no data - aborting. Check your internet connection.")
        return

    df = pd.DataFrame({"VIX": vix, "GSCI": gsci, "DXY": dxy})
    df["EMBI_BP"] = embi_bp
    df["HY_BP"] = hy_bp
    df = df.sort_index()
    df[["EMBI_BP", "HY_BP"]] = df[["EMBI_BP", "HY_BP"]].ffill()
    df = df.dropna(subset=["VIX", "GSCI", "DXY"])

    # derived inputs
    df["GSCI_YOY"] = (df["GSCI"] / df["GSCI"].shift(GSCI_YOY_WINDOW) - 1) * 100
    dxy_ret = np.log(df["DXY"] / df["DXY"].shift(1))
    df["FX_VOL"] = dxy_ret.rolling(FXVOL_WINDOW).std() * math.sqrt(252) * 100

    df = df.dropna(subset=["GSCI_YOY", "FX_VOL", "EMBI_BP", "HY_BP"])
    df = df.tail(N_DAYS_FETCH)
    if df.empty:
        print("Not enough overlapping history after warmup windows - aborting.")
        return

    rows = []
    for dt, r in df.iterrows():
        vix_v, embi_v, gsci_v, hy_v, fx_v = r["VIX"], r["EMBI_BP"], r["GSCI_YOY"], r["HY_BP"], r["FX_VOL"]
        p_vix = percentile(vix_v, VIX_X, VIX_Y)
        p_embi = percentile(embi_v, EMBI_X, EMBI_Y)
        p_gsci = percentile(gsci_v, GSCI_X, GSCI_Y)
        p_hy = percentile(hy_v, HY_X, HY_Y)
        p_fx = percentile(fx_v, FX_X, FX_Y)

        model_a = min(99, max(1, p_vix * 0.40 + p_embi * 0.25 + p_gsci * 0.15 + p_hy * 0.15 + p_fx * 0.05))

        z = (((vix_v - 18.5) / 8.2) * 0.35 + ((embi_v - 310) / 95) * 0.25 +
             ((gsci_v - 3) / 18) * 0.15 + ((hy_v - 400) / 110) * 0.2 +
             ((fx_v - 7.5) / 3.8) * 0.05) - 0.4
        model_b = min(99, max(1, round(100 / (1 + math.exp(-2.5 * z)), 1)))

        tail = (max(p_vix - 80, 0) * 0.15 * 0.38 + max(p_embi - 80, 0) * 0.15 * 0.25 +
                max(p_gsci - 80, 0) * 0.15 * 0.15 + max(p_hy - 80, 0) * 0.15 * 0.17 +
                max(p_fx - 80, 0) * 0.15 * 0.05)
        model_c = min(99, max(1, (p_vix * 0.38 + p_embi * 0.25 + p_gsci * 0.15 + p_hy * 0.17 + p_fx * 0.05) + tail))

        avg = round((model_a + model_b + model_c) / 3, 1)

        rows.append({
            "date": dt.date().isoformat(),
            "indicators": {
                "VIX": {"value": round(float(vix_v), 2), "pct": round(p_vix, 1), "status": indicator_status(p_vix)},
                "EMBI": {"value": round(float(embi_v), 1), "pct": round(p_embi, 1), "status": indicator_status(p_embi)},
                "GSCI_YOY": {"value": round(float(gsci_v), 2), "pct": round(p_gsci, 1), "status": indicator_status(p_gsci)},
                "HY": {"value": round(float(hy_v), 1), "pct": round(p_hy, 1), "status": indicator_status(p_hy)},
                "FX_VOL": {"value": round(float(fx_v), 2), "pct": round(p_fx, 1), "status": indicator_status(p_fx)},
            },
            "modelA": round(model_a, 1),
            "modelB": model_b,
            "modelC": round(model_c, 1),
            "score": avg,
            "stage": stage_label(avg),
        })

    payload_daily = {
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "indexType": "NPS",
        "frequency": "daily",
        "series": rows,
    }
    with open(JSON_OUT_DAILY, "w") as f:
        json.dump(payload_daily, f, indent=2)
    print(f"Daily NPS data written: {JSON_OUT_DAILY} ({len(rows)} points).")

    # weekly = last available reading of each ISO week
    wk = pd.DataFrame(rows)
    wk["date_dt"] = pd.to_datetime(wk["date"])
    wk["yw"] = wk["date_dt"].dt.strftime("%G-W%V")
    weekly_rows = wk.groupby("yw", as_index=False).last().sort_values("date_dt")
    weekly_rows = weekly_rows.drop(columns=["yw", "date_dt"]).to_dict("records")

    payload_weekly = {
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "indexType": "NPS",
        "frequency": "weekly",
        "series": weekly_rows,
    }
    with open(JSON_OUT_WEEKLY, "w") as f:
        json.dump(payload_weekly, f, indent=2)
    print(f"Weekly NPS data written: {JSON_OUT_WEEKLY} ({len(weekly_rows)} points).")
    print("Open/refresh rai_dashboard.html (via run_dashboard.bat) to see it under the NPS toggle.")


if __name__ == "__main__":
    main()
