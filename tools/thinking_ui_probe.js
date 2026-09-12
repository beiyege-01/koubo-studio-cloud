const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://127.0.0.1:8805/', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));
  const out = await page.evaluate(async () => {
    openSettings();
    await new Promise(r => setTimeout(r, 900));
    const sel = document.getElementById('setThinking');
    const hint = document.querySelector('#setModel').parentElement.innerText.replace(/\s+/g,' ').slice(0, 220);
    const r = { thinkOptions: [...sel.options].map(o => o.value + ':' + o.textContent.trim()), current: sel.value, modelHint: hint };
    // 切换成 on 并保存 → 后端应变
    sel.value = 'low';
    const cfg = { base_url: document.getElementById('setBase').value, model: document.getElementById('setModel').value,
                  api_key: document.getElementById('setKey').value, thinking: sel.value };
    await api('/api/settings', cfg);
    const j = await api('/api/settings');
    r.savedThinking = j.thinking;
    // 复原为 off
    await api('/api/settings', { thinking: 'off' });
    return r;
  });
  console.log(JSON.stringify(out, null, 1));
  console.log('pageErrors:', errors.length);
  await browser.close();
})();
