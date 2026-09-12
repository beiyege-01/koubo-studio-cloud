/* 预览播放卡顿诊断 v2: 导航到应用页, 同源加载视频, 实播采样 dropped/waiting/buffered */
const puppeteer = require('puppeteer-core');
const EXE = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EXE, headless: true,
    args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
  });
  const page = await browser.newPage();
  await page.goto('http://127.0.0.1:8795/', { waitUntil: 'domcontentloaded' });

  const test = async (label, file) => {
    const r = await page.evaluate(async (file) => {
      const proj = encodeURIComponent('20260909-215318_赛博女友');
      const url = '/media/' + proj + '/output/' + encodeURIComponent(file) + '?t=' + Date.now();
      const v = document.createElement('video');
      v.muted = true; v.playsInline = true; v.preload = 'auto';
      v.style.cssText = 'width:300px';
      document.body.appendChild(v);
      const loadErr = await new Promise((res) => {
        v.onerror = () => res('error=' + (v.error ? v.error.code + '/' + v.error.message : '?'));
        v.onloadeddata = () => res(null);
        v.src = url;
        setTimeout(() => res('timeout readyState=' + v.readyState), 9000);
      });
      if (loadErr) { v.remove(); return { loadErr }; }
      await v.play();
      const marks = [];
      for (let i = 0; i < 12; i++) {
        await new Promise(r => setTimeout(r, 1000));
        const q = v.getVideoPlaybackQuality();
        marks.push({ t: +v.currentTime.toFixed(2), dropped: q.droppedVideoFrames,
                     starved: v.readyState < 3 ? 1 : 0 });
      }
      v.pause(); v.removeAttribute('src'); v.load(); v.remove();
      const gaps = [];
      for (let i = 1; i < marks.length; i++) gaps.push(+(marks[i].t - marks[i-1].t).toFixed(2));
      return { loadErr: null,
               canH264: !!v.canPlayType('video/mp4; codecs="avc1.64001f"'),
               dropped: marks[marks.length-1].dropped,
               starvedCount: marks.filter(m => m.starved).length,
               gaps, res: marks[0].t };
    }, file);
    console.log(`=== ${label} ===`);
    if (r.loadErr) { console.log('加载失败: ' + r.loadErr); return; }
    console.log(`解码器=${r.canH264 ? 'H264支持' : '无H264!'} 掉帧=${r.dropped} 缓冲饥饿=${r.starvedCount}次/12`);
    console.log(`每秒推进: ${r.gaps.join(',')} (正常=全1)`);
  };

  await test('初稿 25fps 69kbps (用户说不卡)', '初稿.mp4');
  await test('精剪v24 30fps 403kbps (用户说卡)', '精剪v24.mp4');
  await browser.close();
})();
