# 台幣匯率 LINE 通知

每個工作天台灣時間 16:40 左右，自動抓玉山銀行美元即期牌告匯率，推播到你的 LINE：

```
💱 台幣匯率 11/5
美元即期賣出：32.220
台幣升值 0.330（1.01%），11/4 為 32.550
⚠️ 單日升值超過 0.5%，波動較大
近 6 筆：最強 32.220／最弱 32.550
距離目標 31.50 還差 0.72 元
```

匯率低於等於你的目標時，會改成「🔔 已達換匯目標」。國定假日銀行沒有新牌價，不會重複通知。

> 為什麼不是臺灣銀行？臺銀網站對海外伺服器（GitHub 機房在美國）會跳出機器人驗證頁，程式抓不到。玉山銀行有公開的匯率 API，即期牌告和臺銀通常只差幾分錢。
全部在 GitHub 上免費執行，你的電腦不用開著。

> LINE Notify 已在 2025/3/31 停止服務，所以這裡用 LINE 官方建議的 Messaging API。

---

## 一、建立 LINE 官方帳號（取得推播用的 token）

1. 到 **LINE Official Account Manager**（https://manager.line.biz）用你的 LINE 登入，建立一個官方帳號，名稱隨意，例如「我的匯率通知」。
2. 進入這個帳號 → 右上角 **設定** → **Messaging API** → **啟用 Messaging API**，選一個 Provider（沒有就新建一個，名稱隨意）。
3. 到 **LINE Developers Console**（https://developers.line.biz/console/）→ 點剛剛的 Provider → 點這個 Channel → **Messaging API** 分頁。
4. 拉到最下面 **Channel access token (long-lived)** → 按 **Issue** → 複製這串 token。
5. 同一頁上方有 **QR code**，用手機 LINE 掃描，把這個官方帳號**加為好友**（沒加好友收不到通知）。
6. （建議）回到 Official Account Manager → **回應設定**，把「自動回應訊息」關掉，避免它亂回你。

免費方案每月有一定的免費訊息則數，每個工作天一則（一個月約 22 則）通常夠用，額度可以在 Official Account Manager 查看。

## 二、放到 GitHub 自動執行

1. 在 GitHub 建立一個新的 **Private** repository，例如 `fx-line-notify`。
2. 把這個資料夾的檔案上傳上去（網頁上「Add file → Upload files」，把整個資料夾拖進去，要包含 `.github/workflows/fx-notify.yml`）。
3. repo → **Settings** → **Secrets and variables** → **Actions**：
   - **Secrets** 分頁 → New repository secret：名稱 `LINE_CHANNEL_ACCESS_TOKEN`，值貼上第一步的 token。
   - （選填）**Variables** 分頁 → New repository variable：
     - `TARGET_RATE`：換美元目標匯率，預設 `31.5`
     - `BIG_MOVE_PCT`：單日升貶超過幾 % 加註提醒，預設 `0.5`
4. repo → **Settings** → **Actions** → **General** → 最下面 **Workflow permissions** 選 **Read and write permissions** → Save（讓它能存匯率紀錄）。
5. repo → **Actions** 分頁 → 左邊點「台幣匯率 LINE 通知」→ **Run workflow**，手動跑一次。幾十秒後 LINE 應該會收到第一則訊息。

之後每個工作天會自動執行，紀錄存在 `fx_history.json`。

## 常見問題

- **沒收到訊息**：到 Actions 分頁點開那次執行看錯誤。`401` 代表 token 貼錯；成功卻沒收到，通常是還沒把官方帳號加好友。
- **時間不準時**：GitHub 排程尖峰時可能延遲幾分鐘到半小時，屬正常現象。
- **想改通知時間**：改 `.github/workflows/fx-notify.yml` 裡的 `cron`，注意它用 UTC 時間（台灣時間減 8 小時）。
- **想在自己電腦測試**：`DRY_RUN=1 python fx_notify.py` 只印出訊息、不送 LINE。
- **用的是哪個匯率**：玉山銀行美元「即期賣出價」，也就是在帳戶裡用台幣買美元時的價格，和試算網站的「換匯時匯率」一致。匯率 App 預設常顯示「現金匯率」（領現鈔用），會比即期高約 0.2 元，比較時要切到「即期匯率」。玉山抓不到時會改用國際中間價估算，訊息會註明。
