# 📊 Market Dashboard

日本株・米国株のマクロ環境からセンチメント、個別銘柄分析、ポートフォリオ管理までを1つにまとめた
[Streamlit](https://streamlit.io/) 製のマーケットダッシュボードです。

**🔗 公開URL: [windex.streamlit.app](https://windex.streamlit.app/)**

> ⚠️ 本アプリで表示される情報・AIによる分析コメントは投資判断の参考情報であり、投資の助言や勧誘を
> 目的としたものではありません。実際の投資判断はご自身の責任で行ってください。

---

## このアプリでできること

### 🌐 マーケット概況（トップページ）
- 日経平均・NYダウ・S&P500・NASDAQ・SOX指数・VIX・為替・コモディティ・米国債利回り（5年/10年/30年）などを
  一覧のウォッチリストカードで表示（スパークライン付き）
- Shiller CAPE・OECD景気先行指数（LEI）・次回FOMCの利上げ/利下げ織り込み確率（Fed Funds先物ベース）
- Fear & Greed指数、NAAIM機関投資家エクスポージャー、複合センチメントスコア
- 🐻 弱気相場リスク判定、📉 過去の暴落局面との複合指標パターンマッチング
- 🔄 セクターローテーション（RRGチャート）、日経225/S&P500の値動き予測モデル
- 📅 日米の経済指標カレンダー（雇用統計・CPI・FOMC等の発表日と実績値）
- 📰 Yahoo!ファイナンス・株探・みんかぶ・TDnet・日経/ロイターRSSを横断したニュース集約

### 🇯🇵 日本株特化の分析ツール
- モメンタムランキング、日本株シャープレシオTOP10
- 🔍 AI開示評価（LLM as a Judge）— 保有銘柄のTDnet適時開示をAIが重要度・インパクトで採点
- 🔮 来期想定利益スクリーニング（yfinance予想PER/EPS × J-Quants決算データ）
- 📏 サイズ／💰バリュー ファクター分析、🌟 CAPMベースの価値創造（ROE vs 資本コスト）分析
- 🔥 出来高急増・VWAP乖離などの需給スクリーニング、📊 52週高値安値・ゴールデンクロス等の価格パターン分析
- 信用残高（J-Quants / irbank.net）、機関投資家保有・インサイダー取引（米国株）

### 🤖 AI分析・シグナル（トレーディングプロジェクト）
ログインすると、自分の保有銘柄に対してAIが3段階のマルチエージェント構成で投資判断を行います。

```
Agent A（マクロ市場分析）→ Agent B（個別銘柄の並列分析）→ Agent C（ポートフォリオ組み立て）
```

- 🌱長期育成 / ⚡モメンタム / ✨Claude AIミックス / 💡光銘柄ミックス / 🏦配当安定 / 🪨安定成長 の
  6つの投資戦略モードを切り替え可能。各モードの過去1年・3年バックテスト比較付き
- 保有銘柄ごとのニュース・決算情報をAIが日本語で要約
- 予算・リスク許容度に応じた推奨ポートフォリオの自動生成（エントリー価格・損切ライン付き）

### 💰 取引記録・ポートフォリオ管理
- 取引記録の入力・編集・削除（Google Sheetsに永続保存）
- 保有ポジションの含み損益・評価額（円換算統一）・アセットクラス別配分
- 前日比／先週比／前月比／前年比のパフォーマンス追跡（日次スナップショットベース）
- 配当サマリ（月別実績・翌月以降の予想配当・高配当銘柄レコメンド）
- CSV/Excelダウンロード対応

---

## 技術構成

| 分類 | 技術 |
|---|---|
| フレームワーク | [Streamlit](https://streamlit.io/) |
| 言語 | Python 3.11 |
| データ取得 | yfinance, Tiingo, FMP, BLS API, FRED (CSV), J-Quants API v2, Finnhub, Alpha Vantage |
| AI（自動フォールバック） | Gemini → Groq → DeepSeek → NVIDIA NIM → OpenRouter |
| 永続化 | Google Sheets（gspread） — 取引記録・配当キャッシュ・アクセスログ等 |
| 可視化 | Plotly, Matplotlib |
| 自動配信 | GitHub Actions（日次スナップショット記録・LINE/Slackへのポートフォリオ配信）、Slack Bot（Flask） |
| ホスティング | Streamlit Community Cloud（`main`ブランチへのpushで自動デプロイ） |

## ファイル構成

```
app.py                          アプリ本体（ページ全体を構成する単一ファイル）
analytics.py                    アクセス解析（ページビュー・地域/UA検出・Google Sheets連携）
slack_bot.py                    Slackメンションに応答する常時稼働Bot（別デプロイ）
scripts/
  daily_asset_snapshot.py       日次のアセットクラス別評価額をGoogle Sheetsへ記録
  daily_portfolio_line.py       AI生成の市況・保有銘柄サマリーをLINE/Slackへ配信
.github/workflows/               上記スクリプトを定時実行するGitHub Actions
```

## ローカルで動かす

```bash
git clone https://github.com/w-index-m/market-dashboard.git
cd market-dashboard
pip install -r requirements.txt
streamlit run app.py
```

一部の機能（AI分析、J-Quants連携、Google Sheets永続化、Slack配信など）はAPIキーの設定が必要です。
`.streamlit/secrets.toml` に以下のようなキーを設定してください（すべて任意項目。未設定の機能は該当箇所のみ無効化されます）。

```
GEMINI_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY, NVIDIA_API_KEY, OPENROUTER_API_KEY
FMP_API_KEY, TIINGO_API_KEY, ALPHA_VANTAGE_KEY, FINNHUB_API_KEY
JQUANTS_API_KEY, DEEPL_API_KEY, IPINFO_TOKEN
GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON
```
