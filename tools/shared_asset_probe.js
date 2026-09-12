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
    projName = 't_shared_asset_verify';
    const j = await api('/api/project/' + encodeURIComponent(projName));
    customItems = j.custom || [];
    renderSegs(j.segments);
    const ids = [...document.querySelectorAll('#avSegs .avseg')].map(c => c.dataset.id);
    const cusCard = document.querySelector('#avSegs .avseg[data-id="cus01"]');
    const hasVideo = cusCard ? !!cusCard.querySelector('video') : null;
    // 工作台删除 custom 视频
    if (cusCard) {
      const btn = [...cusCard.querySelectorAll('button')].find(b => b.textContent === '删除视频');
      if (btn) { btn.click(); await new Promise(r => setTimeout(r, 2500)); }
    }
    const p = await api('/api/project/' + encodeURIComponent(projName));
    const c = (p.custom || []).find(x => x.id === 'cus01') || {};
    return { avIds: ids, customHadVideo: hasVideo,
             after: { video: c.video, video_status: c.video_status, textField: c.name } };
  });
  console.log(JSON.stringify(out, null, 1));
  console.log('pageErrors:', errors.length);
  await browser.close();
})();
