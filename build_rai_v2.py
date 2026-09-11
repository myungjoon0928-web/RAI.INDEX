#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RAI v2 - Market Data Collector (STEP 1)
---------------------------------------
현재 단계에서는 RAI v2의 CORE 데이터만 수집합니다.

CORE:
- S&P 500
- VIX
- WTI
- US 10Y

출력:
- rai_v2_market_daily.json

중요:
- 인터넷에서 실제 데이터를 가져옵니다.
- 데이터를 임의로 만들거나 빈 날짜를 임의의 값으로 채우지 않습니다.
- 선택 데이터(DXY/HY/MOVE/Gold/Copper/Breadth)는 다음 단계에서 추가합니다.
"""

import csv
import io
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path("rai_v2_market_daily.json")

# Stooq: S&P 500 일별 지수 데이터
STOOQ_SP500_URL = "https://stooq.com/q/d/l/?s=%5Espx&i=d"

# FRED CSV endpoints
FRED_URLS = {
    "vix": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS",
    "wti": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILWTICO",
    "us10y": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
}

START_DATE = "1990-01-01"


def download_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RAI-V2-Market-Risk-Engine/1.0"
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8-sig")


def parse_float(value):
    if value is None:
        return None

    value = str(value).strip()

    if value == "" or value in {".", "NA", "N/A", "null", "None"}:
        return None

    try:
        x = float(value)
        if x != x:  # NaN
            return None
        return x
    except ValueError:
        return None


def normalize_date(value: str):
    value = value.strip()

    # YYYY-MM-DD
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]

    # YYYYMMDD
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"

    return None


def parse_stooq_sp500(text: str):
    reader = csv.DictReader(io.StringIO(text))
    result = {}

    for row in reader:
        date = normalize_date(row.get("Date", ""))
        close = parse_float(row.get("Close"))

        if date and close is not None and date >= START_DATE:
            result[date] = close

    return result


def parse_fred(text: str):
    reader = csv.DictReader(io.StringIO(text))
    result = {}

    for row in reader:
        date = normalize_date(row.get("observation_date", ""))
        if not date:
            # 일부 CSV 형식 대응
            date = normalize_date(row.get("DATE", ""))

        if not date:
            continue

        value = None
        for key in row:
            if key.lower() not in {"observation_date", "date"}:
                value = parse_float(row[key])
                break

        if date >= START_DATE:
            result[date] = value

    return result


def collect():
    print("=" * 70)
    print("RAI v2 Market Data Collector")
    print("=" * 70)

    print("\n[1/4] S&P 500 다운로드...")
    sp500 = parse_stooq_sp500(download_text(STOOQ_SP500_URL))
    print(f"      {len(sp500):,} observations")

    time.sleep(0.5)

    print("[2/4] VIX 다운로드...")
    vix = parse_fred(download_text(FRED_URLS["vix"]))
    print(f"      {len(vix):,} observations")

    time.sleep(0.5)

    print("[3/4] WTI 다운로드...")
    wti = parse_fred(download_text(FRED_URLS["wti"]))
    print(f"      {len(wti):,} observations")

    time.sleep(0.5)

    print("[4/4] US 10Y 다운로드...")
    us10y = parse_fred(download_text(FRED_URLS["us10y"]))
    print(f"      {len(us10y):,} observations")

    # 날짜는 S&P500 거래일을 기준으로 잡습니다.
    dates = sorted(sp500.keys())

    rows = []

    for date in dates:
        rows.append({
            "date": date,
            "sp500": sp500.get(date),
            "vix": vix.get(date),
            "wti": wti.get(date),
            "us10y": us10y.get(date),
        })

    # 품질 점검
    required = ["sp500", "vix", "wti", "us10y"]

    coverage = {}
    for field in required:
        valid = sum(
            1 for row in rows
            if row.get(field) is not None
        )
        coverage[field] = {
            "observations": valid,
            "coverage": round(valid / len(rows), 6) if rows else 0,
        }

    # 너무 적은 데이터면 실패 처리
    if len(rows) < 1000:
        raise RuntimeError(
            f"S&P 500 데이터가 비정상적으로 적습니다: {len(rows)} rows"
        )

    payload = {
        "indexType": "RAI_V2",
        "frequency": "daily",
        "version": "2.0-core",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "start_date_requested": START_DATE,
        "source": {
            "sp500": "Stooq",
            "vix": "FRED VIXCLS / CBOE",
            "wti": "FRED DCOILWTICO / EIA",
            "us10y": "FRED DGS10 / Board of Governors",
        },
        "notes": [
            "S&P 500 거래일을 기준으로 병합했습니다.",
            "결측치는 임의의 값으로 채우지 않았습니다.",
            "DXY/HY/MOVE/Gold/Copper/Breadth는 다음 단계에서 추가합니다.",
            "이 파일은 RAI v2 Historical Risk Engine의 CORE 데이터 입력입니다.",
        ],
        "data_quality": {
            "rows": len(rows),
            "first_date": rows[0]["date"] if rows else None,
            "last_date": rows[-1]["date"] if rows else None,
            "coverage": coverage,
        },
        "rows": rows,
    }

    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print("완료")
    print("=" * 70)
    print(f"출력 파일 : {OUTPUT.resolve()}")
    print(f"전체 rows : {len(rows):,}")
    print(f"기간      : {rows[0]['date']} ~ {rows[-1]['date']}")

    print("\n데이터 커버리지")
    for field, info in coverage.items():
        print(
            f"  {field:>6} : "
            f"{info['observations']:,} / {len(rows):,} "
            f"({info['coverage'] * 100:.2f}%)"
        )


if __name__ == "__main__":
    collect()
