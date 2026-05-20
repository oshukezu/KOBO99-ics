# Kobo 99 元選書行事曆
訂閱資訊：https://calendar.google.com/calendar/ical/u72vcutakmkhahnk8en1q90p3hf0gb98%40import.calendar.google.com/public/basic.ics


這個專案會在每週四 07:00（台北時間）執行，也會在 push 到 `main` 時更新資料並部署到 GitHub Pages。首頁會自動依照目前網址產生 Google Calendar 訂閱連結。

`public/events.json` 是累積資料庫。腳本預設會保留既有資料並合併新抓到的選書，所以過去已寫入的歷史資料不會在每週更新時被清掉。

## GitHub 設定

1. 建立 GitHub repo，將這個資料夾 push 上去。
2. 到 repo 的 `Settings -> Pages`，將 Build and deployment 的 Source 設為 `GitHub Actions`。
3. 到 `Actions` 手動執行 `Update Kobo 99 Calendar` 一次。若要補歷史資料，可填 `history_start_year`，例如 `2024`；也可填 `history_end_year` 指定結束年份。
4. 打開 GitHub Pages 首頁，點 `Google Calendar 訂閱`。
