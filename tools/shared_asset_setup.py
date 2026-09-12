# 造临时项目验证资产共用: segments 段 + custom 自制项, 各配一个假 mp4
import importlib.util, json, subprocess, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('app', r'E:\deepseek-works\koubo-studio\app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

name = 't_shared_asset_verify'
root = app._projects_dir()
d = root / name
if d.exists():
    import shutil; shutil.rmtree(d)
d.mkdir(parents=True)
(d / 'videos').mkdir(exist_ok=True)
(d / 'tts').mkdir(exist_ok=True)
(d / 'audio').mkdir(exist_ok=True)

# 假音频 + 假视频(1秒黑场)
for p in [d / 'tts' / 'seg01.wav', d / 'audio' / 'cus01.wav']:
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', '-t', '1', str(p)], check=True)
for p in [d / 'videos' / 'seg01.mp4', d / 'videos' / 'cus01.mp4']:
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x568:d=1', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', '-shortest', str(p)], check=True)

app._save_segments(d, {'model': 'test', 'segments': [
    {'id': 'seg01', 'text': '共用资产验证段', 'wav': 'seg01.wav', 'status': 'kept', 'dur_sec': 1.0, 'sr': 48000,
     'gen_at': int(time.time()), 'video_task': '', 'video_status': 'kept', 'video': 'seg01.mp4', 'video_error': ''}]})
app._save_custom(d, [
    {'id': 'cus01', 'name': '自制音频验证', 'wav': 'audio/cus01.wav', 'img': '',
     'video_status': 'kept', 'video': 'cus01.mp4', 'video_task': '', 'video_error': ''}])
print('临时项目已建:', d)
print('segments:', json.dumps(app._load_segments(d)['segments'][0], ensure_ascii=False)[:160])
print('custom:', json.dumps(app._load_custom(d)[0], ensure_ascii=False)[:160])
