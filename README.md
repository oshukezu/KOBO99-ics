# Kobo 99 元書單行事曆

自動爬取 KOBO 部落格「一週99元書單」文章、解析書單並輸出可訂閱的 `.ics` (iCalendar) 檔案，供 Google 日曆「從網址新增」訂閱。
訂閱資訊：[複製網址](https://oshukezu.github.io/KOBO99Codex/public/kobo99.ics)
## 說明
這個專案會在每週四 07:00（台北時間）執行，也會在 push 到 `main` 時更新資料並部署到 GitHub Pages。首頁會自動依照目前網址產生 Google Calendar 訂閱連結。
`public/events.json` 是累積資料庫。腳本預設會保留既有資料並合併新抓到的選書，所以過去已寫入的歷史資料不會在每週更新時被清掉。

## 如何新增日曆
- 開啟 [Google 日曆](https://calendar.google.com/)
- 點選右上角 齒輪 > 設定
- 左側 一般 > 新增日曆 > [加入日曆網址](https://oshukezu.github.io/KOBO99Codex/public/kobo99.ics)

<img width="920" height="359" alt="image" src="https://github.com/user-attachments/assets/d5e686ba-d9aa-4a0f-ab17-85c720522143" />

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
