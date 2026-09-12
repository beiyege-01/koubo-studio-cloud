/* 直接验证: seek后 tl.time() 和 capg 状态 */
const puppeteer = require('puppeteer-core');
const path = require('path');
const EXE = 'C:\\Users\\wrpsg\\.cache\\hyperframes\\chrome\\chrome-headless-shell\\win64-152.0.7977.30\\chrome-headless-shell-win64\\chrome-headless-shell.exe';

(async () => {
  const dir = process.argv[2] || '.';
  const browser = await puppeteer.launch({
    executablePath: EXE, headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--autoplay-policy=no-user-gesture-required'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1080, height: 1920 });
  page.on('pageerror', (e) => console.log('[pageerror] ' + String(e).slice(0, 300)));
  await page.goto('file:///' + path.resolve(dir, 'index.html').replace(/\\/g, '/'));
  await new Promise(r => setTimeout(r, 2500));
  const r1 = await page.evaluate(() => {
    const tl = window.__timelines && window.__timelines['main'];
    if (!tl) return { err: 'no tl' };
    tl.pause(0);
    tl.seek(12.097, false);
    const states = [];
    for (let i = 0; i < 8; i++) {
      const e = document.querySelector('#capg' + i);
      if (!e) continue;
      const cs = getComputedStyle(e);
      states.push(`capg${i}:op=${cs.opacity},vis=${cs.visibility}`);
    }
    // 找 tl 里 capg 相关 set 的时间
    const sets = [];
    tl.getChildren(false, true, true).forEach((tw) => {
      if (tw.targets && tw.targets()[0] && String(tw.targets()[0].id || '').startsWith('capg')) {
        sets.push(`${tw.targets()[0].id}@${tw.startTime()}`);
      }
    });
    return { time: tl.time(), dur: tl.duration(), states: states.slice(0, 8), setCount: sets.length, sets: sets.slice(0, 12) };
  });
  console.log(JSON.stringify(r1, null, 1));
  await browser.close();
})();
