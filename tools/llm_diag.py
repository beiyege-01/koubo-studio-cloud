import sys, json, httpx

KEY = sys.argv[1]
BASE = "https://api.deepseek.com"

def show(tag, r):
    print(f"--- {tag} ---")
    print("HTTP", r.status_code)
    try:
        j = r.json()
        print(json.dumps(j, ensure_ascii=False)[:500])
    except Exception:
        print((r.text or "")[:300])

# 1) 列出可用模型(最准)
try:
    r = httpx.get(BASE + "/models", headers={"Authorization": "Bearer " + KEY}, timeout=30)
    show("GET /models", r)
except Exception as e:
    print("GET /models 异常:", type(e).__name__, str(e)[:200])

# 2) 用用户填的 deepseek-flash 试一次
for model in ("deepseek-flash", "deepseek-chat"):
    try:
        r = httpx.post(BASE + "/chat/completions",
                       headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
                       json={"model": model, "messages": [{"role": "user", "content": "只回复两个字:正常"}],
                             "max_tokens": 10, "temperature": 0.1},
                       timeout=60)
        show(f"POST /chat/completions model={model}", r)
    except Exception as e:
        print(f"POST model={model} 异常:", type(e).__name__, str(e)[:200])

# 3) 带 /v1 前缀对照
try:
    r = httpx.post(BASE + "/v1/chat/completions",
                   headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
                   json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                   timeout=30)
    show("POST /v1/chat/completions model=deepseek-chat", r)
except Exception as e:
    print("POST /v1 异常:", type(e).__name__, str(e)[:200])
