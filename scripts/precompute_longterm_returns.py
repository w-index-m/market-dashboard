"""
長期の複利シミュレーション用に、米国の株式・REIT・国債の「配当・利子込みの月次トータルリターン指数」を
作って data/longterm_returns.json に保存する（GitHub Actionsで月1回実行）。

  sp500      : VFINX（バンガード500インデックス・ファンド、1980〜）の分配金再投資後の価格
  reit_idx   : VGSIX（バンガード・リアルエステート・インデックス、1996〜、米国REIT指数連動）
  reit_fund  : FRESX（フィデリティ・リアルエステート・インベストメント、1986〜、アクティブ運用）
  tsy_long   : VUSTX（バンガード長期米国債、1986〜）
  tsy_mid    : VFITX（バンガード中期米国債、1991〜）
  tsy10_synth: FRED GS10（10年国債利回り、1953〜）から計算した「10年国債の合成トータルリターン」
               （毎月、前月末の利回りをクーポンとする残存約9.9年の債券を、当月末の利回りで再評価。
                 実在のファンドではなく利回りからの推定値）
  cpi        : FRED CPIAUCSL（消費者物価指数）。インフレ調整（実質）に使う。
いずれも月末値。ファンドは経費率控除後の実績値で、指数そのものより少し低くなる。
"""
import io
import json
import sys
import warnings
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")
OUTPUT_PATH = "data/longterm_returns.json"
FUNDS = {"sp500": "VFINX", "reit_idx": "VGSIX", "reit_fund": "FRESX", "tsy_long": "VUSTX", "tsy_mid": "VFITX"}


def _month_end(s: pd.Series) -> pd.Series:
    try:
        m = s.resample("ME").last()
    except ValueError:
        m = s.resample("M").last()
    m = m.dropna()
    m.index = m.index.strftime("%Y-%m")
    return m


def _fred(series_id: str) -> pd.Series:
    r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}", timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "v"]
    df["date"] = pd.to_datetime(df["date"])
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    return df.dropna().set_index("date")["v"]


def _bond_price(coupon: float, y: float, n_years: float = 9.9167, freq: int = 2) -> float:
    """額面1、年利率couponの債券を、利回りyで評価した価格。coupon・yは小数（0.04=4%）。"""
    n = n_years * freq
    c, i = coupon / freq, y / freq
    if i <= 0:
        return 1 + c * n
    return c * (1 - (1 + i) ** -n) / i + (1 + i) ** -n


def _synth_10y(gs10: pd.Series) -> pd.Series:
    y = (gs10 / 100.0).sort_index()
    idx, level, prev = [], 1.0, None
    for d, v in y.items():
        if prev is not None:
            level *= prev / 12 + _bond_price(prev, v)
        prev = v
        idx.append(level)
    s = pd.Series(idx, index=y.index)
    s.index = s.index.strftime("%Y-%m")
    return s


def main() -> None:
    out, errors = {}, []
    for key, tk in FUNDS.items():
        try:
            h = yf.Ticker(tk).history(period="max", auto_adjust=True)["Close"].dropna()
            h.index = h.index.tz_localize(None) if h.index.tz is not None else h.index
            m = _month_end(h)
            out[key] = {k: round(float(v / m.iloc[0]), 5) for k, v in m.items()}
            print(key, tk, m.index[0], m.index[-1], len(m))
        except Exception as e:
            errors.append(f"{key}: {e}")
    try:
        s = _synth_10y(_fred("GS10"))
        out["tsy10_synth"] = {k: round(float(v), 5) for k, v in s.items()}
        print("tsy10_synth", s.index[0], s.index[-1], len(s))
        cpi = _month_end(_fred("CPIAUCSL"))
        out["cpi"] = {k: round(float(v), 3) for k, v in cpi.items()}
        print("cpi", cpi.index[0], cpi.index[-1], len(cpi))
    except Exception as e:
        errors.append(f"fred: {e}")
    for e in errors:
        print("ERROR", e, file=sys.stderr)
    if not {"sp500", "tsy10_synth", "cpi"} <= set(out):
        print("必須系列が取れなかったため、既存ファイルを残して終了")
        sys.exit(1)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "series": out},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUTPUT_PATH}: {sorted(out)}")


if __name__ == "__main__":
    main()
