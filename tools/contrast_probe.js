const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const file = 'file:///E:/deepseek-works/koubo-studio/projects/20260909-161159_%E9%9A%8F%E6%9C%BA%E7%99%BE%E5%AD%97%E5%85%A8%E6%B5%81%E7%A8%8B%E6%B5%8B%E8%AF%95/hf/index.html';
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox', '--allow-file-access-from-files'] });
  const page = await browser.newPage();
  await page.goto(file, { waitUntil: 'load', timeout: 30000 });
  const out = await page.evaluate(() => {
    const els = [...document.querySelectorAll('div:nth-of-type(4) > div:nth-of-type(4) > span:nth-of-type(2)')];
    return els.map(e => ({ tag: e.tagName, cls: e.className, txt: (e.textContent || '').slice(0, 24),
      color: getComputedStyle(e).color, parent: e.parentElement.className,
      gp: e.parentElement.parentElement.className, ggp: e.parentElement.parentElement.parentElement.className }));
  });
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
