<a id="japanese"></a>

# 📊 Market Dashboard

日本株・米国株のマクロ環境からセンチメント分析、日本株特化のファクター分析、AIマルチエージェントによる
個別銘柄診断・ポートフォリオ提案、そして実際の取引記録管理までを1つに統合した
[Streamlit](https://streamlit.io/) 製マーケットダッシュボードです。

**🔗 公開URL: [windex.streamlit.app](https://windex.streamlit.app/)** ｜ [🇺🇸 English version ↓](#english)

> ⚠️ 本アプリで表示される情報・AIによる分析コメントは投資判断の参考情報であり、投資の助言や勧誘を
> 目的としたものではありません。実際の投資判断はご自身の責任で行ってください。

---

## 目次

- [このアプリでできること](#このアプリでできること)
- [AIマルチエージェント・パイプライン](#aiマルチエージェントパイプライン)
- [分析手法の透明性（計算式と既知の限界）](#分析手法の透明性計算式と既知の限界)
- [アーキテクチャと設計判断](#アーキテクチャと設計判断)
- [本番運用で踏んだ落とし穴と対処](#本番運用で踏んだ落とし穴と対処)
- [データソースと冗長化戦略](#データソースと冗長化戦略)
- [技術構成・ファイル構成](#技術構成)

---

## このアプリでできること

### 🌐 マーケット概況（トップページ）
- 日経平均・NYダウ・S&P500・NASDAQ・SOX指数・VIX・為替・コモディティ・米国債利回り（5年/10年/30年）などを
  一覧のウォッチリストカードで表示（スパークライン付き）
- Shiller CAPE・OECD景気先行指数（LEI）・次回FOMCの利上げ/利下げ織り込み確率（Fed Funds先物ベース）
- Fear & Greed指数、NAAIM機関投資家エクスポージャー、複合センチメントスコア
- 🐻 弱気相場リスク判定、📉 過去の暴落局面（リーマン・コロナショック等）との複合指標パターンマッチング
- 🔄 セクターローテーション（RRGチャート）、日経225/S&P500の値動き予測モデル（アンサンブル方式）
- 📅 日米の経済指標カレンダー（雇用統計・CPI・FOMC等の発表日と実績値の自動突合）
- 📰 Yahoo!ファイナンス・株探・みんかぶ・TDnet・日経/ロイターRSSを横断したニュース集約

### 🇯🇵 日本株特化の分析ツール
- モメンタムランキング、日本株シャープレシオTOP10（日経225主要銘柄・ベンチマーク日経平均）
- 🔍 AI開示評価（LLM as a Judge）— 保有銘柄のTDnet適時開示を、全銘柄まとめて1回のAI呼び出しで
  重要度・株価インパクト・センチメント・緊急度を採点
- 🔮 来期想定利益スクリーニング（yfinance予想PER/EPS × J-Quants決算短信データの突合）
- 📏 サイズファクター分析（SMB）／💰 バリューファクター分析（HML）／🌟 CAPMベースの価値創造
  （ROE vs 資本コスト）分析 — 伊藤レポート基準のROE 8%ラインも参考表示
- 🔥 出来高急増・VWAP乖離などの需給スクリーニング、📊 52週高値安値・移動平均乖離・
  ゴールデンクロス/デッドクロス等の価格パターン分析
- 信用残高（J-Quants V2 / 取得不可時はirbank.netへ自動フォールバック）、
  機関投資家保有・インサイダー取引（米国株、Finnhub優先・yfinance予備）

### 🤖 AI分析・シグナル（トレーディングプロジェクト）
ログインすると、自分の保有銘柄に対してAIが3段階のマルチエージェント構成で投資判断を行います
（詳細は[後述](#aiマルチエージェントパイプライン)）。

- 🌱長期育成 / ⚡モメンタム / ✨Claude AIミックス / 💡光銘柄ミックス / 🏦配当安定 / 🪨安定成長 の
  6つの投資戦略モードを切り替え可能。各モードの過去1年・3年バックテスト比較付き
  （テーマ固定バスケットと、AIが実際に使うスコアリング基準で選ぶ銘柄群の両方に対応）
- 保有銘柄ごとのニュース・決算情報をAIが並列取得・日本語で要約（進捗をストリーミング表示）
- 予算・リスク許容度に応じた推奨ポートフォリオの自動生成（エントリー価格・損切ライン付き）

### 💰 取引記録・ポートフォリオ管理
- 取引記録の入力・編集・削除（Google Sheetsに永続保存、ユーザーアカウント制）
- 保有ポジションの含み損益・評価額（米国株/日本株混在でも円換算に統一）・アセットクラス別配分
- 前日比／先週比／前月比／前年比のパフォーマンス追跡（日次スナップショットベース、GitHub Actionsで自動記録）
- 配当サマリ（月別実績・翌月以降の予想配当・高配当銘柄レコメンド）
- CSV/Excelダウンロード対応

---

## AIマルチエージェント・パイプライン

「推奨ポートフォリオを生成」機能は、1回のAI呼び出しで済ませるのではなく、役割の異なる3段階の
エージェントを直列に実行します。

```
Agent A（マクロ分析）─┐
                      ├─▶ Agent B（銘柄別分析・並列）─▶ Agent C（ポートフォリオ組み立て）
候補スコアリング ──────┘        │
（AI不使用・純粋な計算）          ▼
                        検証: 実データと矛盾する
                        分析根拠を自動検出・除外
```

| 段階 | 役割 | 入力 | 出力 |
|---|---|---|---|
| 候補選定 | 価格モメンタムでスコアリング（**AI不使用**） | 3ヶ月/6ヶ月/1年リターン、予算内購入可否 | 上位15〜18銘柄 |
| Agent A | マクロ市場環境の分析 | Fear&Greed・VIX・NAAIM・クラッシュリスク・セクターRRG | 強気/弱気スタンス・注目セクター |
| Agent B | 個別銘柄分析（銘柄数だけ並列実行） | 価格・リターン + Agent Aのスタンス | スコア・merits/demerits・買い/様子見/除外 |
| 検証 | Agent Bの出力を実データと突合 | Agent Bの結論とスコア・リターン方向の整合性 | 矛盾があれば選定除外 |
| Agent C | 最終ポートフォリオ組み立て | Agent A + 検証済みAgent Bの全結果 | 銘柄配分・エントリー価格・損切ライン |

**なぜ3段階に分けているか**: 1つの巨大なプロンプトに全情報を詰め込むと、AIが銘柄ごとの根拠を
使い回したり、実際のデータと矛盾する結論を出しても気づけません。役割を分割し、Agent Bの結果を
Agent Cに渡す前に軽量な整合性チェック（LLMを追加で呼ばずに、渡した数値データとの矛盾がないかを
コードで検証）を挟むことで、明らかにおかしい分析根拠を最終成果物から除外しています。

**捏造対策**: このパイプラインには根本的な制約があります。Agent A/Bには**価格とリターンのデータしか
渡していません**（ROIC・ROE・PERのような財務指標は取得も送信もしていません）。以前はAgent Cへの
プロンプト例文に「ROIC 45%」のような具体的数値を含めていたため、AIがこのパターンを模倣して
未提供の財務指標を「もっともらしく」生成してしまう問題がありました。現在はプロンプト・few-shot例文
の両方から具体的な財務数値の記載要求を排除し、さらにAgent Cの出力自体にも「未提供の指標を
数値付きで言及していないか」を検出する正規表現ベースの最終チェックをかけています。それでも
すり抜けた場合は、画面上に「⚠️ 数値未検証」バッジが表示されます。

**🌱長期育成モードだけの例外**: このモードだけは時価総額5〜50億ドルのS&P600小型株を対象にした
「テンバガー候補スクリーニング」を行っており、粗利率・ROE・インサイダー保有比率・負債/EBITDAを
yfinanceから実際に取得してAgent Cに渡しています。他モードと違い、これらの項目に限っては
実データなので、AIが根拠として引用することを明示的に許可しています。

---

## 分析手法の透明性（計算式と既知の限界）

ブラックボックスにしないため、主要な分析ロジックとその限界を明記します。

- **シャープレシオ**: `(年率リターン − 無リスク金利0.5%) ÷ 年率ボラティリティ`。ベンチマークは日経平均。
  過去1年の実績値であり、将来を保証するものではありません。
- **CAPMベースの資本コスト**: `無リスク金利（日本10年国債利回り） + β × 株式リスクプレミアム5%`。
  βは日経平均に対する回帰係数。単純化したモデルであり、実際のWACCとは乖離があります。
- **Fed利上げ/利下げ織り込み確率**: CME 30-Day Fed Funds先物の価格から、会合のない月の先物価格で
  現行の実効金利を逆算し、会合月の先物価格（会合前後を日数加重平均した値）と比較して算出。
  「据え置き or 25bp変更」の二択のみを仮定した簡易近似であり、実際のCME FedWatch Toolのような
  複数シナリオの確率分布ではありません。
- **戦略モードのバックテスト（1年・3年）**: ✨AIミックス・💡光銘柄ミックス・🏦配当安定モードは
  テーマで固定された銘柄群の均等加重バックテストです。一方、🌱長期育成・⚡モメンタム・🪨安定成長
  モードは「今日時点の上位銘柄」を過去に遡って評価しているため、**後知恵バイアス**（実際にその
  戦略を1〜3年前から運用していた場合よりも良い数値が出る）を含みます。画面上にもこの旨を明記しています。
- **需給・価格パターン系のスクリーニング**: あくまで統計的な過去パターンの検出であり、急落や
  出来高急増の「原因」（特定ファンドの強制清算など）までは特定できません。

## アーキテクチャと設計判断

`app.py` は約17,000行の単一ファイルですが、一貫して以下の3層構造に従っています。

```
fetch_* / compute_*   … データ取得・計算。@st.cache_data でキャッシュ。st.* は一切呼ばない
render_*              … Streamlit描画。main()から順番に呼ばれる
main()                … トップレベルのエントリーポイント
```

- **並列プリフェッチ**: 経済カレンダーなど遅いAPIを4つ、`ThreadPoolExecutor`でページ描画の裏側で
  先に走らせておき、他セクションの描画をブロックしないようにしています。
- **バッチ取得の徹底**: 個別銘柄ごとにAPIを叩くのではなく、`yf.download(tickers, ...)`で
  一括取得してから銘柄ごとにスライスするパターンを統一的に使用（レート制限・待ち時間の削減）。
- **AI呼び出しの集約**: 保有銘柄N件それぞれにAIを呼ぶのではなく、全銘柄分のデータを1つのプロンプトに
  まとめて1回のAI呼び出しで済ませる箇所を増やしています（AI無料枠のクォータ消費を抑えるため）。
- **数値とAIコメントの分離**: 決算実績やファンダメンタルズの数値は必ずPython側で決定的に計算し、
  AIには短い定性コメントの生成だけを任せています（数値のハルシネーションを避けるため）。

## 本番運用で踏んだ落とし穴と対処

実運用の中で見つかった問題とその対処を、備忘録として残しています。

- **Reactの`removeChild`クラッシュ**: `st.markdown(unsafe_allow_html=True)`を1件ずつループで
  呼ぶと、React側の差分適用と噛み合わずクラッシュすることがありました。複数行のHTMLを
  1つの文字列にまとめて単一の`st.markdown()`呼び出しにする、または（行ごとのボタンが必要な場合は）
  `st.container(border=True)` + Streamlitネイティブの色付きMarkdown記法に置き換えて解消しています。
- **アクセスログが6週間止まっていた**: ページビューを記録するGoogle Sheetsの「realtime」タブに
  古い行を1行ずつ`delete_rows()`で消す処理を入れたところ、蓄積した行数分だけAPIリクエストが
  発生し、同じ関数内のアクセスログ本体の書き込みまで巻き込んでクォータ超過で失敗し続けていました。
  読み取り1回＋書き戻し1回の計2API呼び出しに固定する実装に変更して解消しています。
- **`st.tabs()`は非表示タブも毎回描画する**: Streamlitの`st.tabs()`はどのタブを見ていても裏側で
  全タブの中身を再実行するため、重い処理を持つタブがあると無関係なタブの表示まで遅くなっていました。
  `st.segmented_control()` + `if/elif`による自作タブ切り替えに置き換え、選択中のタブの処理だけが
  走るようにしています。
- **gspread 6.xの引数順変更**: `Worksheet.update()`の引数順が`(range_name, values)`から
  `(values, range_name)`に変わったバージョンがあり、旧順のまま呼ぶと例外にならず**別の場所に
  黙って書き込まれる**という気づきにくいバグを踏みました。呼び出し箇所にはこの罠を明記したコメントを
  残しています。
- **外部データソースの信頼性のばらつき**: 日本株の株価はyfinanceの値が実際とズレることがあるため
  Tiingo→みんかぶ→yfinanceの順にフォールバック、PCE/Core PCEはFRED CSVエンドポイントが
  恒常的にタイムアウトするようになったため、無理にリトライさせず表示自体を廃止しました。
- **Gemini SDKが1年近く死んでいた**: `google-generativeai`パッケージは2025年8月末でEOL、
  11月末に完全にサポート終了していたことが判明。エラーメッセージが2重に握りつぶされていた
  （実際のエラーではなく固定文言を表示）ため、この間ずっとGeminiだけ機能していないことに
  誰も気づけませんでした。新SDK（`google-genai`）への移行と合わせて、各AIプロバイダーの
  実際の失敗理由（429/404/402等）をそのままUIに表示するよう修正しています。

## データソースと冗長化戦略

| データ | 主な取得元 | フォールバック |
|---|---|---|
| 日本株の株価 | Tiingo | みんかぶ → yfinance |
| 米国株・指数・為替・コモディティ | yfinance | — |
| 経済指標（CPI・雇用統計・FOMC実績） | FMP API | BLS API |
| OECD景気先行指数 | OECD SDMX API | yfinanceイールドカーブ（10Y-3M）で代替 |
| Shiller CAPE | multpl.com（スクレイピング） | — |
| 信用残高・投資部門別売買（日本株） | J-Quants API v2（有料プラン要） | irbank.net（無料・内訳データなしの簡易版） |
| 米国株の機関投資家保有・インサイダー取引 | Finnhub | yfinance |
| S&P600小型株の構成銘柄（テンバガー候補） | Wikipedia（スクレイピング） | — |
| AIコメント生成 | Gemini | Groq → DeepSeek → NVIDIA NIM → OpenRouter |

## 技術構成

| 分類 | 技術 |
|---|---|
| フレームワーク | [Streamlit](https://streamlit.io/) |
| 言語 | Python 3.11 |
| データ取得 | yfinance, Tiingo, FMP, BLS API, FRED (CSV), J-Quants API v2, Finnhub, Alpha Vantage |
| AI（自動フォールバック） | Gemini → Groq → DeepSeek → NVIDIA NIM → OpenRouter |
| 最適化 | scipy.optimize（平均分散最適化・シャープレシオ最大化）, scikit-learn, xgboost |
| 永続化 | Google Sheets（gspread） — 取引記録・配当キャッシュ・アクセスログ等 |
| 可視化 | Plotly, Matplotlib |
| 自動配信 | GitHub Actions（日次スナップショット記録・LINE/Slackへのポートフォリオ配信）、Slack Bot（Flask） |
| ホスティング | Streamlit Community Cloud（`main`ブランチへのpushで自動デプロイ） |
| CI/CD | GitHub Actions（push/PR時に構文チェック・Ruff lint自動実行）、Streamlit Cloud（push即デプロイ） |
| 静的解析 | Ruff |

## ファイル構成

```
app.py                          アプリ本体（ページ全体を構成する単一ファイル、約17,000行）
analytics.py                    アクセス解析（ページビュー・地域/UA検出・Google Sheets連携）
slack_bot.py                    Slackメンションに応答する常時稼働Bot（別デプロイ）
scripts/
  daily_asset_snapshot.py       日次のアセットクラス別評価額をGoogle Sheetsへ記録
  daily_portfolio_line.py       AI生成の市況・保有銘柄サマリーをLINE/Slackへ配信
.github/workflows/               上記スクリプトを定時実行するGitHub Actions、およびpush/PR時のCI
```

実際に動いているものは公開URL（[windex.streamlit.app](https://windex.streamlit.app/)）からご利用ください。

<br>

---
---

<br>

<a id="english"></a>

# 📊 Market Dashboard (English)

A [Streamlit](https://streamlit.io/)-based market dashboard that unifies macro market context, sentiment
analysis, Japan-equity-focused factor analysis, an AI multi-agent stock/portfolio advisor, and real trade
record management for both Japanese and US equities.

**🔗 Live: [windex.streamlit.app](https://windex.streamlit.app/)** | [🇯🇵 日本語版 ↑](#japanese)

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

- Six switchable strategy modes — 🌱 Growth (tenbagger small-cap screener), ⚡ Momentum, ✨ Claude AI Mix,
  💡 Claude Optical Mix, 🏦 Dividend Stable, and 🪨 Stable Growth — each with a 1-year/3-year backtest
  comparison (covering both the fixed theme baskets and the same scoring criteria the AI actually uses to
  narrow candidates)
- Parallel fetch and Japanese-language AI summarization of news/earnings for each held ticker, with
  streaming progress display
- Automatic recommended-portfolio generation based on budget and risk tolerance — stock selection is the
  AI's qualitative call, but allocation weights are solved by real mean-variance optimization
  (`scipy.optimize`, Sharpe-ratio maximization over 2 years of daily returns), not an AI guess

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

**The one exception, 🌱 Growth mode:** this mode alone runs a "tenbagger candidate screen" over S&P600
small-caps ($500M–$5B market cap), and actually fetches gross margin, ROE, insider ownership %, and
debt/EBITDA from yfinance to pass to Agent C. Unlike every other mode, these specific fields are real data,
so the AI is explicitly permitted to cite them as evidence.

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
- **The Gemini SDK was dead for almost a year**: the `google-generativeai` package hit EOL at the end of
  August 2025 and lost all support by the end of November 2025. Because the error message was being
  masked twice over — a hardcoded generic string shown instead of the real exception — nobody noticed that
  Gemini alone had stopped working for months. Migrated to the new SDK (`google-genai`) and, at the same
  time, fixed every AI provider's fallback path to surface its actual failure reason (429/404/402/etc.)
  in the UI instead of a canned message.

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
| S&P600 small-cap constituents (tenbagger candidates) | Wikipedia (scraped) | — |
| AI commentary generation | Gemini | Groq → DeepSeek → NVIDIA NIM → OpenRouter |

## Tech stack

| Category | Technology |
|---|---|
| Framework | [Streamlit](https://streamlit.io/) |
| Language | Python 3.11 |
| Data | yfinance, Tiingo, FMP, BLS API, FRED (CSV), J-Quants API v2, Finnhub, Alpha Vantage |
| AI (automatic fallback chain) | Gemini → Groq → DeepSeek → NVIDIA NIM → OpenRouter |
| Optimization | scipy.optimize (mean-variance / Sharpe-ratio maximization), scikit-learn, xgboost |
| Persistence | Google Sheets (gspread) — trade records, dividend cache, access logs, etc. |
| Visualization | Plotly, Matplotlib |
| Scheduled delivery | GitHub Actions (daily snapshot recording, LINE/Slack portfolio digest), Slack Bot (Flask) |
| Hosting | Streamlit Community Cloud (auto-deploys on push to `main`) |
| CI/CD | GitHub Actions (compile-check + Ruff lint on every push/PR), Streamlit Cloud (deploy on push) |
| Static analysis | Ruff |

## File layout

```
app.py                          The app itself — a single file of ~17,000 lines
analytics.py                    Access analytics (pageviews, geo/UA detection, Google Sheets integration)
slack_bot.py                    Always-on Slack bot that responds to mentions (deployed separately)
scripts/
  daily_asset_snapshot.py       Records daily per-asset-class portfolio value to Google Sheets
  daily_portfolio_line.py       Sends an AI-generated market/portfolio digest to LINE/Slack
.github/workflows/               Scheduled GitHub Actions that run the scripts above, plus push/PR CI
```

The live app is available at [windex.streamlit.app](https://windex.streamlit.app/).
