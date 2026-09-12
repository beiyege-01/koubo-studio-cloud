// 失败路径专项验证: 提交失败后卡片是否常驻显示原因(等所有定时器平息后再点)
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
  const out = await page.evaluate(async () => {
    projName = '20260911-002313_test1全流程';
    const j = await api('/api/project/' + encodeURIComponent(projName));
    renderSegs(j.segments); renderAvatarPanel(j.segments);
    const realApi = window.api;
    window.api = async function (path, body, long) {
      if (path === '/api/avatar/synth') { await new Promise(r => setTimeout(r, 600)); throw new Error('模拟提交失败: 形象图上传超时'); }
      return realApi(path, body, long);
    };
    // ④卡片失败路径
    const c4 = document.querySelector('#segList .segcard[data-id="seg04"]');
    [...c4.querySelectorAll('button')].find(b => /生成视频|重新生成/.test(b.textContent)).click();
    await new Promise(r => setTimeout(r, 1500));
    const s4 = { btn: [...c4.querySelectorAll('button')].map(b => b.textContent.trim()).join('/'),
                 vinfo: [...c4.querySelectorAll('.vinfo')].map(e => e.className + ' :: ' + e.textContent).join('||') };
    // 工作台失败路径
    renderAvatarPanel(j.segments);
    const cW = document.querySelector('#avSegs .avseg[data-id="seg05"]');
    [...cW.querySelectorAll('button')].find(b => /生成视频/.test(b.textContent)).click();
    await new Promise(r => setTimeout(r, 1500));
    const sW = { vinfo: [...cW.querySelectorAll('.vinfo')].map(e => e.className + ' :: ' + e.textContent).join('||') };
    return { c4: s4, workbench: sW };
  });
  console.log(JSON.stringify(out, null, 1));
  console.log('pageErrors:', errors.length);
  await browser.close();
})();
