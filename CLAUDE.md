# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Deployment

- **Platform**: Streamlit Cloud, auto-deploys from `main` branch on GitHub (`w-index-m/market-dashboard`)
- **Push to deploy**: `git push origin main` triggers automatic redeployment
- To force a data refresh after deploy: use the "🔄 マーケットデータ更新" button in the sidebar — **scope note**: this only clears market-dashboard caches (Fear & Greed, macro indicators, momentum ranking, etc.), not the trading-project page's portfolio/dividend caches. For those, use the refresh button described in the trading-project section below.

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

### `render_claude_trading_project` (portfolio tracker: trades → P&L → dividends)

A separate page (own header, mode-selector cards, own tab bar) from the main market-dashboard scroll — reached via its own entry point, not part of `main()`'s long page.

**Tab switching does NOT use `st.tabs()`.** It used to, but `st.tabs()` renders every tab's body on every script run regardless of which one is visually selected — switching tabs is pure client-side CSS, not a server round-trip. That meant clicking the last tab (配当サマリ) still paid for AI分析・シグナル + 取引記録入力 + 損益・ポートフォリオ's full work first, every time. It's now a hand-rolled selector: `st.segmented_control()` writes the chosen label into `st.session_state["trading_active_tab"]`, and the four sections are a plain `if/elif` chain (`_active_tab == _TAB_SIGNAL` / `_TAB_TRADE` / `_TAB_PNL` / `_TAB_SUMMARY`) — only the active branch's code executes. The four tabs were already independent (each does its own login check and its own data fetch; none reads a sibling tab's local variables), so this was a mechanical `with tab_X:` → `if/elif ...:` swap with unchanged bodies. `st.segmented_control` in single-select mode can return `None` if the active pill is clicked again (toggle-off) — guard by only overwriting session state when the return value isn't `None`.

**Portfolio prices are refreshed on tab *entry*, not on a timer or every rerun.** `_compute_portfolio_summary()` (backs 配当サマリ: positions, dividends, allocation) is `@st.cache_data(ttl=TTL_DAILY)` — up to 1h stale on its own. Rather than shortening the TTL (which would raise recompute cost/API calls for every user on every rerun) or relying only on a manual button, the tab-switch handler compares the active tab against `st.session_state["_prev_trading_active_tab"]` and, only on the transition *into* 損益・ポートフォリオ or 配当サマリ, clears that tab's price cache (`fetch_daily_tiingo.clear()` / `_compute_portfolio_summary.clear()`) before it renders. Staying on the same tab across other widget interactions does not re-trigger it. A browser hard-reload creates a fresh Streamlit session (fresh `session_state`), so the next tab entry after a reload is always treated as a fresh switch. `_compute_portfolio_summary()`'s return dict also carries `"computed_at"` (set once, at the point the function body actually runs — frozen across cache hits) so the UI can show a "🕒 データ最終更新" caption; there's also a standalone "🔄 最新の株価に更新" button next to it for a manual refresh without switching tabs away and back.

**`_sheets_ws()` (`analytics.py`, imported into `app.py` as `_anl_sheets_ws`/`_trading_ws`) is `st.cache_resource`'d.** It used to reopen the spreadsheet, look up the worksheet, and re-validate headers on *every* call with zero caching — unlike `_sheets_client()`, which was already cached. Any feature that calls it once per held ticker (e.g. the old per-ticker dividend cache lookup) paid for that multiple times per render. Now the worksheet handle itself is cached process-wide, same pattern as the client. For sheets read once-per-ticker in a loop, prefer a batch read cached with a short `st.cache_data` TTL over N individual `get_all_records()` calls — see `_load_dividend_sheets_cache_all()` / `_load_asset_category_snapshot_all()` for the pattern (read the whole tab once, filter/index client-side).

**Dividend history Sheets cache must preserve timezone.** `_fetch_dividend_history()` checks a Sheets-backed persistent cache (`dividend_cache` tab, 3-day freshness) before hitting yfinance/Tiingo/Finnhub. The cached dates are stored as bare `YYYY-MM-DD` strings; reconstructing them as `pd.Timestamp(k)` (no `tz`) produces a tz-naive index, but every caller compares it against a tz-aware (`tz="UTC"`) cutoff — that raises `TypeError` internally, which is caught by a broad `except`, silently dropping that ticker's dividends from whatever's being computed. Always reconstruct with `pd.Timestamp(k, tz="UTC")`.

**配当サマリ's "おすすめ高配当銘柄" card is a daily-refreshed top-5, not a fixed list.** `_JP_HIGH_DIV_CANDIDATES` / `_US_HIGH_DIV_CANDIDATES` are hand-picked pools (~15-17 large-cap dividend payers each; tickers are manually chosen, never AI-generated, to avoid a hallucinated ticker or company name slipping through) — `_pick_top_high_dividend()` computes each candidate's trailing-12-month yield (sum of `_fetch_dividend_history()`'s last-year dividends ÷ current price, not yfinance's own `dividendYield` field) and returns the top N, cached 24h. The displayed lineup shifts with real price/dividend changes instead of always showing the same names.

**前日比/先週比/前月比/前年比 and the daily CSV export all share one source of truth: `asset_category_snapshot`.** A dedicated GitHub Actions cron (`daily-asset-snapshot.yml`, 00:00 JST) calls `_save_daily_asset_category_snapshot()`, which uses `_compute_portfolio_summary()` (fund NAVs included) to record one row per user per day: `日本株`/`米国株`/`投資信託`/`債券`/`total_value_jpy`. `_load_asset_category_snapshot(username, target_date)` returns the closest snapshot on/before a given date (used for all four period-comparison figures — 先週比/前月比/前年比 fall back to a stock-only recompute via `_compute_portfolio_history()`, marked with `＊`, when no snapshot exists that far back yet); `_load_asset_category_snapshot_all(username)` (cached) backs both that lookup and the "📅 資産クラス別 日次推移" CSV download at the bottom of 配当サマリ. Note this data has a different scope than a household-finance aggregator like MoneyForward (bank accounts, other brokerages, cash, real estate) — it only covers positions entered as trades in this app, by design.
