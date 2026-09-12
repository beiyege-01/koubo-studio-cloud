const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true,
    args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1920, height: 1080 });
  await page.goto('http://127.0.0.1:8795/', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 2000));
  const out = await page.evaluate(async () => {
    const r = {};
    // 本地同步合成期间(前端调用 synthBusy)
    synthBusy(true);
    await new Promise(x => setTimeout(x, 1200));
    r.duringLocal = { card4: document.getElementById('card4').className,
                      cat: !!document.getElementById('live2dcanvas') && document.getElementById('live2dcanvas').className };
    synthBusy(false);
    await new Promise(x => setTimeout(x, 800));
    r.afterLocal = { card4: document.getElementById('card4').className };
    // 各阶段映射(直接调用, 覆盖 card5/card1)
    ['card1','card2','card3','card5','cardAV'].forEach(id => setCat(true, id));
    await new Promise(x => setTimeout(x, 300));
    r.lastSpot = document.getElementById('live2dcanvas').className;
    setCat(true, 'card5');
    r.card5Spot = document.getElementById('live2dcanvas').className;
    r.card5Pos = (() => { const c = document.getElementById('live2dcanvas'); const b = c.getBoundingClientRect();
      return { left: Math.round(b.left), top: Math.round(b.top) }; })();
    return r;
  });
  console.log(JSON.stringify(out, null, 1));
  console.log('pageErrors:', errors.length, errors.slice(0, 3));
  await browser.close();
})();
