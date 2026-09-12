const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://127.0.0.1:8795/', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));
  const out = await page.evaluate(async () => {
    projName = '20260911-002313_test1全流程';
    const j = await api('/api/project/' + encodeURIComponent(projName));
    const r = {};
    // 1) 先预览初稿
    pickRefineV(0);
    const a = document.getElementById('editPreview'), b = document.getElementById('refinePreview');
    r.step1 = { edit: a.style.display, refine: b.style.display, editSrc: (a.src || '').split('/').pop().slice(0, 20) };
    // 2) 再点 v1
    pickRefineV(1);
    r.step2 = { edit: a.style.display, refine: b.style.display, refineSrc: (b.src || '').split('/').pop().slice(0, 24), editHasSrc: !!a.getAttribute('src') };
    // 3) 切回初稿
    pickRefineV(0);
    r.step3 = { edit: a.style.display, refine: b.style.display, refineHasSrc: !!b.getAttribute('src') };
    // 4) 页面残留视频数(可见)
    r.visibleVideos = [...document.querySelectorAll('video')].filter(v => getComputedStyle(v).display !== 'none').length;
    return r;
  });
  console.log(JSON.stringify(out, null, 1));
  console.log('pageErrors:', errors.length);
  await browser.close();
})();
