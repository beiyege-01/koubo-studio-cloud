# 测试: 云端 TTS 工作流只传文本(节点66), 看应用端默认输出音色
import importlib.util, time, json
spec = importlib.util.spec_from_file_location('app', r'E:\deepseek-works\koubo-studio\app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
import httpx
from pathlib import Path

av = app.CONF['avatar']
key = av['api_key'].strip()
text = '各位观众大家好，今天我们聊一聊方便面为什么是圆的，这里面藏着工程学的智慧。'
print('提交(仅节点66, 不传任何参考音频):', text[:20], '...')
r = httpx.post(f"{av['base'].rstrip('/')}/openapi/v2/run/ai-app/2098056520643035137",
               headers={'Authorization': 'Bearer ' + key},
               json={'nodeInfoList': [{'nodeId': '66', 'fieldName': 'inStr', 'fieldValue': text}],
                     'instanceType': 'default'}, timeout=60)
j = r.json()
tid = j.get('taskId')
print('taskId:', tid)
if not tid:
    print('提交失败:', json.dumps(j, ensure_ascii=False)[:300])
    raise SystemExit(1)
url = ''
for i in range(120):
    time.sleep(5)
    st, results, err = app._rh_status(tid)
    print(f'  轮询{i+1}: {st}')
    if st == 'SUCCESS':
        url = next((x['url'] for x in results if x.get('url') and (x.get('outputType') or '').lower() in ('wav', 'mp3', 'flac', 'm4a')), '')
        break
    if st == 'FAILED':
        print('失败:', err)
        raise SystemExit(1)
else:
    print('超时')
    raise SystemExit(1)
print('结果:', (url or '无')[:90])
out = Path(r'E:\deepseek-works\RH应用默认音色测试_仅传文本.wav')
with httpx.stream('GET', url, timeout=300, follow_redirects=True) as resp:
    resp.raise_for_status()
    out.write_bytes(resp.read())
print(f'已保存: {out} · {out.stat().st_size} bytes')
