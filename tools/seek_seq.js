/* 逐次seek, 每次后检查可见capg数量 */
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
  await page.goto('file:///' + path.resolve(dir, 'index.html').replace(/\\/g, '/'));
  await new Promise(r => setTimeout(r, 2500));
  // 先看页面静置2.5s后, video是否在播
  const v0 = await page.evaluate(() => {
    const v = document.getElementById('base');
    return { paused: v.paused, t: Math.round(v.currentTime * 100) / 100, readyState: v.readyState };
  });
  console.log('静置2.5s后 video: ' + JSON.stringify(v0));
  const times = [12.097, 36.291, 60.485, 84.679, 108.873, 133.067, 157.261, 181.455, 205.649];
  for (const t of times) {
    const r = await page.evaluate((tt) => {
      const tl = window.__timelines['main'];
      tl.seek(tt, false);
      let vis = 0, ids = [];
      document.querySelectorAll('.capg').forEach((e) => {
        const cs = getComputedStyle(e);
        if (cs.opacity !== '0' && cs.visibility !== 'hidden') { vis++; ids.push(e.id); }
      });
      return { tlTime: Math.round(tl.time() * 1000) / 1000, visCount: vis, ids: ids.slice(0, 3) };
    }, t);
    console.log(`seek(${t}) → tl.time=${r.tlTime} 可见capg=${r.visCount} ${r.ids.join(',')}`);
    await new Promise(r2 => setTimeout(r2, 300));
  }
  await browser.close();
})();
