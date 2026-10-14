"""オプションから決算前の想定値動きを概算する関数の動作確認（Actions上、一般的な銘柄）。"""
import app

for t in ("AAPL", "KO", "NVDA", "MU", "7203.T"):
    r = app._fetch_option_summary(t)
    print(t, r)
