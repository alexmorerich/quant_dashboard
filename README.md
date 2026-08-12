# Four-Asset Quant Allocation Research Dashboard

An institutional-style research module for studying allocation among four asset
classes:

1. S&P 500
2. Gold
3. U.S. Long Treasury
4. U.S. Cash / T-Bills

The project is designed to answer **whether an allocation is robust enough to
be useful**, not to manufacture one universally “optimal” portfolio. Every
result distinguishes historical in-sample performance, walk-forward
out-of-sample performance, window stability, regime behavior, and data quality.

> Research warning: this project is for research and education. It is not
> investment advice. Historical results are not forecasts.

## Contents

- [What is included](#what-is-included)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Run the local dashboard](#run-the-local-dashboard)
- [Run the research pipeline](#run-the-research-pipeline)
- [Data sources and limitations](#data-sources-and-limitations)
- [Research methodology](#research-methodology)
- [Configuration](#configuration)
- [API](#api)
- [Deploy to Cloudflare Workers](#deploy-to-cloudflare-workers)
- [Update the Cloudflare snapshot](#update-the-cloudflare-snapshot)
- [Testing and verification](#testing-and-verification)
- [Repository map](#repository-map)
- [Known limitations](#known-limitations)

## What is included

The research engine implements:

- Daily and monthly return panels.
- Five research windows: 10Y, 20Y, 30Y, 40Y, and 50Y.
- Configurable primary research window; default: 30Y.
- Long-only, no-leverage constraints with configurable asset caps.
- Equal Weight, Maximum Sharpe, Minimum Volatility, Maximum Sortino,
  Maximum Calmar, Risk-Balanced, and Robust Quant optimizers.
- CAGR, annualized return, volatility, maximum drawdown, CVaR 95%, VaR 95%,
  downside deviation, Sharpe, Sortino, Calmar, turnover, best/worst year,
  negative years, and recovery time.
- Rolling walk-forward testing with a 10Y training window, 12M test window,
  and monthly rebalancing by default.
- Allocation stability across 10Y–50Y windows and rolling allocation history.
- Rolling correlation, covariance, beta, PCA eigenvalues/eigenvectors, and
  minimum-variance eigen-direction.
- Transparent rule-based macro regimes: Risk-On, Risk-Off, Inflation,
  Deflation, Crisis, and Neutral.
- Crisis analysis for 1987, dot-com, GFC, COVID, and the 2022 rates shock.
- Transaction-cost sensitivity at 0, 5, 10, 25, and 50 bps.
- Seeded block bootstrap robustness analysis.
- Cache-keyed research API results and a machine-readable JSON output.
- A responsive dashboard with allocation, risk, robustness, coupling, regime,
  stress, and validation views.

## Architecture

```text
FRED + Shiller + gold-price sources
                │
                ▼
       backend/data_pipeline.py
                │  data/returns_*.csv + data/metadata.json
                ▼
       backend/research_engine.py
                │  research_result.json + cache/*.json
                ▼
       backend/server.py ─────── local dynamic dashboard
                │
                └── cloudflare/build.py
                        │
                        ▼
                Cloudflare Worker + Static Assets
                cloudflare/src/index.js
```

The Python engine remains the source of truth for research calculations. The
Cloudflare deployment is a serverless **precomputed snapshot**: it serves the
generated result at the edge and does not run pandas, NumPy, SciPy, or the
Python optimizer inside a Worker. This keeps the deployment small and makes the
live result reproducible.

## Quick start

### Requirements

- Python 3.10+; the current development environment uses Python 3.13.
- Node.js 22+ and npm.
- Network access for downloading fresh market data.
- A Cloudflare account only if deploying the edge snapshot.

### Install dependencies

```bash
cd quant_dashboard

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

npm install
```

The project installs Wrangler locally as a dev dependency. Cloudflare
recommends using a project-local Wrangler executable rather than relying on a
global installation; use `npx wrangler ...` or the npm scripts below.

## Run the local dashboard

The local dashboard runs the full Python research API dynamically.

```bash
python3 -m backend.server
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The local server exposes:

- `GET /` — dashboard UI.
- `GET /api/health` — server health and data readiness.
- `GET /api/config` — default research configuration.
- `GET /api/research` — cached or newly calculated research result.

Example:

```bash
curl 'http://127.0.0.1:8000/api/research?research_window=30Y&optimizer=robust_quant&frequency=monthly&rebalance_frequency=monthly&transaction_cost_bps=10'
```

The first request for a new parameter combination can take several seconds.
Subsequent identical requests use the cache under `cache/`.

## Run the research pipeline

### Use the existing cached data

```bash
python3 run_research.py
```

This reads `data/returns_daily.csv`, `data/returns_monthly.csv`, and
`data/metadata.json`, then writes `research_result.json`.

### Refresh source data

```bash
python3 run_research.py --download
```

The download step uses the configured sources, writes the normalized panels,
and records retrieval timestamps and data limitations in `data/metadata.json`.
No missing history is silently fabricated.

### Select an optimizer

```bash
python3 run_research.py --optimizer max_sharpe
python3 run_research.py --optimizer min_volatility
python3 run_research.py --optimizer robust_quant
```

The default run is `robust_quant`, with a 30Y research window, monthly data,
monthly rebalancing, 10 bps transaction costs, and a 1,000-simulation seeded
block bootstrap.

## Data sources and limitations

The current pipeline records source metadata for each asset. The exact source
and proxy status are visible in the dashboard’s **Provenance ledger** and in
`data/metadata.json`.

| Asset | Input | Return construction | Important limitation |
|---|---|---|---|
| S&P 500 | FRED `SP500` plus Robert Shiller monthly price/dividend data | Price return plus dividend accrual | Daily dividend accrual between monthly observations is an approximation |
| Gold | `datasets/gold-prices` monthly series; Yahoo Finance `GC=F` daily futures proxy | Price return | Not an ETF total-return series; excludes storage, insurance, and fee effects |
| Long Treasury | FRED `DGS30` 30-year constant-maturity yield | Duration/yield proxy: `duration × (prior yield − current yield) + carry` | Not TLT and not a canonical Treasury total-return index |
| Cash / T-Bills | FRED `DTB3` 3-month Treasury bill rate | Compounded annualized rate | A cash-rate proxy, not a specific fund or account yield |

The common monthly panel currently starts in 1977, so a requested 50Y window
can be explicitly unavailable. The system reports that state rather than
backfilling an invented history. It also does not claim that an ETF has history
before its actual inception.

## Research methodology

### Portfolio constraints

The default constraints are:

```text
sum(weights) = 1
0 <= weight_i <= 0.80
```

The constraint object is defined in `backend/research_engine.py` and can be
passed through the API configuration without changing the optimization code.
The model does not use leverage or short selling.

### Optimizers

- **Equal Weight** — 25% in each asset.
- **Historical Max Sharpe** — maximizes annualized excess return divided by
  annualized volatility.
- **Minimum Volatility** — minimizes portfolio volatility.
- **Maximum Sortino** — maximizes return relative to downside deviation.
- **Maximum Calmar** — maximizes CAGR relative to maximum drawdown.
- **Risk-Balanced** — minimizes the dispersion of asset risk contributions.
- **Robust Quant Allocation** — configurable weighted Sharpe + Sortino +
  Calmar minus maximum drawdown, CVaR, and turnover penalties.

Robust Quant is not described as the best portfolio. It is a configurable
research score whose coefficients are exposed in the JSON result.

### Walk-forward validation

The default walk-forward process is:

1. Take the previous 10 years of monthly training returns.
2. Fit the selected optimizer using training data only.
3. Hold the resulting weights over the next 12 months.
4. Record in-sample and out-of-sample metrics separately.
5. Roll forward and repeat until the available endpoint.

The test block is never passed into its own optimizer. Regime labels are also
shifted by one month before performance attribution so the current realized
return cannot classify itself.

### Robustness score

The overall robustness score is a weighted sum of normalized components:

```text
OOS Sharpe
OOS Sortino
OOS Calmar
drawdown
CVaR
weight stability
turnover
window stability
regime stability
```

The default coefficients sum to 1.0 and the exact coefficients, component
values, and formula are returned in `research_result.json` and displayed in the
dashboard. Weight stability uses:

```text
clip(1 − mean(asset weight standard deviation) / 0.25, 0, 1)
```

## Configuration

The defaults are defined in `backend/research_engine.py` under
`DEFAULT_CONFIG`. Important parameters include:

```python
{
    "research_window": "30Y",
    "optimizer": "robust_quant",
    "frequency": "monthly",
    "rebalance_frequency": "monthly",
    "training_years": 10,
    "test_months": 12,
    "transaction_cost_bps": 10,
    "monte_carlo_simulations": 1000,
    "seed": 42,
}
```

When adding new configuration fields, include them in the cache key. A result
must never be reused when a relevant research parameter changed.

## API

The local API accepts these query parameters on `/api/research`:

| Parameter | Values | Default |
|---|---|---|
| `research_window` | `10Y`, `20Y`, `30Y`, `40Y`, `50Y` | `30Y` |
| `optimizer` | `equal_weight`, `max_sharpe`, `min_volatility`, `max_sortino`, `max_calmar`, `risk_balanced`, `robust_quant` | `robust_quant` |
| `frequency` | `daily`, `monthly` | `monthly` |
| `rebalance_frequency` | `monthly`, `quarterly`, `annual` | `monthly` |
| `transaction_cost_bps` | `0`, `5`, `10`, `25`, `50` | `10` |

The response contains `weights`, `in_sample`, `out_of_sample`, `stability`,
`robustness`, `walk_forward`, `coupling`, `regimes`, `crisis`,
`transaction_sensitivity`, `monte_carlo`, and `provenance` sections.

## Deploy to Cloudflare Workers

### Deployment model

The edge deployment uses [Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
and a small Worker adapter in `cloudflare/src/index.js`:

- Static frontend files are copied to `cloudflare/site/`.
- The generated `research_result.json` is copied beside them.
- `/api/research` serves the versioned snapshot through the Worker.
- `/api/health` returns deployment status.
- The Python research engine remains local/CI-side and is not executed inside
  the Worker runtime.

The Cloudflare UI labels the result as a **Cloudflare edge snapshot** and locks
the research selectors so the deployed page cannot imply that it is running a
new optimizer calculation for an unsupported query.

### Current production deployment

- Dashboard: <https://quant-dashboard.44102189.workers.dev>
- Health check: <https://quant-dashboard.44102189.workers.dev/api/health>
- Research snapshot: <https://quant-dashboard.44102189.workers.dev/api/research>

### Authenticate Wrangler

Use an interactive OAuth login:

```bash
npx wrangler login
npx wrangler whoami
```

The account must have permission to create and deploy Workers. Do not commit
Cloudflare API tokens or secrets. For non-interactive CI, use the CI secret
store and the official Wrangler authentication flow.

### Build the deployment directory

The build requires a current `research_result.json`:

```bash
python3 run_research.py
npm run build:cloudflare
```

The build script copies `frontend/` and `research_result.json` to
`cloudflare/site/`, writes security/cache headers, and leaves the source
frontend untouched.

### Run the Worker locally

```bash
npm run cloudflare:dev
```

Wrangler serves the Worker locally, normally at
[http://localhost:8787](http://localhost:8787). Verify both routes:

```bash
curl http://localhost:8787/api/health
curl http://localhost:8787/api/research
```

### Validate without deploying

```bash
npm run cloudflare:dry-run
```

This builds the Worker and static assets, then asks Wrangler to compile and
validate the deployment without publishing it.

### Deploy production

```bash
npm run deploy
```

The script runs `python3 cloudflare/build.py`, then deploys the `production`
environment from `wrangler.jsonc` with minification and observability enabled.
Wrangler prints the deployed `workers.dev` URL. Save that URL as the public
dashboard URL for the current snapshot.

Cloudflare’s current recommended practice is to keep the Wrangler config as the
source of truth, set a current `compatibility_date`, and use `wrangler deploy`
for deployment. This repository pins `2026-08-11`, the newest runtime date
supported by the installed Wrangler validation binary at the time of this
deployment; update it after upgrading Wrangler and rerun the checks.

## Update the Cloudflare snapshot

To refresh the deployed research result:

```bash
# 1. Refresh raw sources and normalized return panels.
python3 run_research.py --download

# 2. Recalculate the machine-readable result.
python3 run_research.py

# 3. Run tests and validate the Worker bundle.
python3 -m pytest -q
npm run cloudflare:dry-run

# 4. Deploy the new snapshot.
npm run deploy

# 5. Record the new artifact in Git. `data/metadata.json` is ignored by default;
#    use `git add -f` for it only when you intentionally want to version the
#    generated provenance ledger.
git add README.md research_result.json
git commit -m "Refresh quant research snapshot"
git push
```

The raw CSV files remain ignored by default because they are generated data;
the versioned `research_result.json` is the edge deployment artifact. If the
research data source or methodology changes, update the README and provenance
notes with the same commit.

## Testing and verification

Run the Python test suite:

```bash
python3 -m pytest -q
```

The tests cover:

- CAGR, volatility, Sharpe, Sortino, Calmar, drawdown, VaR, and CVaR.
- Portfolio return and covariance construction.
- Weight constraints and all optimizer families.
- Turnover and transaction costs.
- Walk-forward train/test splitting.
- An end-to-end data → optimizer → portfolio → metrics → OOS result path.

Run frontend and Worker checks:

```bash
node --check frontend/app.js
python3 -m compileall -q backend run_research.py cloudflare
npm run cloudflare:dry-run
```

After deployment, verify:

```bash
curl https://<your-worker-subdomain>.workers.dev/api/health
curl -s https://<your-worker-subdomain>.workers.dev/api/research | python3 -m json.tool | head -80
```

Then open the Worker URL in a browser and confirm that the dashboard shows the
Cloudflare edge snapshot notice, four asset weights, IS/OOS metrics, crisis
rows, and provenance ledger.

## Repository map

```text
backend/
  data_pipeline.py       source downloads, return construction, provenance
  research_engine.py     metrics, optimizers, OOS, stability, regimes, PCA
  server.py              local HTTP server and cache-backed API
cloudflare/
  src/index.js           Worker API adapter and static asset handler
  build.py               creates the edge deployment directory
frontend/
  index.html             dashboard structure
  app.js                 charts, controls, API client
  styles.css             responsive institutional-style theme
tests/
  test_research_engine.py unit and end-to-end tests
data/
  metadata.json          generated provenance ledger
research_result.json     versioned Cloudflare snapshot artifact
wrangler.jsonc           Cloudflare Worker and Static Assets config
run_research.py          reproducible research entry point
```

## Known limitations

- The common four-asset monthly panel is shorter than a nominal 50-year period.
- Long Treasury returns are duration/yield approximations, not bond-index total
  returns.
- Gold data uses spot and futures proxies rather than one continuous investable
  total-return instrument.
- S&P dividend accrual between monthly observations is approximate.
- Rule-based regimes are transparent diagnostics, not statistically discovered
  latent states.
- The Cloudflare deployment is a precomputed snapshot. Full parameter changes
  and new optimization runs require the Python pipeline and a new deployment.
- The module makes no statistical significance claims and does not model taxes,
  bid/ask spreads, market impact, financing costs, or investor-specific
  suitability.

## Official references

- [Cloudflare Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
- [Wrangler configuration](https://developers.cloudflare.com/workers/wrangler/configuration/)
- [Wrangler deploy command](https://developers.cloudflare.com/workers/wrangler/commands/workers/#deploy)
- [Cloudflare compatibility dates](https://developers.cloudflare.com/workers/configuration/compatibility-dates/)
- [FRED](https://fred.stlouisfed.org/)
- [Robert Shiller data wrapper](https://posix4e.github.io/shiller_wrapper_data/)
