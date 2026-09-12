# -*- coding: utf-8 -*-
"""lt 六新分支 + title 两新分支 + weight-shift 适配器单元自测"""
import importlib.util, sys

spec = importlib.util.spec_from_file_location('app', r'E:\deepseek-works\koubo-studio\app.py')
app = importlib.util.module_from_spec(spec)
sys.modules['app'] = app
spec.loader.exec_module(app)
print('模块加载 OK')

for st in ['lt-clean-bar', 'lt-soft-pill', 'lt-stack-bars',
           'lt-bold-block', 'lt-mask-reveal', 'lt-accent-underline']:
    css, html, js = app._hf_lt_parts({'style': st, 'text': '赛博女友这条赛道', 'start': 2.1, 'dur': 4.5}, 1080, 1920, '#E6C478')
    assert 'OFF=2.1' in js and 'data-start="2.1"' in html, st
    print(f'{st}: css={len(css)} html={len(html)} js={len(js)} OK')

for st in ['titlecard-calm', 'text-shimmer', 'titlecard-lockup', 'per-word-rise',
           'typewriter', 'headline-slam', 'marker-circle', 'tracking-in']:
    css, html, js = app._hf_title_parts({'style': st, 'text': 'AI 伴侣的温柔陷阱', 'dur': 1.8}, 1080, 1920, '#E6C478')
    assert 'data-duration="1.8"' in html, st
    print(f'{st}: css={len(css)} html={len(html)} js={len(js)} OK')

cues = [(3.0, 2.4, '我发现一个让我后背发凉', 1), (6.0, 1.6, '的事情', 1)]
spec2 = {'menu': {'caption': 'weight-shift'},
         'palette': {'accent': '#E6C478', 'text': '#FFFFFF', 'bg': '#101014', 'border': '#333333'},
         'subtitle': {'size_scale': 1.0}, 'per_item': {}}
css, html, js = app._hf_caps_parts(spec2, cues, 1080, 1920, 10.0)
assert '.wtl' in css and 'capg0 .wtl' in js, 'weight-shift'
print(f'weight-shift: css={len(css)} html={len(html)} js={len(js)} OK')
print('组1 DOM:', html[html.find('capg0'):html.find('capg0') + 220])
print('切换JS预览:', [l.strip() for l in js.splitlines() if 'fontWeight' in l][:4])
# 氛围两新款
lc, lh, lj = app._hf_leak_parts(21.6, 704)
assert 'hf-leak' in lc and 'mix-blend-mode:screen' in lc and 'tl.fromTo("#hf-leak"' in lj, 'leak'
print(f'light-leak: css={len(lc)} html={len(lh)} js={len(lj)} OK')
sc, sh, sj = app._hf_spot_parts(21.6)
assert 'hf-spot' in sc and '--srx' in sc and '"--sx"' in sj, 'spot'
print(f'spotlight: css={len(sc)} html={len(sh)} js={len(sj)} OK')
# 转场闪光(多边界) + 爬条分支
fc, fh, fj = app._hf_flash_parts([12.4, 25.1, 37.9, 50.2, 300.0], 704, 1280)
assert 'tf0' in fh and 'tf4' in fh and 'data-start="12.16"' in fh and 'tl.fromTo("#tf0 .w"' in fj, 'flash'
print(f'flash(5切点): css={len(fc)} html={len(fh)} js={len(fj)} OK')
fc2, fh2, fj2 = app._hf_flash_parts([], 704, 1280)
assert fc2 == '' and fh2 == '', 'flash-empty'
print('flash(空边界): OK')
# 转场统一分发: 四款 + 空边界
for kind, key in [('flash', 'tf0'), ('dip-black', 'tfd0'), ('sweep', 'tfs0'), ('wipe', 'tfw0')]:
    c3, h3, j3 = app._hf_trans_parts(kind, [12.4, 25.1], 704, 1280, '#E6C478')
    assert key in h3 and c3 and j3, kind
    print(f'trans-{kind}: css={len(c3)} html={len(h3)} js={len(j3)} OK')
assert app._hf_trans_parts('none', [12.4], 704, 1280) == ('', '', '')
assert app._hf_trans_parts('dip-black', [], 704, 1280) == ('', '', '')
print('trans-none/空边界: OK')
tc, th, tj = app._hf_lt_parts({'style': 'lt-news-ticker', 'text': '赛博女友这条赛道', 'start': 2.1, 'dur': 4.5}, 704, 1280, '#E6C478')
assert 'ltnt' in th and 'AI 快讯' in th and 'top:' in tc and 'x:-ntT' in tj, 'ticker'
print(f'news-ticker: css={len(tc)} html={len(th)} js={len(tj)} OK')
# 黑底段背景两款 + 空窗口
spec_mini = {'palette': {'accent': '#E6C478', 'text': '#FFFFFF', 'bg': 'rgba(11,11,16,.74)'}}
wins = [(7.72, 14.44), (14.44, 21.16), (21.16, 29.32)]
ac, ah, aj = app._hf_bgb_parts('aurora', wins, 704, 1280, spec_mini)
assert 'bgb0' in ah and 'bgb1' not in ah, 'aurora 合并相邻窗口'
assert 'onUpdate' in aj and '6.2832' in aj and 'z-index:3' in ac, 'aurora'
assert 'opacity:0},' in aj.replace(" ", "") or '{opacity:0}' in aj, 'aurora 硬杀'
print(f'bgb-aurora(3窗并1): css={len(ac)} html={len(ah)} js={len(aj)} OK')
mc, mh, mj = app._hf_bgb_parts('mesh', wins, 704, 1280, spec_mini)
assert 'bgb-m' in mh and '--x1' in mj and 'sine.inOut' in mj, 'mesh'
print(f'bgb-mesh(3窗): css={len(mc)} html={len(mh)} js={len(mj)} OK')
assert app._hf_bgb_parts('aurora', [], 704, 1280, spec_mini) == ('', '', '')
assert app._hf_bgb_parts('none', wins, 704, 1280, spec_mini) == ('', '', '')
print('bgb-none/空窗口: OK')
# 矩阵解码字幕: 组包装+三层解码+扰动确定性
cc, ch, cj = app._hf_caps_parts({'menu': {'caption': 'matrix-decode'}, 'palette': {'accent': '#E6C478', 'text': '#FFFFFF'},
                                 'subtitle': {}, 'per_item': {}},
                                [(2.0, 2.5, '她凌晨三点还在', 0), (4.5, 2.0, 'AI 陪伴伦理', 0)], 704, 1280, 30.0)
assert 'capg0' in ch and 'class="r"' in ch and 's0' in ch and 's1' in ch, 'matrix dom'
assert '.s0' in cj and 'autoAlpha:1' in cj and cj.count('tl.set') >= 10, 'matrix js'
# 扰动确定性: 同输入两次生成一致(函数级重跑)
cc2, ch2, cj2 = app._hf_caps_parts({'menu': {'caption': 'matrix-decode'}, 'palette': {'accent': '#E6C478', 'text': '#FFFFFF'},
                                    'subtitle': {}, 'per_item': {}},
                                   [(2.0, 2.5, '她凌晨三点还在', 0), (4.5, 2.0, 'AI 陪伴伦理', 0)], 704, 1280, 30.0)
assert ch == ch2, 'matrix scramble 确定性'
print(f'matrix-decode: css={len(cc)} html={len(ch)} js={len(cj)} OK(确定性✓)')
# RGB故障 / 渐变扫色: 共享词块 DOM + 词级 tween; DOM 必须配平(历史 bug: 组切换三连闭使 cue3+ 逃逸出 #caps)
caps_base = {'palette': {'accent': '#E6C478', 'text': '#FFFFFF', 'bg': '#0B0B10', 'border': '#B6A884'}, 'subtitle': {}, 'per_item': {}}
cues_t = [(2.0, 2.5, '她凌晨三点还在', 0), (4.5, 2.0, 'AI 陪伴伦理', 0)]
for _st in ('glitch-rgb', 'gradient-fill', 'blend-difference', 'highlight', 'editorial-emphasis', 'neon-glow', 'kinetic-slam', 'neon-accent'):
    _c, _h2, _j = app._hf_caps_parts({**caps_base, 'menu': {'caption': _st}}, cues_t, 704, 1280, 30.0)
    assert _h2.count('<div') == _h2.count('</div>'), f'{_st} div 未配平'
print('字幕 DOM 配平: 8 款 OK')
gc, gh, gj = app._hf_caps_parts({**caps_base, 'menu': {'caption': 'glitch-rgb'}}, cues_t, 704, 1280, 30.0)
assert 'capw0' in gh and '#ff003c' in gj and 'textShadow' in gj and 'tl.to' in gj, 'glitch'
# 3 影同构: 5 影特征串(青影后又跟 0 偏移红影)不得出现
assert '#00e5ff,0px 0 #ff003c' not in gj, 'glitch split 仍多影'
print(f'glitch-rgb: css={len(gc)} html={len(gh)} js={len(gj)} OK')
gc2, gh2, gj2 = app._hf_caps_parts({**caps_base, 'menu': {'caption': 'gradient-fill'}}, cues_t, 704, 1280, 30.0)
assert 'background-clip:text' in gc2 and 'backgroundPosition' in gj2 and '45% 0' in gj2, 'gradient'
print(f'gradient-fill: css={len(gc2)} html={len(gh2)} js={len(gj2)} OK')
# 霓虹辉光: 三影同构 + 点亮/熄灭成对; 动力猛砸: 一词一屏覆盖式 + 4 式轮换 + 金色重点词
nc, nh, nj = app._hf_caps_parts({**caps_base, 'menu': {'caption': 'neon-glow'}}, cues_t, 704, 1280, 30.0)
assert 'rgba(0,255,240,.38)' in nc and '#00FFF0' in nj and '#FF0099' in nj, 'neon colors'
assert nj.count('tl.to') % 2 == 0 and nj.count('textShadow') >= 4, 'neon 点亮/熄灭成对'
assert '#00e5ff' not in nj, 'neon 不应混入 glitch 色'
print(f'neon-glow: css={len(nc)} html={len(nh)} js={len(nj)} OK')
kc, kh, kj = app._hf_caps_parts({**caps_base, 'menu': {'caption': 'kinetic-slam'}}, cues_t, 704, 1280, 30.0)
assert 'font-size:' in kh and 'visibility:hidden' in kc, 'kinetic 内联字号+覆盖式'
assert kj.count('tl.fromTo("') == kh.count('<span'), 'kinetic 每词一个入场 tween'
assert 'expo.out' in kj and 'back.out' in kj, 'kinetic 含飞入/弹跳模式'
assert '#FFD700' in kj, 'kinetic 金色重点词'
print(f'kinetic-slam: css={len(kc)} html={len(kh)} js={len(kj)} OK')
# 霓虹强调: 内联六影 + accent 轮换 + 步进微漂(无 yoyo) + 归零硬杀
ac, ah, aj = app._hf_caps_parts({**caps_base, 'menu': {'caption': 'neon-accent'}}, cues_t, 704, 1280, 30.0)
assert 'font-size:' in ah and 'rgba(83,255,1' in ah.replace(' ', ''), 'neon-accent 内联样式+绿辉光'
ws = [w for w in []]  # noqa
_wl = app._hf_caps_parts({**caps_base, 'menu': {'caption': 'neon-accent'}}, cues_t, 704, 1280, 30.0)
# _na_style 三色轮换单元直测
_c0 = app._na_style('测试词', 0, [{'text': '测试词', 'g': 0}], 0, 60)
_c1 = app._na_style('测试词', 1, [{'text': '测试词', 'g': 0}], 0, 60)
_c2 = app._na_style('测试词', 2, [{'text': '测试词', 'g': 0}], 0, 60)
assert '#53FF01' in _c0 and '#FF0002' in _c1 and '#FCFF00' in _c2, 'neon-accent 三色轮换'
assert 'text-shadow:0 8px 16px' in _c0, 'neon-accent 六影静态'
assert 'yoyo' not in aj and 'tl.set("#capg0",{x:0,y:0}' in aj, 'neon-accent 步进微漂+归零'
assert aj.count('tl.fromTo("#capg') == 2, 'neon-accent 组入场弹出'
print(f'neon-accent: css={len(ac)} html={len(ah)} js={len(aj)} OK')
# 模板存取(临时文件隔离)
app._TPL_FILE = app.Path(app.__file__).parent / 'templates_selftest.json'
app._save_templates([])
app.api_hf_templates_save({'name': '我的金黑', 'style': 'gold', 'prompt': '金色拉丝', 'menu': {'caption': 'glitch-rgb', 'trans': 'flash'}, 'top_mark': True})
t = app._load_templates()
assert len(t) == 1 and t[0]['prompt'] == '金色拉丝' and t[0]['menu']['caption'] == 'glitch-rgb' and t[0]['menu']['trans'] == 'flash' and t[0]['top_mark'] is True, '模板保存+normalize'
app.api_hf_templates_save({'name': '我的金黑', 'style': 'minimal', 'prompt': 'v2', 'menu': {}, 'top_mark': False})
t = app._load_templates()
assert len(t) == 1 and t[0]['prompt'] == 'v2' and t[0]['menu']['caption'] == 'ai', '重名覆盖+空菜单归一为ai'
app.api_hf_templates_delete({'name': '我的金黑'})
assert app._load_templates() == [], '模板删除'
(app.Path(app.__file__).parent / 'templates_selftest.json').unlink()
print('模板存取: 保存/normalize/覆盖/删除 OK')
# 校验旧款回归不受组结构改动影响
for st2 in ['bar', 'pill-karaoke', 'editorial-emphasis', 'highlight', 'blend-difference', 'clip-wipe']:
    spec2['menu']['caption'] = st2
    css, html, js = app._hf_caps_parts(spec2, cues, 1080, 1920, 10.0)
    if st2 != 'bar':  # bar=沿用粗剪字幕条, 设计上返回空
        assert 'capg0' in html, st2
    print(f'回归 {st2}: OK')


# ---- 第11轮: 片尾(cta-close/social-card) + 数据(number-pop) 家族 ----
_sp = {"palette": {"accent": "#E6C478"}, "cta": {"action": "关注我，下期见", "button": "点个关注",
                                                 "brand": "科技观察", "proof": "每期三分钟 讲清一件事"}}
for _k in ("cta-close", "social-card"):
    _c, _h, _j = app._hf_cta_parts(_k, 704, 1280, (23.16, 26.16), _sp)
    assert _c and _h and _j, _k + " 生成空"
    assert 'class="clip"' in _h and 'data-start="23.16"' in _h, _k + " 缺 clip 挂载"
    assert "z-index:32" in _c, _k + " z序应压字幕"
    assert "tl.set" in _j, _k + " 缺终点硬杀(gsap_exit_missing_hard_kill)"
    assert "cta-mask" in _h, _k + " 缺深底遮罩"
_c, _h, _j = app._hf_cta_parts("cta-close", 704, 1280, (23.16, 26.16), _sp)
assert _h.count("cta-w") == 2, "行动语按标点切意群(关注我/下期见 = 2)"
_c2, _h2, _j2 = app._hf_data_parts(704, 1280, 6.0, 3.2, "5.7", "倍", "增长", "#E6C478")
assert _h2.count("np-c") == 3 and "np-u" in _h2, "数字应逐字符 span + 单位"
assert "back.out(1.8)" in _j2 and "stagger:0.055" in _j2 and "tl.set" in _j2, "数字弹入动效/stagger/硬杀"
assert app._first_number([(0, 2, "增长了5.7倍多", 0)]) == ("5.7", "倍")
assert app._first_number([(0, 2, "没有任何数字", 0)]) == ("", "")
assert app._cta_texts({"palette": {}})["action"] == "关注我 下期见", "兜底行动语"
assert app._normalize_menu({"cta": "bogus", "data": "bogus"})["cta"] == "ai", "非法家族值回退"
assert app._hf_data_parts(704, 1280, 6, 3, "", "倍", "", "#fff") == ("", "", ""), "空数值不应渲染"
assert app._hf_cta_parts("none", 704, 1280, (1, 2), {}) == ("", "", ""), "cta=none 不应渲染"
assert app._hf_cta_parts("cta-close", 704, 1280, (23.16, 26.16), {"palette": {}, "cta": {"action": "<b>注入</b>"}})[1].find("<b>") < 0, "文案须 HTML 转义"
print("片尾/数据家族: OK")


# ---- 第12轮: 片尾扩款(cta-lockup) + 数据扩款(conic-ring) + 新家族对比(before-after) ----
_sp2 = {"palette": {"accent": "#E6C478"}, "cta": {"action": "关注我，下期见", "button": "点个关注",
                                                   "microcopy": "免费看全部"}, "intro_title": "测试"}
_lc, _lh, _lj = app._hf_cta_parts("cta-lockup", 704, 1280, (23.16, 26.16), _sp2)
assert "cl-micro" in _lh and "sine.inOut" in _lj and "tl.set" in _lj, "cta-lockup 三段落定/漂移/硬杀"
_rcc, _rch, _rcj = app._hf_ring_parts(704, 1280, 6.0, 3.4, 72, "72", "%", "完成度", "#E6C478")
assert "stroke-dashoffset" in _rcc and "pr-fill" in _rch, "环形需 SVG 描边实现(conic+mask 会裁掉中心数字)"
assert "onUpdate" in _rcj and "tl.set" in _rcj, "环形数字同步计数+硬杀"
_cx, _cxh, _cxj = app._hf_cmp_parts(704, 1280, 14.0, 4.0, app._cta_texts(_sp2), "#E6C478")
assert "clip-path:inset(0 100% 0 0)" in _cx and "clipPath" in _cxj, "对比需直接补间 clip-path(不依赖 CSS 变量)"
assert _cxh.count("cs-after") == 1 and _cxh.count("cs-before") == 1, "对比两栏面板"
assert "tl.set" in _cxj, "对比卡退出硬杀"
assert app._normalize_menu({"cmp": "before-after"})["cmp"] == "before-after"
assert app._normalize_menu({"cmp": "bogus"})["cmp"] == "ai", "非法对比值回退"
assert app._hf_cmp_parts(704, 1280, 5.0, 3.0, app._cta_texts({"palette": {}}), "#fff")[0], "对比卡兜底文案也须渲染"
assert app._num_or_zero("72%") == 72.0 and app._num_or_zero("") == 0.0
assert app._hf_cta_parts("cta-lockup", 704, 1280, (23.16, 26.16), {"palette": {}, "cta": {"action": "x", "microcopy": "<i>e</i>"}})[1].find("<i>") < 0, "微文案须转义"
print("片尾/数据/对比 扩款: OK")


# ---- 第13轮: 数据扩款(number-wheel) + 新家族清单(checklist) ----
_tx3 = app._cta_texts({"palette": {"accent": "#E6C478"}})
_nc, _nh, _nj = app._hf_nwheel_parts(704, 1280, 6.4, 3.2, "72", "%", "增长率", "#E6C478")
assert _nh.count("nw-col") == 2 and _nh.count("nw-strip") == 2, "滚动计数每位一列"
assert '"y":0' in _nj.replace(" ", "") or 'y:0' in _nj, "滚动需 transform 位移补间"
assert "tl.set" in _nj and "power3.out" in _nj, "滚动落定+硬杀"
_lc2, _lh2, _lj2 = app._hf_list_parts(704, 1280, 10.0, 5.0, _tx3, "#E6C478")
assert _lh2.count("ml-row") == 3 and _lh2.count("ml-chk") == 3, "清单三行+三勾"
assert "strokeDashoffset" in _lj2 and "scaleX" in _lj2, "勾/椭圆自绘+下划线展开"
assert "tl.set" in _lj2, "清单卡硬杀"
assert "KaiTi" in _lc2, "中文手写风用楷体"
assert app._normalize_menu({"list": "checklist"})["list"] == "checklist"  # 实现可用; UI 暂缓启用(check 对比度口径)
assert app._normalize_menu({"list": "bogus"})["list"] == "ai", "非法清单值回退"
assert app._cta_texts({"palette": {}})["list1"].count("|") == 1, "清单默认行含 标签|内容 分隔"
print("数据/清单 扩款: OK")


# ---- 第14轮: 对比家族扩款 split-tilt(双卡倾斜) ----
_tt = app._cta_texts({"palette": {"accent": "#E6C478"}})
_tc, _th, _tj = app._hf_tilt_parts(704, 1280, 14.0, 4.0, _tt, "#E6C478")
assert _th.count("tw-card") == 2 and _th.count("tw-pill") == 2, "双卡+双眉标"
assert "rotateY(14deg)" in _tc and "rotateY(-14deg)" in _tc, "镜像 3D 倾斜"
assert "perspective:900px" in _tc, "共享舞台透视"
assert '"back.out(1.9)"' in _tj and "sine.inOut" in _tj and "tl.set" in _tj, "眉标过冲+idle浮动+硬杀"
assert app._normalize_menu({"cmp": "split-tilt"})["cmp"] == "split-tilt"
print("对比扩款 split-tilt: OK")
