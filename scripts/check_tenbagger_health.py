"""
毎日のデータ事前計算の後に、🌱長期育成・🚀日本株10倍株候補の2モードが「サイトで実際に数字が
出る状態か」を、本番と同じapp.pyのコード・実データ・実AIで確かめるヘルスチェック。

チェック内容:
  1. data/配下の事前計算ファイルが新しい（48時間以内）か、件数が十分か
  2. 候補選定関数が候補を返すか（サイトの推奨ポートフォリオが使うもの）
  3. 全モード比較表の1年・3年リターン（_compute_mode_basket_backtest）が計算できるか
どれかが駄目なら終了コード1で終わり、GitHub Actionsのワークフローを失敗させる
（リポジトリの持ち主にGitHubから失敗通知が届く）。
"""
import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

import app  # noqa: E402

MAX_AGE_HOURS = 48
problems = []


def check(ok: bool, label: str, detail: str):
    print(f"{'✅' if ok else '❌'} {label}: {detail}")
    if not ok:
        problems.append(label)


def age_hours(ts: str):
    try:
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600
    except Exception:
        return None


print(f"=== Tenbagger health check ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC) ===")

sp = app._load_precomputed_sp600_candidates()
a = age_hours((sp or {}).get("generated_at") or "")
check(bool(sp) and a is not None and a <= MAX_AGE_HOURS and len(sp["candidates"]) >= 400,
      "S&P600データ", f"生成 {sp and sp.get('generated_at')}（{a and round(a, 1)}時間前）"
      f"・{sp and len(sp['candidates'])}銘柄")

jp = app._load_precomputed_jp_tenbagger()
a = age_hours((jp or {}).get("generated_at") or "")
check(bool(jp) and a is not None and a <= MAX_AGE_HOURS and len(jp["tickers"]) >= 400,
      "東証スタンダードデータ", f"生成 {jp and jp.get('generated_at')}（{a and round(a, 1)}時間前）"
      f"・{jp and len(jp['tickers'])}/{jp and jp.get('universe_size')}銘柄")

g = app._fetch_tenbagger_candidates()
check(len(g) >= 5, "🌱長期育成 候補選定", f"{len(g)}銘柄 {list(g)[:8]}")

j = app._fetch_jp_tenbagger_candidates()
check(len(j) >= 3, "🚀日本株10倍株候補 候補選定",
      f"{len(j)}銘柄 " + ", ".join(f"{t}({v.get('name', '')[:8]} {v.get('score_total')}点)" for t, v in list(j.items())[:10]))

for key, label in (("growth", "🌱長期育成"), ("jp_tenbagger", "🚀日本株10倍株候補")):
    bt = app._compute_mode_basket_backtest(key)
    ok = bool(bt.get("ok")) and bt.get("ret_1y") is not None
    if ok:
        r3 = bt.get("ret_3y")
        detail = (f"1年 {bt['ret_1y']:+.1f}% / 3年 {'—' if r3 is None else f'{r3:+.1f}%'}"
                  f" / {bt.get('n_tickers')}銘柄")
    else:
        detail = f"表示されない: {bt.get('reason')}"
    check(ok, f"{label} 全モード比較表", detail)

print("=== 結果:", "すべて正常" if not problems else f"問題あり → {', '.join(problems)}", "===")
sys.exit(1 if problems else 0)
