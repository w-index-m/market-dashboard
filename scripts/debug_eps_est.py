"""一株予想益（アナリスト予想）がyfinanceで取れるか確認する（Actions上で実行）。"""
import warnings

import yfinance as yf

warnings.filterwarnings("ignore")
for t in ["AVGO", "NVDA", "MU", "VRT", "LITE", "8306.T", "8001.T", "5803.T", "6857.T", "2801.T", "4912.T", "4967.T", "5334.T", "8058.T", "200A.T", "285A.T"]:
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
