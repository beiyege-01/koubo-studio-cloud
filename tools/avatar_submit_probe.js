// 全链路高频验证(真实提交): 200ms采样 + MutationObserver, 捕获「提交中」文案/已提交/running/成功绿脉冲
const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1500, height: 1100 });
  await page.goto('http://127.0.0.1:8795/', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));

  await page.evaluate(async () => {
    projName = '20260911-002313_test1全流程';
    const j = await api('/api/project/' + encodeURIComponent(projName));
    // 先强制回退为待生成, 便于验证完整提交链(视频文件保留在盘上)
    renderSegs(j.segments);
    window.__ev = [];
    window.__t0 = Date.now();
    const stamp = () => ((Date.now() - window.__t0) / 1000).toFixed(1) + 's';
    // 记录所有 vinfo 文案变化 + flash 类出现
    let lastSig = '';
    window.__iv = setInterval(() => {
      const card = document.querySelector('#segList .segcard[data-id="seg01"]');
      if (!card) return;
      const btns = [...card.querySelectorAll('button')].map(b => b.textContent.trim()).join('/');
      const infos = [...card.querySelectorAll('.vinfo')].map(e => e.className + ' :: ' + e.textContent).join(' || ');
      const badges = [...card.querySelectorAll('.badge')].map(b => b.textContent.trim()).join('|');
      const sig = btns + ' ## ' + infos + ' ## ' + badges + ' ## ' + card.classList.contains('flash-ok');
      if (sig !== lastSig) { lastSig = sig; window.__ev.push({ dt: stamp(), btns, infos, badges, flash: card.classList.contains('flash-ok') }); }
    }, 200);
    const card = document.querySelector('#segList .segcard[data-id="seg01"]');
    const btn = [...card.querySelectorAll('button')].find(b => /生成视频|重新生成/.test(b.textContent));
    if (btn) { btn.click(); window.__ev.push({ dt: stamp(), note: '已点击生成视频' }); }
  });

  // 采样到 running 出现(最多 30s)
  await new Promise(r => setTimeout(r, 30000));
  const submitEv = await page.evaluate(() => window.__ev);
  // 轮询到结束
  let final = null;
  for (let i = 0; i < 108; i++) {
    await new Promise(r => setTimeout(r, 5000));
    const st = await page.evaluate(async () => {
      const j = await api('/api/project/' + encodeURIComponent('20260911-002313_test1全流程'));
      const s = j.segments.find(x => x.id === 'seg01');
      return { status: s.video_status, video: s.video, error: s.video_error };
    });
    if (st.status !== 'running') { final = st; break; }
    if (i % 6 === 0) console.log(`  [${(i + 1) * 5}s] ${st.status}`);
  }
  await new Promise(r => setTimeout(r, 2500));
  const tailEv = await page.evaluate(() => { clearInterval(window.__iv); return window.__ev; });
  console.log('=== 事件时间线(去重变化) ===');
  tailEv.forEach(e => console.log(JSON.stringify(e)));
  console.log('=== 终态 ===', JSON.stringify(final));
  console.log('pageErrors:', errors.length);
  await browser.close();
})();
