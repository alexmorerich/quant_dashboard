"""Download and normalize the four-asset research universe.

The pipeline intentionally keeps source metadata next to the return files.  The
long Treasury series is a duration-based total-return proxy built from the
30-year constant-maturity yield; it is never presented as a canonical Treasury
total-return index.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd
import requests


ASSETS = ["SP500", "GOLD", "LONG_TREASURY", "CASH"]
FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
GOLD_URL = "https://raw.githubusercontent.com/datasets/gold-prices/main/data/monthly.csv"
FRED_SERIES = {
    "SP500": "SP500",
    "LONG_TREASURY": "DGS30",
    "CASH": "DTB3",
}
SHILLER_URL = "https://posix4e.github.io/shiller_wrapper_data/data/stock_market_data.json"
TREASURY_DURATION = 18.0


@dataclass
class AssetMetadata:
    instrument: str
    asset_class: str
    data_type: str
    source: str
    start_date: str
    end_date: str
    frequency: str
    currency: str
    return_type: str
    proxy_status: str
    retrieved_at: str
    notes: str


def _retrieved_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _get(url: str) -> requests.Response:
    response = requests.get(url, headers={"User-Agent": "quant-allocation-research/1.0"}, timeout=60)
    response.raise_for_status()
    return response


def _fred(series_id: str) -> pd.Series:
    raw = _get(FRED_BASE.format(series_id)).text
    frame = pd.read_csv(pd.io.common.StringIO(raw))
    frame.columns = ["date", "value"]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"].replace(".", np.nan), errors="coerce")
    frame = frame.dropna(subset=["date", "value"]).drop_duplicates("date").sort_values("date")
    return frame.set_index("date")["value"]


def _shiller_monthly() -> pd.DataFrame:
    payload = _get(SHILLER_URL).json()
    frame = pd.DataFrame(payload["data"])
    year = pd.to_numeric(frame["year"], errors="coerce")
    month = pd.to_numeric(frame["month"], errors="coerce")
    valid = year.between(1800, 2200) & month.between(1, 12)
    frame = frame.loc[valid].copy()
    frame["date"] = pd.to_datetime(
        {"year": year.loc[valid].astype(int).to_numpy(), "month": month.loc[valid].astype(int).to_numpy(), "day": 1},
        errors="coerce",
    )
    frame["price"] = pd.to_numeric(frame["sp500"], errors="coerce")
    frame["dividend"] = pd.to_numeric(frame["dividend"], errors="coerce")
    frame = frame.dropna(subset=["date", "price", "dividend"]).sort_values("date")
    frame = frame.drop_duplicates("date", keep="last").set_index("date")
    frame["dividend_yield_annual"] = frame["dividend"] / frame["price"]
    frame["total_return"] = frame["price"].pct_change() + frame["dividend_yield_annual"] / 12.0
    return frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["total_return"])


def _gold_monthly() -> pd.Series:
    frame = pd.read_csv(pd.io.common.StringIO(_get(GOLD_URL).text))
    frame["date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["Price"], errors="coerce")
    frame = frame.dropna(subset=["date", "value"]).sort_values("date")
    frame["date"] = frame["date"].dt.to_period("M").dt.to_timestamp("M")
    frame = frame.drop_duplicates("date", keep="last")
    return frame.set_index("date")["value"]


def _gold_daily() -> pd.Series:
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?period1=0&period2=4102444800&interval=1d&events=history"
    payload = _get(url).json()["chart"]["result"][0]
    timestamps = pd.to_datetime(payload.get("timestamp", []), unit="s", utc=True).tz_convert(None).normalize()
    quotes = payload.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    frame = pd.DataFrame({"date": timestamps, "value": pd.to_numeric(quotes, errors="coerce")})
    frame = frame.dropna(subset=["date", "value"]).drop_duplicates("date").sort_values("date")
    return frame.set_index("date")["value"]


def _clean_returns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame[ASSETS].replace([np.inf, -np.inf], np.nan).dropna(how="any")
    frame.index = pd.to_datetime(frame.index).normalize()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame


def download_dataset(data_dir: Path, force: bool = False) -> Dict[str, object]:
    """Download FRED/Shiller data and write daily/monthly return panels."""

    data_dir.mkdir(parents=True, exist_ok=True)
    daily_path = data_dir / "returns_daily.csv"
    monthly_path = data_dir / "returns_monthly.csv"
    metadata_path = data_dir / "metadata.json"
    if not force and daily_path.exists() and monthly_path.exists() and metadata_path.exists():
        return json.loads(metadata_path.read_text())

    retrieved_at = _retrieved_at()
    raw = {asset: _fred(series_id) for asset, series_id in FRED_SERIES.items()}
    gold_monthly = _gold_monthly()
    try:
        raw["GOLD"] = _gold_daily()
        gold_source = "Yahoo Finance GC=F daily futures proxy; datasets/gold-prices monthly source"
        gold_instrument = "GC=F / monthly gold price"
        gold_proxy_status = "futures_and_spot_price_proxies"
    except Exception:
        raw["GOLD"] = gold_monthly
        gold_source = "datasets/gold-prices monthly source"
        gold_instrument = "monthly gold price"
        gold_proxy_status = "monthly_spot_price_proxy"
    shiller = _shiller_monthly()

    prices = pd.concat([raw["SP500"].rename("SP500"), raw["GOLD"].rename("GOLD")], axis=1)
    rates = pd.concat(
        [raw["LONG_TREASURY"].rename("LONG_TREASURY"), raw["CASH"].rename("CASH")], axis=1
    )

    # Construct a transparent daily proxy panel.  SP500 includes a monthly
    # dividend-yield accrual; the long-bond leg uses duration * yield change +
    # carry; cash compounds the observed 3-month T-bill rate.
    daily = prices.join(rates, how="inner").dropna()
    sp_price_return = daily["SP500"].pct_change().fillna(0.0)
    monthly_dividend_yield = shiller["dividend_yield_annual"].resample("ME").last()
    daily_dividend_yield = monthly_dividend_yield.reindex(daily.index, method="ffill").fillna(0.0)
    trading_days = 252.0
    sp_total_return = (1.0 + sp_price_return) * (1.0 + daily_dividend_yield / trading_days) - 1.0

    gold_return = daily["GOLD"].pct_change().fillna(0.0)
    treasury_yield = daily["LONG_TREASURY"]
    yield_change = treasury_yield.shift(1) - treasury_yield
    day_count = pd.Series(daily.index.to_series().diff().dt.days.fillna(1.0).to_numpy(), index=daily.index)
    treasury_return = TREASURY_DURATION * yield_change / 100.0 + treasury_yield / 100.0 * day_count / 365.25
    treasury_return = treasury_return.fillna(0.0)

    cash_rate = daily["CASH"].clip(lower=0.0)
    cash_return = (1.0 + cash_rate / 100.0) ** (day_count / 365.25) - 1.0

    daily_returns = _clean_returns(
        pd.DataFrame(
            {"SP500": sp_total_return, "GOLD": gold_return, "LONG_TREASURY": treasury_return, "CASH": cash_return},
            index=daily.index,
        )
    )

    # Strategic monthly returns are independently constructed from the long
    # history inputs. They do not inherit the shorter daily gold history.
    shiller_return = shiller["total_return"].copy()
    shiller_return.index = shiller_return.index.to_period("M").to_timestamp("M")
    gold_return_monthly = gold_monthly.pct_change()
    gold_return_monthly.index = gold_return_monthly.index.to_period("M").to_timestamp("M")
    treasury_monthly = raw["LONG_TREASURY"].resample("ME").last()
    cash_monthly = raw["CASH"].resample("ME").last()
    treasury_monthly_return = TREASURY_DURATION * (treasury_monthly.shift(1) - treasury_monthly) / 100.0 + treasury_monthly / 100.0 / 12.0
    cash_monthly_return = (1.0 + cash_monthly.clip(lower=0.0) / 100.0) ** (1.0 / 12.0) - 1.0
    monthly_returns = _clean_returns(pd.concat({"SP500": shiller_return, "GOLD": gold_return_monthly, "LONG_TREASURY": treasury_monthly_return, "CASH": cash_monthly_return}, axis=1))

    asset_metadata = {
        "SP500": AssetMetadata(
            "SP500 price index + Shiller dividend series", "Equity", "price + dividend", "FRED SP500 + Robert Shiller data",
            str(daily_returns.index.min().date()), str(daily_returns.index.max().date()), "daily/monthly", "USD",
            "total_return_approximation", "price_index_with_dividend_accrual", retrieved_at,
            "Monthly total return uses Shiller price and trailing dividend; daily dividend is apportioned across trading days.",
        ),
        "GOLD": AssetMetadata(
            gold_instrument, "Gold", "daily futures / monthly spot price", gold_source,
            str(raw["GOLD"].dropna().index.min().date()), str(raw["GOLD"].dropna().index.max().date()), "daily/monthly", "USD",
            "price_return", gold_proxy_status, retrieved_at, "USD gold price proxy; daily series is GC=F futures and monthly series is the gold-price dataset; no storage, insurance, or ETF fee adjustment.",
        ),
        "LONG_TREASURY": AssetMetadata(
            "DGS30", "Long Treasury", "30-year constant-maturity yield", "FRED",
            str(raw["LONG_TREASURY"].dropna().index.min().date()), str(raw["LONG_TREASURY"].dropna().index.max().date()), "daily/monthly", "USD",
            "total_return_proxy", "duration_yield_proxy", retrieved_at,
            f"Not a canonical bond total-return index. Approximation uses duration={TREASURY_DURATION:g}, daily yield changes, and carry.",
        ),
        "CASH": AssetMetadata(
            "DTB3", "Cash / T-Bills", "3-month Treasury bill secondary-market rate", "FRED",
            str(raw["CASH"].dropna().index.min().date()), str(raw["CASH"].dropna().index.max().date()), "daily/monthly", "USD",
            "total_return_proxy", "cash_rate_compounding_proxy", retrieved_at,
            "Risk-free cash leg compounds the observed annualized 3-month T-bill rate between observations.",
        ),
    }

    monthly_returns.to_csv(monthly_path, index_label="date")
    daily_returns.to_csv(daily_path, index_label="date")
    metadata = {
        "retrieved_at": retrieved_at,
        "assets": {key: asdict(value) for key, value in asset_metadata.items()},
        "daily_first_date": str(daily_returns.index.min().date()),
        "daily_last_date": str(daily_returns.index.max().date()),
        "monthly_first_date": str(monthly_returns.index.min().date()),
        "monthly_last_date": str(monthly_returns.index.max().date()),
        "missing_data": {
            "daily_common_panel_dropped_rows": int(len(prices.join(rates, how="inner")) - len(daily_returns)),
            "notes": "The common panel is an inner join. No missing observations are silently forward-filled across assets.",
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2))
    return metadata


def load_returns(data_dir: Path, frequency: str = "monthly") -> pd.DataFrame:
    path = data_dir / f"returns_{frequency}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run: python3 run_research.py --download")
    frame = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    return _clean_returns(frame)


def load_metadata(data_dir: Path) -> Dict[str, object]:
    path = data_dir / "metadata.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run: python3 run_research.py --download")
    return json.loads(path.read_text())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download the four-asset research universe")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(download_dataset(args.data_dir, force=args.force), indent=2))
