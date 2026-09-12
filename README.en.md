# 📊 Market Dashboard

A [Streamlit](https://streamlit.io/)-based market dashboard that unifies macro market context, sentiment
analysis, Japan-equity-focused factor analysis, an AI multi-agent stock/portfolio advisor, and real trade
record management for both Japanese and US equities.

**🔗 Live: [windex.streamlit.app](https://windex.streamlit.app/)** | [日本語版 README](README.md)

> ⚠️ Information and AI-generated commentary shown in this app are for reference only and do not
> constitute investment advice or a solicitation to trade. Investment decisions are your own responsibility.

---

## Table of Contents

- [What this app does](#what-this-app-does)
- [The AI multi-agent pipeline](#the-ai-multi-agent-pipeline)
- [Methodology transparency (formulas and known limits)](#methodology-transparency-formulas-and-known-limits)
- [Architecture and design decisions](#architecture-and-design-decisions)
- [Production incidents and fixes](#production-incidents-and-fixes)
- [Data sources and redundancy](#data-sources-and-redundancy)
- [Tech stack and file layout](#tech-stack)

---

## What this app does

### 🌐 Market overview (top page)
- Watchlist cards (with sparklines) for Nikkei 225, Dow, S&P 500, NASDAQ, SOX, VIX, FX, commodities, and
  US Treasury yields (5Y/10Y/30Y)
- Shiller CAPE, OECD Composite Leading Indicator (CLI), and the next FOMC meeting's implied hike/cut
  probability (derived from Fed Funds futures)
- Fear & Greed Index, NAAIM institutional exposure, and a composite sentiment score
- 🐻 Bear-market risk gauge, 📉 pattern matching against historical crash episodes (2008, COVID, etc.)
  using a composite of indicators
- 🔄 Sector rotation (RRG chart), ensemble price-direction models for Nikkei 225 and the US market
- 📅 US/Japan economic calendar with automatic actual-vs-forecast reconciliation (NFP, CPI, FOMC, etc.)
- 📰 Cross-source Japanese news aggregation (Yahoo! Finance, Kabutan, Minkabu, TDnet, Nikkei/Reuters RSS)

### 🇯🇵 Japan-equity-focused analysis tools
- Momentum ranking, and a Sharpe-ratio TOP10 (Nikkei 225 constituents, benchmarked against the index)
- 🔍 AI disclosure scoring (LLM-as-a-Judge) — scores TDnet timely disclosures for held tickers on
  importance, price impact, sentiment, and urgency, in a single batched AI call across all holdings
- 🔮 Forward-earnings screening (yfinance forward PER/EPS cross-referenced with J-Quants earnings data)
- 📏 Size-factor (SMB) and 💰 value-factor (HML) analysis, plus 🌟 CAPM-based value-creation analysis
  (ROE vs. cost of equity), with the Ito Report's ROE ≥ 8% benchmark shown for reference
- 🔥 Supply/demand screens (volume surges, VWAP deviation), 📊 price-pattern screens (52-week highs/lows,
  moving-average deviation, golden/dead crosses)
- Margin balance data (J-Quants V2, falling back to irbank.net), US institutional holdings and insider
  trading (Finnhub primary, yfinance fallback)

### 🤖 AI Analysis & Signals (trading project)
After logging in, your holdings are analyzed by a 3-stage AI multi-agent pipeline (see
[below](#the-ai-multi-agent-pipeline) for details).

- Six switchable strategy modes — 🌱 Growth, ⚡ Momentum, ✨ Claude AI Mix, 💡 Claude Optical Mix,
  🏦 Dividend Stable, and 🪨 Stable Growth — each with a 1-year/3-year backtest comparison (covering both
  the fixed theme baskets and the same scoring criteria the AI actually uses to narrow candidates)
- Parallel fetch and Japanese-language AI summarization of news/earnings for each held ticker, with
  streaming progress display
- Automatic recommended-portfolio generation based on budget and risk tolerance, including entry prices
  and stop-loss levels

### 💰 Trade records & portfolio management
- Add, edit, and delete trade records (persisted to Google Sheets, per user account)
- Unrealized P&L and market value for open positions, normalized to JPY even for mixed US/JP holdings,
  with asset-class allocation breakdown
- Day-over-day / week-over-week / month-over-month / year-over-year performance tracking, based on daily
  snapshots recorded automatically via GitHub Actions
- Dividend summary (monthly actuals, projected dividends for upcoming months, high-yield stock picks)
- CSV/Excel export

---

## The AI multi-agent pipeline

"Generate recommended portfolio" doesn't rely on a single AI call — it runs three agents with distinct
roles in sequence.

```
Agent A (macro analysis) ──┐
                           ├─▶ Agent B (per-stock analysis, parallel) ─▶ Agent C (portfolio assembly)
Candidate scoring ─────────┘        │
(no AI — pure computation)          ▼
                           Verification: auto-detect and drop
                           analysis that contradicts the real data
```

| Stage | Role | Input | Output |
|---|---|---|---|
| Candidate scoring | Score by price momentum (**no AI**) | 3m/6m/1y returns, budget affordability | Top 15–18 tickers |
| Agent A | Macro market analysis | Fear&Greed, VIX, NAAIM, crash risk, sector RRG | Bullish/bearish stance, sectors to watch |
| Agent B | Per-stock analysis (parallel, one call per ticker) | Price/returns + Agent A's stance | Score, merits/demerits, buy/hold/exclude |
| Verification | Cross-checks Agent B's output against the real data | Agent B's conclusion vs. its score/return direction | Drops contradictory picks |
| Agent C | Final portfolio assembly | Agent A + verified Agent B results | Allocation, entry prices, stop-loss levels |

**Why three stages instead of one:** stuffing everything into a single giant prompt makes it easy for the
model to recycle boilerplate reasoning across tickers, or to reach a conclusion that contradicts the data
it was given, without anyone noticing. Splitting the roles — and running a lightweight, code-only
consistency check on Agent B's output before it ever reaches Agent C — lets us drop clearly-wrong reasoning
before it reaches the final result.

**Anti-fabrication measures:** this pipeline has a hard constraint — Agent A and Agent B are only ever given
price and return data. Fundamental metrics like ROIC, ROE, or P/E are never fetched or passed in. Agent C's
prompt used to include a few-shot example with an invented figure like "ROIC 45%," which the model would
naturally imitate, fabricating plausible-sounding fundamentals it was never given for other tickers. Both
the instructions and the example have since been rewritten to rely only on the momentum data actually
provided, and Agent C's own output now goes through the same regex-based fabricated-metric check used for
Agent B. If anything still slips through, it's surfaced in the UI with a "⚠️ unverified figures" badge
rather than presented as fact.

---

## Methodology transparency (formulas and known limits)

To avoid being a black box, here's how the core analytics are computed and where they fall short.

- **Sharpe ratio**: `(annualized return − 0.5% risk-free rate) ÷ annualized volatility`, benchmarked
  against the Nikkei 225. A trailing 1-year figure — not a guarantee of future performance.
- **CAPM cost of equity**: `risk-free rate (Japan 10Y JGB yield) + β × 5% equity risk premium`, with β
  regressed against the Nikkei 225. A simplified model that will diverge from a real-world WACC.
- **Fed hike/cut probability**: backs out the current effective rate from a non-meeting month's CME
  30-Day Fed Funds futures price, then compares it to the meeting month's price (a days-weighted blend of
  pre- and post-meeting rates) to solve for the implied post-meeting rate. This assumes a simple
  "hold vs. one 25bp move" two-outcome split, not CME FedWatch's full multi-scenario probability
  distribution.
- **Strategy-mode backtests (1y/3y)**: the ✨ AI Mix, 💡 Optical Mix, and 🏦 Dividend Stable modes backtest
  their fixed theme baskets, equally weighted. The 🌱 Growth, ⚡ Momentum, and 🪨 Stable Growth modes,
  however, rank "today's top tickers" and evaluate that basket retroactively — carrying real **look-ahead
  bias** (the numbers will look better than what the strategy would have actually returned if run live 1–3
  years ago). This is called out directly in the UI.
- **Supply/demand and price-pattern screens**: these detect statistical patterns in past price/volume data
  only — they cannot identify the actual *cause* of a move (e.g., a specific fund's forced liquidation).

## Architecture and design decisions

`app.py` is a single ~17,000-line file, but it consistently follows a three-layer pattern:

```
fetch_* / compute_*   … data fetching and computation. Cached with @st.cache_data. Never calls st.*
render_*              … Streamlit rendering, called in sequence from main()
main()                … top-level entry point
```

- **Parallel prefetching**: four slow economic-calendar API calls are kicked off in a `ThreadPoolExecutor`
  ahead of the rest of the page render, so they don't block other sections.
- **Batched fetches throughout**: rather than hitting an API once per ticker, the app consistently uses
  `yf.download(tickers, ...)` to fetch in one batch and then slices per ticker — cutting both latency and
  rate-limit exposure.
- **Consolidated AI calls**: instead of calling the AI once per held position, many features gather all
  tickers' data into a single prompt and make one AI call — important given free-tier AI quota limits.
- **Numbers and AI commentary are kept separate**: earnings figures and fundamentals are always computed
  deterministically in Python; the AI is only ever asked for short qualitative commentary, to avoid
  numeric hallucination.

## Production incidents and fixes

A running log of real issues found in production and how they were resolved.

- **React `removeChild` crashes**: calling `st.markdown(unsafe_allow_html=True)` once per item in a loop
  would occasionally crash against React's DOM diffing. Fixed by batching multi-row HTML into a single
  `st.markdown()` call, or — where per-row buttons are required — replacing raw HTML with
  `st.container(border=True)` plus Streamlit's native colored-Markdown syntax.
- **Access logging silently stopped for six weeks**: a fix meant to prune stale rows from the "realtime"
  active-sessions Google Sheet was calling `delete_rows()` once per stale row. Given this app's sparse
  traffic pattern, that meant dozens of individual write requests on nearly every pageview, which
  repeatedly exhausted the Sheets API quota and took down the access-log write in the very same function
  call. Fixed by capping the operation at exactly two API calls (one read, one batched write) regardless
  of backlog size.
- **`st.tabs()` renders every tab, visible or not**: Streamlit's `st.tabs()` re-executes every tab's body
  on every script run regardless of which tab is selected, so a single heavy tab slowed down the whole
  page. Replaced with a hand-rolled `st.segmented_control()` + `if/elif` switch so only the active tab's
  code actually runs.
- **gspread 6.x's argument-order change**: `Worksheet.update()`'s argument order changed from
  `(range_name, values)` to `(values, range_name)` between versions. Calling it in the old order doesn't
  raise — it silently **writes to the wrong place**, which is a nasty bug to catch. Call sites now carry an
  explicit comment warning about this trap.
- **Inconsistent reliability across external data sources**: Japanese stock prices from yfinance can
  diverge from the real price, so the app falls back through Tiingo → Minkabu → yfinance. PCE/Core PCE
  were dropped entirely after FRED's CSV endpoint started timing out persistently — rather than retry a
  dead endpoint forever, the indicator was simply removed.

## Data sources and redundancy

| Data | Primary source | Fallback |
|---|---|---|
| Japanese stock prices | Tiingo | Minkabu → yfinance |
| US stocks, indices, FX, commodities | yfinance | — |
| Economic indicators (CPI, NFP, FOMC actuals) | FMP API | BLS API |
| OECD Composite Leading Indicator | OECD SDMX API | yfinance yield curve (10Y–3M) as a proxy |
| Shiller CAPE | multpl.com (scraped) | — |
| Margin balance / investor-type flows (JP) | J-Quants API v2 (paid plan required) | irbank.net (free, no breakdown) |
| US institutional holdings / insider trading | Finnhub | yfinance |
| AI commentary generation | Gemini | Groq → DeepSeek → NVIDIA NIM → OpenRouter |

## Tech stack

| Category | Technology |
|---|---|
| Framework | [Streamlit](https://streamlit.io/) |
| Language | Python 3.11 |
| Data | yfinance, Tiingo, FMP, BLS API, FRED (CSV), J-Quants API v2, Finnhub, Alpha Vantage |
| AI (automatic fallback chain) | Gemini → Groq → DeepSeek → NVIDIA NIM → OpenRouter |
| Persistence | Google Sheets (gspread) — trade records, dividend cache, access logs, etc. |
| Visualization | Plotly, Matplotlib |
| Scheduled delivery | GitHub Actions (daily snapshot recording, LINE/Slack portfolio digest), Slack Bot (Flask) |
| Hosting | Streamlit Community Cloud (auto-deploys on push to `main`) |
| Static analysis | Ruff |

## File layout

```
app.py                          The app itself — a single file of ~17,000 lines
analytics.py                    Access analytics (pageviews, geo/UA detection, Google Sheets integration)
slack_bot.py                    Always-on Slack bot that responds to mentions (deployed separately)
scripts/
  daily_asset_snapshot.py       Records daily per-asset-class portfolio value to Google Sheets
  daily_portfolio_line.py       Sends an AI-generated market/portfolio digest to LINE/Slack
.github/workflows/               Scheduled GitHub Actions that run the scripts above
```

The live app is available at [windex.streamlit.app](https://windex.streamlit.app/).
