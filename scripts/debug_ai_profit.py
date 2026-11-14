"""AIブームの利益の出方: グループ別に、直近の四半期の売上・営業利益・営業利益率と前年同期比を並べる（yfinanceの財務諸表）。"""
import pandas as pd
import yfinance as yf

G = {
 "半導体(GPU/ASIC/ファウンドリ)": ["NVDA", "AVGO", "AMD", "MRVL", "TSM"],
 "メモリ": ["MU"],
 "光・フォトニクス(米)": ["LITE", "COHR", "CIEN", "AAOI"],
 "ネットワーク機器": ["ANET"],
 "ハイパースケーラー": ["MSFT", "GOOGL", "AMZN", "META", "ORCL"],
 "ネオクラウド": ["CRWV"],
 "日本(光ファイバー・ケーブル)": ["5803.T", "5801.T", "5802.T"],
 "日本(製造装置)": ["6857.T", "8035.T", "6146.T"],
}
for g, syms in G.items():
    print("##", g)
    for s in syms:
        try:
            q = yf.Ticker(s).quarterly_income_stmt
            q = q.loc[:, sorted(q.columns, reverse=True)]
            def row(*names):
                for n in names:
                    if n in q.index:
                        return q.loc[n]
                return None
            rev, op, ni = row("Total Revenue", "Operating Revenue"), row("Operating Income"), row("Net Income")
            if rev is None or len(rev) < 5:
                print(" ", s, "データ不足", None if rev is None else len(rev))
                continue
            c = q.columns[0]
            p = q.columns[4] if len(q.columns) > 4 else None

            def yoy(r):
                return None if r is None or p is None or pd.isna(r[c]) or pd.isna(r[p]) or r[p] == 0 else round((r[c] / r[p] - 1) * 100) if r[p] > 0 else "前年赤字"
            def m(r):
                return None if r is None or pd.isna(r[c]) or not rev[c] else round(r[c] / rev[c] * 100, 1)

            u = 1e8 if s.endswith(".T") else 1e9
            print(f"  {s:7s} {c.date()} 売上{rev[c]/u:,.1f} 前年比{yoy(rev)}% | 営業利益率{m(op)}% 営業益前年比{yoy(op)} | 純利益率{m(ni)}% | 前年同期の営業利益率{m(op) if p is None else (round(op[p]/rev[p]*100,1) if op is not None and rev[p] else None)}%")
        except Exception as e:
            print(" ", s, "ERR", type(e).__name__, e)
