const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1500, height: 980 });
  await page.goto('http://127.0.0.1:8805/?about=1', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 2000));
  const out = await page.evaluate(() => {
    const m = document.getElementById('aboutModal');
    const cs = getComputedStyle(m);
    return {
      autoOpened: cs.display, zIndex: cs.zIndex,
      footerHasTip: document.querySelector('.site-foot').textContent.includes('小彩蛋'),
      title: m.querySelector('h2').textContent.trim(),
      bodyText: m.querySelector('.box').innerText.replace(/\s+/g, ' ').slice(0, 400)
    };
  });
  console.log(JSON.stringify(out, null, 1));
  await page.screenshot({ path: 'E:\\deepseek-works\\koubo_about_modal.png' });
  console.log('pageErrors:', errors.length);
  await browser.close();
})();
