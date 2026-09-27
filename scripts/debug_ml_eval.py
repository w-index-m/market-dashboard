"""
app.pyの機械学習モデル（日経・米国株の翌日方向予測、下落リスクモデル）を、制限のない
GitHub Actionsランナー上で実データで動かし、単純な基準と比べた実力を確かめるデバッグ用
スクリプト。app.pyの該当範囲のソースをそのまま抜き出して実行する（streamlitだけスタブ）。
"""
import logging
import re
import traceback
import types
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pytz
import requests
import yfinance as yf
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("debug_ml")
_src = open("app.py", encoding="utf-8").read()


def _extract(start_marker: str, end_marker: str) -> str:
    s = _src.index(start_marker)
    return _src[s:_src.index(end_marker, s)]


_ns = {
    "np": np, "pd": pd, "yf": yf, "re": re, "requests": requests, "logger": logger,
    "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
    "Dict": Dict, "Any": Any, "Optional": Optional, "List": List, "Tuple": Tuple, "JST": pytz.timezone("Asia/Tokyo"),
    "st": types.SimpleNamespace(cache_data=lambda **_k: (lambda f: f)),
    "TTL_DAILY": 3600, "TTL_INTRADAY": 300, "SKLEARN_AVAILABLE": True, "XGB_AVAILABLE": False,
    "LogisticRegression": LogisticRegression, "StandardScaler": StandardScaler, "accuracy_score": accuracy_score,
}
exec(_extract("def _calc_rsi(", "@st.cache_data(ttl=TTL_DAILY, show_spinner=False)\n"), _ns)
exec(_extract("@st.cache_data(ttl=TTL_DAILY, show_spinner=False)\ndef _fetch_jgb10y_history(",
              "\ndef render_macro_indicators("), _ns)
exec(_extract("@st.cache_data(ttl=TTL_DAILY, show_spinner=False)\ndef run_backtest_us(",
              "@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)\ndef compute_ensemble_us("), _ns)
exec(_extract("# ── 下落リスクモデル", "\ndef render_drawdown_risk_model("), _ns)
exec(_extract("@st.cache_data(ttl=TTL_DAILY, show_spinner=False)\ndef run_backtest(",
              "\n# =====================================================\n# ③"), _ns)

KEYS = ("accuracy", "baseline", "lift", "auc", "brier_skill")


def _show(title, fn):
    print(f"=== {title} ===")
    try:
        fn()
    except Exception:
        traceback.print_exc()


def _us():
    for sym in ("^GSPC", "^IXIC"):
        bt = _ns["run_backtest_us"](sym, lookback_years=3)
        if not bt.get("ok"):
            print(sym, "backtest failed:", bt.get("reason"))
            continue
        ml = _ns["optimize_weights_ml_us"](bt)
        ev = ml.get("eval") or {}
        print(f"  {sym}: n={bt['n_samples']} up_rate={bt['up_rate']}% ->", {k: ev.get(k) for k in KEYS},
              "| prob_up_now:", round(ml.get("optimized_prob", 0), 1))


def _jp():
    bt = _ns["run_backtest"]("^N225", lookback_years=2)
    if not bt.get("ok"):
        print("N225 backtest failed:", bt.get("reason"))
        return
    ml = _ns["optimize_weights_ml"](bt)
    ev = ml.get("eval") or {}
    print(f"  ^N225: n={len(bt['y'])} ->", {k: ev.get(k) for k in KEYS})


def _dd():
    dd = _ns["compute_drawdown_risk_model"]()
    if not dd.get("ok"):
        print("drawdown model failed:", dd.get("reason"))
        return
    ev = dd["eval"]
    print(f"  as_of={dd['as_of']} train={dd['train_start']}~ n={dd['n_train']} vix={dd['vix_now']} pct={dd['vix_pct']}")
    print(f"  prob_now={dd['prob_now']}% base_rate={dd['base_rate']}% ratio={dd['ratio']} bucket={dd['bucket_label']}")
    for row in dd["table"]:
        print("   ", row)
    print("  eval:", {k: ev.get(k) for k in ("prevalence", "pr_auc", "precision", "recall", "alert_rate", "auc")})
    print("  calibration:", ev.get("calibration"))
    print("  folds:", ev.get("folds"))


_show("US direction models (existing)", _us)
_show("Nikkei direction model (existing)", _jp)
_show("Drawdown risk model (new)", _dd)
