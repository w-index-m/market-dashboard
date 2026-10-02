"""米3ヶ月金利の公式値への置換が、実際の米財務省CSV・Yahooのデータで正しく動くかを確認する。"""
import warnings

import yfinance as yf

warnings.filterwarnings("ignore")
import app  # noqa: E402

off = app._fetch_treasury_3m_series()
print(f"公式3ヶ月系列: {len(off)}件 {off.index.min().date() if len(off) else '-'} 〜 {off.index.max().date() if len(off) else '-'}")
print(off.tail(5).to_string())

y = yf.download(["^TNX", "^IRX"], period="1y", progress=False, auto_adjust=True)["Close"]
irx_raw = y["^IRX"].dropna()
irx_new = app._official_3m_yield(irx_raw)
tnx = y["^TNX"].dropna()
print(f"\n^IRX(Yahoo) 最新 {float(irx_raw.iloc[-1]):.3f} → 置換後 {float(irx_new.iloc[-1]):.3f}（{irx_new.index[-1].date()}） 件数 {len(irx_raw)}→{len(irx_new)}")
sp_old = float(tnx.iloc[-1]) - float(irx_raw.iloc[-1])
sp_new = float(tnx.iloc[-1]) - float(irx_new.iloc[-1])
print(f"10Y-3M: 旧 {sp_old:.2f}pt → 新 {sp_new:.2f}pt （公式の10Y-3M目安 1.07pt）")
# 1年前との比較（カードの「カード変化の型」で使う）
print(f"1年前の3ヶ月: ^IRX {float(irx_raw.iloc[0]):.2f} → 公式 {float(irx_new.iloc[0]):.2f}")
