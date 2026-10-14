"""先物・オプション画面で使う取得関数が動くか確認する（Actions上、一般的な銘柄）。"""
import app

d = app._fetch_vol_indices()
print("vol indices:", d.shape, list(d.columns), d.dropna().tail(1).round(2).to_dict("records"))
exps = app._fetch_option_expiries("AAPL")
print("expiries:", len(exps), exps[:3])
v = app._fetch_option_chain_view("AAPL", exps[2])
c, p = v["calls"], v["puts"]
print("spot", v["spot"], "calls", len(c), "puts", len(p), "max_pain", app._max_pain(c, p))
atm = app._atm_info(type("C", (), {"calls": c, "puts": p})(), v["spot"], 30)
print("atm", {k: (round(x, 3) if isinstance(x, float) else x) for k, x in atm.items()})
print("valid IV calls:", int((c["impliedVolatility"] > 0.01).sum()))
