/* 页面截图: 验证美观化改动 */
const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Users\\wrpsg\\.cache\\hyperframes\\chrome\\chrome-headless-shell\\win64-152.0.7977.30\\chrome-headless-shell-win64\\chrome-headless-shell.exe';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EXE, headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--force-color-profile=srgb'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1080, height: 2400, deviceScaleFactor: 1 });
  await page.goto('http://127.0.0.1:8795/', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1200));
  // 确认标题实际用的字体
  const fonts = await page.evaluate(() => {
    const h1 = document.querySelector('.masthead h1');
    const h3 = document.querySelector('.card h3');
    return { h1Font: getComputedStyle(h1).fontFamily.slice(0, 60), h1Size: getComputedStyle(h1).fontSize,
             h3Font: getComputedStyle(h3).fontFamily.slice(0, 60), h3Size: getComputedStyle(h3).fontSize };
  });
  console.log('H1: ' + fonts.h1Font + ' @' + fonts.h1Size);
  console.log('H3: ' + fonts.h3Font + ' @' + fonts.h3Size);
  await page.screenshot({ path: 'E:\\deepseek-works\\koubo_ui_top.png', clip: { x: 0, y: 0, width: 1080, height: 1400 } });
  console.log('✓ 截图已存 E:\\deepseek-works\\koubo_ui_top.png');
  await browser.close();
})();
