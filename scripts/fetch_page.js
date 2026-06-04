#!/usr/bin/env node

/**
 * Kobo 99 選書網頁爬取輔助腳本
 * 使用 Puppeteer + Stealth 插件以避免被 Cloudflare 封鎖
 */

const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');

// 啟用 Stealth 插件
puppeteer.use(StealthPlugin());

const url = process.argv[2];
if (!url) {
  console.error("錯誤: 未提供目標 URL。");
  console.error("使用方式: node fetch_page.js <URL>");
  process.exit(1);
}

(async () => {
  let browser;
  try {
    // 啟動 Chrome 無頭瀏覽器
    browser = await puppeteer.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
      ]
    });

    const page = await browser.newPage();
    
    // 設定常見的視窗大小與語系
    await page.setViewport({ width: 1280, height: 1024 });
    await page.setExtraHTTPHeaders({
      'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8'
    });

    // 載入網頁，等待網路閒置 (networkidle2)
    const response = await page.goto(url, {
      waitUntil: 'networkidle2',
      timeout: 60000
    });

    if (!response) {
      console.error("載入失敗: 瀏覽器未收到任何回應。");
      process.exit(1);
    }

    const status = response.status();
    if (status === 404) {
      // 404 找不到網頁，回傳特有的 exit code
      process.exit(44);
    }

    if (status >= 400) {
      console.error(`載入失敗: HTTP 狀態碼 ${status}`);
      process.exit(1);
    }

    // 稍微等待額外的 2 秒以確保動態內容渲染完成
    await new Promise(resolve => setTimeout(resolve, 2000));

    // 輸出網頁 HTML 內容至 stdout
    const content = await page.content();
    process.stdout.write(content, () => {
      process.exit(0);
    });
  } catch (error) {
    console.error(`執行錯誤: ${error.message}`);
    process.exit(1);
  } finally {
    if (browser) {
      await browser.close();
    }
  }
})();
