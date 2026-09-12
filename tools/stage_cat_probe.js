const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1600, height: 1000 });
  await page.goto('http://127.0.0.1:8795/', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));
  const out = await page.evaluate(async () => {
    projName = '20260911-002313_test1全流程';
    const j = await api('/api/project/' + encodeURIComponent(projName));
    renderSegs(j.segments);
    const r = {};
    // 静止态: 无角标
    r.idle = { cards4: [...document.querySelectorAll('#segList .segcard.stage-running')].map(c => c.dataset.id),
               bigCards: [...document.querySelectorAll('.card.stage-running')].map(c => c.id) };
    // 云端合成中(模拟 cur=seg03)
    const realApi = window.api;
    window.api = async function (p, b, l) { if (p === '/api/tts/rh/status') return { running: true, cur: 'seg03 姿态全变了', finished: 2, total: 6, done_ids: ['seg01','seg02'] }; return realApi(p, b, l); };
    await refreshStageRunning();
    busySegId = 'seg03'; refreshSegCards();
    await new Promise(x => setTimeout(x, 400));
    r.synthesizing = { highlighted: [...document.querySelectorAll('#segList .segcard.stage-running')].map(c => c.dataset.id),
                       card4: document.getElementById('card4').classList.contains('stage-running') };
    // 数字人视频生成中(seg05) → 该段常亮
    const segs = j.segments.map(s => s.id === 'seg05' ? { ...s, video_status: 'running' } : s);
    window.api = async function (p, b, l) {
      if (p === '/api/tts/rh/status') return { running: false };
      if (p.startsWith('/api/project/')) return { ...j, segments: segs };
      return realApi(p, b, l);
    };
    busySegId = ''; window.lastSegs = segs; lastSegs = segs;
    await refreshStageRunning();
    refreshSegCards();
    await new Promise(x => setTimeout(x, 400));
    r.videoRunning = { highlighted: [...document.querySelectorAll('#segList .segcard.stage-running')].map(c => c.dataset.id),
                       cardAV: document.getElementById('cardAV').classList.contains('stage-running') };
    // 角标伪元素样式确认
    const anyCard = document.querySelector('#segList .segcard.stage-running');
    r.cornerStyle = anyCard ? { before: getComputedStyle(anyCard, '::before').borderTopColor, anim: getComputedStyle(anyCard, '::before').animationName } : null;
    window.api = realApi;
    return r;
  });
  console.log(JSON.stringify(out, null, 1));
  console.log('pageErrors:', errors.length, errors.slice(0, 3));
  await browser.close();
})();
