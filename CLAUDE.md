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
| yfinance | Price data, S&P500, Nikkei, VIX | No |
| FRED CSV (`fredgraph.csv?id=SERIES_ID`) | OECD CLI, PCE, Core PCE | **No** — use CSV endpoint, not JSON API |
| multpl.com (scraping) | Shiller CAPE | No |
| FMP API | Economic calendar actuals | `FMP_API_KEY` |
| BLS API | CPI, unemployment, NFP | No |
| Tiingo | Daily OHLCV fallback | `TIINGO_API_KEY` |
| Alpha Vantage | Economic indicators | `ALPHA_VANTAGE_KEY` |
| Finnhub | Company news | `FINNHUB_API_KEY` |
| ipinfo.io / ip-api.com | Geo lookup for analytics | `IPINFO_TOKEN` (optional) |

**FRED note**: Always use the CSV endpoint (`https://fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES_ID`), not the JSON API. The JSON API requires a valid `FRED_API_KEY`; the CSV endpoint is public and key-free. FRED returns `.` for unpublished values — `pd.to_numeric(..., errors="coerce")` converts these to NaN.

### Secrets (Streamlit Cloud `secrets.toml`)

```
GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY
FMP_API_KEY, TIINGO_API_KEY, DEEPL_API_KEY
ALPHA_VANTAGE_KEY, FINNHUB_API_KEY, FRED_API_KEY (legacy, unused)
GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON  (analytics persistence)
IPINFO_TOKEN (optional)
```

## Key Implementation Patterns

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

### Country blocking

`check_country_block()` (line ~109) uses ipinfo.io to block `CN`, `RU`, `KP`. Runs once per session, cached in `st.session_state`.
