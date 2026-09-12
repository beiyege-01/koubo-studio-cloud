const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true,
    args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1600, height: 1000 });
  await page.goto('http://127.0.0.1:8805/', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1800));
  const out = await page.evaluate(async () => {
    const r = {};
    // 1) 页脚
    const links = [...document.querySelectorAll('.site-foot .sf-links a')].map(a => a.getAttribute('href'));
    r.footer = { count: links.length, links, year: document.getElementById('yearNow').textContent,
                 copyright: document.querySelector('.site-foot div').textContent.trim().slice(0, 40) };
    // 2) 让猫出现(模拟任务运行)
    const realApi = window.api;
    window.api = async function (p, b, l) { if (p === '/api/tts/rh/status') return { running: true }; return realApi(p, b, l); };
    await refreshStageRunning();
    await new Promise(x => setTimeout(x, 2000));
    const hot = document.getElementById('catHot');
    const cs = getComputedStyle(hot);
    r.catShown = { cls: hot.className, display: cs.display, cursor: cs.cursor,
                   pos: { left: cs.left, right: cs.right, bottom: cs.bottom, top: cs.top } };
    // 3) 点热区 → 弹窗
    hot.click();
    await new Promise(x => setTimeout(x, 400));
    const m = document.getElementById('aboutModal');
    r.modalOpen = { display: getComputedStyle(m).display, title: m.querySelector('h2').textContent.trim(),
                    tags: [...m.querySelectorAll('.am-tag')].map(t => t.textContent.trim()),
                    linkCount: m.querySelectorAll('.am-links a').length,
                    hasCoffee: m.textContent.includes('请我喝杯咖啡') };
    // 4) Esc 关闭
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await new Promise(x => setTimeout(x, 300));
    r.afterEsc = getComputedStyle(m).display;
    // 5) 热区不挡内容: 猫隐藏时 display 应为 none
    window.api = async function (p, b, l) { if (p === '/api/tts/rh/status') return { running: false }; return realApi(p, b, l); };
    await refreshStageRunning();
    await new Promise(x => setTimeout(x, 900));
    r.afterTaskEnd = { hotDisplay: getComputedStyle(hot).display, catOpacity: getComputedStyle(document.getElementById('live2dcanvas')).opacity };
    return r;
  });
  console.log(JSON.stringify(out, null, 1));
  console.log('pageErrors:', errors.length, errors.slice(0, 3));
  await page.screenshot({ path: 'E:\\deepseek-works\\koubo_about_modal.png', fullPage: false });
  await browser.close();
})();
