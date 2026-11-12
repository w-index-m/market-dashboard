#!/usr/bin/env python3
"""
自動更新（GitHub Actions）の健全性を毎日確認し、問題があればLINE・Slackへ知らせる。
 ① 主要ワークフローの直近の実行が「失敗」になっていないか（GitHub API）
 ② 事前計算データ（data/*.json）の更新が古くなっていないか（generated_at）
問題が無ければ何も送らない（毎週月曜だけ「異常なし」の簡単な報告を送る）。
環境変数: GITHUB_TOKEN, GITHUB_REPOSITORY（Actionsが自動設定）, LINE_CHANNEL_ACCESS_TOKEN / SLACK_WEBHOOK_URL
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

# ワークフローのファイル名 → 想定する更新間隔（時間）。この間隔を超えて成功が無ければ「遅れ」とみなす
WORKFLOWS = {
    "precompute-sp600-candidates.yml": 36, "earnings-review.yml": 72, "ai-forecast-log.yml": 72,
    "marks-valuation.yml": 72, "rising-candidates.yml": 72, "mode-backtest.yml": 72, "options-cache.yml": 72,
    "fundamentals-cache.yml": 24 * 5, "holdings-alerts.yml": 72, "daily-portfolio-line.yml": 48,
    "daily-asset-snapshot.yml": 48, "selloff-watch.yml": 96, "edgar-watch.yml": 24, "cot-positions.yml": 24 * 9, "tokyo-re-index.yml": 24 * 9, "longterm-returns.yml": 24 * 35, "marks-backtest.yml": 24 * 35,
}
# データファイル → 許容する古さ（時間）
DATA_FILES = {
    "data/rising_candidates.json": 72, "data/mode_backtest.json": 72, "data/marks_valuation.json": 72,
    "data/earnings_reviews.json": 72, "data/ai_forecasts.json": 72, "data/sp600_candidates.json": 48,
    "data/jp_tenbagger_raw.json": 48, "data/cot_positions.json": 24 * 10, "data/longterm_returns.json": 24 * 40,
    "data/tokyo_re_index.json": 24 * 10, "data/marks_backtest.json": 24 * 40, "data/selloff_log.json": 96,
}


def content_problems(now: datetime) -> list:
    """データファイルの『中身』の異常を検査する（更新日時だけでは気づけない不具合: 値が空・古い扱い・前日と同じ値など）。"""
    out = []
    # ① 急落の記録（毎営業日の判定とRSI）
    try:
        with open("data/selloff_log.json", encoding="utf-8") as f:
            ents = (json.load(f).get("entries") or [])
        if not ents:
            out.append("🧪 data/selloff_log.json: 記録が1件もありません")
        else:
            last = ents[-1]
            last_d = datetime.strptime(last["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if (now - last_d).days > 5:
                out.append(f"🧪 data/selloff_log.json: 最新の記録が古い（{last['date']}）")
            recent = ents[-5:]
            empty = [e["date"] for e in recent if not e.get("groups") or not e.get("rsi") or not e.get("indexes")]
            if empty:
                out.append(f"🧪 data/selloff_log.json: 主要な値（グループ別騰落・RSI・指数）が空の日があります: {', '.join(empty)}")
            many_stale = [e["date"] for e in recent if len(e.get("stale_skipped") or []) >= 8]
            if len(many_stale) >= 2:
                out.append(f"🧪 data/selloff_log.json: 多数の銘柄が「古いデータ」として除外された日が続いています: {', '.join(many_stale)}")
            if len(ents) >= 2 and ents[-1].get("indexes") and ents[-1].get("indexes") == ents[-2].get("indexes"):
                out.append(f"🧪 data/selloff_log.json: {ents[-1]['date']} の指数の騰落が前日と全く同じです（取得が1日遅れた可能性）")
    except FileNotFoundError:
        out.append("🧪 data/selloff_log.json: ファイルがありません")
    except Exception as e:
        out.append(f"🧪 data/selloff_log.json: 検査エラー（{str(e)[:40]}）")
    # ② 上昇銘柄スクリーナーの母集団（約1,300社のはず）
    try:
        with open("data/rising_candidates.json", encoding="utf-8") as f:
            n = len(json.load(f).get("items") or {})
        if n < 800:
            out.append(f"🧪 data/rising_candidates.json: 銘柄数が少なすぎます（{n}社。通常は約1,300社）")
    except Exception as e:
        out.append(f"🧪 data/rising_candidates.json: 検査エラー（{str(e)[:40]}）")
    # ③ ニュースメモ（手動更新）: 14日以上更新がなければ、お知らせ
    try:
        with open("data/news_memo.json", encoding="utf-8") as f:
            u = json.load(f).get("updated_at", "")
        age = (now - datetime.strptime(u, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
        if age > 14:
            out.append(f"📝 data/news_memo.json: {age}日間更新されていません（手動メモ。古い内容が表示されている可能性）")
    except Exception:
        pass
    return out


def _post(text: str) -> None:
    line, slack = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""), os.environ.get("SLACK_WEBHOOK_URL", "")
    if line:
        try:
            requests.post("https://api.line.me/v2/bot/message/broadcast",
                          headers={"Authorization": f"Bearer {line}", "Content-Type": "application/json"},
                          json={"messages": [{"type": "text", "text": text[:4900]}]}, timeout=15).raise_for_status()
        except Exception as e:
            print(f"LINE failed: {e}", file=sys.stderr)
    if slack:
        try:
            requests.post(slack, json={"text": text}, timeout=15).raise_for_status()
        except Exception as e:
            print(f"Slack failed: {e}", file=sys.stderr)


def main() -> None:
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", "")
    now = datetime.now(timezone.utc)
    problems = []
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"} if token else {}
    for wf, max_h in WORKFLOWS.items():
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs", headers=hdr,
                             params={"per_page": 8, "status": "completed"}, timeout=20)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            runs = [x for x in r.json().get("workflow_runs", []) if x.get("event") in ("schedule", "workflow_dispatch")]
        except Exception as e:
            print(f"{wf}: API error {e}", file=sys.stderr)
            continue
        if not runs:
            continue
        latest = runs[0]
        ok = next((x for x in runs if x.get("conclusion") == "success"), None)
        if latest.get("conclusion") == "failure":
            problems.append(f"❌ {wf}: 直近の実行が失敗（{latest['created_at'][:16].replace('T', ' ')} UTC）")
        elif ok is None or now - datetime.fromisoformat(ok["updated_at"].replace("Z", "+00:00")) > timedelta(hours=max_h):
            problems.append(f"⏳ {wf}: {max_h}時間以上、成功した実行がありません")
    for path, max_h in DATA_FILES.items():
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            gen = d.get("generated_at") or d.get("updated_at")   # ai_forecasts.json は updated_at を使う
            age = now - datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if age > timedelta(hours=max_h):
                problems.append(f"🗂️ {path}: 更新が古い（{int(age.total_seconds() // 3600)}時間前）")
        except FileNotFoundError:
            problems.append(f"🗂️ {path}: ファイルがありません")
        except Exception as e:
            problems.append(f"🗂️ {path}: 読み取りエラー（{str(e)[:40]}）")
    problems += content_problems(now)
    stamp = now.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M")
    if problems:
        text = f"🩺 自動更新のヘルスチェック（{stamp} JST）：{len(problems)}件の問題\n\n" + "\n".join(problems) + \
               "\n\n※ GitHubのActionsタブで該当のワークフローを確認してください。"
        print(text)
        _post(text)
        print(f"::notice title=health::problems={len(problems)}")
    else:
        print(f"all ok ({stamp} JST)")
        print("::notice title=health::all ok")
        if now.weekday() == 0:   # 月曜だけ、異常なしの報告を送る
            _post(f"🩺 自動更新のヘルスチェック（{stamp} JST）：異常なし（{len(WORKFLOWS)}のワークフロー・{len(DATA_FILES)}のデータを確認）")


if __name__ == "__main__":
    main()
