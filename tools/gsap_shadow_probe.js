const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.goto('file:///E:/deepseek-works/koubo-studio/tools/_gsap_probe.html');
  await page.waitForFunction('window.gsap !== undefined', { timeout: 8000 });
  const r = await page.evaluate(() => {
    const out = {};
    const tl = gsap.timeline({ paused: true });
    tl.to('#a', { x: -10.5, textShadow: '8.6px 0 #ff003c,-8.6px 0 #00e5ff,0 5px 18px rgba(0,0,0,.52)', duration: 0.08, ease: 'none' }, 0);
    tl.to('#a', { x: 0, textShadow: '0px 0 #ff003c,0px 0 #00e5ff,0 5px 18px rgba(0,0,0,.52)', duration: 0.16, ease: 'power3.out' }, 0.08);
    tl.seek(0.04);
    out.a_mid = getComputedStyle(document.getElementById('a')).textShadow;
    return out;
  });
  console.log('同构修复后 t=0.04:', r.a_mid);
  await browser.close();
})();
