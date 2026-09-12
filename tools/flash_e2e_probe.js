// 端到端验证 v2: 走真实 UI 路径(点按钮触发 splitSynth), MutationObserver 捕获 flash-ok/tick
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

  const setup = await page.evaluate(async () => {
    const j = await api('/api/project/create', { name: 'test逐段反馈验证2' });
    projName = j.project;
    $('script').value = '大家好，这是逐段反馈验证的第一段，看看绿脉冲会不会出现。这是第二段，两段都完成后按钮会闪绿色的生成完毕。';
    setTtsEng('rh');
    // observer 挂好(在点按钮之前)
    window.__flashLog = [];
    const mo = new MutationObserver(muts => {
      muts.forEach(m => {
        if (m.type === 'attributes') {
          const cl = m.target.classList;
          if (cl.contains('flash-ok') || cl.contains('tick')) {
            window.__flashLog.push({ tag: m.target.tagName, cls: m.target.className.slice(0, 50), t: Date.now() });
          }
        }
      });
    });
    mo.observe(document.getElementById('segList'), { attributes: true, attributeFilter: ['class'], subtree: true });
    mo.observe(document.getElementById('btnSplit'), { attributes: true, attributeFilter: ['class'] });
    return { project: projName };
  });
  console.log('setup:', JSON.stringify(setup));

  // 真实点击按钮(splitSynth 走云端分支)
  await page.click('#btnSplit');

  let st;
  for (let i = 0; i < 80; i++) {
    await new Promise(r => setTimeout(r, 4000));
    st = await page.evaluate(async () => api('/api/tts/rh/status'));
    if (!st.running) break;
  }
  await new Promise(r => setTimeout(r, 1200));
  const log = await page.evaluate(() => window.__flashLog);
  console.log('status:', JSON.stringify({ running: st.running, finished: st.finished, total: st.total, error: (st.error || '').slice(0, 80) }));
  console.log('捕获动画事件:', JSON.stringify(log, null, 1));
  console.log('pageErrors:', errors.length, errors.slice(0, 3));
  await browser.close();
})();
