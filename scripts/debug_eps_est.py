"""一株予想益（アナリスト予想）がyfinanceで取れるか確認する（Actions上で実行）。"""
import warnings

import yfinance as yf

warnings.filterwarnings("ignore")
for t in ["AAPL", "MSFT", "KO", "JNJ", "7203.T", "8306.T", "9432.T"]:
    try:
        tk = yf.Ticker(t)
        info = tk.info or {}
        print("==", t, "trailEPS", info.get("trailingEps"), "fwdEPS", info.get("forwardEps"), "epsCY", info.get("epsCurrentYear"),
              "PE", info.get("trailingPE"), "fwdPE", info.get("forwardPE"), "peg", info.get("pegRatio") or info.get("trailingPegRatio"),
              "nAnalyst", info.get("numberOfAnalystOpinions"))
        try:
            ee = tk.earnings_estimate
            if ee is not None and not ee.empty:
                print(ee[["avg", "low", "high", "yearAgoEps", "numberOfAnalysts", "growth"]].round(2).to_string())
            else:
                print("  earnings_estimate: empty")
        except Exception as e:
            print("  earnings_estimate ERR", str(e)[:80])
    except Exception as e:
        print(t, "ERR", e)
