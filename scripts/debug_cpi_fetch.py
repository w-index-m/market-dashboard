"""
app.pyの米CPI取得（FRED CSV → BLS APIフォールバック）を、制限のないGitHub Actions
ランナー上で実際に実行して確かめるデバッグ用スクリプト。app.pyの該当関数ソースを
そのまま抜き出して実行する（streamlit等の重い依存を読み込まずに本物のコードを試す）。
"""
import logging
import re
from datetime import datetime
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_cpi")

_src = open("app.py", encoding="utf-8").read()


def _extract(start_marker: str, end_marker: str) -> str:
    s = _src.index(start_marker)
    return _src[s:_src.index(end_marker, s)]


_ns = {"requests": requests, "datetime": datetime, "Optional": Optional, "logger": logger, "re": re}
exec(_extract("def _fetch_us_cpi_yoy(", "\n# FREDのシリーズID"), _ns)
exec(_extract("_FRED_TO_BLS_CPI = ", "\n\n\n@st.cache_data"), _ns)

for sid in ("CPIAUCSL", "CPILFESL"):
    print(f"[FRED→BLS chain] {sid}: {_ns['_fetch_us_cpi_yoy'](sid)}")
    print(f"[BLS only]       {sid}: {_ns['_fetch_us_cpi_yoy_bls'](sid)}")
