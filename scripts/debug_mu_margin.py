"""MUの売上・粗利率・営業利益率・設備投資・FCFの推移（年次と四半期）をActions上で取得する。MUはアプリの固定バスケットにある銘柄。"""
import warnings

import yfinance as yf

import app

warnings.filterwarnings("ignore")
tk = yf.Ticker("MU")


def show(df, title, keys):
    print(title)
    if df is None or df.empty:
        print("  なし")
        return
    cols = sorted(df.columns)
    for c in cols:
        row = {}
        for lab, k in keys.items():
            row[lab] = df.loc[k, c] if k in df.index else None
        rev = row.get("売上")
        def pct(x):
            return f"{x / rev * 100:.0f}%" if x is not None and x == x and rev else "—"
        print(f"  {c.date()} 売上{(rev or 0) / 1e9:.1f}B 粗利率{pct(row.get('粗利'))} 営業利益率{pct(row.get('営業利益'))} 純利益率{pct(row.get('純利益'))} "
              f"EPS{row.get('EPS')}")

K = {"売上": "Total Revenue", "粗利": "Gross Profit", "営業利益": "Operating Income", "純利益": "Net Income", "EPS": "Diluted EPS"}
show(tk.income_stmt, "年次（会計年度は8月期）", K)
show(tk.quarterly_income_stmt, "四半期", K)
for name, df in (("年次CF", tk.cashflow), ("四半期CF", tk.quarterly_cashflow)):
    print(name)
    for c in sorted(df.columns):
        cap = df.loc["Capital Expenditure", c] if "Capital Expenditure" in df.index else None
        fcf = df.loc["Free Cash Flow", c] if "Free Cash Flow" in df.index else None
        ocf = df.loc["Operating Cash Flow", c] if "Operating Cash Flow" in df.index else None
        print(f"  {c.date()} 営業CF{(ocf or 0) / 1e9:.1f}B 設備投資{abs(cap or 0) / 1e9:.1f}B FCF{(fcf or 0) / 1e9:.1f}B")
# 過去のPER（決算日の株価÷直近4決算EPS）
f = app._fetch_fundamentals_history("MU")
eh = [x for x in f.get("eps_hist", []) if x.get("a") is not None]
px = f.get("px_at", {})
print("過去のPER（決算日の株価÷直近4決算のEPS合計）と、そのTTM EPS")
for i in range(3, len(eh)):
    ttm = sum(x["a"] for x in eh[i - 3:i + 1])
    p0 = px.get(eh[i]["d"])
    if p0 and ttm > 0 and i % 2 == 1:
        print(f"  {eh[i]['d']} 株価{p0:.0f} TTM EPS {ttm:.2f} PER {p0 / ttm:.1f}")
