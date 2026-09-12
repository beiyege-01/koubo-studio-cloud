# 补完 20260911-002313_test1全流程 项目剩余 pending 段(串行+重试, 完成用户上次被 SSL 断线打断的合成)
import importlib.util, time
spec = importlib.util.spec_from_file_location('app', r'E:\deepseek-works\koubo-studio\app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

d = app._find_project('20260911-002313_test1全流程')
data = app._load_segments(d)
for s in data['segments']:
    if s['status'] == 'kept':
        continue
    print('合成', s['id'], s['text'][:16], '...', flush=True)
    dur, sr = app._rh_tts_one(s['text'], d / 'tts' / s['wav'])
    s['dur_sec'] = round(dur, 2)
    s['sr'] = sr
    s['gen_at'] = int(time.time())
    s['status'] = 'kept'
    app._save_segments(d, data)
    print('  ✓', s['id'], f'{dur}s', flush=True)
kept = sum(1 for s in app._load_segments(d)['segments'] if s['status'] == 'kept')
print(f'完成: {kept}/{len(data["segments"])} 段已合成')
