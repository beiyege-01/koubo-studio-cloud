import sys, json, httpx
KEY = sys.argv[1]
BASE = "https://api.deepseek.com"
def call(tag, extra=None, model="deepseek-flash", temp=0.1):
    body = {"model": model, "messages": [{"role":"user","content":"只回复两个字:正常"}], "max_tokens": 64}
    if temp is not None: body["temperature"] = temp
    if extra: body.update(extra)
    r = httpx.post(BASE + "/chat/completions", headers={"Authorization":"Bearer "+KEY}, json=body, timeout=60)
    j = r.json()
    m = j["choices"][0]["message"] if r.status_code == 200 else {}
    rc = m.get("reasoning_content") or ""
    print(f"{tag}: HTTP {r.status_code} | content={ (m.get('content') or '')[:20]!r} | reasoning={len(rc)}字 | finish={j['choices'][0].get('finish_reason') if r.status_code==200 else j.get('error')}")
call("默认(思考开启)")
call("关闭思考", {"thinking": {"type": "disabled"}})
call("关闭思考+低强度", {"thinking": {"type": "disabled"}, "reasoning_effort": "low"})
call("v4-pro", None, model="deepseek-v4-pro")
