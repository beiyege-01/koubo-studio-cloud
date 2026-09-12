/* 家族菜单胶囊布局验证: 滚动到 menufam 区域截图 + 断言 chip 选中态 */
const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Users\\wrpsg\\.cache\\hyperframes\\chrome\\chrome-headless-shell\\win64-152.0.7977.30\\chrome-headless-shell-win64\\chrome-headless-shell.exe';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EXE, headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--force-color-profile=srgb'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1080, height: 900, deviceScaleFactor: 1 });
  await page.goto('http://127.0.0.1:8795/', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 900));
  // 打开四个 details + 滚到 menufam
  const info = await page.evaluate(() => {
    document.querySelectorAll('.menufam details').forEach(d => { d.open = true; });
    const fam = document.querySelector('.menufam');
    fam.scrollIntoView({ block: 'start' });
    const labels = document.querySelectorAll('.menufam details label');
    const cs = getComputedStyle(labels[2]); // 第一个家族的第一个具体款
    const checked = document.querySelector('.menufam details label:has(input:checked)');
    return {
      labelCount: labels.length,
      display: cs.display, borderRadius: cs.borderRadius, padding: cs.padding,
      checkedFound: !!checked,
      checkedBorder: checked ? getComputedStyle(checked).borderColor : '',
      checkedBg: checked ? getComputedStyle(checked).backgroundColor : '',
    };
  });
  console.log('labels=' + info.labelCount + ' display=' + info.display + ' radius=' + info.borderRadius + ' pad=' + info.padding);
  console.log('checkedFound=' + info.checkedFound + ' border=' + info.checkedBorder + ' bg=' + info.checkedBg);
  await new Promise(r => setTimeout(r, 400));
  const box = await page.evaluate(() => {
    const el = document.querySelector('.menufam');
    const r = el.getBoundingClientRect();
    return { x: Math.max(0, r.x + window.scrollX - 8), y: Math.max(0, r.y + window.scrollY - 8),
             width: Math.min(1064, r.width + 16), height: Math.min(880, r.height + 16) };
  });
  await page.screenshot({ path: 'E:\\deepseek-works\\koubo_chips.png', clip: box });
  console.log('OK 截图 E:\\deepseek-works\\koubo_chips.png');
  await browser.close();
})();
