#!/usr/bin/env python3
"""台幣匯率 LINE 通知

每天抓臺灣銀行美元牌告匯率，跟上一次紀錄比較，用 LINE Messaging API 推播：
  - 台幣今天升值或貶值多少
  - 距離你的「換美元目標匯率」還差多少，達標時特別提醒
  - 單日大幅升貶時加註提醒

只用 Python 標準函式庫，不需要安裝任何套件。

環境變數：
  LINE_CHANNEL_ACCESS_TOKEN  LINE Messaging API 的 Channel access token（必填，除非 DRY_RUN=1）
  TARGET_RATE                換美元目標匯率，預設 31.5（即期賣出價低於等於這個數字就提醒）
  BIG_MOVE_PCT               單日升貶超過幾 % 算大幅波動，預設 0.5
  HISTORY_FILE               歷史紀錄檔，預設 fx_history.json
  DRY_RUN                    設為 1 時只印出訊息、不送 LINE
  SAMPLE_CSV                 測試用：指定一個本機 CSV 檔取代線上抓取
"""

import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BOT_CSV_URL = "https://rate.bot.com.tw/xrt/flcsv/0/day"
FALLBACK_URL = "https://open.er-api.com/v6/latest/USD"
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
TW = timezone(timedelta(hours=8))
UA = {"User-Agent": "fx-line-notify/1.0"}


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_bot_csv(text):
    """回傳 (即期買入, 即期賣出)。臺銀 CSV 每列：幣別,本行買入,現金,即期,...,本行賣出,現金,即期,..."""
    for row in csv.reader(io.StringIO(text)):
        if not row or row[0].strip() != "USD":
            continue
        cells = [c.strip() for c in row]
        buy_i = cells.index("本行買入")
        sell_i = cells.index("本行賣出")
        spot_buy = float(cells[buy_i + 2])
        spot_sell = float(cells[sell_i + 2])
        if spot_buy <= 0 or spot_sell <= 0:
            raise ValueError("臺銀 USD 即期匯率為 0")
        return spot_buy, spot_sell
    raise ValueError("臺銀 CSV 找不到 USD")


def fetch_rate():
    """優先用臺灣銀行牌告；抓不到時改用國際中間價（並在訊息註明來源）。"""
    sample = os.environ.get("SAMPLE_CSV")
    if sample:
        with open(sample, encoding="utf-8-sig") as f:
            buy, sell = parse_bot_csv(f.read())
        return {"spot_buy": buy, "spot_sell": sell, "source": "臺灣銀行（測試檔）"}
    try:
        buy, sell = parse_bot_csv(http_get(BOT_CSV_URL).decode("utf-8-sig"))
        return {"spot_buy": buy, "spot_sell": sell, "source": "臺灣銀行即期"}
    except Exception as e:  # noqa: BLE001
        print(f"臺銀匯率抓取失敗，改用備援：{e}", file=sys.stderr)
    data = json.loads(http_get(FALLBACK_URL))
    mid = float(data["rates"]["TWD"])
    # 備援只有中間價，用常見價差 0.1 元估算買賣價
    return {"spot_buy": round(mid - 0.05, 3), "spot_sell": round(mid + 0.05, 3),
            "source": "國際中間價（臺銀暫時抓不到，數字為估算）"}


def load_history(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(path, history):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history[-400:], f, ensure_ascii=False, indent=1)


def build_message(today, rate, prev, history, target, big_pct):
    sell = rate["spot_sell"]
    md = f"{today.month}/{today.day}"
    lines = [f"💱 台幣匯率 {md}", f"美元即期賣出：{sell:.3f}"]

    if prev:
        diff = sell - prev["spot_sell"]
        pct = diff / prev["spot_sell"] * 100
        _, pm, pdd = prev["date"].split("-")
        pd = f"{int(pm)}/{int(pdd)}"
        if abs(diff) < 0.0005:
            lines.append(f"與 {pd} 相同（{prev['spot_sell']:.3f}）")
        else:
            word = "升值" if diff < 0 else "貶值"
            lines.append(f"台幣{word} {abs(diff):.3f}（{abs(pct):.2f}%），{pd} 為 {prev['spot_sell']:.3f}")
            if abs(pct) >= big_pct:
                lines.append(f"⚠️ 單日{word}超過 {big_pct}%，波動較大")
    else:
        lines.append("（第一筆紀錄，明天起會顯示升貶）")

    recent = [h["spot_sell"] for h in history[-20:]]
    if len(recent) >= 5:
        lines.append(f"近 {len(recent)} 筆：最強 {min(recent):.3f}／最弱 {max(recent):.3f}")

    if sell <= target:
        lines.append(f"🔔 已達換匯目標 {target:.2f}！台幣夠強，可以考慮分批換美元")
    else:
        lines.append(f"距離目標 {target:.2f} 還差 {sell - target:.2f} 元")

    if "估算" in rate["source"]:
        lines.append(f"資料來源：{rate['source']}")
    return "\n".join(lines)


def send_line(token, text):
    body = json.dumps({"messages": [{"type": "text", "text": text}]}).encode("utf-8")
    req = urllib.request.Request(
        LINE_BROADCAST_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}", **UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"LINE 推播失敗（HTTP {e.code}）：{detail}") from None


def main():
    target = float(os.environ.get("TARGET_RATE") or 31.5)
    big_pct = float(os.environ.get("BIG_MOVE_PCT") or 0.5)
    hist_path = os.environ.get("HISTORY_FILE") or "fx_history.json"
    dry = os.environ.get("DRY_RUN") == "1"
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not dry and not token:
        raise SystemExit("缺少 LINE_CHANNEL_ACCESS_TOKEN，請到 GitHub 的 Secrets 設定")

    now = datetime.now(TW)
    today_s = now.strftime("%Y-%m-%d")
    rate = fetch_rate()
    history = [h for h in load_history(hist_path) if h.get("date") != today_s]
    prev = history[-1] if history else None

    # 國定假日臺銀不掛新牌價，數字會和上次完全一樣：不重複通知
    if prev and prev.get("spot_sell") == rate["spot_sell"] and prev.get("spot_buy") == rate["spot_buy"]:
        print(f"匯率與 {prev['date']} 完全相同（可能是假日），不通知")
        return

    history.append({"date": today_s, "spot_buy": rate["spot_buy"],
                    "spot_sell": rate["spot_sell"], "source": rate["source"]})
    msg = build_message(now, rate, prev, history, target, big_pct)
    print(msg)
    if dry:
        print("\n[DRY_RUN] 沒有送出 LINE")
    else:
        send_line(token, msg)
        print("\n已送出 LINE 通知")
    save_history(hist_path, history)


if __name__ == "__main__":
    main()
