const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1500, height: 1000 });
  await page.goto('http://127.0.0.1:8795/', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));
  // 打开 test1 项目 + 挂 observer + 找 seg01 的重新合成按钮点击
  const setup = await page.evaluate(async () => {
    projName = '20260911-002313_test1全流程';
    setTtsEng('local');
    const j = await api('/api/project/' + encodeURIComponent(projName));
    renderSegs(j.segments);
    window.__flashLog = [];
    const mo = new MutationObserver(muts => muts.forEach(m => {
      if (m.type === 'attributes') {
        const cl = m.target.classList;
        if (cl.contains('flash-ok') || cl.contains('tick'))
          window.__flashLog.push({ tag: m.target.tagName, cls: m.target.className.slice(0, 45) });
      }
    }));
    mo.observe(document.getElementById('segList'), { attributes: true, attributeFilter: ['class'], subtree: true });
    mo.observe(document.getElementById('btnSplit'), { attributes: true, attributeFilter: ['class'] });
    const card = document.querySelector('#segList .segcard[data-id="seg01"]');
    const bR = [...card.querySelectorAll('button')].find(b => b.textContent === '重新合成');
    if (!bR) return { err: '找不到重新合成按钮' };
    bR.click();
    return { clicked: true };
  });
  console.log('setup:', JSON.stringify(setup));
  // 等本地单段合成完成(模型加载~40s+合成~20s)
  let log = [];
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 5000));
    log = await page.evaluate(() => window.__flashLog);
    if (log.length >= 2) break;
  }
  await new Promise(r => setTimeout(r, 500));
  const final = await page.evaluate(() => {
    const card = document.querySelector('#segList .segcard[data-id="seg01"]');
    return { flashCount: window.__flashLog.length, log: window.__flashLog,
             freshBadge: !!card.querySelector('.freshjump') };
  });
  console.log('捕获:', JSON.stringify(final, null, 1));
  console.log('pageErrors:', errors.length);
  await browser.close();
})();
