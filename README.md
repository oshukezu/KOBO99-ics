# Kobo 99 元選書行事曆

這個專案會從 Kobo 公開部落格文章擷取「Kobo99選書」標題，產生：

- `public/events.json`
- `public/kobo99.ics`
- `public/index.html`

GitHub Actions 會在每週三 22:00（台北時間）執行，也會在 push 到 `main` 時更新資料並部署到 GitHub Pages。首頁會自動依照目前網址產生 Google Calendar 訂閱連結。

`public/events.json` 是累積資料庫。腳本預設會保留既有資料並合併新抓到的選書，所以過去已寫入的歷史資料不會在每週更新時被清掉。

## 本機執行

```bash
python scripts/kobo99.py --out public --year 2025 --week 51
```

若要補掃整年，會自動合併既有歷史資料：

```bash
python scripts/kobo99.py --out public --full-year 2025
```

若要一次補掃多個年份：

```bash
python scripts/kobo99.py --out public --history-start-year 2024 --history-end-year 2026
```

只有在想捨棄既有歷史資料並重建時，才使用：

```bash
python scripts/kobo99.py --out public --replace-existing --full-year 2025
```

## GitHub 設定

1. 建立 GitHub repo，將這個資料夾 push 上去。
2. 到 repo 的 `Settings -> Pages`，將 Build and deployment 的 Source 設為 `GitHub Actions`。
3. 到 `Actions` 手動執行 `Update Kobo 99 Calendar` 一次。若要補歷史資料，可填 `history_start_year`，例如 `2024`；也可填 `history_end_year` 指定結束年份。
4. 打開 GitHub Pages 首頁，點 `Google Calendar 訂閱`。

## 手動更新並上傳

設定好 GitHub remote 後，可以用這個檔案手動更新最新書單並推上 GitHub：

```bash
bash scripts/publish_latest.sh
```

它會依序執行：

- 抓最新 Kobo 99 書單並合併到歷史資料
- 更新 `public/events.json`、`public/kobo99.ics`、`public/index.html`
- 若有變更則 commit
- push 到目前設定的 GitHub remote

也可以自訂 commit message：

```bash
bash scripts/publish_latest.sh "Update latest Kobo 99 books"
```

## 擷取規則

文章網址格式：

```text
https://www.kobo.com/zh/blog/weekly-dd99-{year}-w{week}
```

標題格式：

```text
{Date}{星期}Kobo99選書：{書名}
```

ICS 事件：

- 標題：書名
- 內容：書名、查看電子書、來源文章

爬蟲只讀公開文章頁面，並使用節流與重試來降低請求量；不包含 CAPTCHA 破解、代理輪換或封鎖規避。
如果 Kobo 對一般 HTTP 用戶端回 403，腳本會在 `auto` 模式改用 Playwright 瀏覽器載入公開頁面；若仍遇到人工驗證或 CAPTCHA，該次執行會失敗並保留既有輸出。
