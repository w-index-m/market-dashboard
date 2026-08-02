# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Deployment

- **Platform**: Streamlit Cloud, auto-deploys from `main` branch on GitHub (`w-index-m/market-dashboard`)
- **Push to deploy**: `git push origin main` triggers automatic redeployment
- To force a data refresh after deploy: use the "🔄 キャッシュクリア & 更新" button in the sidebar

## File Structure

Only two source files matter:

- `app.py` — The entire application (~17,000 lines). Single-file Streamlit app.
- `analytics.py` — Access analytics module (page views, Google Sheets persistence, IP/UA detection).

## Architecture Overview

### Single-file Streamlit app pattern

`app.py` follows a strict three-layer pattern:

1. **`fetch_*` / `compute_*` functions** — Data fetching, always decorated with `@st.cache_data(ttl=N)`. Never call `st.*` inside these.
2. **`render_*` functions** — Streamlit rendering, called sequentially from `main()`. No caching.
3. **`main()`** — The top-level entry point that calls render functions in page order.

Private helpers use a `_` prefix (e.g., `_fetch_eco_actuals_fmp`).

### Page rendering order in `main()`

```
render_market_summary()
→ [ThreadPoolExecutor prefetch for calendar data]
→ st.empty() placeholder  ← calendar fills in here at end of main()
render_macro_indicators()
render_bear_market_checker()
render_momentum_ranking()
render_optical_vs_semi()
Fear & Greed section (inline)
render_composite_sentiment()
render_naaim_section()
render_sector_rotation()
render_nikkei_prediction()
render_us_prediction()
... (more sections)
→ _calendar_placeholder.container() ← filled last with preloaded data
```

The economic calendar section (`render_economic_events_section`) is rendered last into a placeholder that appears early in the page. The four slow fetch functions (`_fetch_eco_actuals_fmp`, `_fetch_eco_actuals_bls`, `_fetch_eco_actuals_fred`, `_fetch_event_market_reactions`) are started in parallel via `ThreadPoolExecutor` at the top of `main()`.

### Cache TTL constants

```python
TTL_DAILY    = 3600   # 1h — most market data
TTL_INTRADAY = 300    # 5min — live prices, Fear & Greed
TTL_RSS      = 1800   # 30min — news feeds
TTL_MARKET_NEWS = 14400  # 4h — AI-generated summaries
TTL_CHART    = 600    # 10min — rendered chart images (bytes)
```

Macro indicators (`fetch_macro_indicators`) use `ttl=3600*6` (6h). If stale data is suspected, clear the cache via the sidebar button — do not reduce the TTL unnecessarily.

### AI fallback chain

`call_ai_with_fallback()` tries in order: **Gemini → Groq → OpenRouter**. Gemini quota errors (HTTP 429) trigger automatic fallback. Gemini model candidates are defined in `MODEL_FALLBACKS` list.

### Data sources

| Source | Used for | Key required |
|---|---|---|
| yfinance | Price data, S&P500, Nikkei, VIX, US institutional holders (13F) | No |
| FRED CSV (`fredgraph.csv?id=SERIES_ID`) | OECD CLI, PCE, Core PCE | **No** — use CSV endpoint, not JSON API |
| multpl.com (scraping) | Shiller CAPE | No |
| FMP API | Economic calendar actuals | `FMP_API_KEY` |
| BLS API | CPI, unemployment, NFP | No |
| Tiingo | Daily OHLCV fallback | `TIINGO_API_KEY` |
| Alpha Vantage | Economic indicators | `ALPHA_VANTAGE_KEY` |
| Finnhub | Company news, analyst rating changes, **US insider trading** (`/stock/insider-transactions`, primary source) | `FINNHUB_API_KEY` |
| J-Quants V2 API | JP margin balance / investor-type data (`fetch_margin_data_jquants`) | `JQUANTS_API_KEY` — **requires a paid Standard+ plan**; free tier gets HTTP 403 |
| irbank.net (scraping) | JP margin balance fallback (`fetch_irbank_margin_data`) when J-Quants is plan-restricted | No |
| ipinfo.io / ip-api.com | Geo lookup for analytics | `IPINFO_TOKEN` (optional) |

**FRED note**: Always use the CSV endpoint (`https://fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES_ID`), not the JSON API. The JSON API requires a valid `FRED_API_KEY`; the CSV endpoint is public and key-free. FRED returns `.` for unpublished values — `pd.to_numeric(..., errors="coerce")` converts these to NaN.

**J-Quants note**: API is V2 (`https://api.jquants.com/v2`) — auth is a single `x-api-key` header with the dashboard-issued key (no more `auth_user`/`auth_refresh` token exchange; V1 endpoints return HTTP 410 Gone). `margin-interest` and `investor-types` both require a **paid Standard plan or higher** — a free-tier key gets `HTTP 403 {"message": "This API is not available on your subscription..."}`. `fetch_margin_data_jquants()` detects this specific 403+"subscription" pattern and shows a friendly message instead of a raw dump; when it returns no data, callers fall back to `fetch_irbank_margin_data()` (scrapes irbank.net's free per-ticker `/margin` page — no plan-tier restriction, but only gives total buy/sell balance and ratio, no 制度信用/一般信用 breakdown).

**yfinance holder-data gotchas** (`fetch_us_institutional_holders`): `institutional_holders`, `mutualfund_holders`, and `insider_transactions` are three separate properties but share **one underlying quoteSummary HTTP request** — the first property access fetches and caches all three. If the first access fails (e.g. Yahoo rate-limits with "Too Many Requests"), don't retry the other two independently — they'll hit the same failing request and triple the load. Also, the installed yfinance version's own rename of `pctHeld` → `% Out` is commented out in its source (`yfinance/scrapers/holders.py`), so the percent-of-float column is actually still named `pctHeld` — read that first, falling back to `% Out` for forward compatibility. Because of Yahoo's rate limiting, Finnhub's `/stock/insider-transactions` (free tier) is used as the primary source for insider buy/sell data; yfinance's `insider_transactions` is only a fallback when Finnhub returns nothing.

### Secrets (Streamlit Cloud `secrets.toml`)

```
GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY
FMP_API_KEY, TIINGO_API_KEY, DEEPL_API_KEY
ALPHA_VANTAGE_KEY, FINNHUB_API_KEY, FRED_API_KEY (legacy, unused)
JQUANTS_API_KEY (JP margin/investor-type data; needs paid J-Quants plan for full functionality)
GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON  (analytics persistence)
IPINFO_TOKEN (optional)
```

## Key Implementation Patterns

### Streamlit rendering pitfalls (React DOM `removeChild` errors)

Two patterns reliably cause a `NotFoundError: removeChild` crash in the browser and must be avoided:

1. **Looping `st.markdown(..., unsafe_allow_html=True)` once per item** (e.g. one call per card in a `for` loop). Fix: build a list of HTML strings and `"".join()` them into a **single** `st.markdown()` call per section. This is the standard pattern throughout the codebase — see `_supply_cards`/`_wl_cards`-style lists in `render_ticker_chart_compare` and `render_momentum_ranking`.
2. **Polling a single `st.empty()` placeholder in a tight loop** (e.g. `while thread.is_alive(): placeholder.markdown(...); time.sleep(1)`). Fix: use synchronous `st.spinner()` blocks instead of threading + placeholder polling.

### Dark-theme HTML cards

Most cards/panels use `st.markdown(..., unsafe_allow_html=True)` with inline styles. The color palette:
- Backgrounds: `#0f172a`, `#1e293b`, `#0d1117`
- Text: `#e2e8f0` (primary), `#94a3b8` (muted), `#64748b` (subtle)
- Borders: `#334155`

### Plotly charts in dark theme

Always set these explicitly — global `font=dict(color=...)` alone does not propagate to all axis elements:

```python
fig.update_layout(
    paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
    font=dict(color="#e2e8f0"),
    yaxis=dict(tickfont=dict(color="#e2e8f0"), title=dict(font=dict(color="#e2e8f0"))),
    xaxis=dict(tickfont=dict(color="#e2e8f0")),
    legend=dict(font=dict(color="#e2e8f0")),
    hoverlabel=dict(bgcolor="#1e293b", font=dict(color="#e2e8f0")),
)
```

### Anchor tags for in-page navigation

Each section starts with `st.markdown('<a id="section-name"></a>', unsafe_allow_html=True)`. The nav bar (`DASHBOARD_LINKS_TOP_HTML` near line 870) links to these anchors. CSS uses `!important` on `.nav-btn` color properties to override browser default link blue.

### FMP economic calendar same-date collision fix

FMP results are keyed by composite `f"{date}|{event_name}"`. Lookups in the render function try the composite key first, then fall back to date-only:

```python
fmp = eco_actuals.get(f"{ev_date_str}|{ev['name']}") or eco_actuals.get(ev_date_str)
```

### Basket definitions for optical vs semiconductor comparison

```python
_OPTICAL_BASKET  # line ~8169 — CIEN, COHR, LITE, VIAV, AAOI, ATXI, 5803.T
_SEMI_BASKET     # line ~8182 — SMH, SOXX, NVDA, AMD, AVGO, INTC, QCOM
```

Tickers with fewer than 5 non-NaN values in the selected period are silently excluded from the equal-weighted basket average.

### Momentum/factor anomaly detectors (`render_momentum_ranking`)

Beyond the per-stock momentum ranking, this section has three market-wide "notice something's off" checks, all using SMH as the AI/semiconductor proxy (a real investable ETF, not a hand-built basket, to avoid look-ahead bias):

- **MTUM monthly crash check** — `_fetch_factor_monthly_returns("MTUM", ...)` + `_analyze_factor_crash_risk()`: flags when the momentum factor ETF's current month return is a historical extreme (z-score/percentile vs 15y of months).
- **Relative weakness (real-time)** — `_detect_relative_weakness("SMH", "^GSPC")`: compares SMH's trailing 1/5/10-day return to S&P500's over the same window. Doesn't need a decline to finish playing out — flags an in-progress divergence. If SMH underperforms by 5pt+ while the market is flat/up, labeled likely sector rotation; if the market is down too, labeled likely broad risk-off.
- **Whiplash detector (retrospective)** — `_detect_whiplash_episodes()` wraps `_find_drawdown_episodes()` filtered to episodes where both the decline and the recovery were fast (≤10 and ≤15 trading days). Cross-references `_US_ECO_CALENDAR` (`_find_macro_events_in_range`) to note whether a known high-impact macro event coincided with the decline. This can only flag an episode *after* the recovery has happened — it's a complement to, not a substitute for, the real-time relative-weakness check.

None of these can identify an actual *cause* (e.g. a specific fund's forced liquidation) — only the price/volume pattern.

### `render_ticker_chart_compare` (free-input ticker chart & analysis tool)

A large, separate tool (distinct from the fixed-watchlist `render_stock_screener`) that takes any ticker/company name via `_resolve_ticker_query()` and renders: multi-year price+volume chart with Bollinger Bands/support-resistance, RSI divergence, year-over-year seasonal overlay, drawdown history vs `_MAJOR_CRASH_REFERENCE`, thin-volume-move detection, golden-cross+volume backtest, TDnet/SEC EDGAR disclosure fetch + AI summarization, analyst-downgrade-in-uptrend backtest, and AI chart analysis (combined + per-year). The 需給分析 (supply/demand) sub-section branches on ticker suffix: `.T` → J-Quants/IRBANK margin data; otherwise → `fetch_us_institutional_holders()` (yfinance 13F + Finnhub insider trading). Called from multiple places, so it takes a `key_prefix` param to avoid Streamlit duplicate-widget-key errors.

### Country blocking

`check_country_block()` (line ~109) uses ipinfo.io to block `CN`, `RU`, `KP`. Runs once per session, cached in `st.session_state`.
