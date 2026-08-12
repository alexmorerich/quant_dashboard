# Four-Asset Quant Allocation Research Dashboard

This is a self-contained research module for a four-asset universe: S&P 500,
Gold, U.S. Long Treasury, and U.S. Cash / T-Bills. It is intentionally a
research dashboard, not a portfolio recommendation engine. Every selected
allocation is labeled by its optimizer, sample, and validation status.

## Run

From this directory:

```bash
python3 -m pip install -r requirements.txt
python3 run_research.py --download
python3 -m backend.server
```

Open `http://127.0.0.1:8000`. If the data files already exist, `python3
run_research.py` runs from the cached data. To refresh the source data, use
`--download`.

Run tests with:

```bash
pytest
```

The machine-readable result is written to `research_result.json`.

## Data sources and return construction

The download pipeline uses FRED for `SP500`, `GOLDAMGBD228NLBM` (gold fixing),
`DGS30` (30-year constant-maturity Treasury yield), and `DTB3` (3-month
Treasury bill rate). S&P 500 monthly total return uses the existing Shiller
price and trailing dividend data source where available. Gold is a spot-price
return proxy. Cash compounds the annualized T-bill rate. The long Treasury leg
is an explicit duration/yield total-return proxy:

`bond return ≈ duration × (prior yield − current yield) + current yield carry`

It is not a canonical bond total-return index and it is not TLT. Metadata in
`data/metadata.json` records the instrument, source, dates, frequency, return
type, proxy status, and limitations. The common panel is an inner join; missing
history is reported rather than silently filled.

## Research methodology

- Windows: 10Y, 20Y, 30Y, 40Y, 50Y. The primary window defaults to 30Y and is a
  configuration parameter.
- Daily data supports volatility, drawdown, CVaR, rolling correlation, and
  portfolio construction. Monthly data supports strategic optimization,
  regimes, and long-horizon stability.
- Constraints are long-only, fully invested, and configurable per asset. The
  default maximum is 80% per asset.
- Optimizers: Equal Weight, Historical Max Sharpe, Minimum Volatility, Maximum
  Sortino, Maximum Calmar, Risk-Balanced risk contribution, and Robust Quant.
- Robust Quant score: configurable weighted Sharpe + Sortino + Calmar minus
  drawdown, CVaR, and turnover penalties. The UI and JSON output expose the
  coefficients.

## Walk-forward and robustness

The default walk-forward model uses 10 years of training, a 12-month test
window, and monthly rebalancing. Each optimizer sees only its training sample;
OOS metrics are calculated on subsequent test blocks. The result keeps IS and
OOS fields separate.

Weight stability is computed across 10Y–50Y endpoint windows as:

`clip(1 − mean(asset weight standard deviation) / 0.25, 0, 1)`

The overall robustness score is a weighted sum of normalized OOS Sharpe,
Sortino, Calmar, drawdown, CVaR, turnover, window stability, and regime
stability components. The default coefficients sum to 1 and can be changed in
the engine configuration.

Regimes are labeled **Rule-Based Macro Regime**. They use trailing equity,
gold, and bond momentum plus equity volatility. Labels are shifted one month
before attribution to prevent current-month leakage. This is diagnostic, not an
objectively discovered regime model; the architecture can later replace it
with an HMM, Markov switching, clustering, or machine learning classifier.

The module also calculates covariance/correlation, 36M/60M/120M rolling
correlations, rolling beta, PCA eigenvalues/eigenvectors, crisis windows,
transaction-cost sensitivity at 0/5/10/25/50 bps, and a seeded block bootstrap
for Sharpe/CAGR/MaxDD distributions.

## Caching and reproducibility

Raw return panels and metadata are cached in `data/`. API research results are
cached under `cache/` with a SHA-256 key over research parameters and the data
retrieval timestamp. `research_result.json` is reproducible for the same data,
configuration, and seed. The bootstrap default is 1,000 simulations; tests use
a smaller count for speed.

## Known limitations

Historical availability is the intersection of the four source series. A
requested 50Y view may be unavailable or shorter than a nominal 50 years. The
long Treasury return is a duration approximation, gold excludes carrying and
storage costs, and the S&P daily dividend allocation is an approximation
between monthly observations. No statistical significance claims are made.
