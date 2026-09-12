/* 复刻 check 的 sweep 指纹采集: 9 采样点逐个 seek + 指纹, 打印差异与诊断 */
const puppeteer = require('puppeteer-core');
const path = require('path');
const crypto = require('crypto');
const EXE = 'C:\\Users\\wrpsg\\.cache\\hyperframes\\chrome\\chrome-headless-shell\\win64-152.0.7977.30\\chrome-headless-shell-win64\\chrome-headless-shell.exe';

function collectLayoutGeometry() {
  const root =
    document.querySelector("[data-composition-id][data-width][data-height]") ||
    document.querySelector("[data-composition-id]") ||
    document.body;
  const isVisibleElement = (el) => {
    if (['SCRIPT', 'STYLE', 'LINK', 'META', 'HEAD', 'TITLE'].includes(el.tagName)) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
    return true;
  };
  const opacityChain = (el) => {
    let o = 1, e = el;
    while (e) { o *= parseFloat(getComputedStyle(e).opacity || '1'); e = e.parentElement; }
    return o;
  };
  const round = (v) => Math.round(v * 100) / 100;
  const elements = Array.from(root.querySelectorAll('*')).filter((el) => {
    if (!isVisibleElement(el)) return false;
    return opacityChain(el) >= 0.01;   // 与真实审计近似: 透明元素排除
  });
  const parts = elements.map((el) => {
    const r = el.getBoundingClientRect();
    return `${round(r.left)},${round(r.top)},${round(r.width)},${round(r.height)},${round(opacityChain(el))}`;
  });
  return parts.join('|');
}

function mediaPixelHash(el) {
  try {
    const w = el.videoWidth || el.width || el.getBoundingClientRect().width;
    const h = el.videoHeight || el.height || el.getBoundingClientRect().height;
    if (!w || !h) return 'x';
    const off = document.createElement('canvas');
    off.width = 8; off.height = 8;
    const ctx = off.getContext('2d');
    if (!ctx) return 'x';
    ctx.drawImage(el, 0, 0, 8, 8);
    const d = ctx.getImageData(0, 0, 8, 8).data;
    let hash = 0;
    for (let i = 0; i < d.length; i++) hash = (hash * 31 + d[i]) >>> 0;
    return String(hash);
  } catch { return 'x'; }
}

(async () => {
  const dir = process.argv[2] || '.';
  const browser = await puppeteer.launch({
    executablePath: EXE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--autoplay-policy=no-user-gesture-required'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1080, height: 1920 });
  page.on('pageerror', (e) => console.log('[pageerror] ' + String(e).slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') console.log('[console.error] ' + m.text().slice(0, 200)); });
  await page.goto('file:///' + path.resolve(dir, 'index.html').replace(/\\/g, '/'));
  await new Promise(r => setTimeout(r, 2500));
  const diag = await page.evaluate(() => {
    const tl = window.__timelines && window.__timelines['main'];
    const c0 = document.querySelector('#capg0');
    const s0 = c0 ? getComputedStyle(c0) : null;
    return {
      hasGsap: typeof window.gsap !== 'undefined',
      hasTl: !!tl,
      tlDur: tl ? Math.round(tl.duration() * 10) / 10 : -1,
      capg0: s0 ? `op=${s0.opacity} vis=${s0.visibility}` : 'missing',
      capgAll: document.querySelectorAll('.capg').length,
    };
  });
  console.log('诊断: ' + JSON.stringify(diag));
  const times = [12.097, 36.291, 60.485, 84.679, 108.873, 133.067, 157.261, 181.455, 205.649];
  const fps = [];
  for (const t of times) {
    await page.evaluate((tt) => {
      const tl = window.__timelines && window.__timelines['main'];
      if (tl) tl.seek(tt, false);
      const v = document.getElementById('base');
      if (v) { try { v.currentTime = tt; } catch (e) {} }
    }, t);
    await new Promise(r => setTimeout(r, 300));
    const fp = await page.evaluate(collectLayoutGeometry);
    const vh = await page.evaluate(mediaPixelHash, );
    fps.push(fp);
    console.log(`t=${t}  元素数=${fp.split('|').length}  指纹=${crypto.createHash('md5').update(fp).digest('hex').slice(0, 10)}  视频hash=${vh}`);
  }
  const allSame = fps.every((f) => f === fps[0]);
  console.log(allSame ? '\n⚠️ 全部指纹相同 → 会触发 sweep_static' : '\n✓ 指纹有差异 → 不会触发');
  if (!allSame) {
    const p1 = fps[0].split('|'), p2 = fps[3].split('|');
    const n = Math.max(p1.length, p2.length);
    let diff = 0;
    for (let i = 0; i < n && diff < 10; i++) {
      if (p1[i] !== p2[i]) { console.log(`差异[${i}]: t12=${p1[i]}  t84=${p2[i]}`); diff++; }
    }
  }
  // 诊断: 列出 t=12 时全部可见元素的身份
  const ids = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll('[data-composition-id] *').forEach((el) => {
      const cs = getComputedStyle(el);
      if (cs.display !== 'none' && cs.visibility !== 'hidden') {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) out.push(`${el.tagName.toLowerCase()}#${el.id || ''}.${el.className && el.className.baseVal === undefined ? el.className : ''} ${Math.round(r.width)}x${Math.round(r.height)} op=${cs.opacity}`);
      }
    });
    return out.slice(0, 30);
  });
  console.log('可见元素清单: ' + ids.join(' | '));
  await browser.close();
})();
