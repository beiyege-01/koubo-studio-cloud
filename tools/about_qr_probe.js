const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1500, height: 1000 });
  await page.goto('http://127.0.0.1:8805/?about=1', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 3000));
  const out = await page.evaluate(() => {
    const m = document.getElementById('aboutModal'), box = m.querySelector('.box');
    const imgs = [...m.querySelectorAll('.am-qr-item img')];
    return {
      boxSize: Math.round(box.getBoundingClientRect().width) + 'x' + Math.round(box.getBoundingClientRect().height),
      qr: imgs.map(i => ({ w: Math.round(i.getBoundingClientRect().width), h: Math.round(i.getBoundingClientRect().height),
                           loaded: i.naturalWidth > 0, cursor: getComputedStyle(i).cursor })),
      hint: m.querySelector('.am-qr-hint').textContent
    };
  });
  console.log(JSON.stringify(out, null, 1));
  await page.screenshot({ path: 'E:\\deepseek-works\\koubo_about_big.png' });
  console.log('pageErrors:', errors.length);
  await browser.close();
})();
