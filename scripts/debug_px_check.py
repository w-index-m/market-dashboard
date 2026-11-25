"""株価の確認: 直近の終値と週ごとの騰落、高値からの位置（公開データ・汎用ティッカー）。"""
import yfinance as yf

for s in ("5803.T", "5801.T", "5802.T", "NVDA", "SMH", "LITE", "^N225"):
    c = yf.download(s, period="1y", interval="1d", auto_adjust=False, progress=False)["Close"].squeeze().dropna()
    print(s, "直近8日:", ", ".join(f"{d.strftime('%m/%d')} {v:.1f}" for d, v in c.tail(8).items()))
    w = c.resample("W-FRI").last().pct_change().dropna().tail(8) * 100
    print("   週次:", ", ".join(f"{d.strftime('%m/%d')} {v:+.1f}%" for d, v in w.items()),
          f"| 1年高値 {c.max():.1f}（{c.idxmax().date()}） 高値から {(c.iloc[-1] / c.max() - 1) * 100:+.1f}% | 年初来 {(c.iloc[-1] / c[c.index < '2026-01-01'].iloc[-1] - 1) * 100:+.1f}%")
