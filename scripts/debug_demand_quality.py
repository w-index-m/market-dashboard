"""AI関連の代表銘柄で、四半期ごとの売上・利益率と『実需かどうか』の目安（在庫日数・売掛金日数・営業CF/純利益）を並べる（公開データ・汎用ティッカー）。"""
import pandas as pd
import yfinance as yf

SYMS = ["NVDA", "MU", "LITE", "AAOI"]


def pick(df, *names):
    for n in names:
        if df is not None and n in df.index:
            return df.loc[n]
    return None


for s in SYMS:
    t = yf.Ticker(s)
    inc, bs, cf = t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow
    cols = sorted(inc.columns, reverse=True)[:6]
    rev, gp, op, ni = pick(inc, "Total Revenue"), pick(inc, "Gross Profit"), pick(inc, "Operating Income"), pick(inc, "Net Income")
    cogs = pick(inc, "Cost Of Revenue")
    inv, ar = pick(bs, "Inventory"), pick(bs, "Accounts Receivable", "Receivables")
    ocf, capex = pick(cf, "Operating Cash Flow"), pick(cf, "Capital Expenditure")
    print(f"## {s}（単位: 百万ドル）")
    print("期末        売上   前年比  粗利率  営業利益率  純利益  在庫日数  売掛日数  営業CF  設備投資")
    for c in cols:
        def g(x):
            return None if x is None or c not in x.index or pd.isna(x[c]) else float(x[c])
        r = g(rev)
        prev = [p for p in sorted(inc.columns) if (c - p).days > 330 and (c - p).days < 400]
        yoy = (r / g(rev.rename(None)) if False else None)
        if prev and rev is not None and not pd.isna(rev[prev[-1]]) and rev[prev[-1]]:
            yoy = (r / float(rev[prev[-1]]) - 1) * 100
        def pct(x):
            v = g(x)
            return f"{v / r * 100:5.1f}%" if (v is not None and r) else "   - "
        invd = (g(inv) / g(cogs) * 91) if (g(inv) and g(cogs)) else None
        ard = (g(ar) / r * 91) if (g(ar) and r) else None
        print(f"{c.date()} {r / 1e6:8,.0f} {'' if yoy is None else f'{yoy:+5.0f}%':>7} {pct(gp)} {pct(op):>9} {(g(ni) or 0) / 1e6:8,.0f} "
              f"{'' if invd is None else f'{invd:6.0f}日':>8} {'' if ard is None else f'{ard:6.0f}日':>8} {(g(ocf) or 0) / 1e6:7,.0f} {abs(g(capex) or 0) / 1e6:7,.0f}")
    print()
