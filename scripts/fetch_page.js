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
        '--disable-gpu',
        '--window-size=1280,1024',
      ]
    });

    const page = await browser.newPage();
    
    // 設定常見的視窗大小與語系
    await page.setViewport({ width: 1280, height: 1024 });
    await page.setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36');
    await page.setExtraHTTPHeaders({
      'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8'
    });

    // 載入網頁，先等待 DOM 載入，以利偵測 Cloudflare 挑戰
    const response = await page.goto(url, {
      waitUntil: 'domcontentloaded',
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

    // 檢查是否遇到 Cloudflare 挑戰頁面
    let title = await page.title();
    let content = await page.content();
    const isChallenge = title.includes("Just a moment") || content.includes("challenges.cloudflare.com") || content.includes("cf-challenge");

    if (isChallenge) {
      console.error("偵測到 Cloudflare 挑戰頁面，開始等待挑戰自動通過...");
      const maxWait = 15; // 最多等待 15 秒
      let passed = false;
      for (let i = 0; i < maxWait; i++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        title = await page.title();
        content = await page.content();
        if (!title.includes("Just a moment") && !content.includes("cf-challenge") && (content.includes("Kobo") || content.includes("kobo"))) {
          passed = true;
          console.error(`Cloudflare 挑戰在 ${i + 1} 秒後通過！`);
          // 挑戰通過後再多等待 2 秒以確保內容完整渲染
          await new Promise(resolve => setTimeout(resolve, 2000));
          break;
        }
      }
      if (!passed) {
        console.error("警告: 等待 Cloudflare 挑戰超時，將嘗試直接回傳當前內容。");
      }
    } else {
      // 沒遇到挑戰，稍微等待額外的 2 秒以確保動態內容渲染完成
      await new Promise(resolve => setTimeout(resolve, 2000));
      content = await page.content();
    }

    // 輸出網頁 HTML 內容至 stdout
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
