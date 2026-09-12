const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
(async () => {
  const browser = await puppeteer.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const proj = encodeURIComponent('20260909-215318_赛博女友');
  const url = `http://127.0.0.1:8795/media/${proj}/output/${encodeURIComponent('初稿.mp4')}`;
  const r = await page.evaluate(async (url) => {
    const resp = await fetch(url, { method: 'GET', headers: { Range: 'bytes=0-1023' } });
    const status = resp.status + ' range-suffix=' + (resp.headers.get('content-range') || 'none') + ' accept-ranges=' + (resp.headers.get('accept-ranges') || 'none') + ' len=' + resp.headers.get('content-length');
    const v = document.createElement('video');
    const err = await new Promise((res) => {
      v.onerror = () => res('error=' + (v.error ? v.error.code + '/' + v.error.message : '?'));
      v.oncanplay = () => res('canplay readyState=' + v.readyState);
      v.src = url;
      setTimeout(() => res('timeout readyState=' + v.readyState + ' networkState=' + v.networkState), 8000);
    });
    const canTypeH264 = v.canPlayType('video/mp4; codecs="avc1.64001f"');
    return { status, err, canTypeH264, ua: navigator.userAgent.match(/Chrome\/[\d.]+/)[0] };
  }, url);
  console.log(JSON.stringify(r, null, 1));
  await browser.close();
})();
