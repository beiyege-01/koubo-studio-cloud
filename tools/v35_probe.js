/* v35 定位测量: 藏视频, 纯叠加层坐标 */
const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 704, height: 1280 });
  await page.goto('file:///E:/deepseek-works/koubo-studio/projects/20260909-215318_赛博女友/hf/index.html', { waitUntil: 'load', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));
  const st = await page.evaluate(() => {
    document.querySelectorAll('video').forEach(v => { v.pause(); v.style.visibility = 'hidden'; });
    const tl = window.__timelines.main;
    tl.pause(); tl.seek(7.82);
    const rc = el => { const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height) }; };
    const out = {};
    out.caps = rc(document.getElementById('caps'));
    out.capg3 = rc(document.getElementById('capg3'));
    // capg3 的 offsetParent 链
    let p = document.getElementById('capg3'), chain = [];
    while (p && p.id !== 'html') { chain.push(p.id || p.tagName + (p.className ? '.' + p.className : '')); p = p.offsetParent; }
    out.offsetChain = chain;
    return out;
  });
  console.log(JSON.stringify(st));
  await new Promise(r => setTimeout(r, 300));
  await page.screenshot({ path: 'E:/deepseek-works/koubo_v35_live2.png', clip: { x: 0, y: 950, width: 704, height: 330 } });
  await browser.close();
})();
