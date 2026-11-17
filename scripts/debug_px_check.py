"""株価の確認: 直近の終値と週ごとの騰落（公開データ・汎用ティッカー）。"""
import yfinance as yf

for s in ("NVDA", "SMH", "QQQ", "^DJI", "^VIX", "^TNX", "MU", "LITE", "AAOI"):
    c = yf.download(s, period="4mo", interval="1d", auto_adjust=False, progress=False)["Close"].squeeze().dropna()
    print(s, "直近10日:", ", ".join(f"{d.strftime('%m/%d')} {v:.2f}" for d, v in c.tail(6).items()))
    w = c.resample("W-FRI").last().pct_change().dropna().tail(8) * 100
    print("   週次:", ", ".join(f"{d.strftime('%m/%d')} {v:+.1f}%" for d, v in w.items()), "| 高値", f"{c.max():.2f}", c.idxmax().date())
