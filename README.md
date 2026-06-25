# Kobo 99 元書單行事曆 (ICS)

自動爬取 KOBO 部落格「一週99元書單」文章、解析書單並輸出可訂閱的 `.ics` (iCalendar) 檔案，供 Google 日曆「從網址新增」訂閱。
訂閱資訊：[複製網址](https://oshukezu.github.io/KOBO99-ics/public/kobo99.ics)
## 說明
這個專案會在每週四 07:00（台北時間）執行，也會在 push 到 `main` 時更新資料並部署到 GitHub Pages。首頁會自動依照目前網址產生 Google Calendar 訂閱連結。
`public/events.json` 是累積資料庫。腳本預設會保留既有資料並合併新抓到的選書，所以過去已寫入的歷史資料不會在每週更新時被清掉。

## 如何新增日曆
- 開啟 [Google 日曆](https://calendar.google.com/)
- 點選右上角 齒輪 > 設定
- 左側 一般 > 新增日曆 > [加入日曆網址](https://oshukezu.github.io/KOBO99-ics/public/kobo99.ics)

<img width="809" height="371" alt="image" src="https://github.com/user-attachments/assets/ef6de6e8-a740-497a-9e66-0cc912edf561" />

## 本地開發與執行

本專案使用 Python 與 Node.js (Puppeteer) 進行開發，Python 部分僅使用標準庫，無外部依賴。為維護環境乾淨，推薦使用 `uv` 管理虛擬環境。

### 1. 準備環境
請確保已安裝 `uv` 以及 `Node.js`。

```bash
# 建立並啟用虛擬環境 (.venv)
uv venv
source .venv/bin/activate  # macOS / Linux

# 安裝 Node.js 依賴 (爬蟲所需的 Puppeteer)
npm install
```

### 2. 執行指令
```bash
# 僅重新渲染 HTML 與 ICS 檔案（不爬取官網）
python scripts/kobo99.py --render-only

# 爬取當週與前後週的最新特價資料並更新
python scripts/kobo99.py --out public

# 執行回填歷史資料（例如回填 2025 年所有資料）
python scripts/kobo99.py --out public --history-start-year 2025
```

## 免責與技術限制宣告 (Disclaimer & Limitations)
本專案為個人非營利性質之自動化開源工具，所產生之行事曆與書單資料僅供個人閱讀參考。使用本工具前請知悉以下限制：

- 資訊以 Kobo 官網為最終依準
本專案所有書單、價格、優惠時效及活動細節，完全以 Kobo 樂天Calendar官方網站/部落格 當下實際公布之內容為準。本腳本不保證產出資料之絕對即時性與準確性。

- 資料擷取可能存在遺漏或不完整
由於網路傳輸延遲、Kobo 網頁結構變更、或是單一文章內文格式不一，自動化腳本在極端情況下（例如：當天同時有兩本以上特價書，但網頁排版格式異常）可能發生僅成功擷取到其中一本、甚至漏爬之情況。

- 無防封鎖與規避機制（反爬蟲限制）
本腳本為保持低負載與合規爬取，僅使用基礎節流與重試機制。若 Kobo 官方加強反爬蟲機制（如強制跳出驗證碼 CAPTCHA、430/403 封鎖），腳本將會自動中斷執行並保留既有歷史檔案，不保證每次皆能順利更新當週最新書單。

- 訂閱同步時間差
經由本專案產出之 .ics 檔案，其更新速度亦取決於您所使用的行事曆軟體（如 Google Calendar, Apple Calendar）的重新整理頻率（Google Calendar 同步可能有 8-24 小時之延遲），無法確保即時同步。
