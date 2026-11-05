#!/usr/bin/env python3
"""台幣匯率 LINE 通知

每天抓玉山銀行美元即期牌告匯率（臺銀網站擋海外伺服器），跟上一次紀錄比較，用 LINE Messaging API 推播：
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
  SAMPLE_JSON                測試用：指定一個本機玉山 API JSON 檔取代線上抓取
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# 臺灣銀行網站對海外伺服器（GitHub 機房在美國）會跳出機器人驗證頁，抓不到 CSV，
# 所以改用玉山銀行公開的牌告匯率 API；兩家銀行的即期牌告通常只差幾分錢。
ESUN_URL = "https://www.esunbank.com/api/client/ExchangeRate/LastRateInfo"
FALLBACK_URL = "https://open.er-api.com/v6/latest/USD"
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
TW = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (compatible; fx-line-notify/1.1)"}


def http_get(url, timeout=20, data=None):
    headers = dict(UA)
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_esun(data):
    """玉山 API：Rates 裡 CCY 為 USD/TWD 那筆。BBoardRate/SBoardRate＝即期買入/賣出。"""
    for r in data.get("Rates", []):
        if r.get("CCY") == "USD/TWD":
            spot_buy, spot_sell = float(r["BBoardRate"]), float(r["SBoardRate"])
            if not (20 < spot_buy < 45 and 20 < spot_sell < 45):
                raise ValueError(f"玉山 USD 匯率不合理：{spot_buy}/{spot_sell}")
            cash_sell = r.get("CashSBoardRate")
            ts = ""
            m = str(r.get("UpdateTime", ""))
            if m.startswith("/Date(") and m[6:-2].isdigit():
                ts = datetime.fromtimestamp(int(m[6:-2]) / 1000, TW).strftime("%m/%d %H:%M")
            return {"spot_buy": spot_buy, "spot_sell": spot_sell,
                    "cash_sell": float(cash_sell) if cash_sell not in (None, "", "-") else None,
                    "updated": ts, "source": "玉山銀行牌告", "source_key": "esun"}
    raise ValueError("玉山 API 找不到 USD/TWD")


def fetch_rate():
    """優先用玉山銀行即期牌告；抓不到時改用國際中間價（並在訊息註明是估算）。"""
    sample = os.environ.get("SAMPLE_JSON")
    if sample:
        with open(sample, encoding="utf-8") as f:
            return parse_esun(json.load(f))
    try:
        return parse_esun(json.loads(http_get(ESUN_URL, data=b"{}")))
    except Exception as e:  # noqa: BLE001
        print(f"玉山匯率抓取失敗，改用備援：{e}", file=sys.stderr)
    data = json.loads(http_get(FALLBACK_URL))
    mid = float(data["rates"]["TWD"])
    # 備援只有中間價（每天更新一次），用常見價差 0.1 元估算買賣價
    return {"spot_buy": round(mid - 0.05, 3), "spot_sell": round(mid + 0.05, 3),
            "cash_sell": None, "updated": "",
            "source": "國際中間價（銀行牌告暫時抓不到，數字為估算）", "source_key": "estimate"}


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
    when = f"（{rate['updated']} 掛牌）" if rate.get("updated") else ""
    lines = [f"💱 台幣匯率 {md}", f"美元即期賣出：{sell:.3f}{when}"]
    if rate.get("cash_sell"):
        lines.append(f"現金賣出：{rate['cash_sell']:.3f}（領美金現鈔才用這個）")

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

    same = [h for h in history if h.get("source_key") == rate.get("source_key")]
    recent = [h["spot_sell"] for h in same[-20:]]
    if len(recent) >= 5:
        lines.append(f"近 {len(recent)} 筆：最強 {min(recent):.3f}／最弱 {max(recent):.3f}")

    if sell <= target:
        lines.append(f"🔔 已達換匯目標 {target:.2f}！台幣夠強，可以考慮分批換美元")
    else:
        lines.append(f"距離目標 {target:.2f} 還差 {sell - target:.2f} 元")

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
    # 只和同一個來源的紀錄比較，避免銀行牌告和估算值互比造成假升貶
    same = [h for h in history if h.get("source_key") == rate["source_key"]]
    prev = same[-1] if same else None

    # 國定假日銀行不掛新牌價，數字會和上次完全一樣：不重複通知
    if prev and prev.get("spot_sell") == rate["spot_sell"] and prev.get("spot_buy") == rate["spot_buy"]:
        print(f"匯率與 {prev['date']} 完全相同（可能是假日），不通知")
        return

    history.append({"date": today_s, "spot_buy": rate["spot_buy"], "spot_sell": rate["spot_sell"],
                    "source": rate["source"], "source_key": rate["source_key"]})
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
