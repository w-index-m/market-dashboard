"""発行済み株数・現金・負債の推移（増資による希薄化の確認。公開データ・汎用ティッカー）。"""
import yfinance as yf

for s in ("AAOI",):
    t = yf.Ticker(s)
    bs, inc, cf = t.quarterly_balance_sheet, t.quarterly_income_stmt, t.quarterly_cashflow
    for c in sorted(bs.columns, reverse=True)[:7]:
        g = lambda df, *n: next((float(df.at[k, c]) for k in n if k in df.index and c in df.columns and df.at[k, c] == df.at[k, c]), None)   # noqa: E731
        print(c.date(), "株数", g(bs, "Ordinary Shares Number", "Share Issued"), "現金", g(bs, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"),
              "負債", g(bs, "Total Debt"), "売上", g(inc, "Total Revenue"), "株式発行", g(cf, "Issuance Of Capital Stock", "Common Stock Issuance"),
              "営業CF", g(cf, "Operating Cash Flow"), "設備投資", g(cf, "Capital Expenditure"))
