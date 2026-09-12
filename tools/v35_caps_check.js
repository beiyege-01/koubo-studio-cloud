const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 704, height: 1280 });
  await page.goto('file:///E:/deepseek-works/koubo-studio/projects/20260909-215318_赛博女友/hf/index.html', { waitUntil: 'load' });
  await new Promise(r => setTimeout(r, 1200));
  const st = await page.evaluate(() => {
    const caps = document.getElementById('caps');
    const cs = getComputedStyle(caps);
    // 数一下样式表里实际生效的规则有没有 #caps
    let found = [];
    for (const sh of document.styleSheets) {
      let rules; try { rules = sh.cssRules; } catch (e) { continue; }
      const walk = (rs) => { for (const r of rs) {
        if (r.selectorText && r.selectorText.includes('#caps')) found.push(r.cssText.slice(0, 120));
        if (r.cssRules) walk(r.cssRules);
      } };
      walk(rules);
    }
    return { position: cs.position, bottom: cs.bottom, zIndex: cs.zIndex, rulesFound: found,
             styleTagCount: document.styleSheets.length };
  });
  console.log(JSON.stringify(st, null, 1));
  await browser.close();
})();
