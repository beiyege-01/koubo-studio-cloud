# -*- coding: utf-8 -*-
r"""口播辅助创作台 · 云端版 (RunningHub TTS + 数字人 + HyperFrames 精剪)
运行(源码): uvicorn app:app --host 127.0.0.1 --port 8795
运行(exe):  双击 koubo-studio-cloud.exe (自动选端口并打开浏览器)
"""
from __future__ import annotations

import json, os, random, re, shutil, subprocess, sys, threading, time, wave, webbrowser
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import unquote

import httpx
try:
    import numpy as np          # 仅本地 TTS(soar) 写 wav 需要; 云端版可缺省
except Exception:               # pragma: no cover
    np = None
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

# ---- 路径基座: 冻结(exe) 与源码两种形态 ----
FROZEN = bool(getattr(sys, "frozen", False)) or ("__compiled__" in globals())   # PyInstaller 用 sys.frozen; Nuitka 注入 __compiled__
if FROZEN:
    ROOT = Path(sys.executable).resolve().parent          # 可写: config.json / projects / templates.json
    RES = Path(getattr(sys, "_MEIPASS", ROOT))            # 只读: assets / vendor / hf-library / skills
else:
    ROOT = Path(__file__).resolve().parent
    RES = ROOT
CONF_PATH = ROOT / "config.json"

_DEFAULT_CONF = {
    "llm": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash", "api_key": "",
            "thinking": "off"},   # off=关闭思考(更快更省, 写稿/选配推荐) | on=开启 | low=开启+低强度
    "avatar": {"base": "https://www.runninghub.ai", "api_key": "",
               # 默认指向作者公开的数字人应用; 用户可在 config.json 换自己的应用 ID
               "workflow_id": "2097438782312632322",
               "node_image": "327", "node_audio": "393", "instance_type": "plus", "default_image": "", "max_parallel": 1},   # 并行上限(1=串行排队, API 不支持多线时也安全)
    "tts_rh": {"workflow_id": "2098056520643035137", "node_text": "66", "node_ref_audio": "13",
               "node_ref_text": "67", "ref_audio": "", "ref_text": "", "instance_type": "default",
               "fallback_ref_audio": "", "fallback_ref_text_file": ""},
    "tts": {"model_path": "", "precision": "bfloat16", "ref_audio": "", "ref_text_file": "",
            "projects_dir": "", "segment": {"min": 30, "max": 40}},
}
if CONF_PATH.exists():
    try:
        CONF = json.loads(CONF_PATH.read_text(encoding="utf-8"))
    except Exception:
        CONF = dict(_DEFAULT_CONF)
else:
    CONF = json.loads(json.dumps(_DEFAULT_CONF))      # 首次运行自建(用户不必手改文件)
    try:
        CONF_PATH.write_text(json.dumps(CONF, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
for _k, _v in _DEFAULT_CONF.items():                  # 老配置补新键
    if _k not in CONF:
        CONF[_k] = json.loads(json.dumps(_v))
    elif isinstance(_v, dict):
        for _kk, _vv in _v.items():
            CONF[_k].setdefault(_kk, _vv)

def _save_conf():
    CONF_PATH.write_text(json.dumps(CONF, ensure_ascii=False, indent=2), encoding="utf-8")

def _projects_dir() -> Path:
    r"""项目根目录: 配置优先; 空 = 软件目录\projects (exe = 安装目录)。"""
    v = (CONF.get("projects_dir") or "").strip()
    p = Path(v) if v else ROOT / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p

app = FastAPI(title="口播辅助创作台", docs_url=None, redoc_url=None)

# ---------------- LLM (用户在设置面板配置) ----------------
_llm_lock = threading.Lock()

def _seed_key_from_modlens():
    """首次运行且用户未填 key 时从本机 modlens 配置导入(开发便利)。发行版禁用。"""
    if FROZEN:      # exe 版: 绝不读取用户机器上的第三方配置, 避免静默用上别人的 key
        return
    llm = CONF.get("llm", {})
    if llm.get("api_key"):
        return
    try:
        raw = os.path.expandvars("%USERPROFILE%\\.modlens\\config.json")
        node = json.loads(Path(raw).read_text(encoding="utf-8"))
        for part in "providers.openai.apiKey".split("."):
            node = node[part]
        CONF["llm"]["api_key"] = str(node)
        _save_conf()
        print("[llm] 已从本机 modlens 配置导入默认 key (可在设置面板覆盖)", flush=True)
    except Exception:
        pass

_seed_key_from_modlens()

def llm_msgs(msgs: list[dict], temperature: float | None = None) -> str:
    cfg = CONF["llm"]
    key = (cfg.get("api_key") or "").strip()
    if not key:
        raise HTTPException(400, "尚未配置 LLM API Key — 请点右上角 ⚙ 设置 填入")
    body = {"model": cfg.get("model", ""), "messages": msgs,
            "max_tokens": int(cfg.get("max_tokens") or 8192)}   # 思考模式会先消耗推理 token, 预算给足
    # 思考模式开关(官方: {"thinking":{"type":"enabled|disabled"}}; 强度 reasoning_effort: low/high/max)
    _tk = str(cfg.get("thinking") or "off").lower()
    if _tk in ("off", "disabled"):
        body["thinking"] = {"type": "disabled"}
    elif _tk in ("on", "enabled", "low"):
        body["thinking"] = {"type": "enabled"}
        if _tk == "low":
            body["reasoning_effort"] = "low"
    if temperature is not None and _tk in ("off", "disabled"):
        body["temperature"] = temperature    # 思考模式不支持 temperature(传了不生效), 关闭时才传
    with _llm_lock:
        last = ""
        for _ in range(2):
            try:
                r = httpx.post(cfg["base_url"].rstrip("/") + "/chat/completions",
                               json=body, headers={"Authorization": "Bearer " + key},
                               timeout=cfg.get("timeout_sec", 300))
                r.raise_for_status()
                _msg = r.json()["choices"][0]["message"]
                content = (_msg.get("content") or "").strip()
                if content:
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
                    if "<think>" in content:          # 未闭合(被截断) → 取思考结束后的正文
                        content = content.rsplit("</think>", 1)[-1].strip()
                    if content:
                        return content
                # 空响应诊断: 推理型模型常见(推理未结束/预算不够) → 带上线索便于定位
                _rc = (_msg.get("reasoning_content") or "")
                last = ("模型只返回了推理内容未给出正文(推理型模型, 推理 %d 字, finish=%s) — "
                        "建议把模型换成 deepseek-chat, 或增大 max_tokens"
                        % (len(_rc), r.json()["choices"][0].get("finish_reason"))) if _rc else "空响应"
            except HTTPException:
                raise
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(2)
    raise RuntimeError("LLM 调用失败: " + last)

def llm(system: str, user: str, temperature: float | None = None) -> str:
    return llm_msgs([{"role": "system", "content": system},
                     {"role": "user", "content": user}], temperature)

# ---------------- 技能注册表 (skills/<stage>/*.md, 用户可自由加) ----------------
STAGES = ("collect", "research", "unify")

def _skill_list(stage: str) -> list[str]:
    d = RES / "skills" / stage
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md"))

def _skill_text(stage: str, name: str) -> str:
    f = RES / "skills" / stage / f"{name}.md"
    if not f.exists() or ".." in name:
        raise HTTPException(404, f"技能不存在: {stage}/{name}")
    return f.read_text(encoding="utf-8")

def _default_skills(stage: str) -> list[str]:
    return CONF.get("skill_defaults", {}).get(stage) or _skill_list(stage)[:1]

# ---------------- aihot 公开API (免Key 匿名只读) ----------------
def aihot_evidence() -> str:
    ua = {"User-Agent": "koubo-studio/1.0 (+https://aihot.news/aihot-skill/)"}
    lines = []
    try:
        hot = httpx.get("https://aihot.news/api/v1/hot-topics", headers=ua, timeout=20).json()
        for it in (hot if isinstance(hot, list) else hot.get("items", hot.get("topics", [])))[:10]:
            rank = it.get("rank", "")
            title = it.get("title", "")
            src = (it.get("source") or {}).get("name", "") if isinstance(it.get("source"), dict) else it.get("source", "")
            ts = it.get("publishedAt") or it.get("discoveredAt") or ""
            lines.append(f"热点第{rank}名: {title}（{src} · {ts}）")
            if it.get("reason"):
                lines.append("  推荐理由: " + str(it["reason"])[:120])
            if it.get("summary"):
                lines.append("  摘要: " + str(it["summary"])[:200])
    except Exception as e:
        lines.append(f"(热点榜获取失败: {type(e).__name__})")
    try:
        items = httpx.get("https://aihot.news/api/v1/items",
                          params={"mode": "selected", "window": "7d", "limit": 10},
                          headers=ua, timeout=20).json()
        arr = items.get("items", items) if isinstance(items, dict) else items
        lines.append("")
        lines.append("近7天精选:")
        for it in (arr or [])[:10]:
            lines.append(f"- {it.get('title','')}（{(it.get('source') or {}).get('name','') if isinstance(it.get('source'), dict) else ''}）")
            if it.get("summary"):
                lines.append("  " + str(it["summary"])[:200])
    except Exception as e:
        lines.append(f"(精选获取失败: {type(e).__name__})")
    return "\n".join(lines) or "(AIHOT 无返回)"

def aihot_tool(mode: str = "hot", query: str = "") -> str:
    ua = {"User-Agent": "koubo-studio/1.0 (+https://aihot.news/aihot-skill/)"}
    try:
        if mode == "hot":
            j = httpx.get("https://aihot.news/api/v1/hot-topics", headers=ua, timeout=20).json()
            arr = j if isinstance(j, list) else j.get("items", j.get("topics", []))
            lines = [f"热点第{it.get('rank','?')}名: {it.get('title','')}（{(it.get('source') or {}).get('name','') if isinstance(it.get('source'), dict) else ''}）"
                     + (f" | 理由: {str(it['reason'])[:100]}" if it.get("reason") else "")
                     for it in (arr or [])[:10]]
        else:
            params = {"mode": "selected", "window": "7d", "limit": 10}
            if query:
                params["q"] = query
            j = httpx.get("https://aihot.news/api/v1/items", params=params, headers=ua, timeout=20).json()
            arr = j.get("items", j) if isinstance(j, dict) else j
            lines = [f"- {it.get('title','')} | {str(it.get('summary',''))[:160]}" for it in (arr or [])[:10]]
        return "\n".join(lines) or "(AIHOT 无返回)"
    except Exception as e:
        return f"(AIHOT 调用失败 {type(e).__name__})"

# ---------------- 搜索链 (DDG → DDG-lite → Bing, 免key) ----------------
def ddg_search(query: str, n: int = 8) -> list[dict]:
    try:
        r = httpx.get("https://html.duckduckgo.com/html/", params={"q": query},
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                      timeout=20, follow_redirects=True)
        r.raise_for_status()
        items = []
        for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
            href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
            mm = re.search(r"uddg=([^&]+)", href)
            if mm:
                href = unquote(mm.group(1))
            items.append({"title": unescape(title).strip(), "url": href})
            if len(items) >= n:
                break
        snips = [unescape(re.sub(r"<[^>]+>", "", s)).strip()
                 for s in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)]
        for i, it in enumerate(items):
            it["snippet"] = snips[i] if i < len(snips) else ""
        return items
    except Exception:
        return []

def ddg_lite_search(query: str, n: int = 8) -> list[dict]:
    try:
        r = httpx.get("https://lite.duckduckgo.com/lite/", params={"q": query},
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"},
                      timeout=20, follow_redirects=True)
        r.raise_for_status()
        items = []
        for m in re.finditer(r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
            href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
            mm = re.search(r"uddg=([^&]+)", href)
            if mm:
                href = unquote(mm.group(1))
            items.append({"title": unescape(title).strip(), "url": href, "snippet": ""})
            if len(items) >= n:
                break
        return items
    except Exception:
        return []

def bing_search(query: str, n: int = 8) -> list[dict]:
    try:
        r = httpx.get("https://www.bing.com/search", params={"q": query, "count": str(n)},
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
                               "Accept-Language": "zh-CN,zh;q=0.9"},
                      timeout=20, follow_redirects=True)
        r.raise_for_status()
        items = []
        for m in re.finditer(r'<li class="b_algo"[^>]*>(.*?)</li>', r.text, re.S):
            block = m.group(1)
            a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S) or \
                re.search(r'<a[^>]+href="(http[^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not a:
                continue
            url, title = a.group(1), re.sub(r"<[^>]+>", "", a.group(2))
            sn = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
            snippet = unescape(re.sub(r"<[^>]+>", "", sn.group(1))).strip() if sn else ""
            items.append({"title": unescape(title).strip(), "url": url, "snippet": snippet})
            if len(items) >= n:
                break
        return items
    except Exception:
        return []

def web_search(query: str, n: int = 8) -> tuple[list[dict], str]:
    """搜索链: DDG-html → DDG-lite → Bing, 返回(结果, 后端名)"""
    for fn in (ddg_search, ddg_lite_search, bing_search):
        items = fn(query, n)
        if items:
            return items, fn.__name__
    return [], "none"

def web_fetch(url: str, cap: int = 4500) -> str:
    try:
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"},
                      timeout=25, follow_redirects=True)
        r.raise_for_status()
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.S | re.I)
        t = unescape(re.sub(r"<[^>]+>", " ", t))
        t = re.sub(r"\s+", " ", t).strip()
        return t[:cap] or "(页面无正文)"
    except Exception as e:
        return f"(抓取失败 {type(e).__name__}: {str(e)[:120]})"

def _search_block(items: list[dict]) -> str:
    if not items:
        return "(本次搜索无结果)"
    return "\n\n".join(f"[{i}] {it['title']}\nURL: {it['url']}\n摘要: {it['snippet']}"
                       for i, it in enumerate(items, 1))

# ---------------- Agent 工具循环 (ReAct, 让技能真正联网) ----------------
TOOL_MANUAL = """

## 实时工具手册（本环境已接入真联网工具）
**检索轮**：每轮只输出一个 JSON 对象，不要输出任何其他文字：
- 搜索：{"thought":"一句话理由","tool":"web_search","args":{"query":"搜索词","n":6}}
- 读网页：{"thought":"...","tool":"web_fetch","args":{"url":"https://..."}}
- AI热点：{"thought":"...","tool":"aihot","args":{"mode":"hot 或 items","query":"可选关键词"}}

**最终产出**：检索完成后，第一行单独写 `FINAL`，从第二行开始直接写按技能要求的完整 markdown 产出（**不要再包 JSON**，不要转义）。

规则：
1. 涉及「最近/现在/最新/热度/谁在做/数据」的判断**必须先用工具检索验证**，禁止凭记忆断言
2. 搜索结果只给线索，重要结论用 web_fetch 读原文；一手来源（官方博客/公告）优先
3. final 里引用的事实在 thought 里都要能对应到某次工具结果；检索不到的写明「未检索到」，不编造
4. 最多 8 轮，够用就好
"""

def _extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.M).strip()
    for s in (t, ) + (re.search(r"\{.*\}", t, re.S) and (re.search(r"\{.*\}", t, re.S).group(0), ) or ()):
        try:
            return json.loads(s)
        except Exception:
            pass
        try:   # 模型常在字符串里带裸换行/制表符 → strict=False 容忍控制字符
            return json.loads(s, strict=False)
        except Exception:
            pass
    raise ValueError("no json")

def run_agent(system: str, user: str, temperature: float | None = None, max_rounds: int = 10, min_tools: int = 2) -> tuple[str, list[str]]:
    """ReAct 工具循环: 检索轮=JSON工具调用, 终稿=FINAL裸文本。返回(产出, 轨迹)"""
    msgs = [{"role": "system", "content": system + TOOL_MANUAL},
            {"role": "user", "content": user}]
    trail: list[str] = []
    tools_used = 0

    def _take_final(reply: str) -> str | None:
        m = re.search(r"^\s*FINAL\s*:?\s*\r?\n", reply, re.I | re.M)
        if m:
            return reply[m.end():].strip()
        m2 = re.match(r'^\s*FINAL\s*:?\s*(.+)$', reply, re.I | re.S)
        if m2 and "{" not in m2.group(1)[:2]:
            return m2.group(1).strip()
        return None

    for rnd in range(max_rounds):
        reply = llm_msgs(msgs, temperature)
        print(f"[agent r{rnd}] {reply[:110]!r}", flush=True)
        fin = _take_final(reply)
        if fin:
            if tools_used < min_tools:
                msgs.append({"role": "assistant", "content": reply[:2000]})
                msgs.append({"role": "user", "content": f"你现在还一次工具都没用（要求至少{min_tools}次真实检索）。这份产出不合格——请先输出JSON工具调用（web_search/web_fetch/aihot）完成检索，再考虑FINAL。"})
                continue
            trail.append(f"final(r{rnd})")
            return fin, trail
        try:
            j = _extract_json(reply)
        except ValueError:
            msgs.append({"role": "assistant", "content": reply[:2000]})
            msgs.append({"role": "user", "content": "输出不合法。检索轮只输出一个JSON工具调用；最终产出第一行写FINAL。"})
            continue
        if isinstance(j, dict) and j.get("final"):
            if tools_used < min_tools:
                msgs.append({"role": "assistant", "content": reply[:2000]})
                msgs.append({"role": "user", "content": f"你还没完成必要的联网检索（要求至少{min_tools}次）。请先输出JSON工具调用检索。"})
                continue
            trail.append(f"final-json(r{rnd})")
            return str(j["final"]).strip(), trail
        tool, args = j.get("tool"), j.get("args") or {}
        if tool == "web_search":
            items, backend = web_search(str(args.get("query", ""))[:120], int(args.get("n", 6)))
            trail.append(f"web_search[{backend}] {args.get('query','')!r} → {len(items)}条")
            result = _search_block(items)
        elif tool == "web_fetch":
            url = str(args.get("url", ""))[:500]
            trail.append(f"web_fetch {url[:60]!r}")
            result = web_fetch(url)
        elif tool == "aihot":
            trail.append(f"aihot {args.get('mode','hot')} {args.get('query','')!r}")
            result = aihot_tool(str(args.get("mode", "hot")), str(args.get("query", ""))[:80])
        else:
            result = f"(未知工具 {tool!r}，可用: web_search/web_fetch/aihot)"
        tools_used += 1
        nudge = (f"\n\n(已完成 {tools_used} 次检索。资料足够时请立即输出最终结果：第一行单独写 FINAL，"
                 f"第二行起直接写完整 markdown，不要再包 JSON；确有必要才可再检索一次。)")
        msgs.append({"role": "assistant", "content": reply[:2000]})
        msgs.append({"role": "user", "content": "TOOL_RESULT:\n" + result[:6500] + nudge})
    msgs.append({"role": "user", "content": "检索轮次已用完。现在输出最终结果：第一行单独写 FINAL，第二行开始写完整 markdown。"})
    reply = llm_msgs(msgs, temperature)
    print(f"[agent last] {reply[:110]!r}", flush=True)
    fin = _take_final(reply)
    if fin:
        trail.append("final(超时兜底)")
        return fin, trail
    return reply.strip(), trail + ["final(原文兜底)"]

# ---------------- 项目存储 ----------------
def _safe_slug(topic: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "", topic).strip("。　 ")
    return (s or "未命名选题")[:24]

def _find_or_create(topic: str) -> Path:
    base = _projects_dir()
    base.mkdir(parents=True, exist_ok=True)
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / "选题.txt"
        if d.is_dir() and f.exists() and f.read_text(encoding="utf-8").strip() == topic.strip():
            return d
    d = base / (time.strftime("%Y%m%d-%H%M%S") + "_" + _safe_slug(topic))
    (d / "tts").mkdir(parents=True, exist_ok=True)
    return d

def _find_project(name: str) -> Path:
    d = _projects_dir() / name
    if not d.is_dir() or ".." in name or "/" in name or "\\" in name:
        raise HTTPException(404, "项目不存在")
    return d

def _write_materials(d: Path, body: dict):
    if (body.get("topic") or "").strip():
        (d / "选题.txt").write_text(body["topic"].strip(), encoding="utf-8")
    if (body.get("direction") or "").strip():
        (d / "方向.txt").write_text(body["direction"].strip(), encoding="utf-8")
    for key, fname in (("material1", "灵感资料.md"), ("material2", "调研资料.md"), ("script", "文案.md")):
        v = (body.get(key) or "").strip()
        if v:
            (d / fname).write_text(v + "\n", encoding="utf-8")

@app.post("/api/script/import")
def api_script_import(body: dict):
    """导入用户自带口播稿(txt/md) → 文案.md; 必须在项目内(统一管理资产), 前置步骤可跳过"""
    d = _find_project(body.get("project") or "")
    p = _clean_path(body.get("path") or "")
    pp = Path(p)
    if not p or not pp.exists() or not pp.is_file():
        raise HTTPException(400, "文件不存在: " + p)
    if pp.suffix.lower() not in (".txt", ".md", ".markdown"):
        raise HTTPException(400, "仅支持 .txt / .md 口播稿: " + pp.name)
    if pp.stat().st_size > 512 * 1024:
        raise HTTPException(400, "文件过大（>512KB），请确认是口播稿")
    raw = pp.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise HTTPException(400, "无法解码文件 — 请另存为 UTF-8 的 .txt/.md 再导入")
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise HTTPException(400, "文件是空的")
    _write_materials(d, {"script": text})
    return {"script": text, "chars": len(text), "name": pp.name}

# ---------------- 分段算法 (标点优先, 30-40字, 数字保护) ----------------
_SENT_END = "。！？!?…"
_CLAUSE = "，,、；;：:"

def _cut_ok(t: str, i: int) -> bool:
    """边界在 i|_{i+1} 之间。禁止切进数字: 47,700 / 4,200 / 3.14; 也禁止切进英文单词内(Git|HubStar)"""
    if i < 0 or i >= len(t) - 1:
        return True                     # 末尾可以切
    a, b = t[i], t[i + 1]
    if a.isdigit() and b.isdigit():
        return False
    if a in ",.，。、；;" and i > 0 and t[i - 1].isdigit() and b.isdigit():
        return False                    # 千分位/小数点两侧都是数字
    if a.isascii() and a.isalnum() and b.isascii() and b.isalnum():
        return False                    # 英文字母/数字连续串中间不切(中文不受影响)
    return True

def _best_cut(t: str, lo: int, hi: int) -> int:
    """在 t[lo..hi] 里选切点: 优先最靠后的标点, 且不得切进数字; 返回边界index(含)。"""
    hi = min(hi, len(t) - 1)
    cands = [i for i in range(hi, lo - 1, -1) if t[i] in _CLAUSE or t[i] in _SENT_END]
    for c in cands:
        if _cut_ok(t, c):
            return c
    for c in range(hi, lo - 1, -1):     # 无标点可依: 硬切也避开数字
        if _cut_ok(t, c):
            return c
    return hi

def split_segments(text: str, mn: int = 30, mx: int = 40) -> list[str]:
    text = re.sub(r"\s+", "", text)
    units: list[str] = []
    for sent in filter(None, re.split(rf"(?<=[{_SENT_END}])", text)):
        if len(sent) <= mx:
            units.append(sent)
            continue
        buf = ""
        for part in filter(None, re.split(rf"(?<=[{_CLAUSE}])", sent)):
            if len(buf) + len(part) <= mx:
                buf += part
                continue
            if buf:
                units.append(buf)
            if len(part) <= mx:
                buf = part
            else:
                while len(part) > mx:   # 单子句超长: 切点避开数字
                    c = _best_cut(part, mx - 12, mx - 1)
                    units.append(part[:c + 1])
                    part = part[c + 1:]
                buf = part
        if buf:
            units.append(buf)

    segs, cur = [], ""
    for u in units:
        cand = cur + u
        if len(cand) <= mx:
            cur = cand
            continue
        if len(cur) >= mn:
            segs.append(cur)
            cur = u
        else:                       # 语义优先: 不足 mn 时并进来, 允许轻度超长
            cur = cand
            if len(cur) > mx + 8:   # 超出软容差 → 切在窗口内最后一个安全标点(绝不切断语义/数字)
                cut = _best_cut(cur, mn - 8, mx + 7)
                segs.append(cur[:cut + 1])
                cur = cur[cut + 1:]
    if cur:
        if segs and len(segs[-1]) < mn and len(segs[-1]) + len(cur) <= mx:
            segs[-1] += cur
        else:
            segs.append(cur)
    return segs

# ---------------- TTS (dots soar, 懒加载单例) ----------------
_tts_lock = threading.Lock()
_rt = None
_ref_text = ""

def _get_rt():
    global _rt, _ref_text
    with _tts_lock:
        if _rt is None:
            sys.path.insert(0, r"E:\deepseek-works\dots-tts\src")
            from dots_tts.runtime import DotsTtsRuntime
            t = CONF["tts"]
            _ref_text = Path(t["ref_text_file"]).read_text(encoding="utf-8").strip()
            print("[tts] loading model (first synth, ~20-40s):", t["model_path"], flush=True)
            _rt = DotsTtsRuntime.from_pretrained(t["model_path"], precision=t.get("precision", "bfloat16"), optimize=False)
            print("[tts] model ready", flush=True)
        return _rt

def _tts_one(text: str, wav_path: Path, ref_audio: str | None = None, ref_text: str | None = None):
    rt = _get_rt()
    t = CONF["tts"]
    ra = ref_audio or t["ref_audio"]          # 项目自定义音色 > 内置兜底音色
    rtxt = ref_text if (ref_text or "").strip() else _ref_text
    with _tts_lock:
        r = rt.generate(text=text, prompt_audio_path=ra, prompt_text=rtxt)
    if np is None:
        raise HTTPException(400, "本地 TTS 需要 numpy（本版本为云端版，请改用云端 TTS）")
    audio = r["audio"].float().cpu().squeeze().numpy()
    sr = int(r["sample_rate"])
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return len(audio) / sr, sr

def _load_segments(pdir: Path) -> dict:
    f = pdir / "segments.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"segments": []}

def _load_voice_ref(pdir: Path) -> dict:
    """项目自定义音色 (tts\voice_ref.* + voice.json); 文件缺失视为未启用"""
    f = pdir / "voice.json"
    if f.exists():
        try:
            v = json.loads(f.read_text(encoding="utf-8"))
            if v.get("ref_audio") and Path(v["ref_audio"]).exists() and (v.get("ref_text") or "").strip():
                return v
        except Exception:
            pass
    return {}

def _save_segments(pdir: Path, data: dict):
    (pdir / "segments.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

def _rd_if(d: Path, f: str) -> str:
    return (d / f).read_text(encoding="utf-8") if (d / f).exists() else ""

# ---------------- 数字人 (RunningHub ai-app API) ----------------
_rh_upload_cache: dict = {}

def _rh_upload(fp: str) -> str:
    p = Path(fp)
    if not p.exists():
        raise HTTPException(400, f"文件不存在: {fp}")
    k = str(p) + "|" + str(int(p.stat().st_mtime))
    if k in _rh_upload_cache:
        return _rh_upload_cache[k]
    av = CONF.get("avatar", {})
    key = (av.get("api_key") or "").strip()
    if not key:
        raise HTTPException(400, "尚未配置 RunningHub API Key（⚙ 设置）")
    r = httpx.post(av["base"].rstrip("/") + "/openapi/v2/media/upload/binary",
                   headers={"Authorization": "Bearer " + key},
                   files={"file": (p.name, open(p, "rb"))}, timeout=300)
    j = r.json()
    if j.get("code") != 0:
        raise HTTPException(400, "上传失败: " + str(j.get("msg") or j)[:200])
    fn = j["data"]["fileName"]
    _rh_upload_cache[k] = fn
    return fn

def _rh_create(img_fn: str, aud_fn: str) -> str:
    if not str(CONF["avatar"].get("workflow_id") or "").strip():
        raise HTTPException(400, "未配置数字人应用 ID（config.json → avatar.workflow_id，或联系作者获取）")

    """v2 路径式创建: webappId 在 URL, Bearer 认证, body 只有 nodeInfoList/instanceType。"""
    av = CONF["avatar"]
    url = f"{av['base'].rstrip('/')}/openapi/v2/run/ai-app/{av['workflow_id']}"
    r = httpx.post(url,
                   headers={"Authorization": "Bearer " + (av.get("api_key") or "").strip()},
                   json={"nodeInfoList": [
                             {"nodeId": str(av.get("node_image", "327")), "fieldName": "image", "fieldValue": img_fn},
                             {"nodeId": str(av.get("node_audio", "393")), "fieldName": "audio", "fieldValue": aud_fn}],
                         "instanceType": av.get("instance_type", "plus")},   # 本工作流必须48G显存
                   timeout=60)
    j = r.json()
    tid = j.get("taskId")
    if not tid:
        _e = str(j.get("errorMessage") or j.get("msg") or j)[:200]
        if "Invalid URL" in _e or "链接无效" in _e:
            raise HTTPException(400, "创建任务失败: 数字人应用 ID 无效（当前: "
                                + str(CONF["avatar"].get("workflow_id") or "(空)") + "）— 请在 config.json 的 avatar.workflow_id 填入正确的应用 ID")
        raise HTTPException(400, "创建任务失败: " + _e)
    return str(tid)

def _rh_status(tid: str) -> tuple[str, list, str]:
    """v2 查询: POST /openapi/v2/query → 顶层 {status, results[], errorMessage}。"""
    av = CONF["avatar"]
    r = httpx.post(av["base"].rstrip("/") + "/openapi/v2/query",
                   headers={"Authorization": "Bearer " + (av.get("api_key") or "").strip()},
                   json={"taskId": tid}, timeout=60)
    j = r.json()
    st = j.get("status", "")
    results = j.get("results") or []
    err = j.get("errorMessage") or json.dumps(j.get("failedReason") or {}, ensure_ascii=False)
    return st, results, str(err)[:200]

# ---------------- API: 基础/设置 ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "tts_loaded": _rt is not None}

@app.get("/api/settings")
def api_settings():
    llm = CONF["llm"]
    key = llm.get("api_key") or ""
    av = CONF.get("avatar", {})
    akey = av.get("api_key") or ""
    return {"base_url": llm.get("base_url", ""), "model": llm.get("model", ""),
            "thinking": llm.get("thinking", "off"),
            "has_key": bool(key),
            "key_hint": ("••••" + key[-4:] if len(key) > 8 else ("已配置" if key else "")),
            "projects_dir": str(_projects_dir()),
            "projects_dir_custom": bool((CONF.get("projects_dir") or "").strip()),
            "avatar_image": av.get("default_image", ""),
            "avatar_instance": av.get("instance_type", "plus"),
            "avatar_has_key": bool(akey),
            "avatar_key_hint": ("••••" + akey[-4:] if len(akey) > 8 else ""),
            "max_parallel": _avatar_cap()}

@app.post("/api/settings")
def api_settings_save(body: dict):
    llm = CONF["llm"]
    if body.get("base_url"):
        llm["base_url"] = body["base_url"].strip()
    if body.get("model"):
        llm["model"] = body["model"].strip()
    if body.get("thinking") in ("off", "on", "low"):
        llm["thinking"] = body["thinking"]
    k = (body.get("api_key") or "").strip()
    if k:                                # 留空=保留现有
        llm["api_key"] = k
    if "projects_dir" in body:
        v = (body.get("projects_dir") or "").strip().strip('"')
        CONF["projects_dir"] = v          # 空 = 恢复默认(软件目录\projects)
        if v:
            try:
                Path(v).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise HTTPException(400, f"目录不可用: {e}")
    av = CONF.setdefault("avatar", {})
    if "avatar_image" in body:
        av["default_image"] = _clean_path(body.get("avatar_image") or "")
    ak = (body.get("avatar_key") or "").strip()
    if ak:
        av["api_key"] = ak
    if "max_parallel" in body:
        try:
            av["max_parallel"] = max(1, min(4, int(body.get("max_parallel") or 1)))
        except Exception:
            pass
    _save_conf()
    return api_settings()

@app.post("/api/settings/test")
def api_settings_test():
    try:
        out = llm("你是回声机。", "只回复两个字:正常", 0.1)
        return {"ok": True, "reply": out[:50]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}

@app.get("/api/gate/status")
def api_gate_status():
    """流程门禁彩蛋数据: LLM Key / RunningHub Key 是否已配置(项目状态由前端本地判断)。"""
    return {"llm_key": bool((CONF.get("llm", {}).get("api_key") or "").strip()),
            "rh_key": bool((CONF.get("avatar", {}).get("api_key") or "").strip())}

# ---------------- API: 技能 ----------------
@app.get("/api/skills/{stage}")
def api_skills(stage: str):
    if stage not in STAGES:
        raise HTTPException(404, "stage 不合法")
    return {"skills": _skill_list(stage), "defaults": _default_skills(stage)}

@app.post("/api/skill/collect")
def api_collect(body: dict):
    skills = body.get("skills") or _default_skills("collect")
    direction = (body.get("direction") or "").strip()
    parts, trails = [], {}
    for name in skills:
        sys_p = _skill_text("collect", name)
        if name == "aihot":
            user = ("请产出本期口播灵感素材。\n\n**方向提示**：" + (direction or "(未指定，全域热点)")
                    + "\n\n以下是 AIHOT 公开API 预取的实时证据（可用 aihot 工具追加查询）：\n\n" + aihot_evidence())
        else:
            user = ("请产出本期口播灵感素材。\n\n**方向提示**：" + (direction or "(未指定，全域)")
                    + "\n\n要求真实：先用 web_search 检索本周动态（官方博客/科技媒体），重要结论用 web_fetch 读原文。")
        out, trail = run_agent(sys_p, user, CONF["llm"].get("temperature_collect", 0.8),
                               min_tools=1 if name == "aihot" else 2)
        parts.append({"skill": name, "output": out}); trails[name] = trail
    merged = "\n\n---\n\n".join(f"## 技能 {p['skill']} 的产出\n\n{p['output']}" for p in parts)
    return {"output": merged, "parts": parts, "skills": skills, "trails": trails}

@app.post("/api/skill/research")
def api_research(body: dict):
    topic = (body.get("topic") or "").strip()
    m1 = (body.get("material1") or "").strip()
    skills = body.get("skills") or _default_skills("research")
    if not topic:
        raise HTTPException(400, "topic 必填")
    items, backend = web_search(topic)
    parts, trails = [], {}
    for name in skills:
        sys_p = _skill_text("research", name)
        user = (f"选题：{topic}\n\n=== 初步灵感资料（用户提供，已确认） ===\n{m1 or '(无)'}"
                f"\n\n=== 起始搜索证据（后端 {backend}，可用 web_search/web_fetch 继续深挖） ===\n{_search_block(items)}")
        out, trail = run_agent(sys_p, user, CONF["llm"].get("temperature_research", 0.4), min_tools=1)
        parts.append({"skill": name, "output": out}); trails[name] = trail
    merged = "\n\n---\n\n".join(f"## 技能 {p['skill']} 的产出\n\n{p['output']}" for p in parts)
    return {"output": merged, "parts": parts, "skills": skills, "sources_found": len(items), "backend": backend, "trails": trails}

@app.post("/api/skill/unify")
def api_unify(body: dict):
    topic = (body.get("topic") or "").strip()
    m1 = (body.get("material1") or "").strip()
    m2 = (body.get("material2") or "").strip()
    skill = (body.get("skill") or (_default_skills("unify") or ["khazix-writer"])[0])
    if not topic:
        raise HTTPException(400, "topic 必填")
    sys_p = _skill_text("unify", skill)
    user = (f"请根据以下选题和资料，给我生成文案\n\n"
            f"选题：{topic}\n\n=== 初步灵感资料 ===\n{m1 or '(无)'}\n\n=== 深度调研资料 ===\n{m2 or '(无)'}")
    script = llm(sys_p, user, CONF["llm"].get("temperature_unify", 0.7))
    script = re.sub(r"^#.*$", "", script, flags=re.M).strip()
    return {"script": script, "skill": skill}

# ---------------- API: 项目 ----------------
@app.post("/api/project/create")
def api_project_create(body: dict):
    name = _safe_slug(body.get("name") or "")
    if not name:
        raise HTTPException(400, "项目名必填")
    base = _projects_dir()
    base.mkdir(parents=True, exist_ok=True)
    d = base / (time.strftime("%Y%m%d-%H%M%S") + "_" + name)
    (d / "tts").mkdir(parents=True, exist_ok=True)
    return {"project": d.name, "dir": str(d)}

@app.post("/api/project/save")
def api_project_save(body: dict):
    topic = (body.get("topic") or "").strip()
    pname = (body.get("project") or "").strip()
    if pname:
        d = _find_project(pname)
    elif topic:
        d = _find_or_create(topic)
    else:
        raise HTTPException(400, "project 或 topic 必填其一")
    _write_materials(d, body)
    return {"project": d.name, "dir": str(d)}

@app.get("/api/projects")
def api_projects():
    base = _projects_dir()
    if not base.exists():
        return {"projects": []}
    out = []
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        # 项目判定: 有 tts\ 工作目录(新建即建) 或已有 选题.txt; 新项目尚无选题也可见
        if d.is_dir() and ((d / "tts").exists() or (d / "选题.txt").exists()):
            seg = _load_segments(d)
            kept = sum(1 for s in seg["segments"] if s["status"] == "kept")
            topic = ""
            tf = d / "选题.txt"
            if tf.exists():
                topic = tf.read_text(encoding="utf-8").strip()
            out.append({"name": d.name, "topic": topic,
                        "segments": len(seg["segments"]), "kept": kept})
    return {"projects": out}

@app.post("/api/project/delete")
def api_project_delete(body: dict):
    """删除历史项目: 整目录移除(文案/音频/视频/成片), _find_project 自带防穿越"""
    name = (body.get("project") or "").strip()
    if not name:
        raise HTTPException(400, "缺少项目名")
    d = _find_project(name)
    try:
        shutil.rmtree(d)
    except OSError as e:
        raise HTTPException(500, f"删除失败(可能有文件被占用): {e}")
    return {"ok": True, "deleted": name}

# ---------------- 自定义设计模板: 风格+提示词+全家族菜单的命名快照 ----------------
_TPL_FILE = ROOT / "templates.json"

def _load_templates() -> list:
    try:
        return json.loads(_TPL_FILE.read_text(encoding="utf-8")).get("templates", [])
    except Exception:
        return []

def _save_templates(tpls: list):
    _TPL_FILE.write_text(json.dumps({"templates": tpls}, ensure_ascii=False, indent=1), encoding="utf-8")

@app.get("/api/hf/templates")
def api_hf_templates():
    return {"templates": _load_templates()}

@app.post("/api/hf/templates/save")
def api_hf_templates_save(body: dict):
    name = (body.get("name") or "").strip()[:24]
    if not name:
        raise HTTPException(400, "模板名不能为空")
    tpls = [t for t in _load_templates() if t["name"] != name]
    tpls.append({"name": name,
                 "style": body.get("style") or "ai",
                 "prompt": (body.get("prompt") or "")[:800],
                 "menu": _normalize_menu(body.get("menu") or {}),
                 "top_mark": bool(body.get("top_mark")),
                 "ts": time.strftime("%Y-%m-%d %H:%M")})
    _save_templates(tpls)
    return {"ok": True, "count": len(tpls)}

@app.post("/api/hf/templates/delete")
def api_hf_templates_delete(body: dict):
    name = (body.get("name") or "").strip()
    _save_templates([t for t in _load_templates() if t["name"] != name])
    return {"ok": True}

@app.get("/api/project/{name}")
def api_project(name: str):
    d = _find_project(name)
    seg = _load_segments(d)
    direction = _rd_if(d, "方向.txt").strip()
    for s in seg["segments"]:          # 音频文件真实存在与否(分段时就有wav字段名, 但文件未必已合成)
        s["audio_ok"] = bool(s.get("wav")) and (d / "tts" / s["wav"]).exists()
    return {"name": name, "topic": _rd_if(d, "选题.txt").strip(), "direction": direction,
            "material1": _rd_if(d, "灵感资料.md").strip(), "material2": _rd_if(d, "调研资料.md").strip(),
            "script": _rd_if(d, "文案.md").strip(), "segments": seg["segments"],
            "images": _load_images(d), "custom": _load_custom(d)}

# ---------------- API: 分段合成 ----------------
@app.post("/api/segments/split")
def api_segments_split(body: dict):
    d = _find_project(body.get("project") or "")
    script = (body.get("script") or _rd_if(d, "文案.md") or "").strip()
    script = re.sub(r"^#.*$", "", script, flags=re.M).strip()
    if not script:
        raise HTTPException(400, "没有文案可分段")
    mn = int(CONF["tts"]["segment"]["min"]); mx = int(CONF["tts"]["segment"]["max"])
    segs = split_segments(script, mn, mx)
    data = {"model": os.path.basename(CONF["tts"]["model_path"]),
            "segments": [{"id": f"seg{i+1:02d}", "text": s, "wav": f"seg{i+1:02d}.wav", "status": "pending"}
                         for i, s in enumerate(segs)]}
    _save_segments(d, data)
    return {"segments": data["segments"], "count": len(segs)}

@app.post("/api/segments/synth")
def api_segments_synth(body: dict):
    d = _find_project(body.get("project") or "")
    only = body.get("id")
    data = _load_segments(d)
    if not data["segments"]:
        raise HTTPException(400, "先分段")
    if not only:
        trash = d / "tts" / "_trash"
        trash.mkdir(exist_ok=True)
        for s in data["segments"]:
            if s["status"] == "deleted":
                continue
            old = d / "tts" / s["wav"]
            if old.exists():
                os.replace(old, trash / s["wav"])
            s["status"] = "pending"
    vr = _load_voice_ref(d)   # 项目自定义音色; 空 = 内置兜底
    out = []
    for s in data["segments"]:
        if only and s["id"] != only:
            continue
        if s["status"] == "deleted":
            continue
        dur, sr = _tts_one(s["text"], d / "tts" / s["wav"],
                           vr.get("ref_audio"), vr.get("ref_text"))
        s["dur_sec"] = round(dur, 2); s["sr"] = sr
        s["gen_at"] = int(time.time())   # 合成版本号: 前端音频URL带上它, 强制绕过浏览器缓存
        s["status"] = "kept"
        out.append(s)
        _save_segments(d, data)
    _save_segments(d, data)
    return {"segments": data["segments"], "synthed": [s["id"] for s in out]}

# ---------------- 云端 TTS (RunningHub ai-app; API Key/域名/上传/查询 全部复用数字人设施) ----------------
_rh_tts_job = {"running": False, "done": False, "error": "", "total": 0, "finished": 0, "cur": "", "project": "", "done_ids": []}

def _tts_rh_cfg() -> dict:
    c = CONF.setdefault("tts_rh", {})
    c.setdefault("workflow_id", "2098056520643035137")
    c.setdefault("node_text", "66")          # 台词文本
    c.setdefault("node_ref_audio", "13")     # 参考音频(可选, 自定义音色)
    c.setdefault("node_ref_text", "67")      # 参考音频对应文本
    c.setdefault("ref_audio", "")            # 用户上传到 RH 的自定义参考音频 fileName
    c.setdefault("ref_text", "")
    c.setdefault("instance_type", "default")
    # 兜底音色: 用户未上传自定义音色时自动传这套(应用端工作流默认音色不可控, 实测是梁文锋)
    c.setdefault("fallback_ref_audio", r"D:\speech-to-speech\voices\wenr-liangmi\ref_audio.wav")
    c.setdefault("fallback_ref_text_file", r"D:\speech-to-speech\voices\wenr-liangmi\ref_text.txt")
    return c

def _wav_probe(p: Path) -> tuple:
    with wave.open(str(p), "rb") as f:
        return round(f.getnframes() / f.getframerate(), 2), f.getframerate()

def _rh_tts_one(text: str, out: Path) -> tuple:
    """单段云端合成, 段级重试(网络偶发 SSL EOF; 查询中断线容忍, 连续失败才放弃)"""
    last = None
    for attempt in range(3):
        try:
            return _rh_tts_one_once(text, out)
        except (HTTPException, httpx.HTTPError) as e:
            last = e
            if isinstance(e, HTTPException) and e.status_code == 400:
                raise        # 业务错误(参数/配额)不重试
            time.sleep(8 * (attempt + 1))
    raise last

def _rh_tts_one_once(text: str, out: Path) -> tuple:
    """单段云端合成: 提交(66=台词+兜底/自定义音色13/67) → 轮询 → 下载(链接24h内立即落盘) → 非 wav 转 48k 单声道"""
    c = _tts_rh_cfg()
    av = CONF["avatar"]
    key = (av.get("api_key") or "").strip()
    if not key:
        raise HTTPException(400, "尚未配置 RunningHub API Key（⚙ 设置 · 与数字人共用同一个 Key）")
    nodes = [{"nodeId": c["node_text"], "fieldName": "inStr", "fieldValue": text}]
    ref_fn = c.get("ref_audio")
    ref_txt = (c.get("ref_text") or "").strip()
    if not ref_fn:
        # 兜底: 用户没传自定义音色 → 自动用内置湖南女生参考音频(_rh_upload 有缓存, 只上传一次)
        fb = (c.get("fallback_ref_audio") or "").strip()
        if fb and Path(fb).exists():
            ref_fn = _rh_upload(fb)
            if not ref_txt:
                ftf = (c.get("fallback_ref_text_file") or "").strip()
                if ftf and Path(ftf).exists():
                    ref_txt = Path(ftf).read_text(encoding="utf-8").strip()
    if ref_fn:
        nodes.append({"nodeId": c["node_ref_audio"], "fieldName": "audio", "fieldValue": ref_fn})
        if ref_txt:
            nodes.append({"nodeId": c["node_ref_text"], "fieldName": "inStr", "fieldValue": ref_txt})
    r = httpx.post(f"{av['base'].rstrip('/')}/openapi/v2/run/ai-app/{c['workflow_id']}",
                   headers={"Authorization": "Bearer " + key},
                   json={"nodeInfoList": nodes, "instanceType": c.get("instance_type", "default")}, timeout=60)
    j = r.json()
    tid = j.get("taskId")
    if not tid:
        raise HTTPException(400, "云端提交失败: " + str(j.get("errorMessage") or j.get("msg") or j)[:200])
    deadline = time.time() + 900
    url = ""
    fail_streak = 0
    while time.time() < deadline:
        time.sleep(5)
        try:
            st, results, err = _rh_status(tid)
            fail_streak = 0
        except Exception:          # 查询断线容忍: 网络抖动不该终止整批
            fail_streak += 1
            if fail_streak >= 6:   # 连续 30 秒查询失败才放弃
                raise HTTPException(502, "查询任务状态连续失败(网络不稳定)")
            continue
        if st == "SUCCESS":
            url = next((x["url"] for x in results
                        if x.get("url") and (x.get("outputType") or "").lower() in ("wav", "mp3", "flac", "m4a")), "")
            if not url:
                raise HTTPException(400, "云端任务成功但无音频输出: " + str(results)[:200])
            break
        if st == "FAILED":
            raise HTTPException(400, "云端合成失败: " + err)
    else:
        raise HTTPException(504, "云端合成超时(单段15分钟)")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".dl")
    for datt in range(3):          # 下载同样容忍网络抖动
        try:
            with httpx.stream("GET", url, timeout=300, follow_redirects=True) as resp:
                resp.raise_for_status()
                tmp.write_bytes(resp.read())
            break
        except httpx.HTTPError:
            if datt == 2:
                raise
            time.sleep(5 * (datt + 1))
    ext = url.rsplit("?", 1)[0].rsplit(".", 1)[-1].lower()
    if ext == "wav":
        os.replace(tmp, out)
    else:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-ar", "48000", "-ac", "1", str(out)],
                       check=True, timeout=300)
        tmp.unlink(missing_ok=True)
    return _wav_probe(out)

def _rh_tts_worker(d: Path, only: str):
    j = _rh_tts_job
    try:
        data = _load_segments(d)
        if not only:
            trash = d / "tts" / "_trash"
            trash.mkdir(exist_ok=True)
            for s in data["segments"]:
                if s["status"] == "deleted":
                    continue
                old = d / "tts" / s["wav"]
                if old.exists():
                    os.replace(old, trash / s["wav"])
                s["status"] = "pending"
            _save_segments(d, data)
        todo = [s for s in data["segments"]
                if s["status"] != "deleted" and (not only or s["id"] == only)]
        j["total"] = len(todo)
        j["finished"] = 0
        failed = []
        for s in todo:
            j["cur"] = s["id"] + " " + s["text"][:18]
            try:
                dur, sr = _rh_tts_one(s["text"], d / "tts" / s["wav"])
            except Exception as e:
                # 单段失败(重试3次后)不拖垮整批: 记录后继续后面的段
                failed.append(s["id"] + ": " + str(e)[:90])
                continue
            s["dur_sec"] = round(dur, 2)
            s["sr"] = sr
            s["gen_at"] = int(time.time())
            s["status"] = "kept"
            _save_segments(d, data)
            j["finished"] += 1
            j["done_ids"].append(s["id"])   # 逐段完成反馈: 前端据此给卡片绿脉冲
        if failed:
            j["error"] = ("部分段失败(其余已合成, 失败段可单独重试): " + "; ".join(failed))[:300]
        j["done"] = True
    except Exception as e:
        j["error"] = str(e)[:300]
    finally:
        j["running"] = False
        j["cur"] = ""

@app.post("/api/tts/rh/synth")
def api_tts_rh_synth(body: dict):
    if _rh_tts_job["running"]:
        raise HTTPException(409, "云端合成已在进行中")
    d = _find_project(body.get("project") or "")
    if not _load_segments(d)["segments"]:
        raise HTTPException(400, "先分段")
    _rh_tts_job.update({"running": True, "done": False, "error": "",
                        "total": 0, "finished": 0, "cur": "", "project": d.name, "done_ids": []})
    threading.Thread(target=_rh_tts_worker, args=(d, body.get("id")), daemon=True).start()
    return {"started": True}

@app.get("/api/tts/rh/status")
def api_tts_rh_status():
    j = dict(_rh_tts_job)
    if j.get("done") and not j["running"] and j.get("project"):
        j["segments"] = _load_segments(_find_project(j["project"]))["segments"]
    return j

# ---------------- TTS 能力探测: 本地同款 TTS 可用 → 自动走本地; 不可用 → 自动转云端 ----------------
_tts_probe: dict = {"local": None, "error": ""}

def _tts_local_ok() -> tuple:
    """探测本地同款 TTS(dots soar): 模型目录+源码目录存在即视为可用(不真加载模型, 免 40s 冷启动)"""
    if _tts_probe["local"] is None:
        try:
            import importlib.util as _ilu
            # 打包版必须判不可用: 引擎模块可导入 + 模型路径非空存在 双条件
            if _ilu.find_spec("dots_tts") is None:
                raise RuntimeError("未安装本地 TTS 引擎(dots_tts)")
            t = CONF["tts"]
            if not str(t.get("model_path") or "").strip():
                raise RuntimeError("未配置本地 TTS 模型路径")
            mp = Path(t["model_path"])
            src = Path(t.get("src_dir") or r"E:\deepseek-works\dots-tts\src")
            ok = mp.exists() and (src / "dots_tts" / "runtime.py").exists()
            err = "" if ok else f"本地模型目录不存在: {mp}"
        except Exception as e:
            ok, err = False, "未配置本地 TTS: " + str(e)[:120]
        _tts_probe.update({"local": ok, "error": err})
    return _tts_probe["local"], _tts_probe["error"]

@app.get("/api/tts/capability")
def api_tts_capability():
    ok, err = _tts_local_ok()
    return {"local": ok, "local_error": err}

@app.get("/api/tts/rh/config")
def api_tts_rh_config():
    c = _tts_rh_cfg()
    av = CONF.get("avatar", {})
    ak = (av.get("api_key") or "").strip()
    return {"has_key": bool(ak),
            "key_hint": ("••••" + ak[-4:] if len(ak) > 8 else ("已配置" if ak else "")),
            "workflow_id": c["workflow_id"], "ref_audio": c.get("ref_audio", ""),
            "has_ref": bool(c.get("ref_audio")), "ref_text": c.get("ref_text", ""),
            "instance_type": c.get("instance_type", "default")}

@app.post("/api/tts/rh/config")
def api_tts_rh_config_save(body: dict):
    c = _tts_rh_cfg()
    if body.get("workflow_id"):
        c["workflow_id"] = body["workflow_id"].strip()
    if "ref_text" in body:
        c["ref_text"] = (body.get("ref_text") or "")[:600]
    if body.get("instance_type"):
        c["instance_type"] = body["instance_type"]
    if body.get("ref_audio_path"):
        c["ref_audio"] = _rh_upload(_clean_path(body["ref_audio_path"]))
    if body.get("clear_ref"):
        c["ref_audio"] = ""
    _save_conf()
    return api_tts_rh_config()

@app.post("/api/segments/status")
def api_segments_status(body: dict):
    d = _find_project(body.get("project") or "")
    seg_id = body.get("id") or ""
    action = body.get("action")
    data = _load_segments(d)
    hit = next((s for s in data["segments"] if s["id"] == seg_id), None)
    if not hit:
        raise HTTPException(404, "找不到分段 " + seg_id)
    if action == "delete":
        w = d / "tts" / hit["wav"]
        if w.exists():
            trash = d / "tts" / "_trash"; trash.mkdir(exist_ok=True)
            os.replace(w, trash / hit["wav"])
        hit["status"] = "deleted"
    elif action == "restore":
        # 误删后悔药: 音频从回收目录还原; 文件已不在则回 pending(文本仍在, 可重合成)
        trash = d / "tts" / "_trash"
        w_old = trash / hit["wav"]
        w_new = d / "tts" / hit["wav"]
        if w_old.exists():
            w_new.parent.mkdir(exist_ok=True)
            os.replace(w_old, w_new)
            hit["status"] = "kept"
        elif w_new.exists():
            hit["status"] = "kept"
        else:
            hit["status"] = "pending"
    elif action in ("keep", "pending"):
        hit["status"] = "kept" if action == "keep" else "pending"
    _save_segments(d, data)
    return {"segments": data["segments"]}

@app.post("/api/tts/ref")
def api_tts_ref(body: dict):
    """项目自定义参考音色: 设置(音频路径+对应文本) / 移除; 不设置则用内置兜底音色"""
    d = _find_project(body.get("project") or "")
    f = d / "voice.json"
    if (body.get("action") or "") == "remove":
        if f.exists():
            f.unlink()
        return {"voice": None}
    p = _clean_path(body.get("path") or "")
    ref_text = (body.get("ref_text") or "").strip()
    if not ref_text:
        raise HTTPException(400, "请填写参考音频对应的文本（它说了什么）")
    pp = Path(p)
    if not p or not pp.exists() or pp.suffix.lower() not in (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"):
        raise HTTPException(400, "音频文件不存在或格式不支持: " + p)
    try:
        dur = _ffprobe_dur(pp)
    except Exception:
        raise HTTPException(400, "无法读取该音频文件")
    if dur < 3 or dur > 60:
        raise HTTPException(400, f"音频时长 {dur:.1f}s 不合适 — 建议上传 10~15 秒干净、无噪声的纯人声")
    dest = d / "tts" / ("voice_ref" + pp.suffix.lower())
    shutil.copy(pp, dest)
    v = {"ref_audio": str(dest), "ref_text": ref_text, "dur": round(dur, 2), "name": pp.name}
    f.write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"voice": v}

@app.get("/api/tts/ref")
def api_tts_ref_get(project: str = ""):
    d = _find_project(project)
    return {"voice": _load_voice_ref(d) or None}

# ---------------- 内置静态资源 (彩蛋: Live2D 黑猫 hijiki 等) ----------------
_ASSET_MT = {".js": "application/javascript", ".json": "application/json",
             ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
             ".moc": "application/octet-stream", ".mtn": "application/octet-stream",
             ".css": "text/css", ".html": "text/html; charset=utf-8"}

@app.get("/assets/{path:path}")
def builtin_assets(path: str):
    base = (RES / "assets").resolve()
    f = (base / path).resolve()
    if not str(f).startswith(str(base)) or not f.is_file():
        raise HTTPException(404, "资源不存在")
    return FileResponse(f, media_type=_ASSET_MT.get(f.suffix.lower(), "application/octet-stream"))

@app.get("/audio/{name}/{fname}")
def audio(name: str, fname: str):
    d = _find_project(name)
    if "/" in fname or "\\" in fname or ".." in fname:
        raise HTTPException(400, "bad file")
    f = d / "tts" / fname
    if not f.exists():
        raise HTTPException(404, "音频不存在")
    # no-cache: 重合成后同 URL 必须回源验证(配合前端 ?v=gen_at 版本号), 防浏览器回放旧音频
    return FileResponse(f, media_type="audio/wav", headers={"Cache-Control": "no-cache"})

# ---------------- API: 数字人视频 (RunningHub) ----------------
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
# 内置兜底形象图: 项目无图库/无绑定/无 config 默认时最后回落
def _default_avatar_image() -> str:
    """兜底形象图: 优先随包内置资源(任何机器都能用), 再回退开发机路径"""
    for c in (RES / "assets" / "avatar-default.jpg", ROOT / "assets" / "avatar-default.jpg",
              Path(r"D:\素材图\01数字人\file1788180868069_441356.jpg")):
        if c.exists():
            return str(c)
    return ""

DEFAULT_AVATAR_IMAGE = _default_avatar_image()

def _clean_path(p: str) -> str:
    """用户粘贴的路径容错: 去首尾空白与各类引号, 正斜杠归一为反斜杠"""
    p = (p or "").strip().strip("\"'“”‘’`").strip()
    return p.replace("/", "\\") if p else ""

_dlg_lock = threading.Lock()

@app.post("/api/dialog/pick-image")
def api_dialog_pick(kind: str = "image"):
    """弹出系统原生文件选择窗口(本机应用同机运行, 对话框直接出现在用户桌面)"""
    if not _dlg_lock.acquire(blocking=False):
        raise HTTPException(409, "已有一个选择窗口未关闭")
    try:
        import tkinter as tk
        from tkinter import filedialog
        if kind == "audio":
            ftypes = [("音频", "*.mp3 *.wav *.m4a *.flac *.ogg *.aac"), ("所有文件", "*.*")]
        elif kind == "script":
            ftypes = [("口播稿", "*.txt *.md *.markdown"), ("所有文件", "*.*")]
        else:
            ftypes = [("图片", "*.jpg *.jpeg *.png *.webp *.bmp"), ("所有文件", "*.*")]
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askopenfilename(
                title="选择数字人形象图" if kind not in ("audio", "script") else
                      ("选择 BGM 音乐" if kind == "audio" else "选择自定义口播稿"),
                filetypes=ftypes)
        finally:
            root.destroy()
        return {"path": _clean_path(path)}
    finally:
        _dlg_lock.release()

def _load_images(d: Path) -> list[str]:
    f = d / "images.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("images", [])
        except Exception:
            return []
    return []

def _load_custom(d: Path) -> list[dict]:
    f = d / "custom_items.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            return []
    return []

def _save_custom(d: Path, items: list[dict]):
    (d / "custom_items.json").write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")

def _item_file(d: Path, hit: dict) -> Path:
    """段音频文件: TTS段在 tts/ 下, 自制音频在 audio/ 下"""
    w = hit.get("wav") or ""
    return d / w if w.startswith("audio/") else d / "tts" / w

@app.post("/api/avatar/custom-upload")
async def api_custom_upload(request: Request):
    """上传自制音频(免TTS): 原始字节流, ?project=&name=xxx.mp3"""
    d = _find_project(request.query_params.get("project") or "")
    raw = await request.body()
    name = _clean_path(request.query_params.get("name") or "audio")
    ext = Path(name).suffix.lower()
    if ext not in (".wav", ".mp3", ".m4a", ".flac", ".ogg"):
        raise HTTPException(400, "不支持的音频格式: " + ext)
    if len(raw) < 1000:
        raise HTTPException(400, "音频文件太小或为空")
    if len(raw) > 80 * 1024 * 1024:
        raise HTTPException(400, "音频超过 80MB")
    cid = "cus" + time.strftime("%H%M%S")
    adir = d / "audio"; adir.mkdir(exist_ok=True)
    (adir / (cid + ext)).write_bytes(raw)
    items = _load_custom(d)
    item = {"id": cid, "name": name, "wav": f"audio/{cid}{ext}",
            "img": "", "video_status": "none", "video": "", "video_task": ""}
    items.insert(0, item)
    _save_custom(d, items)
    return {"item": item}

# ---------------- 工作台文本直生成 TTS (双引擎; 复用④同一套云端/本地逻辑) ----------------
_gen_job: dict = {"running": False, "done": False, "error": "", "total": 0, "finished": 0, "cur": "", "project": "", "items": []}

@app.post("/api/avatar/tts-gen")
def api_tts_gen(body: dict):
    """上传分段文本逐行生成 TTS 音频并加入工作台素材; engine=rh(云端, 每行建议≤40字) | local(本地 soar)"""
    if _gen_job["running"]:
        raise HTTPException(409, "文本生成已在进行中")
    d = _find_project(body.get("project") or "")
    lines = [l.strip() for l in (body.get("lines") or []) if (l or "").strip()]
    if not lines:
        raise HTTPException(400, "先填写要合成的文本（每行一段）")
    engine = body.get("engine") or "local"
    if engine not in ("rh", "local"):
        raise HTTPException(400, "engine 须为 rh 或 local")
    if engine == "rh" and not (CONF.get("avatar", {}).get("api_key") or "").strip():
        raise HTTPException(400, "尚未配置 RunningHub API Key（⚙ 设置 · 与数字人共用）")
    _gen_job.update({"running": True, "done": False, "error": "", "total": len(lines),
                     "finished": 0, "cur": "", "project": d.name, "items": []})
    threading.Thread(target=_tts_gen_worker, args=(d, lines, engine), daemon=True).start()
    return {"started": True, "total": len(lines)}

def _tts_gen_worker(d: Path, lines: list, engine: str):
    j = _gen_job
    try:
        items = _load_custom(d)
        (d / "audio").mkdir(exist_ok=True)
        for i, line in enumerate(lines):
            j["cur"] = f"第{i + 1}行 {line[:16]}"
            cid = "gen" + time.strftime("%H%M%S") + f"{i:02d}"
            wav = d / "audio" / (cid + ".wav")
            if engine == "local":
                vr = _load_voice_ref(d)
                _tts_one(line, wav, vr.get("ref_audio"), vr.get("ref_text"))
            else:
                _rh_tts_one(line, wav)   # 40字上限为音色保持度建议, 超长不拦截
            item = {"id": cid,
                    "name": ("云端TTS" if engine == "rh" else "本地TTS") + "·" + line[:10],
                    "wav": f"audio/{cid}.wav", "img": "", "video_status": "none",
                    "video": "", "video_task": "", "text": line}
            items.insert(0, item)
            _save_custom(d, items)
            j["items"].append(item)
            j["finished"] = i + 1
        j["done"] = True
    except Exception as e:
        j["error"] = str(e)[:300]
    finally:
        j["running"] = False
        j["cur"] = ""

@app.get("/api/avatar/tts-gen/status")
def api_tts_gen_status():
    return dict(_gen_job)

@app.post("/api/avatar/custom-remove")
def api_custom_remove(body: dict):
    d = _find_project(body.get("project") or "")
    cid = body.get("id") or ""
    items = _load_custom(d)
    hit = next((c for c in items if c["id"] == cid), None)
    if not hit:
        raise HTTPException(404, "找不到自定义音频项 " + cid)
    for rel in (hit.get("wav"), ("videos/" + hit["video"]) if hit.get("video") else None):
        if rel:
            f = d / rel.replace("/", "\\")
            if f.exists():
                f.unlink()
    items = [c for c in items if c["id"] != cid]
    _save_custom(d, items)
    return {"ok": True}

@app.post("/api/open-folder")
def api_open_folder(body: dict):
    """在资源管理器中打开对应栏目的存储目录"""
    scope = body.get("scope") or "projects"
    base = _projects_dir()
    if scope == "projects":
        p = base
    else:
        name = body.get("project") or ""
        if not name:
            raise HTTPException(400, "请先新建或打开项目")
        d = _find_project(name)
        p = {"project": d, "tts": d / "tts", "videos": d / "videos", "audio": d / "audio",
             "output": d / "output"}.get(scope, d)
        if not p.exists():
            p = d
    if scope == "projects":
        p.mkdir(parents=True, exist_ok=True)
    os.startfile(str(p))   # Windows 资源管理器
    return {"path": str(p)}

@app.post("/api/avatar/images")
def api_avatar_images(body: dict):
    """项目图片库: add/remove 本地图片路径, 供拖拽绑定到段"""
    d = _find_project(body.get("project") or "")
    action = body.get("action") or "add"
    imgs = _load_images(d)
    p = _clean_path(body.get("path") or "")
    if action == "add":
        pp = Path(p)
        if not p or not pp.exists() or pp.suffix.lower() not in _IMG_EXT:
            raise HTTPException(400, "图片不存在或格式不支持: " + p)
        if p not in imgs:
            imgs.append(p)
    elif action == "remove":
        imgs = [x for x in imgs if x != p]
    imgs = list(dict.fromkeys(_clean_path(x) for x in imgs if _clean_path(x)))   # 归一化去重
    (d / "images.json").write_text(json.dumps({"images": imgs}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"images": imgs}

@app.post("/api/avatar/bind")
def api_avatar_bind(body: dict):
    """把某张形象图绑定到某段 (img 为空 = 解绑, 回落默认形象图)"""
    d = _find_project(body.get("project") or "")
    seg_id = body.get("seg") or ""
    img = (body.get("img") or "").strip()
    data = _load_segments(d)
    hit = next((s for s in data["segments"] if s["id"] == seg_id), None)
    if not hit:
        raise HTTPException(404, "找不到分段 " + seg_id)
    if img and not Path(img).exists():
        raise HTTPException(400, "图片不存在: " + img)
    hit["img"] = _clean_path(img)
    _save_segments(d, data)
    return {"seg": hit}

def _find_item(d: Path, item_id: str):
    """在 TTS 段与自制音频两项存储中查找条目, 返回 (hit, save回调)"""
    seg = _load_segments(d)
    hit = next((s for s in seg["segments"] if s["id"] == item_id), None)
    if hit:
        return hit, (lambda: _save_segments(d, seg))
    items = _load_custom(d)
    hit = next((c for c in items if c["id"] == item_id), None)
    if hit:
        return hit, (lambda: _save_custom(d, items))
    return None, None

def _avatar_cap() -> int:
    """数字人视频并行上限(1-4, 默认 1 = 串行排队)。"""
    try:
        v = int(CONF.get("avatar", {}).get("max_parallel") or 1)
    except Exception:
        v = 1
    return max(1, min(4, v))

def _iter_all_items(d: Path):
    """遍历 TTS 段与自制音频全部条目, 返回 [(item, save回调)]"""
    out = []
    seg = _load_segments(d)
    for s in seg["segments"]:
        out.append((s, lambda: _save_segments(d, seg)))
    cus = _load_custom(d)
    for c in cus:
        out.append((c, lambda: _save_custom(d, cus)))
    return out

def _avatar_running(d: Path, exclude: str = "") -> int:
    n = 0
    for it, _sv in _iter_all_items(d):
        if it.get("id") != exclude and it.get("video_status") == "running":
            n += 1
    return n

def _avatar_submit(d: Path, hit: dict, save, image: str = "") -> dict:
    """提交单段数字人任务到 RunningHub(上传形象图/音频 → 建任务 → 落库 running)。"""
    # 形象图优先级: 条目绑定 > 请求指定 > 项目默认(config) > 内置兜底
    img = _clean_path((hit.get("img") or "").strip() or (image or "").strip()
        or CONF.get("avatar", {}).get("default_image", "").strip())
    if (not img or not Path(img).exists()) and Path(DEFAULT_AVATAR_IMAGE).exists():
        img = DEFAULT_AVATAR_IMAGE
    wav = _item_file(d, hit)
    if not wav.exists():
        raise HTTPException(400, "该段音频尚未合成或不存在")
    if not img or not Path(img).exists():
        raise HTTPException(400, "形象图不存在: " + (img or "(未设置, 请先添加图片或设默认形象图)"))
    img_fn = _rh_upload(img)
    aud_fn = _rh_upload(str(wav))
    tid = _rh_create(img_fn, aud_fn)
    hit["video_task"] = tid
    hit["video_status"] = "running"
    hit["video_error"] = ""
    save()
    return {"seg": hit}

def _avatar_pick_queued(d: Path):
    for it, save_cb in _iter_all_items(d):
        if it.get("video_status") == "queued":
            return it, save_cb
    return None, None

_promote_lock = threading.Lock()

def _avatar_promote_async(d: Path):
    threading.Thread(target=_avatar_promote_worker, args=(d,), daemon=True).start()

def _avatar_promote_worker(d: Path):
    """后台并发补位: queued 段按顺序自动续交。
    实测(2026-09-14) RunningHub 同账户不允许并行(第二个提交返回 421 'API 并发数已达上线'),
    且前任务刚完成时平台额度释放有延迟 → 补位提交撞 421 须等待重试, 而非误标失败。"""
    if not _promote_lock.acquire(blocking=False):
        return                      # 已有补位线程在跑
    try:
        while _avatar_running(d) < _avatar_cap():
            hit, save_cb = _avatar_pick_queued(d)
            if not hit:
                return
            for _attempt in range(6):            # 单段最多重试 6 次(约 2 分钟)
                try:
                    _avatar_submit(d, hit, save_cb, "")
                    break                        # 提交成功 → 回 while 取下一段
                except HTTPException as e:
                    msg = str(getattr(e, "detail", None) or e)
                    if getattr(e, "status_code", 0) == 421 or "queue limit" in msg or "并发数已达" in msg:
                        time.sleep(20)           # 平台并发额度未释放, 稍后重试(保留 queued)
                        continue
                    hit["video_status"] = "none"
                    hit["video_error"] = ("排队提交失败: " + msg)[:200]
                    save_cb()
                    break                        # 永久性错误 → 标失败, 取下一段
                except Exception as e:
                    hit["video_status"] = "none"
                    hit["video_error"] = ("排队提交失败: " + f"{type(e).__name__}: {e}")[:200]
                    save_cb()
                    break
            else:
                return                           # 重试耗尽 → 保留 queued, 下次轮询再触发
    finally:
        _promote_lock.release()

@app.post("/api/avatar/synth")
def api_avatar_synth(body: dict):
    d = _find_project(body.get("project") or "")
    seg_id = body.get("seg") or ""
    hit, save = _find_item(d, seg_id)
    if not hit:
        raise HTTPException(404, "找不到分段 " + seg_id)
    # 并发闸: 运行数已达上限 → 本段排队落库, 前序完成后由 _avatar_promote 自动续交
    if _avatar_running(d, exclude=seg_id) >= _avatar_cap():
        hit["video_status"] = "queued"
        hit["video_error"] = ""
        save()
        return {"seg": hit, "queued": True}
    try:
        return _avatar_submit(d, hit, save, (body.get("image") or "").strip())
    except Exception as e:
        # 提交失败(上传/网络/参数)同样落库, 卡片常驻显示原因(不再只靠瞬时提示)
        hit["video_status"] = "none"
        hit["video_error"] = str(getattr(e, "detail", None) or f"{type(e).__name__}: {e}")[:200]
        save()
        raise

@app.post("/api/avatar/status")
def api_avatar_status(body: dict):
    d = _find_project(body.get("project") or "")
    seg_id = body.get("seg") or ""
    action = body.get("action") or "poll"
    hit, save = _find_item(d, seg_id)
    if not hit:
        raise HTTPException(404, "找不到分段 " + seg_id)
    vdir = d / "videos"
    vname = hit["wav"].replace(".wav", "").replace(".mp3", "").replace(".m4a", "").replace(".flac", "").replace(".ogg", "").split("/")[-1] + ".mp4"
    if action == "delete":
        v = vdir / vname
        if v.exists():
            trash = vdir / "_trash"; trash.mkdir(exist_ok=True)
            os.replace(v, trash / vname)
        hit["video_status"] = "deleted"; hit["video"] = ""
        save()
        return {"seg": hit}
    if action == "keep":
        hit["video_status"] = "kept"
        save()
        return {"seg": hit}
    # poll
    if hit.get("video_status") != "running" or not hit.get("video_task"):
        return {"seg": hit}
    try:
        st, results, err = _rh_status(hit["video_task"])
    except Exception as e:
        return {"seg": hit, "poll_error": f"{type(e).__name__}: {e}"[:150]}
    if st == "SUCCESS":
        urls = [x["url"] for x in results if str(x.get("outputType", "")).lower() in ("mp4", "video", "webm") and x.get("url")]
        if not urls:
            urls = [x["url"] for x in results if x.get("url")]
        if urls:
            vdir.mkdir(exist_ok=True)
            dest = vdir / vname
            with httpx.stream("GET", urls[0], timeout=300, follow_redirects=True) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            hit["video"] = vname
            hit["video_status"] = "kept"
        else:
            hit["video_status"] = "none"
            hit["video_error"] = "任务成功但无视频结果"
    elif st == "FAILED":
        hit["video_status"] = "none"
        hit["video_error"] = err or "任务失败"
    save()
    if hit.get("video_status") in ("kept", "none"):
        _avatar_promote_async(d)    # 本段终态 → 后台补位(queued 段自动续交, 421 等待重试)
    return {"seg": hit}

@app.get("/media/{name}/{path:path}")
def media(name: str, path: str):
    """自制音频等媒体文件 (项目内相对路径, 防穿越)"""
    d = _find_project(name)
    if "\\" in path or ".." in path or path.startswith("/"):
        raise HTTPException(400, "bad path")
    f = d / path
    if not f.exists():
        raise HTTPException(404, "文件不存在")
    mt = {"mp4": "video/mp4", "webm": "video/webm", "mp3": "audio/mpeg", "m4a": "audio/mp4",
          "wav": "audio/wav", "flac": "audio/flac", "ogg": "audio/ogg"}.get(f.suffix.lower().lstrip("."), "application/octet-stream")
    return FileResponse(f, media_type=mt)

# ---------------- API: 自动剪辑 · 视频初稿 (阶段四) ----------------
_OUT_W, _OUT_H, _FPS = 576, 1024, 25
_edit_job = {"running": False, "stage": "", "pct": 0, "done": False, "error": "", "output": "", "kind": "", "v": 0}
_FONT = "C\\:/Windows/Fonts/msyh.ttc"

def _ffprobe_dur(fp) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(fp)], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip().splitlines()[0])
    except Exception:
        return 0.0

def _item_video(d: Path, hit: dict) -> str:
    """条目可用的视频(项目内相对路径), 没有则空串"""
    if hit.get("video"):
        if (d / "videos" / hit["video"]).exists():
            return "videos/" + hit["video"]
    return ""

_dur_cache: dict = {}


def _dur_of(fp) -> float:
    """时长: 进程内缓存(同一文件只探测一次)。ffprobe 在部分环境首次启动很慢,
    逐条探测会让接口超时 → 调用方优先用合成时已写入的 dur_sec。"""
    key = str(fp)
    if key in _dur_cache:
        return _dur_cache[key]
    v = round(_ffprobe_dur(fp), 2)
    _dur_cache[key] = v
    return v


@app.get("/api/edit/items")
def api_edit_items(name: str):
    d = _find_project(name)
    items = []
    for s in _load_segments(d)["segments"]:
        if s["status"] != "kept":
            continue
        _du = s.get("dur_sec")                      # 合成时已写入, 无需再探测
        items.append({"id": s["id"], "kind": "seg", "name": s["text"][:32],
                      "video": _item_video(d, s), "audio": "tts/" + s["wav"],
                      "dur": round(float(_du), 2) if _du else _dur_of(d / "tts" / s["wav"])})
    for c in _load_custom(d):
        _du = c.get("dur_sec")
        items.append({"id": c["id"], "kind": "cus", "name": c.get("name", c["id"]),
                      "video": _item_video(d, c), "audio": c["wav"],
                      "dur": round(float(_du), 2) if _du else _dur_of(d / c["wav"])})
    return {"items": items, "has_draft": (d / "output" / "初稿.mp4").exists(),
            "has_video": any(x["video"] for x in items),
            "topic": _rd_if(d, "选题.txt").strip()}

@app.get("/api/edit/config")
def api_edit_config_get(name: str):
    d = _find_project(name)
    f = d / "edit.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}

@app.post("/api/edit/config")
def api_edit_config_set(body: dict):
    d = _find_project(body.get("project") or "")
    (d / "edit.json").write_text(json.dumps(body.get("config") or {}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True}

def _run_ff(args: list, total: float, wa: float, wb: float, tmp: Path):
    """跑一步 ffmpeg; -progress pipe:1 逐行直读 stdout, 进度映射到 [wa, wb]%"""
    lf = tmp / "last.log"
    if lf.exists():
        lf.unlink()
    cmd = ["ffmpeg", "-y", "-v", "error", "-progress", "pipe:1", "-nostats"] + args
    us = 0.0
    with open(lf, "wb") as errf:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf,
                             text=True, encoding="utf-8", errors="replace")
        for line in (p.stdout or []):
            line = line.strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = max(us, float(line.split("=", 1)[1]))
                except ValueError:
                    pass
                frac = max(0.0, min(1.0, us / 1e6 / max(total, 0.1)))
                _edit_job["pct"] = int(wa + (wb - wa) * frac)
        p.wait()
    if p.returncode != 0:
        tail = lf.read_text(encoding="utf-8", errors="replace")[-500:] if lf.exists() else ""
        raise RuntimeError("ffmpeg 失败: " + tail)

def _video_size(fp: Path) -> tuple:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(fp)],
                           capture_output=True, text=True, timeout=30)
        w, h = r.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except Exception:
        return _OUT_W, _OUT_H

def _esc_filter_path(p: Path) -> str:
    return "'" + p.as_posix().replace(":", "\\:") + "'"

def _wrap_text(t: str, w: int = 12) -> list[str]:
    t = t.replace("\r", "").replace("\n", " ").strip()
    return [t[i:i + w] for i in range(0, len(t), w)][:4]

def _make_card(out: Path, lines: list[str], dur: float, gold: bool, tmp: Path, wa: float, wb: float, w: int, h: int):
    tf = tmp / (out.stem + "_text.txt")
    tf.write_text("\n".join(lines), encoding="utf-8")
    fs = 48 if len(lines) == 1 else 40
    color = "0xE8D9A8" if gold else "0xF2F2F2"
    vf = (f"drawtext=fontfile='{_FONT}':textfile={_esc_filter_path(tf)}"
          f":fontcolor={color}:fontsize={fs}:line_spacing=20"
          ":x=(w-text_w)/2:y=(h-text_h)/2")
    _run_ff(["-f", "lavfi", "-i", f"color=c=0x0B0B10:s={w}x{h}:r={_FPS}",
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-t", f"{dur:.3f}", "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", str(out)], dur, wa, wb, tmp)

def _make_placeholder(out: Path, wav: Path, dur: float, tmp: Path, wa: float, wb: float, w: int, h: int):
    _run_ff(["-f", "lavfi", "-i", f"color=c=0x0B0B10:s={w}x{h}:r={_FPS}",
             "-i", str(wav), "-t", f"{max(dur, 0.5):.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
             "-shortest", str(out)], dur, wa, wb, tmp)

def _fc_opt(script: Path) -> list:
    """滤镜脚本参数: \u65b0\u7248 ffmpeg(9.x) \u79fb\u9664\u4e86 -filter_complex_script, \u6539\u4e3a\u5185\u8054 -filter_complex; \u811a\u672c\u8fc7\u957f\u65f6\u56de\u9000\u8001\u5199\u6cd5\u3002"""
    try:
        fc = script.read_text(encoding="utf-8")
        if len(fc) <= 20000:
            return ["-filter_complex", fc]
    except Exception:
        pass
    return ["-filter_complex_script", str(script)]


def _concat_all(paths: list, out: Path, tmp: Path, wa: float, wb: float, w: int, h: int):
    n = len(paths)
    total = sum(_ffprobe_dur(p) for p in paths)
    lines = []
    for i, _ in enumerate(paths):
        lines.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={_FPS},setsar=1,format=yuv420p[v{i}];")
        lines.append(f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{i}];")
    lines.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vout][aout]")
    script = tmp / "concat.flt"
    script.write_text("\n".join(lines), encoding="utf-8")
    args = []
    for p in paths:
        args += ["-i", str(p)]
    args += _fc_opt(script) + ["-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
    _run_ff(args, total, wa, wb, tmp)

def _mix_bgm(base: Path, bgm: Path, vol: float, out: Path, tmp: Path, wa: float, wb: float):
    T = _ffprobe_dur(base)
    fc = (f"[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[voice];"
          f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
          f"volume={vol:.3f},afade=t=in:d=2,atrim=0:{T:.3f},asetpts=PTS-STARTPTS,"
          f"afade=t=out:st={max(T - 3, 0):.3f}:d=3[bgm];"
          f"[bgm][voice]sidechaincompress=threshold=0.04:ratio=6:attack=80:release=700[duck];"
          f"[voice][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    script = tmp / "mix.flt"
    script.write_text(fc, encoding="utf-8")
    _run_ff(["-i", str(base), "-stream_loop", "-1", "-i", str(bgm),
             *_fc_opt(script), "-map", "0:v", "-map", "[aout]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{T:.3f}", str(out)],
            T, wa, wb, tmp)

def _edit_worker(d: Path, plan: list, body: dict):
    j = _edit_job
    try:
        tmp = d / "output" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        # 参考尺寸 = 第一个已生成视频的实际尺寸; 黑底替代跟随它
        rw, rh = _OUT_W, _OUT_H
        for p in plan:
            if p["video"]:
                f = d / p["video"].replace("/", "\\")
                rw, rh = _video_size(f)
                break
        n_pre = sum(1 for p in plan if not p["video"]) + (1 if body.get("outro", True) else 0)
        unit = 28.0 / max(n_pre, 1)
        w = 2.0
        ordered = []
        manifest_items = []
        t0 = 0.0  # 纯素材时间轴: 0 秒即开口(片头由精剪层 HyperFrames 呈现)
        for p in plan:
            if p["video"]:
                f = d / p["video"].replace("/", "\\")
                ordered.append(f)
            else:
                f = tmp / (p["id"] + "_ph.mp4")
                j["stage"] = "黑底替代 " + p["id"]
                _make_placeholder(f, p["wav"], p["dur"], tmp, w, w + unit, rw, rh)
                w += unit
                ordered.append(f)
            dur = _ffprobe_dur(f)
            manifest_items.append({"role": "item", "id": p["id"], "kind": p["kind"],
                                   "start": round(t0, 3), "dur": round(dur, 3),
                                   "text": p["text"], "video": bool(p["video"])})
            t0 += dur
        if body.get("outro", True):
            f = tmp / "card_outro.mp4"
            j["stage"] = "片尾卡"
            _make_card(f, ["本视频", "由 AI 生成"], 3.0, False, tmp, w, w + unit, rw, rh)
            w += unit
            ordered.append(f)
            manifest_items.append({"role": "outro", "start": round(t0, 3), "dur": _ffprobe_dur(f)})
        base = d / "output" / "tmp_base.mp4"
        j["stage"] = f"拼接 {len(ordered)} 段"
        _concat_all(ordered, base, tmp, 30, 85, rw, rh)
        bgm = _clean_path(str(body.get("bgm") or "")).strip()
        final = d / "output" / "初稿.mp4"
        if bgm and Path(bgm).exists():
            j["stage"] = "BGM 混音"
            _mix_bgm(base, Path(bgm), float(body.get("bgm_vol") or 0.12), final, tmp, 85, 99)
        else:
            os.replace(base, final)
            j["pct"] = 99
        for f in ordered:
            if f.parent == tmp and f.exists():
                f.unlink()
        if base.exists():
            base.unlink()
        total = _ffprobe_dur(final)
        (d / "output" / "manifest.json").write_text(json.dumps(
            {"w": rw, "h": rh, "fps": _FPS, "total": round(total, 3), "items": manifest_items},
            ensure_ascii=False, indent=1), encoding="utf-8")
        j.update(pct=100, done=True, running=False, stage="完成", output=str(final))
    except Exception as e:
        j.update(running=False, done=False, stage="失败", error=f"{type(e).__name__}: {e}")

@app.post("/api/edit/render")
def api_edit_render(body: dict):
    if _edit_job["running"]:
        raise HTTPException(409, "已有剪辑任务在运行")
    d = _find_project(body.get("project") or "")
    sel = body.get("items") or []
    if not sel:
        raise HTTPException(400, "未选择任何素材")
    segs = {s["id"]: s for s in _load_segments(d)["segments"]}
    cuss = {c["id"]: c for c in _load_custom(d)}
    plan = []
    for it in sel:
        iid, kind = it.get("id") or "", it.get("kind") or ""
        hit = (segs.get(iid) if kind == "seg" else cuss.get(iid)) if kind in ("seg", "cus") else None
        if not hit:
            raise HTTPException(400, f"素材不存在: {iid}")
        wav = (d / "tts" / hit["wav"]) if kind == "seg" else (d / hit["wav"])
        if not wav.exists():
            raise HTTPException(400, f"音频缺失: {iid}")
        plan.append({"id": iid, "kind": kind, "video": _item_video(d, hit), "wav": wav,
                     "text": (hit.get("text") or hit.get("name") or hit["id"]),
                     "dur": _ffprobe_dur(wav)})
    if not any(p["video"] for p in plan):
        raise HTTPException(400, "至少需要一段已生成的数字人视频才能合成初稿（缺视频的段会用黑底+音频替代）")
    _edit_job.update(running=True, stage="准备", pct=1, done=False, error="", output="", kind="draft")
    threading.Thread(target=_edit_worker, args=(d, plan, body), daemon=True).start()
    return {"ok": True}

@app.get("/api/edit/status")
def api_edit_status():
    return {k: _edit_job.get(k, "") for k in ("running", "stage", "pct", "done", "error", "output", "kind", "v")}

# ---------------- API: HyperFrames 精剪 (阶段五) ----------------
_HF_VERSION = "0.8.20"
_HF_TEMPLATES = {
    "gold": {"name": "黑金简约", "spec": {
        "font": "Microsoft YaHei",
        "palette": {"bg": "rgba(11,11,16,.74)", "text": "#F5EEDD", "accent": "#B6A884",
                    "border": "rgba(182,168,132,.55)"},
        "subtitle": {"shape": "pill", "size_scale": 1.0, "weight": 600, "radius": 12},
        "extras": []}},
    "magazine": {"name": "杂志编辑感", "spec": {
        "font": "Microsoft YaHei",
        "palette": {"bg": "rgba(19,21,26,.88)", "text": "#FFFFFF", "accent": "#FFD400",
                    "border": "rgba(255,212,0,.8)"},
        "subtitle": {"shape": "bar", "size_scale": 1.05, "weight": 900, "radius": 0},
        "extras": ["cut-marks", "num-badge", "progress-bar"]}},
    "variety": {"name": "综艺花字", "spec": {
        "font": "Microsoft YaHei",
        "palette": {"bg": "transparent", "text": "#FFFFFF", "accent": "#FF4D6D",
                    "border": "transparent"},
        "subtitle": {"shape": "bare", "size_scale": 1.22, "weight": 900, "radius": 0},
        "extras": []}},
    "minimal": {"name": "极简白", "spec": {
        "font": "Microsoft YaHei",
        "palette": {"bg": "rgba(255,255,255,.92)", "text": "#15151A", "accent": "#15151A",
                    "border": "transparent"},
        "subtitle": {"shape": "bar", "size_scale": 0.92, "weight": 600, "radius": 0},
        "extras": []}},
}
_HF_EXTRA_SET = ("cut-marks", "progress-bar", "num-badge", "watermark")
_HF_FONTS = ("Microsoft YaHei", "SimSun", "KaiTi")

def _html_esc(s) -> str:
    """用户文案 → HTML 安全文本(片尾/数据卡内联)"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))

def _roman(n: int) -> str:
    vals = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out or "I"

def _ok_color(v: str) -> bool:
    return bool(re.fullmatch(r"#[0-9a-fA-F]{3,8}", v) or
                re.fullmatch(r"rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*"
                             r"(,\s*(0|1|0?\.\d+)\s*)?\)", v))

def _parse_css_color(v: str):
    """'#RGB/#RGBA/#RRGGBB/#RRGGBBAA' 或 'rgba(r,g,b,a)' → (r,g,b,a) | None"""
    v = (v or "").strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        if len(h) not in (6, 8):
            return None
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return (r, g, b, a)
    m = re.fullmatch(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(0|1|0?\.\d+)\s*)?\)", v)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                float(m.group(4)) if m.group(4) else 1.0)
    return None

def _rel_lum(rgb):
    def ch(v):
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])

def _wcag_ratio(c1, c2):
    l1, l2 = _rel_lum(c1), _rel_lum(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)

# ---- 官方组件菜单(hf-library 预装, 家族式: AI选配/手选; 已适配40件=标题8+字幕12+氛围4+信息条10+转场4+黑底背景2) ----
_HF_TITLE_MENU = {"tracking-in": "聚焦显字(深底chip白衬线大字,模糊聚拢定住,通用稳妥)",
                  "titlecard-lockup": "定版细线(极简定版,细线左→右画出,冷静克制)",
                  "per-word-rise": "逐字升起(逐字模糊落位,节奏感强,适合短标题/悬念开场)",
                  "typewriter": "打字机(逐字显影+光标收尾,适合揭秘/教程/故事)",
                  "headline-slam": "重锤标题(巨字砸落+镜头微震,适合强钩子/娱乐向)",
                  "marker-circle": "手绘圈题(马克笔绕题一圈,适合圈重点/点题)",
                  "titlecard-calm": "冷静定版(小标签+大字次第淡升,高级留白,适合观点/知识向)",
                  "text-shimmer": "扫光标题(定版字面一道高光扫过,精致克制)"}
_HF_CAPTION_MENU = {"bar": "现有字幕条(全宽色带,稳重通用,最不出错)",
                    "pill-karaoke": "卡拉OK药丸(浅色药丸逐字点亮,节奏感强,适合快节奏/年轻化)",
                    "editorial-emphasis": "杂志重读(色带+衬线大号重读块,适合观点输出/杂志感)",
                    "highlight": "高亮扫过(白字+色块逐词扫过,TikTok感,适合强节奏口播)",
                    "blend-difference": "反色显字(白字混合反色,亮暗自动反转,无底带最清爽)",
                    "clip-wipe": "逐词擦入(白字逐词从左擦出,干净利落,通用)",
                    "weight-shift": "字重焦点(双行静读,读到哪行哪行加粗,安静优雅)",
                    "matrix-decode": "矩阵解码(字符扰动两段解码显字,科技感/黑客风)",
                    "glitch-rgb": "RGB故障(色差分离抖动显字,红青错位,电子故障感)",
                    "gradient-fill": "渐变扫色(白色字读到哪哪块流过彩虹渐变,活力流行感)",
                    "neon-glow": "霓虹辉光(未读暗霓虹管,读到哪哪块点亮青色辉光,重点词粉红,夜店霓虹感)",
                    "kinetic-slam": "动力猛砸(一词一屏超大字,上砸/左右飞/缩放弹四式轮换入场,强冲击)",
                    "neon-accent": "霓虹强调(白字+彩色霓虹重点词大一号带辉光,整句常驻,美式vlog感)"}
_HF_BGB_MENU = {"none": "无背景(纯黑底+字幕,最简)",
                "aurora": "极光漂移(三色光球缓漂,深邃有氛围)",
                "mesh": "网格渐变(三色光斑呼吸漂移,柔和现代)"}   # 仅作用于黑底替代段(video:false), 数字人段不受影响
_HF_ATMO_MENU = {"none": "无氛围(最干净,体积最小)",
                 "vignette": "电影暗角(静态压边聚焦,零体积代价)",
                 "light-leak": "暖调漏光(三层光晕缓漂扫过,胶片记忆感,零体积)",
                 "spotlight": "羽化聚光(压暗四周洞含主体,视线聚焦,零体积)",
                 "grain": "胶片颗粒(⚠️时域噪声,成片体积暴涨10~30倍)"}   # AI 选配池不含 grain, 仅手选
_HF_LT_MENU = {"lt-kicker-name": "看点签条(标签chip落下+衬线大字+底线描画,信息感强)",
               "lt-color-block": "色块签条(强调色块左滑入+大字+角色行,冲击力强)",
               "lt-side-rule": "竖线签条(竖线描画+大字滑入,冷静编辑感)",
               "lt-clean-bar": "白卡签条(竖标+暖白卡左擦入,黑底段也清晰)",
               "lt-soft-pill": "圆牌签条(圆牌左下弹出+状态点,轻盈亲和)",
               "lt-stack-bars": "双条签条(名条与角色条对向擦入,动感双层)",
               "lt-bold-block": "重块签条(强调色块擦入+大字砸升+黑标签弹出,冲击力强)",
               "lt-mask-reveal": "扫光签条(竖光扫过揭出大字,无卡最轻)",
               "lt-accent-underline": "底线签条(大字+描画底线+角色行,极简编辑感)",
               "lt-news-ticker": "新闻爬条(顶部横幅快讯爬动,资讯感强)"}   # 开场后2.1s入,4.5s停;爬条=顶部专项布局

_HF_TRANS_MENU = {"none": "无转场(保留原始硬切)",
                  "flash": "切点闪光(三层暖调闪光在每段切点爆开衰减,弱化硬切感)",
                  "dip-black": "黑场过渡(切点前后压向黑再浮起,沉稳段落分隔)",
                  "sweep": "光带扫场(斜向光带在切点扫过全屏,利落换段)",
                  "wipe": "色板擦除(强调色板盖过切点左进右出,编辑杂志风)"}   # AI 选配池含全部

_TR_NAMES = {"flash": "切点闪光", "dip-black": "黑场过渡", "sweep": "光带扫场", "wipe": "色板擦除"}

# 家族⑧ 片尾 (官方 cta-close / social-proof-card / cta-lockup DNA → 收尾行动号召/品牌证明卡)
_HF_CTA_MENU = {"cta-close": "行动号召(大字逐词落定+胶囊按钮弹出,片尾收束,通用推荐)",
                "cta-lockup": "标准收尾(行动语→胶囊→微文案三段落定+轻微漂移hold,最完整)",
                "social-card": "口碑收尾卡(品牌名+五星逐颗弹入+证明行+三项+按钮,产品/口碑向)",
                "logo-sting": "品牌落版(品牌名砸定+光环扩散+一帧白闪,冲击力强,适合品牌收束)"}

# 家族⑨ 数据 (官方 number-pop-in / conic-progress-ring / number-wheel DNA → 讲数据/进度时点题)
_HF_DATA_MENU = {"number-pop": "数字弹入(逐字符模糊上浮落定+单位小字,讲数据时自然点题)",
                 "conic-ring": "环形进度(圆环填充+中心数字同步计数,讲完成度/占比最直观)",
                 "number-wheel": "滚动计数(每位数字独立滚轮落定,像计数器/仪表盘,大数字冲击力强)"}

# 家族⑩ 对比 (官方 comparison-split DNA → 前后对比条, 讲变化/改造前后)
_HF_CMP_MENU = {"before-after": "前后对比(中部两栏面板,右栏擦入揭示: 左=以前 右=现在)",
                "split-tilt": "双卡倾斜(两张卡从两侧飞入+3D镜像倾斜+眉标弹入,对比更有纵深)"}

# 家族⑪ 清单 (官方 marker-checklist-card DNA → 手写要点清单卡, 讲步骤/要点)
_HF_LIST_MENU = {"checklist": "要点清单卡(手写风标题+下划线+圈词+三行要点逐条落地+勾自绘)"}

def _cta_texts(spec: dict) -> dict:
    """片尾/数据/对比 文本: 用户配置优先, 否则兜底"""
    cfg = spec.get("cta") or {}
    st = spec.get("style_cfg") or {}
    g = lambda k, d="": (cfg.get(k) or st.get(k) or d).strip()
    action = g("action") or "关注我 下期见"
    return {"action": action, "button": g("button") or "点个关注",
            "brand": g("brand") or (spec.get("intro_title") or "").strip() or "本频道",
            "proof": g("proof") or "每期三分钟 讲清一件事",
            "microcopy": g("microcopy") or "免费浏览全部内容",
            "left_title": g("left_title") or "以前", "left_text": g("left_text") or "零散经验 靠感觉试",
            "right_title": g("right_title") or "现在", "right_text": g("right_text") or "一套流程 照着做",
            "list_title": g("list_title") or "今天讲清三件事", "list_circled": g("list_circled") or "三件事",
            "list1": g("list1") or "选题|从热点里挑真问题", "list2": g("list2") or "成稿|先说结论再给证据",
            "list3": g("list3") or "交付|一条视频讲完一个点"}

def _hf_ring_parts(W: int, H: int, start: float, dur: float, progress: float, label: str,
                   unit: str, tag: str, accent: str):
    """官方 conic-progress-ring DNA(圆环填充+中心数字同步计数) → 改用 SVG stroke-dashoffset 实现:
    conic-gradient+mask 会让中心数字被 mask 一并裁掉, 且 calc(% * %) 非法 → 环不可见;
    SVG 描边是确定性标准属性, GSAP 直接补间, 无 @property 注册依赖。位置中上部避开字幕带。"""
    pct = max(0.0, min(100.0, float(progress or 0)))
    size = max(150, round(min(W * 0.46, H * 0.27)))
    thick = max(7, round(size * 0.075))
    r = (size - thick) / 2.0
    circ = 2 * 3.141592653589793 * r
    lab_fs = max(26, round(size * 0.24))
    css = (f"#pring{{position:absolute;left:0;right:0;top:{round(H*0.17)}px;display:flex;"
           f"flex-direction:column;align-items:center;gap:{round(H*0.014)}px;z-index:19;pointer-events:none}}"
           f"#pring .pr-tag{{color:#BDB7A8;font-size:{max(13, round(W*0.028))}px;letter-spacing:.06em;opacity:0}}"
           f"#pring .pr-stage{{position:relative;width:{size}px;height:{size}px;opacity:0}}"
           f"#pring svg{{width:100%;height:100%;display:block;transform:rotate(-90deg)}}"
           f"#pring .pr-track{{fill:none;stroke:rgba(255,255,255,.13);stroke-width:{thick}}}"
           f"#pring .pr-fill{{fill:none;stroke:{accent};stroke-width:{thick};stroke-linecap:round;"
           f"stroke-dasharray:{circ:.2f};stroke-dashoffset:{circ:.2f};"
           f"filter:drop-shadow(0 0 {max(3, round(size*0.02))}px rgba(0,0,0,.45))}}"
           f"#pring .pr-val{{position:absolute;inset:{round(size*0.19)}px;display:flex;align-items:center;"
           f"justify-content:center;background:#0B0C0F;border-radius:50%;"
           f"color:#F7F3E9;font-weight:800;font-size:{lab_fs}px;letter-spacing:-.02em;"
           f"text-shadow:0 {round(3*W/1080)}px {round(14*W/1080)}px rgba(0,0,0,.6)}}"
           f"#pring .pr-unit{{font-size:{round(lab_fs*0.42)}px;color:#CFC9BB;margin-left:{round(size*0.02)}px}}")
    html = (f'<div id="pring" class="clip" data-start="{round(start, 3)}" data-duration="{round(dur, 3)}">'
            + (f'<div class="pr-tag">{_html_esc(tag)}</div>' if tag else "")
            + f'<div class="pr-stage">'
            + f'<svg viewBox="0 0 {size} {size}"><circle class="pr-track" cx="{size/2:.1f}" cy="{size/2:.1f}" r="{r:.1f}"/>'
            + f'<circle class="pr-fill" cx="{size/2:.1f}" cy="{size/2:.1f}" r="{r:.1f}"/></svg>'
            + f'<div class="pr-val"><span class="pr-num">{_html_esc(label or "0")}</span>'
            + (f'<small class="pr-unit">{_html_esc(unit)}</small>' if (unit or "").strip() else "")
            + '</div></div></div>')
    js = ""
    if tag:
        js += (f'  tl.fromTo("#pring .pr-tag",{{opacity:0,y:8}},'
               f'{{opacity:1,y:0,duration:0.32,ease:"power2.out"}},{round(start, 3)});\n')
    target_off = round(circ * (1 - pct / 100.0), 2)
    num_target = float(_num_or_zero(label))
    js += (f'  tl.fromTo("#pring .pr-stage",{{opacity:0,scale:0.94}},'
           f'{{opacity:1,scale:1,duration:0.4,ease:"power2.out"}},{round(start + 0.05, 3)});\n'
           f'  tl.fromTo("#pring .pr-fill",{{strokeDashoffset:{circ:.2f}}},'
           f'{{strokeDashoffset:{target_off},duration:1.4,ease:"power2.out",'
           f'onUpdate:function(){{var el=document.querySelector("#pring .pr-num");'
           f'if(!el)return;var off=parseFloat(this.targets()[0].style.strokeDashoffset)||0;'
           f'var v=(1-off/{circ:.2f})*100;el.textContent=String(Math.round(v/100*{num_target}));}}}},'
           f'{round(start + 0.05, 3)});\n')
    js += (f'  tl.to("#pring .pr-stage, #pring .pr-tag",{{opacity:0,duration:0.32,ease:"power2.in"}},'
           f'{round(start + dur - 0.34, 3)});\n'
           f'  tl.set("#pring .pr-stage, #pring .pr-tag",{{opacity:0}},{round(start + dur, 3)});\n')
    return css, html, js

def _hf_nwheel_parts(W: int, H: int, start: float, dur: float, value: str, unit: str,
                     tag: str, accent: str):
    """官方 number-wheel DNA → 滚动计数: 每位数字一列, strip 从 0 滚到目标位(y 位移, transform 安全),
    power3.out 落定。中文环境用等宽数字字体, 位置中上部避开字幕带。"""
    digits = [c for c in str(value) if c.isdigit()][:6] or ["0"]
    fs = max(44, round(W * 0.13))
    css = (f"#nwheel{{position:absolute;left:0;right:0;top:{round(H*0.20)}px;display:flex;"
           f"flex-direction:column;align-items:center;gap:{round(H*0.012)}px;z-index:19;pointer-events:none}}"
           f"#nwheel .nw-tag{{color:#EDE7DA;font-size:{max(13, round(W*0.028))}px;letter-spacing:.06em;opacity:0;background:#0B0C0F;padding:{round(H*0.006)}px {round(W*0.022)}px;border-radius:999px}}"
           f"#nwheel .nw-row{{display:flex;align-items:flex-end;gap:{round(W*0.004)}px;"
           f"font-family:Consolas,'Microsoft YaHei',monospace;opacity:0}}"
           f"#nwheel .nw-col{{height:{fs}px;overflow:hidden;border-radius:{round(W*0.008)}px;background:#F2EDE2}}"
           f"#nwheel .nw-strip{{display:flex;flex-direction:column;will-change:transform}}"
           f"#nwheel .nw-strip span{{display:block;height:{fs}px;line-height:{fs}px;font-size:{fs}px;"
           f"font-weight:800;color:#16171A;text-align:center;text-shadow:none}}"
           f"#nwheel .nw-unit{{color:#EDE7DA;font-weight:700;font-size:{round(fs*0.42)}px;margin-left:{round(W*0.006)}px;")
    cols = "".join(
        '<div class="nw-col"><div class="nw-strip" id="nw{d}">{s}</div></div>'.format(
            d=i, s="".join(f"<span>{k}</span>" for k in range(10)) + f"<span>{d}</span>")
        for i, d in enumerate(digits))
    html = (f'<div id="nwheel" class="clip" data-start="{round(start, 3)}" data-duration="{round(dur, 3)}">'
            + (f'<div class="nw-tag">{_html_esc(tag)}</div>' if tag else "")
            + f'<div class="nw-row">{cols}'
            + (f'<small class="nw-unit">{_html_esc(unit)}</small>' if (unit or "").strip() else "")
            + "</div></div>")
    js = ""
    if tag:
        js += (f'  tl.fromTo("#nwheel .nw-tag",{{opacity:0,y:8}},'
               f'{{opacity:1,y:0,duration:0.3,ease:"power2.out"}},{round(start, 3)});\n')
    js += (f'  tl.fromTo("#nwheel .nw-row",{{opacity:0}},{{opacity:1,duration:0.25,ease:"none"}},'
           f'{round(start + 0.05, 3)});\n')
    for i, d in enumerate(digits):
        # strip = 0..9 + 目标位(共 11 行, 索引 0..10) → 落定位移 = -10 * 行高
        js += (f'  tl.fromTo("#nw{i}",{{y:0}},{{y:{-(10) * fs},duration:1.15,ease:"power3.out"}},'
               f'{round(start + 0.08 + i * 0.07, 3)});\n')
    js += (f'  tl.to("#nwheel .nw-row, #nwheel .nw-tag",{{opacity:0,duration:0.3,ease:"power2.in"}},'
           f'{round(start + dur - 0.32, 3)});\n'
           f'  tl.set("#nwheel .nw-row, #nwheel .nw-tag",{{opacity:0}},{round(start + dur, 3)});\n')
    return css, html, js

def _hf_list_parts(W: int, H: int, start: float, dur: float, tx: dict, accent: str):
    """官方 marker-checklist-card DNA → 手写要点清单卡(中文用楷体 KaiTi 代替手写体):
    纸卡浮入 → 标题 → 下划线(scaleX) → 圈词(SVG 椭圆 stroke-dashoffset 自绘) →
    三行要点逐条落地 → 每行勾(SVG polyline 自绘)。纸卡放中上部, 避开底部字幕带。"""
    top = round(H * 0.13)
    ph = round(H * 0.42)
    fs_t = max(20, round(W * 0.052))
    fs_r = max(15, round(W * 0.034))
    rows = []
    for k in ("list1", "list2", "list3"):
        raw = (tx.get(k) or "").split("|")
        lab = (raw[0] if raw else "").strip()[:4]
        val = (raw[1] if len(raw) > 1 else "").strip()[:14]
        rows.append((lab, val))
    css = (f"#mlist{{position:absolute;left:{round(W*0.07)}px;right:{round(W*0.07)}px;top:{top}px;"
           f"height:{ph}px;z-index:31;pointer-events:none;opacity:0;"
           f"background:#15161A;border-radius:{round(W*0.03)}px;"
           f"box-shadow:0 {round(12*W/1080)}px {round(40*W/1080)}px rgba(0,0,0,.45);"
           f"padding:{round(H*0.028)}px {round(W*0.05)}px;display:flex;flex-direction:column;"
           f"gap:{round(H*0.016)}px;font-family:'KaiTi','Microsoft YaHei',serif;color:#F6F1E4}}"
           f"#mlist .ml-head{{color:#F6F1E4;position:relative;display:inline-block;align-self:flex-start;"
           f"font-size:{fs_t}px;font-weight:700;letter-spacing:.02em}}"
           f"#mlist .ml-ul{{height:{max(3, round(W*0.006))}px;background:#E6C478;border-radius:3px;"
           f"transform-origin:0 50%;margin-top:{round(H*0.004)}px}}"
           f"#mlist .ml-ell{{position:absolute;right:-{round(W*0.03)}px;top:-{round(H*0.006)}px;"
           f"width:{round(W*0.22)}px;height:{round(H*0.05)}px;overflow:visible}}"
           f"#mlist .ml-ell ellipse{{fill:none;stroke:#D2453B;stroke-width:3;opacity:0}}"
           f"#mlist .ml-row{{display:flex;align-items:center;gap:{round(W*0.022)}px;font-size:{fs_r}px;opacity:0}}"
           f"#mlist .ml-lab{{color:#B9B3A5;letter-spacing:.14em;flex:none;min-width:{round(W*0.13)}px}}"
           f"#mlist .ml-val{{color:#FFFFFF;font-weight:700}}"
           f"#mlist .ml-chk{{width:{round(W*0.05)}px;height:{round(W*0.05)}px;flex:none;margin-left:auto}}"
           f"#mlist .ml-chk polyline{{fill:none;stroke:#2E9E5B;stroke-width:6;stroke-linecap:round;"
           f"stroke-dasharray:40;stroke-dashoffset:40}}")
    row_html = "".join(
        f'<div class="ml-row" id="mlr{i}"><span class="ml-lab">{_html_esc(lab)}</span>'
        f'<span class="ml-val">{_html_esc(val)}</span>'
        f'<svg class="ml-chk" viewBox="0 0 24 24"><polyline points="4,13 10,19 20,6"/></svg></div>'
        for i, (lab, val) in enumerate(rows))
    html = (f'<div id="mlist" class="clip" data-start="{round(start, 3)}" data-duration="{round(dur, 3)}">'
            f'<div class="ml-head">{_html_esc(tx["list_title"])}'
            f'<svg class="ml-ell" viewBox="0 0 120 34"><ellipse cx="60" cy="17" rx="55" ry="14"/></svg>'
            f'</div><div class="ml-ul"></div>{row_html}</div>')
    js = (f'  tl.fromTo("#mlist",{{opacity:0,y:{round(H*0.02)}}},'
          f'{{opacity:1,y:0,duration:0.4,ease:"power2.out"}},{round(start, 3)});\n'
          f'  tl.fromTo("#mlist .ml-ul",{{scaleX:0}},{{scaleX:1,duration:0.35,ease:"power2.out"}},'
          f'{round(start + 0.42, 3)});\n'
          f'  tl.set("#mlist .ml-ell ellipse",{{opacity:1}},{round(start + 0.6, 3)});\n'
          f'  tl.fromTo("#mlist .ml-ell ellipse",{{strokeDashoffset:172}},'
          f'{{strokeDashoffset:0,duration:0.55,ease:"power1.inOut"}},{round(start + 0.6, 3)});\n')
    for i in range(3):
        at = round(start + 0.95 + i * 0.28, 3)
        js += (f'  tl.fromTo("#mlr{i}",{{opacity:0,x:-{round(W*0.02)}}},'
               f'{{opacity:1,x:0,duration:0.35,ease:"power2.out"}},{at});\n'
               f'  tl.fromTo("#mlr{i} .ml-chk polyline",{{strokeDashoffset:40}},'
               f'{{strokeDashoffset:0,duration:0.3,ease:"power1.inOut"}},{round(at + 0.18, 3)});\n')
    js += (f'  tl.to("#mlist",{{opacity:0,duration:0.32,ease:"power2.in"}},{round(start + dur - 0.34, 3)});\n'
           f'  tl.set("#mlist",{{opacity:0}},{round(start + dur, 3)});\n')
    return css, html, js

def _num_or_zero(s) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", str(s or "")) or 0)
    except Exception:
        return 0.0

def _hf_cmp_parts(W: int, H: int, start: float, dur: float, tx: dict, accent: str):
    """官方 comparison-split DNA → 前后对比条(中部两栏): 左=以前(压暗底), 右=现在(强调色,
    以 clip-path inset 从左向右擦入 + 分隔线/圆点 left 同步)。⚠ 不用 CSS 变量补间
    (未注册的变量 GSAP 补间不可靠 → 整卡不可见), 直接补间 clipPath 与 left 百分比。"""
    top = round(H * 0.20)
    ph = round(H * 0.30)
    css = (f"#cmp{{position:absolute;left:{round(W*0.08)}px;right:{round(W*0.08)}px;top:{top}px;height:{ph}px;"
           f"z-index:19;pointer-events:none;opacity:0}}"
           f"#cmp .cs-before,#cmp .cs-after{{position:absolute;top:0;height:100%;border-radius:{round(W*0.022)}px;"
           f"padding:{round(H*0.022)}px {round(W*0.03)}px;display:flex;flex-direction:column;justify-content:center;"
           f"gap:{round(H*0.01)}px}}"
           f"#cmp .cs-before{{left:0;width:100%;background:rgba(24,25,29,.90);border:1px solid rgba(255,255,255,.10)}}"
           f"#cmp .cs-after{{left:0;width:100%;background:{accent};color:#14110A;clip-path:inset(0 100% 0 0);"
           f"box-shadow:0 {round(8*W/1080)}px {round(26*W/1080)}px rgba(0,0,0,.4)}}"
           f"#cmp .cs-t{{font-size:{max(13, round(W*0.026))}px;letter-spacing:.08em;opacity:.78}}"
           f"#cmp .cs-x{{font-size:{max(17, round(W*0.038))}px;font-weight:700;line-height:1.35}}"
           f"#cmp .cs-div{{position:absolute;top:-{round(H*0.014)}px;bottom:-{round(H*0.014)}px;left:0;width:2px;"
           f"background:{accent};border-radius:2px;opacity:.9}}"
           f"#cmp .cs-dot{{position:absolute;top:50%;left:0;width:{round(W*0.028)}px;height:{round(W*0.028)}px;"
           f"margin:-{round(W*0.014)}px 0 0 -{round(W*0.014)}px;border-radius:50%;background:{accent};"
           f"box-shadow:0 0 {round(W*0.02)}px rgba(0,0,0,.5)}}")
    html = (f'<div id="cmp" class="clip" data-start="{round(start, 3)}" data-duration="{round(dur, 3)}">'
            f'<div class="cs-before"><span class="cs-t">{_html_esc(tx["left_title"])}</span>'
            f'<span class="cs-x">{_html_esc(tx["left_text"])}</span></div>'
            f'<div class="cs-after"><span class="cs-t">{_html_esc(tx["right_title"])}</span>'
            f'<span class="cs-x">{_html_esc(tx["right_text"])}</span></div>'
            f'<div class="cs-div"></div><div class="cs-dot"></div></div>')
    wipe = round(min(1.1, max(0.6, dur * 0.4)), 3)
    at = round(start + 0.32, 3)
    js = (f'  tl.fromTo("#cmp",{{opacity:0}},{{opacity:1,duration:0.4,ease:"power2.out"}},{round(start, 3)});\n'
          f'  tl.fromTo("#cmp .cs-after",{{clipPath:"inset(0 100% 0 0)"}},'
          f'{{clipPath:"inset(0 42% 0 0)",duration:{wipe},ease:"sine.inOut"}},{at});\n'
          # 分隔线/圆点用 transform 位移(布局属性 left 补间会被 check 的 gsap_non_transform_motion 拦下)
          f'  tl.fromTo("#cmp .cs-div, #cmp .cs-dot",{{x:0}},'
          f'{{x:{round(W*0.84*0.58)},duration:{wipe},ease:"sine.inOut"}},{at});\n'
          f'  tl.to("#cmp",{{opacity:0,duration:0.32,ease:"power2.in"}},{round(start + dur - 0.34, 3)});\n'
          f'  tl.set("#cmp",{{opacity:0}},{round(start + dur, 3)});\n')
    return css, html, js

def _hf_tilt_parts(W: int, H: int, start: float, dur: float, tx: dict, accent: str):
    """官方 split-tilt-cards DNA → 双卡倾斜对比(中部): 两卡从两侧飞入(x 位移 + scale),
    各带镜像 rotateY(±14deg) 与向外投影, 眉标 pill 逆向小幅过冲弹入(右卡迟 0.3s),
    保持期极轻相位错开的 idle 浮动 → 结束淡出。位置/尺寸避开底部字幕带。"""
    top = round(H * 0.19)
    ph = round(H * 0.30)
    pad = round(W * 0.07)
    gap = round(W * 0.03)
    cw = round((W - pad * 2 - gap) / 2)
    wing = round(W * 0.42)
    lab_fs = max(13, round(W * 0.026))
    tx_fs = max(17, round(W * 0.037))
    css = (f"#tilt{{position:absolute;left:0;right:0;top:{top}px;height:{ph}px;z-index:19;pointer-events:none;"
           f"perspective:900px;perspective-origin:50% 50%}}"
           f"#tilt .tw-a,#tilt .tw-b{{position:absolute;top:0;height:100%;width:{cw}px;opacity:0}}"
           f"#tilt .tw-a{{left:{pad}px}}#tilt .tw-b{{right:{pad}px}}"
           f"#tilt .tw-card{{position:relative;width:100%;height:100%;border-radius:{round(W*0.024)}px;"
           f"padding:{round(H*0.024)}px {round(W*0.04)}px;display:flex;flex-direction:column;gap:{round(H*0.012)}px;"
           f"transform-style:preserve-3d}}"
           f"#tilt .tw-a .tw-card{{transform:rotateY(14deg);background:rgba(22,23,27,.92);"
           f"border:1px solid rgba(255,255,255,.13);box-shadow:-{round(16*W/1080)}px {round(12*W/1080)}px "
           f"{round(34*W/1080)}px rgba(0,0,0,.5)}}"
           f"#tilt .tw-b .tw-card{{transform:rotateY(-14deg);background:{accent};color:#14110A;"
           f"box-shadow:{round(16*W/1080)}px {round(12*W/1080)}px {round(34*W/1080)}px rgba(0,0,0,.5)}}"
           f"#tilt .tw-lab{{font-size:{lab_fs}px;letter-spacing:.14em;opacity:.8}}"
           f"#tilt .tw-tx{{font-size:{tx_fs}px;font-weight:700;line-height:1.4}}"
           f"#tilt .tw-pill{{position:absolute;bottom:-{round(H*0.022)}px;left:{round(W*0.03)}px;"
           f"padding:{round(H*0.008)}px {round(W*0.026)}px;border-radius:999px;background:#0B0C0F;color:#F2EDE2;"
           f"font-size:{max(12, round(W*0.024))}px;opacity:0;border:1px solid rgba(255,255,255,.22)}}")
    html = (f'<div id="tilt" class="clip" data-start="{round(start, 3)}" data-duration="{round(dur, 3)}">'
            f'<div class="tw-a"><div class="tw-card"><span class="tw-lab">{_html_esc(tx["left_title"])}</span>'
            f'<span class="tw-tx">{_html_esc(tx["left_text"])}</span></div>'
            f'<span class="tw-pill" id="tpill-a">旧</span></div>'
            f'<div class="tw-b"><div class="tw-card"><span class="tw-lab">{_html_esc(tx["right_title"])}</span>'
            f'<span class="tw-tx">{_html_esc(tx["right_text"])}</span></div>'
            f'<span class="tw-pill" id="tpill-b">新</span></div></div>')
    js = ""
    for i, (cls, dx, tl_) in enumerate((("tw-a", -wing, 0.0), ("tw-b", wing, 0.08))):
        at = round(start + tl_, 3)
        js += (f'  tl.fromTo(".{cls}",{{x:{dx},scale:0.94,opacity:0}},'
               f'{{x:0,scale:1,opacity:1,duration:0.7,ease:"power3.out"}},{at});\n')
    js += (f'  tl.fromTo("#tpill-a",{{opacity:0,scale:0.6}},'
           f'{{opacity:1,scale:1,duration:0.4,ease:"back.out(1.9)"}},{round(start + 0.62, 3)});\n'
           f'  tl.fromTo("#tpill-b",{{opacity:0,scale:0.6}},'
           f'{{opacity:1,scale:1,duration:0.4,ease:"back.out(1.9)"}},{round(start + 0.92, 3)});\n')
    hold = round(max(dur - 1.6, 0.4), 3)
    js += (f'  tl.to(".tw-a .tw-card",{{y:-{round(H*0.006)},duration:{hold},ease:"sine.inOut"}},'
           f'{round(start + 1.3, 3)});\n'
           f'  tl.to(".tw-b .tw-card",{{y:{round(H*0.006)},duration:{hold},ease:"sine.inOut"}},'
           f'{round(start + 1.3, 3)});\n')
    js += (f'  tl.to("#tilt",{{opacity:0,duration:0.32,ease:"power2.in"}},{round(start + dur - 0.34, 3)});\n'
           f'  tl.set("#tilt",{{opacity:0}},{round(start + dur, 3)});\n')
    return css, html, js

def _cta_chunks(text: str) -> list:
    """片尾行动语切词: 先按空格/标点断意群, 意群内再按中文 2-3 字切(逐词落定用)"""
    parts = [p for p in re.split(r"[\s，。！？；：、,.;:!?—…]+", (text or "").strip()) if p]
    out = []
    for p in parts:
        out.append(p) if len(p) <= 4 else out.extend(_chunk_cjk(p))
    return out or ([text.strip()] if (text or "").strip() else [])

def _hf_cta_parts(kind: str, W: int, H: int, win, spec: dict):
    """官方 cta-close / social-proof-card DNA → 片尾收尾卡(全屏覆盖, z-32 压住字幕/信息条)。
    cta-close=行动语逐词落定(0.06起/0.62长/stagger0.1)+胶囊按钮 0.72s 过冲弹出+静止 hold;
    social-card=品牌名→五星逐颗 back.out(2.4)→证明行→三项→按钮 静默 stagger。
    深底遮罩保证可读; 退出挂内层+终点 tl.set 硬杀(gsap_exit_missing_hard_kill 规约)。"""
    if kind not in _HF_CTA_MENU or not win:
        return "", "", ""
    s, e = win
    dur = round(max(e - s, 2.4), 3)
    tx = _cta_texts(spec)
    accent = (spec.get("palette") or {}).get("accent", "#E6C478")
    act_fs = max(30, round(min(W * 0.088, H * 0.115)))
    btn_fs = max(15, round(W * 0.032))
    sc = W / 1080.0
    css = (f"#cta{{position:absolute;inset:0;z-index:32;display:grid;place-items:center;pointer-events:none}}"
           f"#cta .cta-mask{{position:absolute;inset:0;opacity:0;"
           f"background:radial-gradient(ellipse at 50% 46%,rgba(10,11,14,.82) 0%,rgba(7,8,10,.95) 70%)}}"
           f"#cta .cta-lock{{position:relative;display:flex;flex-direction:column;align-items:center;"
           f"gap:{round(H*0.045)}px;max-width:{round(W*0.9)}px;text-align:center}}"
           f"#cta .cta-act{{margin:0;color:#F7F3E9;font-weight:700;line-height:1.12;"
           f"font-size:{act_fs}px;letter-spacing:.01em;white-space:nowrap}}"
           f"#cta .cta-w{{display:inline-block;white-space:nowrap;will-change:transform,opacity,filter}}"
           f"#cta .cta-btn{{display:inline-block;padding:{round(H*0.022)}px {round(W*0.055)}px;border-radius:999px;"
           f"background:{accent};color:#14110A;font-weight:700;font-size:{btn_fs}px;line-height:1;"
           f"letter-spacing:.02em;white-space:nowrap;transform-origin:50% 50%;"
           f"box-shadow:0 {round(6*sc)}px {round(22*sc)}px rgba(0,0,0,.42)}}")
    if kind == "cta-close":
        chunks = _cta_chunks(tx["action"]) or [tx["action"]]
        spans = "".join(f'<span class="cta-w">{_html_esc(c)}</span>' for c in chunks)
        html = (f'<div id="cta" class="clip" data-start="{s}" data-duration="{dur}">'
                f'<div class="cta-mask"></div><div class="cta-lock">'
                f'<h2 class="cta-act">{spans}</h2>'
                f'<span class="cta-btn">{_html_esc(tx["button"])}</span></div></div>')
        js = (f'  tl.fromTo("#cta .cta-mask",{{opacity:0}},{{opacity:1,duration:0.5,ease:"power2.out"}},{s});\n')
        for i, _c in enumerate(chunks):
            at = round(s + 0.06 + i * 0.1, 3)
            js += (f'  tl.fromTo("#cta .cta-w:nth-child({i+1})",'
                   f'{{opacity:0,y:{round(H*0.028)},filter:"blur(8px)"}},'
                   f'{{opacity:1,y:0,filter:"blur(0px)",duration:0.62,ease:"power3.out"}},{at});\n')
        js += (f'  tl.fromTo("#cta .cta-btn",{{opacity:0,scale:0.82}},'
               f'{{opacity:1,scale:1,duration:0.6,ease:"back.out(1.6)"}},{round(s + 0.72, 3)});\n')
    elif kind == "cta-lockup":
        # 官方 cta-lockup DNA: 行动语 → 胶囊过冲 → 微文案 三段落定(IN 1.6s) + 极缓漂移 hold
        css += (f"#cta .cl-micro{{color:#B4AE9F;font-size:{max(13, round(W*0.026))}px;letter-spacing:.04em}}")
        html = (f'<div id="cta" class="clip" data-start="{s}" data-duration="{dur}">'
                f'<div class="cta-mask"></div><div class="cta-lock">'
                f'<h2 class="cta-act">{_html_esc(tx["action"])}</h2>'
                f'<span class="cta-btn">{_html_esc(tx["button"])}</span>'
                f'<span class="cl-micro">{_html_esc(tx["microcopy"])}</span></div></div>')
        js = (f'  tl.fromTo("#cta .cta-mask",{{opacity:0}},{{opacity:1,duration:0.5,ease:"power2.out"}},{s});\n'
              f'  tl.fromTo("#cta .cta-act",{{opacity:0,y:{round(H*0.03)},filter:"blur(6px)"}},'
              f'{{opacity:1,y:0,filter:"blur(0px)",duration:0.6,ease:"power3.out"}},{round(s + 0.05, 3)});\n'
              f'  tl.fromTo("#cta .cta-btn",{{opacity:0,scale:0.8}},'
              f'{{opacity:1,scale:1,duration:0.55,ease:"back.out(1.5)"}},{round(s + 0.72, 3)});\n'
              f'  tl.fromTo("#cta .cl-micro",{{opacity:0,y:{round(H*0.012)}}},'
              f'{{opacity:1,y:0,duration:0.45,ease:"power2.out"}},{round(s + 1.18, 3)});\n'
              # HOLD 弹性: 极缓漂移(留给长片尾, 短窗口自动压缩)
              f'  tl.to("#cta .cta-lock",{{y:-{round(H*0.006)},duration:{round(max(dur - 1.8, 0.3), 3)},'
              f'ease:"sine.inOut"}},{round(s + 1.65, 3)});\n')
    elif kind == "logo-sting":
        # 官方 logo-sting DNA: 品牌名 scale 1.15→1 砸定(expo.out) + 冲击触发光环扩散 + 一帧白闪, 之后静止
        css += (f"#cta .ls-mark{{position:relative;color:#FBF7EE;font-weight:800;letter-spacing:.04em;"
                f"font-size:{max(34, round(min(W*0.14, H*0.10)))}px;line-height:1.1;transform-origin:50% 50%;"
                f"text-shadow:0 {round(4*W/1080)}px {round(22*W/1080)}px rgba(0,0,0,.5)}}"
                f"#cta .ls-ring{{position:absolute;left:50%;top:50%;width:{round(min(W,H)*0.34)}px;"
                f"height:{round(min(W,H)*0.34)}px;margin:{round(-min(W,H)*0.17)}px 0 0 {round(-min(W,H)*0.17)}px;"
                f"border-radius:50%;border:{max(2, round(W*0.005))}px solid {accent};opacity:0;"
                f"transform-origin:50% 50%;pointer-events:none}}"
                f"#cta .ls-flash{{position:absolute;inset:0;background:#FFFFFF;opacity:0;pointer-events:none}}")
        html = (f'<div id="cta" class="clip" data-start="{s}" data-duration="{dur}">'
                f'<div class="cta-mask"></div><div class="cta-lock">'
                f'<span class="ls-mark">{_html_esc(tx["brand"][:16])}</span>'
                f'<span class="ls-ring"></span><span class="ls-flash"></span></div></div>')
        js = (f'  tl.fromTo("#cta .cta-mask",{{opacity:0}},{{opacity:1,duration:0.4,ease:"power2.out"}},{s});\n'
              f'  tl.fromTo("#cta .ls-mark",{{scale:1.15,opacity:0}},'
              f'{{scale:1,opacity:1,duration:0.55,ease:"expo.out"}},{round(s + 0.1, 3)});\n'
              f'  tl.set("#cta .ls-ring",{{opacity:0.92,scale:0.6}},{round(s + 0.65, 3)});\n'
              f'  tl.to("#cta .ls-ring",{{scale:2.4,opacity:0,duration:0.6,ease:"power3.out"}},{round(s + 0.65, 3)});\n'
              f'  tl.set("#cta .ls-flash",{{opacity:0.85}},{round(s + 0.65, 3)});\n'
              f'  tl.set("#cta .ls-flash",{{opacity:0}},{round(s + 0.68, 3)});\n')
    else:   # social-card
        f3 = ["每日更新", "AI 制作", "三分钟讲清"]
        stars = "".join('<span class="sp-star">★</span>' for _ in range(5))
        feats = "".join(f'<span class="sp-feat">{_html_esc(x)}</span>' for x in f3)
        css += (f"#cta .sp-brand{{color:#F7F3E9;font-weight:800;font-size:{max(20, round(W*0.046))}px;"
                f"letter-spacing:.04em}}"
                f"#cta .sp-stars{{display:flex;gap:{round(10*sc)}px;color:{accent};"
                f"font-size:{max(18, round(W*0.042))}px;line-height:1}}"
                f"#cta .sp-star{{display:inline-block;will-change:transform}}"
                f"#cta .sp-proof{{color:#CFC9BB;font-size:{max(15, round(W*0.03))}px}}"
                f"#cta .sp-feats{{display:flex;gap:{round(18*sc)}px;flex-wrap:wrap;justify-content:center}}"
                f"#cta .sp-feat{{color:#9D9789;font-size:{max(13, round(W*0.026))}px}}")
        html = (f'<div id="cta" class="clip" data-start="{s}" data-duration="{dur}">'
                f'<div class="cta-mask"></div><div class="cta-lock">'
                f'<div class="sp-brand">{_html_esc(tx["brand"][:16])}</div>'
                f'<div class="sp-stars">{stars}</div>'
                f'<div class="sp-proof">{_html_esc(tx["proof"][:26])}</div>'
                f'<div class="sp-feats">{feats}</div>'
                f'<span class="cta-btn">{_html_esc(tx["button"])}</span></div></div>')
        js = (f'  tl.fromTo("#cta .cta-mask",{{opacity:0}},{{opacity:1,duration:0.5,ease:"power2.out"}},{s});\n'
              f'  tl.fromTo("#cta .sp-brand",{{opacity:0,y:{round(H*0.016)}}},'
              f'{{opacity:1,y:0,duration:0.4,ease:"power2.out"}},{round(s + 0.25, 3)});\n')
        for i in range(5):
            js += (f'  tl.fromTo("#cta .sp-star:nth-child({i+1})",{{opacity:0,scale:0.4}},'
                   f'{{opacity:1,scale:1,duration:0.3,ease:"back.out(2.4)"}},{round(s + 0.55 + i * 0.08, 3)});\n')
        js += (f'  tl.fromTo("#cta .sp-proof",{{opacity:0,y:{round(H*0.012)}}},'
               f'{{opacity:1,y:0,duration:0.35,ease:"power2.out"}},{round(s + 1.05, 3)});\n')
        for i in range(3):
            js += (f'  tl.fromTo("#cta .sp-feat:nth-child({i+1})",{{opacity:0,y:{round(H*0.014)}}},'
                   f'{{opacity:1,y:0,duration:0.4,ease:"power2.out"}},{round(s + 1.3 + i * 0.15, 3)});\n')
        js += (f'  tl.fromTo("#cta .cta-btn",{{opacity:0,scale:0.85}},'
               f'{{opacity:1,scale:1,duration:0.45,ease:"back.out(1.8)"}},{round(s + 1.95, 3)});\n')
    # 退出: 挂内层 + 终点硬杀(规约), 末 0.35s 淡出
    js += (f'  tl.to("#cta .cta-lock",{{opacity:0,duration:0.3,ease:"power2.in"}},{round(e - 0.34, 3)});\n'
           f'  tl.set("#cta .cta-lock",{{opacity:0}},{round(e, 3)});\n')
    return css, html, js

def _hf_data_parts(W: int, H: int, start: float, dur: float, value: str, unit: str,
                   label: str, accent: str):
    """官方 number-pop-in DNA → 数字逐字弹入(模糊上浮落定, back.out(1.8), stagger .055)。
    位置中上部(避开底部字幕带与顶部标识); z-19 在字幕层之下语义无害(几何不重叠)。"""
    if not value:
        return "", "", ""
    chars = list(str(value))
    spans = "".join(f'<span class="np-c">{_html_esc(c)}</span>' for c in chars)
    uni = f'<small class="np-u">{_html_esc(unit)}</small>' if (unit or "").strip() else ""
    lab = f'<div class="np-lab">{_html_esc(label)}</div>' if (label or "").strip() else ""
    fs = max(48, round(W * 0.145))
    us = max(20, round(W * 0.058))
    css = (f"#npop{{position:absolute;left:0;right:0;top:{round(H*0.19)}px;display:flex;"
           f"flex-direction:column;align-items:center;gap:{round(H*0.012)}px;z-index:19;pointer-events:none}}"
           f"#npop .np-lab{{color:#BDB7A8;font-size:{max(14, round(W*0.032))}px;letter-spacing:.06em;opacity:0}}"
           f"#npop .np-row{{display:inline-flex;align-items:baseline;gap:{round(W*0.006)}px;"
           f"background:#0B0C0F;border-radius:{round(W*0.022)}px;"
           f"padding:{round(H*0.008)}px {round(W*0.032)}px;"
           f"font-family:'Microsoft YaHei',sans-serif}}"
           f"#npop .np-c{{display:inline-block;color:{accent};font-weight:800;font-size:{fs}px;line-height:0.94;"
           f"letter-spacing:-.02em;text-shadow:0 {round(4*W/1080)}px {round(18*W/1080)}px rgba(0,0,0,.55);"
           f"will-change:transform,opacity,filter}}"
           f"#npop .np-u{{margin-left:{round(W*0.004)}px;color:#CFC9BB;font-weight:700;font-size:{us}px;"
           f"display:inline-block;will-change:transform,opacity,filter}}")
    html = (f'<div id="npop" class="clip" data-start="{round(start, 3)}" data-duration="{round(dur, 3)}">'
            f'{lab}<div class="np-row">{spans}{uni}</div></div>')
    js = ""
    if lab:
        js += (f'  tl.fromTo("#npop .np-lab",{{opacity:0,y:8}},'
               f'{{opacity:1,y:0,duration:0.3,ease:"power2.out"}},{round(start, 3)});\n')
    js += (f'  tl.fromTo("#npop .np-c, #npop .np-u",'
           f'{{opacity:0,y:"24px",scale:0.82,filter:"blur(8px)"}},'
           f'{{opacity:1,y:"0px",scale:1,filter:"blur(0px)",duration:0.34,stagger:0.055,'
           f'ease:"back.out(1.8)"}},{round(start + 0.08, 3)});\n')
    js += (f'  tl.to("#npop .np-row, #npop .np-lab",{{opacity:0,duration:0.3,ease:"power2.in"}},'
           f'{round(start + dur - 0.32, 3)});\n'
           f'  tl.set("#npop .np-row, #npop .np-lab",{{opacity:0}},{round(start + dur, 3)});\n')
    return css, html, js

def _first_number(cues: list) -> tuple:
    """从字幕文本提取第一个数字及其单位(讲数据时自动点题)"""
    for _s, _d, c, _i in (cues or []):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(%|％|万|亿|倍|分钟|秒|小时|天|元|美元|个|款|项)?", c or "")
        if m:
            unit = (m.group(2) or "").replace("％", "%")
            return m.group(1), unit
    return "", ""



def _hf_bgb_parts(kind: str, windows: list, W: int, H: int, spec: dict):
    """官方 aurora-drift / mesh-gradient-bg DNA → 黑底段背景(video:false 段窗口铺动效背景)。
    aurora=三色光球(blur 12%短边, screen 混合, 相位代理 onUpdate 驱动, 循环不变量 2π→0) + 深底 + 暗角;
    mesh=三层径向光斑(CSS 变量补间 sine.inOut 单程) 柔和呼吸。z-3: 压黑底视频, 让位字幕(20)/信息条(21)。
    返回 (css, html, js); 无黑底段窗口或 kind=none 返回空"""
    if kind not in ("aurora", "mesh") or not windows:
        return "", "", ""
    pal = spec.get("palette") or {}
    pc = _parse_css_color(pal.get("accent", "#B6A884"))
    ar, ag, ab = (pc[:3] if pc else (182, 168, 132))
    # 合并相邻黑底窗口(间隙≤0.35s): 连续黑底段只铺一整段, 免去段间脉冲+减少节点
    merged = []
    for s, e in sorted(windows):
        if merged and s - merged[-1][1] <= 0.35:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    windows = [(s, e) for s, e in merged]
    css = html = js = ""
    if kind == "aurora":
        bl = max(20, round(min(W, H) * 0.12))
        css = (f".bgb{{position:absolute;inset:0;z-index:3;overflow:hidden;pointer-events:none}}"
               f".bgb-in{{position:absolute;inset:0;opacity:0}}"
               f".bgb-base{{position:absolute;inset:0;background:"
               f"radial-gradient(ellipse at 50% 42%,rgba({ar},{ag},{ab},.13) 0%,#05070D 68%)}}"
               f".bgb-b{{position:absolute;border-radius:50%;mix-blend-mode:screen;"
               f"filter:blur({bl}px) saturate(.84);will-change:transform}}"
               f".bgb-a{{width:{round(W*0.76)}px;height:{round(H*0.72)}px;left:{round(-W*0.18)}px;top:{round(-H*0.22)}px;"
               f"opacity:.60;background:radial-gradient(ellipse at center,rgba({min(255,ar+58)},{min(255,ag+58)},{min(255,ab+58)},.94) 0%,rgba({ar},{ag},{ab},.46) 48%,transparent 76%)}}"
               f".bgb-b1{{width:{round(W*0.84)}px;height:{round(H*0.78)}px;right:{round(-W*0.28)}px;top:{round(-H*0.02)}px;"
               f"opacity:.48;background:radial-gradient(ellipse at center,rgba(125,211,252,.88) 0%,rgba(56,189,248,.40) 52%,transparent 78%)}}"
               f".bgb-c{{width:{round(W*0.72)}px;height:{round(H*0.74)}px;left:{round(W*0.18)}px;bottom:{round(-H*0.34)}px;"
               f"opacity:.42;background:radial-gradient(ellipse at center,rgba(196,181,253,.86) 0%,rgba(167,139,250,.36) 50%,transparent 76%)}}"
               f".bgb-vig{{position:absolute;inset:0;background:radial-gradient(ellipse at center,transparent 35%,rgba(0,0,0,.55) 100%)}}")
        for i, (s, e) in enumerate(windows):
            dur = round(max(e - s, 0.8), 3)
            html += (f'<div class="bgb clip" id="bgb{i}" data-start="{round(s, 3)}" data-duration="{dur}">'
                     f'<div class="bgb-in"><div class="bgb-base"></div><div class="bgb-b bgb-a"></div>'
                     f'<div class="bgb-b bgb-b1"></div><div class="bgb-b bgb-c"></div>'
                     f'<div class="bgb-vig"></div></div></div>')
            # 淡入/淡出挂内层 .bgb-in(clip 元素本体零 tween), 退出后硬杀 — gsap_exit_missing_hard_kill 规约
            js += (f'  tl.fromTo("#bgb{i} .bgb-in",{{opacity:0}},{{opacity:1,duration:0.45,ease:"power2.out"}},{round(s, 3)});\n'
                   f'  tl.to("#bgb{i} .bgb-in",{{opacity:0,duration:0.45,ease:"power2.in"}},{round(e - 0.45, 3)});\n'
                   f'  tl.set("#bgb{i} .bgb-in",{{opacity:0}},{round(e, 3)});\n'
                   f'  var st{i}={{p:0}};\n'
                   f'  var P{i}=[["#bgb{i} .bgb-a",{round(W*0.046,2)},{round(H*0.032,2)},0],'
                   f'["#bgb{i} .bgb-b1",{round(W*0.041,2)},{round(H*0.038,2)},2.0944],'
                   f'["#bgb{i} .bgb-c",{round(W*0.036,2)},{round(H*0.030,2)},4.1888]];\n'
                   f'  tl.to(st{i},{{p:6.2832,duration:{dur},ease:"none",onUpdate:function(){{'
                   f'var ph=st{i}.p>=6.2832?0:st{i}.p;'
                   f'for(var k=0;k<3;k++){{var q=P{i}[k];'
                   f'gsap.set(q[0],{{x:Math.sin(ph+q[3])*q[1],y:Math.sin(ph+q[3]+1.5708)*q[2]}})}}}}}},'
                   f'{round(s, 3)});\n')
    else:  # mesh
        bl = max(18, round(H * 0.055))
        css = (f".bgb{{position:absolute;inset:0;z-index:3;overflow:hidden;pointer-events:none}}"
               f".bgb-in{{position:absolute;inset:0;opacity:0}}"
               f".bgb-m{{position:absolute;inset:-22%;filter:blur({bl}px);opacity:.55;"
               f"background:radial-gradient(circle at var(--x1,20%) var(--y1,30%),"
               f"rgba({min(255,ar+40)},{min(255,ag+40)},{min(255,ab+40)},.9) 0 18%,transparent 34%),"
               f"radial-gradient(circle at var(--x2,76%) var(--y2,52%),rgba(244,114,182,.75) 0 16%,transparent 34%),"
               f"radial-gradient(circle at var(--x3,46%) var(--y3,78%),rgba(52,211,153,.7) 0 18%,transparent 34%)}}")
        for i, (s, e) in enumerate(windows):
            dur = round(max(e - s, 0.8), 3)
            html += (f'<div class="bgb clip" id="bgb{i}" data-start="{round(s, 3)}" data-duration="{dur}">'
                     f'<div class="bgb-in"><div class="bgb-m"></div></div></div>')
            js += (f'  tl.fromTo("#bgb{i} .bgb-in",{{opacity:0}},{{opacity:1,duration:0.45,ease:"power2.out"}},{round(s, 3)});\n'
                   f'  tl.to("#bgb{i} .bgb-in",{{opacity:0,duration:0.45,ease:"power2.in"}},{round(e - 0.45, 3)});\n'
                   f'  tl.set("#bgb{i} .bgb-in",{{opacity:0}},{round(e, 3)});\n'
                   f'  tl.fromTo("#bgb{i} .bgb-m",'
                   f'{{"--x1":"18%","--y1":"28%","--x2":"78%","--y2":"46%","--x3":"44%","--y3":"80%"}},'
                   f'{{"--x1":"34%","--y1":"42%","--x2":"62%","--y2":"60%","--x3":"58%","--y3":"64%",'
                   f'duration:{dur},ease:"sine.inOut"}},{round(s, 3)});\n')
    return css, html, js


def _hf_trans_parts(kind: str, bounds: list, W: int, H: int, accent: str = "#B6A884"):
    """转场统一分发: 四款共用「边界贴片」挂载(每切点一个 clip, z-14 压视频不压文字)。
    flash=官方 editorial-flash-overlay DNA; dip-black/sweep/wipe=编辑传统三件套(全比例化)"""
    if kind == "flash":
        return _hf_flash_parts(bounds, W, H)
    if not bounds:
        return "", "", ""
    css = html = js = ""
    if kind == "dip-black":
        css = ".tfd{position:absolute;inset:0;background:#050507;opacity:0;z-index:14;pointer-events:none}"
        html = "".join(f'<div class="tfd clip" id="tfd{i}" data-start="{round(b - 0.42, 3)}" '
                       f'data-duration="0.92"></div>' for i, b in enumerate(bounds))
        for i, b in enumerate(bounds):
            js += (f'  tl.fromTo("#tfd{i}",{{opacity:0}},{{opacity:0.94,duration:0.34,ease:"power2.in"}},{round(b - 0.34, 3)});\n'
                   f'  tl.to("#tfd{i}",{{opacity:0,duration:0.46,ease:"power2.out"}},{round(b + 0.06, 3)});\n')
    elif kind == "sweep":
        css = (".tfs{position:absolute;inset:0;overflow:hidden;z-index:14;pointer-events:none}"
               ".tfs i{position:absolute;top:-18%;bottom:-18%;left:0;width:38%;"
               "background:linear-gradient(90deg,transparent,rgba(255,244,214,.34) 22%,"
               "rgba(255,255,255,.92) 50%,rgba(255,214,140,.30) 78%,transparent);"
               "mix-blend-mode:screen;transform:skewX(-18deg);will-change:transform}")
        html = "".join(f'<div class="tfs clip" id="tfs{i}" data-start="{round(b - 0.45, 3)}" '
                       f'data-duration="0.95"><i></i></div>' for i, b in enumerate(bounds))
        for i, b in enumerate(bounds):
            js += (f'  tl.fromTo("#tfs{i} i",{{xPercent:-140}},'
                   f'{{xPercent:340,duration:0.9,ease:"power2.inOut"}},{round(b - 0.45, 3)});\n')
    elif kind == "wipe":
        css = (".tfw{position:absolute;inset:0;z-index:14;pointer-events:none;will-change:transform;"
               "background:linear-gradient(100deg," + accent + " 0 86%,rgba(255,255,255,.20) 93%," + accent + " 97%)}")
        html = "".join(f'<div class="tfw clip" id="tfw{i}" data-start="{round(b - 0.5, 3)}" '
                       f'data-duration="1.1"></div>' for i, b in enumerate(bounds))
        for i, b in enumerate(bounds):
            js += (f'  tl.fromTo("#tfw{i}",{{xPercent:-101}},{{xPercent:0,duration:0.5,ease:"power2.in"}},{round(b - 0.5, 3)});\n'
                   f'  tl.to("#tfw{i}",{{xPercent:101,duration:0.5,ease:"power2.out"}},{round(b, 3)});\n')
    return css, html, js


def _hf_flash_parts(bounds: list, W: int, H: int):
    """官方 editorial-flash-overlay DNA: 三层暖调闪光 — wash 纯色爆闪(0.04s power4.in) / core 径向白核
    scale .86→1 / sweep 斜带 xPercent 扫过, hit=切点时刻, 衰减 0.18-0.34s; 每切点一个贴片 clip
    z-14 压视频不压文字(caps20/lt21 之上保持可读)"""
    if not bounds:
        return "", "", ""
    css = (".tf{position:absolute;inset:-12%;pointer-events:none;z-index:14;overflow:hidden}"
           ".tf i{position:absolute;inset:0;display:block;opacity:0;will-change:opacity,transform}"
           ".tf .w{background:rgb(255,253,250)}"
           ".tf .c{inset:-18%;background:radial-gradient(ellipse 68% 92% at 37% 47%,#FFFFFF 0%,"
           "rgba(255,255,255,.98) 20%,rgba(255,253,250,.86) 45%,rgba(255,174,105,.32) 67%,transparent 86%);"
           "mix-blend-mode:screen;transform-origin:38% 48%}"
           ".tf .s{inset:-28%;background:linear-gradient(108deg,transparent 21%,rgba(255,174,105,.10) 38%,"
           "rgba(255,253,250,.90) 49%,rgba(255,255,255,.98) 53%,rgba(183,218,255,.24) 62%,transparent 79%);"
           "mix-blend-mode:screen}")
    html = "".join(
        f'<div class="tf clip" id="tf{i}" data-start="{round(b - 0.24, 3)}" data-duration="0.62">'
        f'<i class="w"></i><i class="c"></i><i class="s"></i></div>' for i, b in enumerate(bounds))
    js = ""
    for i, b in enumerate(bounds):
        hit = round(b, 3)
        js += (f'  tl.fromTo("#tf{i} .w",{{opacity:0}},{{opacity:0.92,duration:0.04,ease:"power4.in"}},{round(hit - 0.04, 3)});\n'
               f'  tl.fromTo("#tf{i} .c",{{opacity:0,scale:0.86,rotation:-5}},'
               f'{{opacity:1,scale:1,rotation:-5,duration:0.05,ease:"power4.in"}},{round(hit - 0.05, 3)});\n'
               f'  tl.fromTo("#tf{i} .s",{{opacity:0,xPercent:-12}},'
               f'{{opacity:0.9,xPercent:8,duration:0.04,ease:"power4.in"}},{hit});\n'
               f'  tl.to("#tf{i} .w",{{opacity:0,duration:0.18,ease:"power3.out"}},{round(hit + 0.04, 3)});\n'
               f'  tl.to("#tf{i} .c",{{opacity:0,scale:1.18,duration:0.34,ease:"power2.out"}},{round(hit + 0.04, 3)});\n'
               f'  tl.to("#tf{i} .s",{{opacity:0,xPercent:28,duration:0.3,ease:"power2.out"}},{round(hit + 0.04, 3)});\n')
    return css, html, js


def _normalize_menu(m: dict) -> dict:
    """菜单规范化: ai/random/具体id 原样保留(解析推迟到精剪执行时), cfg 存这份原始意图"""
    m = m if isinstance(m, dict) else {}
    def _v(v, allowed):
        v = str(v or "").strip()
        return v if v in allowed else allowed[0]
    return {"title": _v(m.get("title"), ["ai", "random"] + list(_HF_TITLE_MENU)),
            "caption": _v(m.get("caption"), ["ai", "random"] + list(_HF_CAPTION_MENU)),
            "atmo": _v(m.get("atmo"), ["ai", "none", "vignette", "grain", "light-leak", "spotlight"]),
            "trans": _v(m.get("trans"), ["ai", "random", "none", "flash", "dip-black", "sweep", "wipe"]),
            "bgb": _v(m.get("bgb"), ["ai", "random", "none", "aurora", "mesh"]),
            "lt": _v(m.get("lt"), ["ai", "random", "none"] + list(_HF_LT_MENU)),
            "cta": _v(m.get("cta"), ["ai", "random", "none"] + list(_HF_CTA_MENU)),
            "data": _v(m.get("data"), ["ai", "random", "none"] + list(_HF_DATA_MENU)),
            "cmp": _v(m.get("cmp"), ["ai", "random", "none"] + list(_HF_CMP_MENU)),
            "list": _v(m.get("list"), ["ai", "random", "none"] + list(_HF_LIST_MENU)),
            "top_mark": bool(m.get("top_mark"))}

_HF_STYLE_MENU = {"gold": "黑金简约(暗底金字,沉稳贵气,金融/奢侈品/高端感)",
                  "magazine": "杂志编辑感(高对比黑底+亮强调+编辑装饰,观点输出/锐评)",
                  "variety": "综艺花字(无底大字重强调+描边,娱乐搞笑/情绪激烈)",
                  "minimal": "极简白(白底黑字克制排版,知识科普/冷静叙述)"}

def _ai_menu(topic: str, segs: list) -> dict:
    """🤖 AI 选配: 一把为 模板/标题/字幕/氛围 各挑一款, 按主题与段落内容"""
    cands = {"style": _HF_STYLE_MENU, "title": _HF_TITLE_MENU, "caption": _HF_CAPTION_MENU,
             "atmo": {k: v for k, v in _HF_ATMO_MENU.items() if k != "grain"},
             "lt": {"none": "不加信息条(画面最简洁)", **_HF_LT_MENU},
             "trans": {"none": "不加转场(保留硬切,最稳)",
                       **{k: v for k, v in _HF_TRANS_MENU.items() if k != "none"}},
             "bgb": {"none": "黑底段不加背景(最简)",
                     **{k: v for k, v in _HF_BGB_MENU.items() if k != "none"}},
             "cta": {"none": "不加片尾卡(直接结束)",
                     **{k: v for k, v in _HF_CTA_MENU.items()}},
             "data": {"none": "不加数据数字卡",
                      **{k: v for k, v in _HF_DATA_MENU.items()}},
             "cmp": {"none": "不加前后对比条",
                     **{k: v for k, v in _HF_CMP_MENU.items()}},
             "list": {"none": "不加要点清单卡"}}   # checklist 暂缓: HF check 对比度口径对淡入前元素误判(见笔记)
    fam_desc = "\n".join(
        f"[{fam}] 候选:\n" + "\n".join(f"  - {k}: {d}" for k, d in cd.items())
        for fam, cd in cands.items())
    txt = llm("你是短视频包装导演, 为一条口播视频挑选包装组件。只输出一个 JSON 对象，不要解释、不要代码块围栏。",
              f"按视频内容从每个家族各挑一款最合适的。\n{fam_desc}\n"
              f"视频主题: {(topic or '(无)')[:80]}\n"
              "段落抽样(每段前18字):\n" + "\n".join(segs[:6]) +
              f'输出: {{"style":"id","title":"id","caption":"id","atmo":"id","lt":"id","trans":"id","bgb":"id",'
              f'"cta":"id","data":"id","cmp":"id","list":"id",'
              f'"why":{{"style":"≤16字理由","title":"≤16字理由","caption":"≤16字理由","atmo":"≤16字理由","lt":"≤16字理由","trans":"≤16字理由","bgb":"≤16字理由","cta":"≤16字理由","data":"≤16字理由","cmp":"≤16字理由","list":"≤16字理由"}}}}')
    m = re.search(r"\{.*\}", txt, re.S)
    out, why = {}, {}
    j = json.loads(m.group(0)) if m else {}
    for fam, cd in cands.items():
        pick = str(j.get(fam) or "")
        if pick not in cd:
            pick = random.choice(list(cd))
        out[fam] = pick
        why[fam] = re.sub(r"\s+", "", str((j.get("why") or {}).get(fam) or ""))[:18]
    return {"picks": out, "why": why}

def _shade_for_white(hex_color: str, min_ratio: float = 4.6) -> str:
    """把强调色压暗到与白字对比达标(高亮字幕底色用)"""
    c = _parse_css_color(hex_color)
    if not c:
        return "#B3122E"
    for f in (0.55, 0.42, 0.32, 0.24, 0.18):
        dark = tuple(round(x * f) for x in c[:3])
        if _wcag_ratio((255, 255, 255), dark) >= min_ratio:
            return "#%02X%02X%02X" % dark
    return "#1A1A1E"

def _readability_guard(spec) -> bool:
    """字幕文字 vs 自身底色的确定性护栏: 对黑/白两种视频底色的最坏合成情况都须 ≥4.5:1,
    否则把文字钳到黑或白中更优者。返回是否发生修正。"""
    bg = _parse_css_color(spec["palette"]["bg"])
    fg = _parse_css_color(spec["palette"]["text"])
    if not bg or not fg:
        return False
    worst = []
    for base in ((0, 0, 0), (255, 255, 255)):
        comp_bg = tuple(round(bg[i] * bg[3] + base[i] * (1 - bg[3])) for i in range(3))
        comp_fg = tuple(round(fg[i] * fg[3] + comp_bg[i] * (1 - fg[3])) for i in range(3))
        worst.append(_wcag_ratio(comp_fg, comp_bg))
    if min(worst) >= 4.5:
        return False
    cands = []
    for t in ((10, 10, 10), (255, 255, 255)):
        r = []
        for base in ((0, 0, 0), (255, 255, 255)):
            comp_bg = tuple(round(bg[i] * bg[3] + base[i] * (1 - bg[3])) for i in range(3))
            r.append(_wcag_ratio(t, comp_bg))
        cands.append((min(r), t))
    cands.sort(reverse=True)
    best = cands[0][1]
    spec["palette"]["text"] = "#0A0A0A" if best[0] < 128 else "#FFFFFF"
    return True

def _merge_spec(spec: dict, ov: dict):
    """LLM 覆盖项白名单合并, 越界值一律丢弃/钳制"""
    pal = ov.get("palette") or {}
    if isinstance(pal, dict):
        for k in ("bg", "text", "accent", "border"):
            v = str(pal.get(k) or "").strip()
            if _ok_color(v):
                spec["palette"][k] = v
    s = ov.get("subtitle") or {}
    if isinstance(s, dict):
        if s.get("shape") in ("pill", "bar", "bare"):
            spec["subtitle"]["shape"] = s["shape"]
        try:
            spec["subtitle"]["size_scale"] = min(1.6, max(0.7, float(s.get("size_scale", spec["subtitle"]["size_scale"]))))
        except Exception:
            pass
        try:
            spec["subtitle"]["weight"] = int(min(900, max(300, int(s.get("weight", spec["subtitle"]["weight"])))))
        except Exception:
            pass
        try:
            spec["subtitle"]["radius"] = int(min(24, max(0, int(s.get("radius", spec["subtitle"]["radius"])))))
        except Exception:
            pass
    f = str(ov.get("font") or "").strip()
    if f in _HF_FONTS:
        spec["font"] = f
    ex = ov.get("extras")
    if isinstance(ex, list):
        keep = [x for x in ex if x in _HF_EXTRA_SET]
        spec["extras"] = keep
    wt = re.sub(r"\s+", "", str(ov.get("watermark_text") or ""))[:12]
    if wt and "watermark" in spec["extras"]:
        spec["watermark_text"] = wt
    return _readability_guard(spec)

_TOP_MARK_NOTE = ("\n合规标识位: 顶部中央将常驻渲染小字「本视频由AI合成」(高度约画布宽2.4%), "
                  "你的设计不得占用顶部中央区域, 右上角标/四角裁切标记等均需避让。")

def _design_spec(style, prompt: str, man: dict, top_mark: bool = False):
    """模板规格(None=无模板·自由设计) + 可选 LLM 提示词定制 → (spec, 来源说明)"""
    tpl = _HF_TEMPLATES.get(style or "", None)
    if tpl:
        spec = json.loads(json.dumps(tpl["spec"]))
        base_note = f"模板「{tpl['name']}」"
    else:
        spec = {"font": "Microsoft YaHei",
                "palette": {"bg": "rgba(11,11,16,.74)", "text": "#FFFFFF",
                            "accent": "#FFD400", "border": "transparent"},
                "subtitle": {"shape": "bar", "size_scale": 1.0, "weight": 800, "radius": 0},
                "extras": []}
        base_note = "无模板·自由设计"
    if not (prompt or "").strip() and tpl:
        return spec, f"{base_note}默认"
    try:
        n_items = len([m for m in man["items"] if m.get("role") == "item"])
        txt = llm(
            "你是竖屏口播视频的包装设计师。只输出一个 JSON 对象，不要解释、不要代码块围栏。",
            "按设计要求输出设计规格 JSON。字段契约（仅限这些字段, 取值合规）:\n"
            '{"palette":{"bg":"css色","text":"css色","accent":"css色","border":"css色"},'
            '"subtitle":{"shape":"pill|bar|bare","size_scale":0.7~1.6,"weight":300~900,"radius":0~24},'
            '"font":"Microsoft YaHei|SimSun|KaiTi",'
            '"extras":["cut-marks","progress-bar","num-badge","watermark"] 的子集,'
            '"watermark_text":"不超过12字, 仅当含 watermark 才填"}\n'
            + (f"当前模板「{tpl['name']}」默认规格: {json.dumps(spec, ensure_ascii=False)}\n"
               if tpl else
               "无固定模板: 自行决定整套配色/字幕形态/装饰, 贴合用户要求; 保持竖屏口播可读性\n")
            + f"视频信息: 总时长 {man['total']}s, {n_items} 个段落, 画布 {man['w']}x{man['h']}\n"
            f"用户设计要求: {prompt.strip()}"
            + (_TOP_MARK_NOTE if top_mark else ""))
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            raise ValueError("响应中无 JSON")
        fixed = _merge_spec(spec, json.loads(m.group(0)))
        note = f"{base_note}" + ("（LLM）" if tpl else "·提示词设计（LLM）")
        if fixed:
            note += "· 字幕文字对比度已自动修正"
        return spec, note
    except Exception as e:
        return spec, f"{base_note}（提示词解析失败已回退: {type(e).__name__}）"

def _hf_versions(d: Path):
    """扫描 output\精剪vN.mp4 + hf-designs\vN.json → 版本列表(升序)"""
    out = []
    outdir = d / "output"
    designs = d / "hf-designs"
    if not outdir.exists():
        return out
    pat = re.compile(r"^精剪v(\d+)\.mp4$")
    for f in outdir.iterdir():
        m = pat.match(f.name)
        if f.is_file() and m:
            v = int(m.group(1))
            note = ""
            dj = designs / f"v{v}.json"
            if dj.exists():
                try:
                    note = json.loads(dj.read_text(encoding="utf-8")).get("note", "")
                except Exception:
                    pass
            out.append({"v": v, "note": note, "mtime": int(f.stat().st_mtime)})
    out.sort(key=lambda x: x["v"])
    return out

def _design_revise(base_spec: dict, feedback: str, man: dict, top_mark: bool = False):
    """多轮精剪: 在上一版设计规格基础上按用户反馈修订"""
    n_items = len([m for m in man["items"] if m.get("role") == "item"])
    segs = []
    i = 0
    for m in man["items"]:
        if m.get("role") != "item":
            continue
        i += 1
        t = re.sub(r"\s+", "", m.get("text", ""))[:14]
        segs.append(f"第{i}段[{m['start']:.1f}s起 {m['dur']:.1f}s]: {t}")
    txt = llm(
        "你是竖屏口播视频的包装设计师。用户对你上一版设计给出了修改反馈。"
        "只输出一个修改后的完整 JSON 设计规格对象，不要解释、不要代码块围栏。",
        "设计规格字段契约（仅限这些字段, 取值合规）:\n"
        '{"palette":{"bg":"css色","text":"css色","accent":"css色","border":"css色"},'
        '"subtitle":{"shape":"pill|bar|bare","size_scale":0.7~1.6,"weight":300~900,"radius":0~24},'
        '"font":"Microsoft YaHei|SimSun|KaiTi",'
        '"extras":["cut-marks","progress-bar","num-badge","watermark"] 的子集,'
        '"watermark_text":"不超过12字",'
        '"per_item":{"<段落号>":{"size_scale":0.7~1.6,"hide":true} 可选} — 对单个段落微调字幕(段落号从1起)}\n'
        "未提及的字段保持上一版的值，只修改反馈要求的内容。\n"
        f"上一版规格: {json.dumps(base_spec, ensure_ascii=False)}\n"
        f"段落信息({n_items}段, 总时长 {man['total']}s):\n" + "\n".join(segs) +
        f"\n用户修改反馈: {feedback.strip()}"
        + (_TOP_MARK_NOTE if top_mark else ""))
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("响应中无 JSON")
    spec = json.loads(json.dumps(base_spec))
    ov = json.loads(m.group(0))
    fixed = _merge_spec(spec, ov)
    pi = ov.get("per_item")
    if isinstance(pi, dict):
        clean = {}
        for k, v in pi.items():
            try:
                ik = int(k)
            except Exception:
                continue
            if not (1 <= ik <= n_items) or not isinstance(v, dict):
                continue
            e = {}
            try:
                sc = float(v.get("size_scale", 0))
                if 0.7 <= sc <= 1.6:
                    e["size_scale"] = round(sc, 2)
            except Exception:
                pass
            if v.get("hide") is True:
                e["hide"] = True
            if e:
                clean[str(ik)] = e
        if clean:
            spec["per_item"] = clean
        fixed = fixed or bool(clean)
    note = "反馈修订（LLM）"
    if fixed:
        note += "· 字幕文字对比度已自动修正"
    return spec, note

def _split_cues(text: str, win_start: float, win_dur: float, max_chars: int = 16, min_dur: float = 0.9):
    """段文本 → 字幕 cue [(start, dur, text)]; 标点层级切分, 字数占比估时"""
    text = re.sub(r"\s+", "", text or "")
    if not text:
        return []
    sents = [s for s in re.split(r"(?<=[。！？；!?;])", text) if s.strip()]
    parts: list[str] = []
    for s in sents:
        s = s.strip()
        if len(s) > max_chars:
            buf = ""
            for x in [x for x in re.split(r"(?<=[，、—…,:])", s) if x.strip()]:
                if buf and len(buf) + len(x) > max_chars:
                    parts.append(buf)
                    buf = x
                else:
                    buf += x
            if buf:
                parts.append(buf)
        else:
            parts.append(s)
    merged: list[str] = []
    for p in parts:
        if merged and len(merged[-1]) + len(p) <= max_chars:
            merged[-1] += p
        else:
            merged.append(p)
    total_chars = sum(len(p) for p in merged) or 1
    cues, t = [], win_start
    for i, p in enumerate(merged):
        d = win_dur * len(p) / total_chars
        if i == len(merged) - 1:
            d = win_start + win_dur - t
        if d < min_dur and cues:
            a, b, c = cues[-1]
            cues[-1] = (a, (t + d) - a, c + p)
        else:
            cues.append((t, d, p))
        t += d
    return [(round(a, 3), round(b, 3), c.strip()) for a, b, c in cues if c.strip()]

def _chunk_cjk(text: str) -> list[str]:
    """字幕组块: 拉丁词/数字串整体, 中文按2字一组(卡拉OK节奏)"""
    chunks, buf, latin = [], "", ""
    for ch in text:
        if re.match(r"[0-9A-Za-z]", ch):
            if buf:
                chunks.append(buf); buf = ""
            latin += ch
        else:
            if latin:
                chunks.append(latin); latin = ""
            buf += ch
            if len(buf) >= 2:
                chunks.append(buf); buf = ""
    if latin:
        chunks.append(latin)
    if buf:
        chunks.append(buf)
    return chunks or [text]

def _hf_caps_parts(spec: dict, cues: list, W: int, H: int, total: float):
    """官方字幕组件适配: 返回 (css, html, js)。组=字幕cue, 词=2字组块按字数占比分时间。"""
    from html import escape as _hesc
    menu = spec.get("menu") or {}
    style = menu.get("caption") or "bar"
    if style == "bar":
        return "", "", ""
    pal, sub = spec["palette"], spec["subtitle"]
    sc = W / 1920.0
    base_fs = max(24, round(64 * sc * float(sub.get("size_scale", 1.0))))
    accent = pal.get("accent", "#E6C478")
    groups, words = [], []
    for s, d, c, ii in cues:
        ov = (spec.get("per_item") or {}).get(str(ii)) or {}
        if ov.get("hide"):
            continue
        gid = len(groups)
        _k = (len(c) + 1) // 2
        groups.append({"start": round(s, 3), "end": round(s + d, 3),
                       "t0": c if len(c) <= 3 else c[:_k], "t1": "" if len(c) <= 3 else c[_k:]})
        chunks = _chunk_cjk(c)
        n = sum(len(x) for x in chunks) or 1
        t = s
        for j, ck in enumerate(chunks):
            cd = d * len(ck) / n
            end = round(s + d, 3) if j == len(chunks) - 1 else round(t + cd, 3)
            words.append({"text": ck, "start": round(t, 3), "end": end, "g": gid,
                          "first": j == 0, "i": len(words)})
            t += cd
    cap_html = ('<div id="caps">'
                + "".join(f'<div class="capg" id="capg{i}">' for i in range(len(groups)))
                + "</div>")
    css = (f"#caps{{position:absolute;left:0;right:0;bottom:{round(H*0.10)}px;height:{round(H*0.26)}px;"
           f"z-index:20;pointer-events:none}}"
           f".capg{{position:absolute;left:50%;transform:translateX(-50%);bottom:0;opacity:0;visibility:hidden;"
           f"display:flex;justify-content:center;max-width:{round(1560*sc)}px}}")
    js = ""
    if style == "pill-karaoke":
        css += (f".cappill{{background:#ECEAE8;border-radius:{round(22*sc)}px;"
                f"padding:{round(14*sc)}px {round(38*sc)}px;box-shadow:0 2px 10px rgba(0,0,0,.14);max-width:100%}}"
                f".capcopy{{display:flex;flex-wrap:wrap;justify-content:center;column-gap:{round(10*sc)}px;"
                f"font-family:'Microsoft YaHei',sans-serif;font-weight:700;font-size:{base_fs}px;"
                f"line-height:1.32;color:#6E6E76}}"
                f".capw{{display:inline-block;white-space:nowrap}}")
        # 组内词着色: 官方 DNA — 首词即激活, 后续词到点变色 (深字在浅药丸上, 恒过对比)
        for w in words:
            if w["first"]:
                continue
            js += (f'  tl.to("#capw{w["i"]}",{{color:"#17181C",duration:0.1,ease:"none"}},'
                   f'{round(max(w["start"] - 0.05, groups[w["g"]]["start"]), 3)});\n')
    elif style == "blend-difference":
        # 官方 blend-difference DNA: mix-blend-mode:difference 逐像素反色 — 根节点必须 isolation:isolate
        css = "#root{isolation:isolate}" + css
        css += (f".capg{{flex-wrap:wrap;align-items:flex-end;column-gap:{round(14*sc)}px;row-gap:{round(6*sc)}px;"
                f"padding:0 {round(50*sc)}px;mix-blend-mode:difference}}"
                f".capw{{display:inline-block;white-space:nowrap;color:#FFFFFF;"
                f"font-family:'Microsoft YaHei',sans-serif;font-weight:800;"
                f"font-size:{round(base_fs*1.1)}px;line-height:1.26}}")
    elif style == "clip-wipe":
        # 官方 clip-wipe DNA: 每词 clip-path inset 左→右擦入; 关键词变色
        css += (f".capg{{flex-wrap:wrap;align-items:flex-end;column-gap:{round(12*sc)}px;row-gap:{round(6*sc)}px;"
                f"padding:0 {round(50*sc)}px}}"
                f".capw{{display:inline-block;white-space:nowrap;color:#FFFFFF;"
                f"font-family:'Microsoft YaHei',sans-serif;font-weight:800;"
                f"font-size:{round(base_fs*1.06)}px;line-height:1.24;"
                f"text-shadow:0 2px 12px rgba(0,0,0,.5);clip-path:inset(0 100% 0 0);will-change:clip-path}}")
        for w in words:
            js += (f'  tl.fromTo("#capw{w["i"]}",{{clipPath:"inset(0 100% 0 0)"}},'
                   f'{{clipPath:"inset(0 0% 0 0)",duration:0.26,ease:"power2.out"}},{w["start"]});\n')
            if _is_emph(w["text"], words, w["g"]):
                js += (f'  tl.to("#capw{w["i"]}",{{color:"{accent}",duration:0.12,ease:"none"}},'
                       f'{round(w["start"] + 0.12, 3)});\n')
    elif style == "highlight":
        a = _shade_for_white(accent, 4.6)
        ca = _parse_css_color(a)[:3]
        b = "#%02X%02X%02X" % tuple(round(x * 0.86) for x in ca)
        css += (f".capg{{flex-wrap:wrap;align-items:flex-end;column-gap:{round(10*sc)}px;row-gap:{round(8*sc)}px;"
                f"padding:0 {round(50*sc)}px}}"
                f".capw{{position:relative;display:inline-block;white-space:nowrap;"
                f"padding:{round(6*sc)}px {round(14*sc)}px;line-height:1.18;"
                f"font-family:'Microsoft YaHei',sans-serif;font-weight:800;font-size:{round(base_fs*1.12)}px;color:#FFFFFF}}"
                f".capw .bg{{position:absolute;inset:0;background:linear-gradient(135deg,{a} 0%,{b} 100%);"
                f"border-radius:{round(10*sc)}px;box-shadow:0 6px 18px rgba(0,0,0,.28);"
                f"opacity:0;transform:scaleX(0);transform-origin:0% 50%;z-index:0}}"
                f".capw .tx{{position:relative;z-index:1;text-shadow:0 3px 10px rgba(0,0,0,.35)}}")
        for w in words:
            js += (f'  tl.fromTo("#capw{w["i"]} .bg",{{opacity:0,scaleX:0}},'
                   f'{{opacity:1,scaleX:1,duration:0.15,ease:"power2.out"}},{w["start"]});\n'
                   f'  tl.fromTo("#capw{w["i"]} .bg",{{opacity:1,scaleX:1}},'
                   f'{{opacity:0,scaleX:1.02,duration:0.1,ease:"power2.in"}},{w["end"]});\n')
    elif style == "weight-shift":
        # 官方 caption-weight-shift DNA: 每 cue 双行, 读哪行哪行粗 (700↔300 字重切换)
        # 中文适配: 中文无平滑字重梯度 → 行0 700 / 行1 400, cue 中点 tl.set 即切(焦点跳行);
        #          左对齐(居中会随字宽跳动); 雅黑 400/700 皆有真字重
        fs_w = round(base_fs * 1.02)
        css += (f".capg{{flex-direction:column;align-items:flex-start;transform:none;"
                f"left:{round(80*sc)}px;row-gap:{round(6*sc)}px}}"
                f".wtl{{font-family:'Microsoft YaHei',sans-serif;font-size:{fs_w}px;line-height:1.28;"
                f"color:#FFFFFF;text-shadow:0 2px 14px rgba(0,0,0,.55);white-space:nowrap;"
                f"font-weight:700}}"
                f".wtl.w2{{font-weight:400}}")
    elif style == "matrix-decode":
        # 官方 caption-matrix-decode DNA: 每(组)块三层 = 真字(藏) + 两串扰动字符, 0.1s 步进解码
        # (t: scr0现 → +0.1 scr1现/scr0隐 → +0.2 真字现/scr1隐); 中文适配: 扰动池=全片假名+全形数字(等宽不跳动),
        # 拉丁块用字母数字池; 真字=调色板文字色(护栏), 扰动层=强调色发光噪点
        fs_m = round(base_fs * 0.98)
        pc = _parse_css_color(accent)
        glow = f"rgba({pc[0]},{pc[1]},{pc[2]},.5)" if pc else "rgba(230,196,120,.5)"
        css += (f".capg{{column-gap:{round(6*sc)}px}}"
                f".capw{{position:relative;display:inline-block;white-space:nowrap;"
                f"font-family:Consolas,'Courier New','Microsoft YaHei',monospace;font-weight:700;"
                f"font-size:{fs_m}px;line-height:1.3}}"
                f".capw .r{{opacity:0;visibility:hidden;color:{pal['text']};"
                f"text-shadow:0 0 {round(14*sc)}px {glow}}}"
                f".capw .s0,.capw .s1{{position:absolute;left:0;top:0;white-space:nowrap;opacity:0;"
                f"visibility:hidden;color:{accent};text-shadow:0 0 {round(10*sc)}px {glow}}}")
    elif style == "glitch-rgb":
        # 官方 caption-glitch-rgb DNA: 单层 span, 词起点 x 位移+textShadow 红青分离(0.08s) → 回位清除(0.16s);
        # 长词/含数字词在 cue 尾部做有限次余震脉冲; rng 确定性
        css += (f".capg{{column-gap:{round(10*sc)}px}}"
                f".capw{{display:inline-block;white-space:nowrap;font-family:'Microsoft YaHei',sans-serif;"
                f"font-weight:700;font-size:{base_fs}px;line-height:1.28;color:#FFFFFF;"
                f"text-shadow:0px 0 #ff003c,0px 0 #00e5ff,0 5px 18px rgba(0,0,0,.52);will-change:transform,text-shadow}}")
    elif style == "gradient-fill":
        # 官方 caption-gradient-fill DNA: Siri 彩虹渐变 background-clip:text, 350% 底图;
        # 词默认停在 100%(纯白区), 阅读窗口内 45%→0% 扫过彩色区 + 1.04 缩放脉冲, 词尾回白
        css += (f".capg{{column-gap:{round(10*sc)}px}}"
                f".capw{{display:inline-block;white-space:nowrap;font-family:'Microsoft YaHei',sans-serif;"
                f"font-weight:700;font-size:{base_fs}px;line-height:1.28;"
                f"background-image:linear-gradient(90deg,#fe9f1b 0%,#f76e49 10%,#ff2063 20%,"
                f"#fd56cb 30%,#ef7aff 40%,#fe9f1b 50%,#FFFFFF 50.5%,#FFFFFF 100%);"
                f"background-size:350% 100%;background-position:100% 0;"
                f"-webkit-background-clip:text;background-clip:text;color:transparent;"
                f"padding-bottom:.05em;will-change:transform,background-position}}")
    elif style == "neon-glow":
        # 官方 caption-neon-glow DNA: 未读=暗霓虹管(低透明基色), 读到点亮三层辉光 0.06s, 词尾熄灭;
        # 中文暗管基色 0.14→0.38 提亮(黑底可辨认轮廓); ⚠ 三影同构(基态/激活/熄灭全 3 层)
        css += (f".capg{{column-gap:{round(10*sc)}px}}"
                f".capw{{display:inline-block;white-space:nowrap;font-family:'Microsoft YaHei',sans-serif;"
                f"font-weight:700;font-size:{base_fs}px;line-height:1.28;color:rgba(0,255,240,.38);"
                f"text-shadow:0 0 10px rgba(0,255,240,0),0 0 35px rgba(0,255,240,0),0 0 90px rgba(0,255,240,0);"
                f"will-change:color,text-shadow}}")
    elif style == "kinetic-slam":
        # 官方 caption-kinetic-slam DNA: 一词一屏超大字, 4 式入场轮换(上砸/左飞/右飞/缩放弹);
        # span 全屏 flex 覆盖式居中(留 transform 给 GSAP), 词块默认 hidden 由 JS 逐词点亮
        css += (f".capg{{display:block}}"
                f".capw{{visibility:hidden;position:absolute;inset:0;display:flex;align-items:center;"
                f"justify-content:center;font-family:'Microsoft YaHei',sans-serif;font-weight:700;"
                f"color:#FFFFFF;text-align:center;white-space:nowrap;will-change:transform,opacity}}")
    elif style == "neon-accent":
        # 官方 caption-neon-accent DNA: 全句常驻, 白主词+彩色霓虹重点词(1.18 倍, 六影辉光);
        # 词色/字号/阴影全静态内联(官方不 tween 阴影, 零同构问题), 组级只做入场弹出+微漂
        css += (f".capg{{column-gap:{round(8*sc)}px;align-items:baseline}}"
                f".capw{{display:inline-block;white-space:nowrap;font-family:'Microsoft YaHei',sans-serif;"
                f"font-weight:700;line-height:1.3;will-change:transform}}")
    else:  # editorial-emphasis — 杂志重读: 调色板色带 + 衬线大号重读块
        fs_n = round(base_fs * 1.05)
        fs_e = min(round(H * 0.085), round(fs_n * 2.0))
        em_color = pal["text"]
        pc, bc = _parse_css_color(accent), _parse_css_color(pal["bg"])
        if pc and bc:
            pc3, bc3 = pc[:3], bc[:3]
            pa = pc[3] if len(pc) > 3 else 1.0
            comp = tuple(round(pc3[i] * pa + bc3[i] * (1 - pa)) for i in range(3))
            if _wcag_ratio(comp, bc3) >= 3.0:
                em_color = accent
        band_bg = "#%02X%02X%02X" % tuple(bc[:3]) if bc else "#101116"  # 色带实底化: 采样即所得
        css += (f"#caps{{bottom:0;height:auto}}"
                f".capg{{left:0;right:0;transform:none;bottom:{round(H*0.052)}px;flex-wrap:wrap;align-items:baseline;"
                f"column-gap:{round(14*sc)}px;row-gap:{round(4*sc)}px;padding:{round(12*sc)}px {round(36*sc)}px;"
                f"background:{band_bg};border-top:1px solid {pal['border']}}}"
                f".capw{{display:inline-block;white-space:nowrap;color:{pal['text']};"
                f"font-family:'Microsoft YaHei',sans-serif;font-weight:500;font-size:{fs_n}px;line-height:1.3}}"
                f".capw.e{{font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs_e}px;"
                f"color:{em_color};line-height:1.08;letter-spacing:.02em}}")
    # 组可见性窗口 (官方: 持续到下一组开始, 末组+0.4s缓冲)
    for i, g in enumerate(groups):
        end = round(min(groups[i + 1]["start"], g["end"] + 0.3) if i + 1 < len(groups)
                    else min(total, g["end"] + 0.4), 3)
        js += (f'  tl.set("#capg{i}",{{opacity:1,visibility:"visible"}},{g["start"]});\n'
               f'  tl.set("#capg{i}",{{opacity:0,visibility:"hidden"}},{end});\n')
    if style == "neon-accent":
        # 入场弹出(官方 scale .65→1 power3.out; 中文取 .8 折中) + 确定性步进微漂:
        # 官方 yoyo wiggle 挂 clip 元素且终点贴相邻 clip 起点 → 会撞 gsap_exit_missing_hard_kill,
        # 改 0.42s 步长 ±W*0.006 交替 tl.set(有限/确定/终点归零硬杀)
        for gi, g in enumerate(groups):
            js += (f'  tl.fromTo("#capg{gi}",{{scale:0.8}},{{scale:1,duration:0.23,ease:"power3.out"}},'
                   f'{round(g["start"], 3)});\n')
            k, t = 0, round(g["start"] + 0.42, 3)
            stop = round(g["end"] - 0.12, 3)
            while t < stop:
                d = round(W * 0.006 * (1 if k % 2 == 0 else -1), 1)
                js += f'  tl.set("#capg{gi}",{{x:{d},y:{round(-d * 0.6, 1)}}},{t});\n'
                k += 1
                t = round(t + 0.42, 3)
            js += f'  tl.set("#capg{gi}",{{x:0,y:0}},{stop});\n'
    if style == "neon-glow":
        # 霓虹点亮: 词首 color+三层辉光 0.06s(重点词粉/其余青), 词尾熄灭回暗管基态 — 全程 3 影同构
        base_c = "rgba(0,255,240,.38)"
        base_s = "0 0 10px rgba(0,255,240,0),0 0 35px rgba(0,255,240,0),0 0 90px rgba(0,255,240,0)"
        for w in words:
            c = "#FF0099" if _is_emph(w["text"], words, w["g"]) else "#00FFF0"
            glow = f"0 0 10px {c},0 0 35px {c},0 0 90px {c}"
            js += (f'  tl.to("#capw{w["i"]}",{{color:"{c}",textShadow:"{glow}",duration:0.06,ease:"none"}},{w["start"]});\n'
                   f'  tl.to("#capw{w["i"]}",{{color:"{base_c}",textShadow:"{base_s}",duration:0.06,ease:"none"}},{w["end"]});\n')
    if style == "kinetic-slam":
        # 动力猛砸: i%4 轮换(上砸/左飞/右飞/缩放弹), 词尾 0.1s 淡出后隐藏复位; 重点词金色
        for w in words:
            i = w["i"]
            m = i % 4
            if m == 0:
                frm = f'{{y:{round(-H * 0.10)},opacity:0}}'
                tw = '{y:0,opacity:1,duration:0.22,ease:"back.out(1.7)"}'
            elif m == 1:
                frm = f'{{x:{round(-W * 0.43)},opacity:0}}'
                tw = '{x:0,opacity:1,duration:0.2,ease:"expo.out"}'
            elif m == 2:
                frm = f'{{x:{round(W * 0.43)},opacity:0}}'
                tw = '{x:0,opacity:1,duration:0.2,ease:"expo.out"}'
            else:
                frm = '{scale:0.4,opacity:0}'
                tw = '{scale:1,opacity:1,duration:0.24,ease:"back.out(2.2)"}'
            js += (f'  tl.set("#capw{i}",{{visibility:"visible"}},{w["start"]});\n'
                   f'  tl.fromTo("#capw{i}",{frm},{tw},{w["start"]});\n'
                   f'  tl.to("#capw{i}",{{opacity:0,duration:0.1,ease:"power2.in"}},{w["end"]});\n'
                   f'  tl.set("#capw{i}",{{visibility:"hidden",opacity:1}},{round(w["end"] + 0.1, 3)});\n')
            if _is_emph(w["text"], words, w["g"]):
                js += f'  tl.set("#capw{i}",{{color:"#FFD700"}},{w["start"]});\n'
    if style == "glitch-rgb":
        # 故障色差: 词起点红青分离+左移(0.08s) → 回位清除(0.16s); 长词/含数字词 cue 尾 2 次有限余震
        # ⚠ GSAP textShadow 补间要求两端阴影数同构(实测: 不匹配时静默失效, x 照走) → 三段全部 3 影结构
        drop = "0 5px 18px rgba(0,0,0,.52)"
        plain = f"0px 0 #ff003c,0px 0 #00e5ff,{drop}"
        for w in words:
            rng = random.Random(w["i"] * 777 + 13).random
            travel = round(-(W * 0.012 + rng() * W * 0.011), 1)
            sm = round(W * 0.006 + rng() * W * 0.009, 1)
            split = f"{sm}px 0 #ff003c,-{sm}px 0 #00e5ff,{drop}"
            js += (f'  tl.to("#capw{w["i"]}",{{x:{travel},textShadow:"{split}",duration:0.08,ease:"none"}},{w["start"]});\n'
                   f'  tl.to("#capw{w["i"]}",{{x:0,textShadow:"{plain}",duration:0.16,ease:"power3.out"}},{round(w["start"] + 0.08, 3)});\n')
            if len(w["text"]) >= 6 or re.search(r"[0-9]", w["text"]):
                for k in range(2):
                    t0 = round(w["end"] + 0.08 + k * 0.22, 3)
                    sm2 = round(W * 0.005 + rng() * W * 0.007, 1)
                    tv2 = round(-(W * 0.006 + rng() * W * 0.007), 1)
                    psplit = f"{sm2}px 0 #ff003c,-{sm2}px 0 #00e5ff,{drop}"
                    js += (f'  tl.to("#capw{w["i"]}",{{x:{tv2},textShadow:"{psplit}",duration:0.045,ease:"none"}},{t0});\n'
                           f'  tl.to("#capw{w["i"]}",{{x:0,textShadow:"{plain}",duration:0.075,ease:"power3.out"}},{round(t0 + 0.045, 3)});\n')
    if style == "gradient-fill":
        # 渐变扫色: 词阅读窗口内 45%→0% 彩虹扫过 + 1.04 缩放脉冲, 词尾回白
        for w in words:
            dur = round(max(w["end"] - w["start"], 0.2), 3)
            js += (f'  tl.set("#capw{w["i"]}",{{scale:1.04}},{w["start"]});\n'
                   f'  tl.fromTo("#capw{w["i"]}",{{backgroundPosition:"45% 0"}},'
                   f'{{backgroundPosition:"0% 0",duration:{dur},ease:"none"}},{w["start"]});\n'
                   f'  tl.set("#capw{w["i"]}",{{backgroundPosition:"100% 0"}},{w["end"]});\n'
                   f'  tl.to("#capw{w["i"]}",{{scale:1,duration:0.15,ease:"power2.out"}},{w["end"]});\n')
    if style == "matrix-decode":
        # 矩阵解码 DOM/JS: 每(组)块三层; 扰动文本渲染期生成(确定性 seed), 渲染期零建树
        _POOL_CJK = "アイウエオカキクケコサシスセソタチツテトナニヌネノ0123456789"
        _POOL_LAT = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        dom = []
        cur_g = -1
        for w in words:
            if w["g"] != cur_g:
                if cur_g >= 0:
                    dom.append("</div>")
                dom.append(f'<div class="capg" id="capg{w["g"]}">')
                cur_g = w["g"]
            pool = _POOL_LAT if re.fullmatch(r"[0-9A-Za-z]+", w["text"]) else _POOL_CJK
            rng = random.Random(w["i"] * 7919 + 13)
            s0 = "".join(rng.choice(pool) for _ in w["text"])
            s1 = "".join(rng.choice(pool) for _ in w["text"])
            dom.append(f'<span class="capw" id="capw{w["i"]}">'
                       f'<span class="r">{_html_esc(w["text"])}</span>'
                       f'<span class="s0">{_html_esc(s0)}</span><span class="s1">{_html_esc(s1)}</span></span>')
        if cur_g >= 0:
            dom.append("</div>")
        cap_html = '<div id="caps">' + "".join(dom) + "</div>"
        for w in words:
            js += (f'  tl.set("#capw{w["i"]} .s0",{{autoAlpha:1}},{w["start"]});\n'
                   f'  tl.set("#capw{w["i"]} .s1",{{autoAlpha:1}},{round(w["start"] + 0.1, 3)});\n'
                   f'  tl.set("#capw{w["i"]} .s0",{{autoAlpha:0}},{round(w["start"] + 0.1, 3)});\n'
                   f'  tl.set("#capw{w["i"]} .r",{{autoAlpha:1}},{round(w["start"] + 0.2, 3)});\n'
                   f'  tl.set("#capw{w["i"]} .s1",{{autoAlpha:0}},{round(w["start"] + 0.2, 3)});\n')
        return css, cap_html, js
    if style == "weight-shift":
        # 字重焦点 DOM: 每组双行(前半/后半), 组可见性走上面公共窗口; cue 中点切换阅读行
        dom = []
        for gi, g in enumerate(groups):
            lines = f'<div class="wtl w1">{_html_esc(g["t0"])}</div>'
            if g.get("t1"):
                lines += f'<div class="wtl w2">{_html_esc(g["t1"])}</div>'
            dom.append(f'<div class="capg" id="capg{gi}">{lines}</div>')
            if g.get("t1"):
                mid = round((g["start"] + g["end"]) / 2, 3)
                js += (f'  tl.set("#capg{gi} .wtl",{{fontWeight:400}},{mid});\n'
                       f'  tl.set("#capg{gi} .wtl.w2",{{fontWeight:700}},{mid});\n')
        cap_html = '<div id="caps">' + "".join(dom) + "</div>"
        return css, cap_html, js
    # 词块 DOM (在 JS 数据之后于 Python 直接生成, 渲染期零建树)
    dom = []
    cur_g = -1
    for w in words:
        if w["g"] != cur_g:
            if cur_g >= 0:
                # ⚠ 非 pill 组只有 capg 一层: 三连闭会把 #caps 和合成容器关掉 → 后续 cue 全逃逸到 body 贴帧底
                dom.append("</div></div></div>" if style == "pill-karaoke" else "</div>")
            cls = "capw e" if (style == "editorial-emphasis" and _is_emph(w["text"], words, w["g"])) else "capw"
            inner = (f'<span class="bg"></span><span class="tx">{_html_esc(w["text"])}</span>'
                     if style == "highlight" else _html_esc(w["text"]))
            wst = ""
            if style == "kinetic-slam":
                wst = f' style="font-size:{_kin_fs(w["text"], W)}px"'
            elif style == "neon-accent":
                wst = f' style="{_na_style(w["text"], w["i"], words, w["g"], base_fs)}"'
            if style == "pill-karaoke":
                dom.append(f'<div class="capg" id="capg{w["g"]}"><div class="cappill"><div class="capcopy">'
                           f'<span class="capw" id="capw{w["i"]}">{inner}</span>')
            else:
                dom.append(f'<div class="capg" id="capg{w["g"]}">'
                           f'<span class="{cls}" id="capw{w["i"]}"{wst}>{inner}</span>')
            cur_g = w["g"]
        else:
            cls = "capw e" if (style == "editorial-emphasis" and _is_emph(w["text"], words, w["g"])) else "capw"
            inner = (f'<span class="bg"></span><span class="tx">{_html_esc(w["text"])}</span>'
                     if style == "highlight" else _html_esc(w["text"]))
            wst = f' style="font-size:{_kin_fs(w["text"], W)}px"' if style == "kinetic-slam" else ""
            if style == "neon-accent":
                wst = f' style="{_na_style(w["text"], w["i"], words, w["g"], base_fs)}"'
            dom.append(f'<span class="{cls}" id="capw{w["i"]}"{wst}>{inner}</span>')
    if cur_g >= 0:
        dom.append("</div></div></div>" if style == "pill-karaoke" else "</div>")
    cap_html = '<div id="caps">' + "".join(dom) + "</div>"
    return css, cap_html, js

def _kin_fs(text: str, W: int) -> int:
    """kinetic-slam 一词一屏字号: CJK 按 1em/字 拉丁按 0.55em 估宽, 基准 22%W 封顶 75%W"""
    width = sum(1.0 if ord(ch) > 0x2E7F else 0.55 for ch in text)
    return max(18, round(min(W * 0.22, W * 0.75 / max(width, 0.6))))

_NA_ACCENTS = ("#53FF01", "#FF0002", "#FCFF00")

def _na_shadow(color: str) -> str:
    """neon-accent 六影(官方 shadowForColor: 双黑投影 + 四段彩辉光) — 静态内联, 不 tween, 零同构问题"""
    h = color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    glow = (f"0 0 4px rgba({r},{g},{b},1),0 0 10px rgba({r},{g},{b},.9),"
            f"0 0 20px rgba({r},{g},{b},.7),0 0 40px rgba({r},{g},{b},.4)")
    return f"0 8px 16px rgba(0,0,0,.8),0 16px 40px rgba(0,0,0,.6),{glow}"

def _na_style(text: str, idx: int, words: list, gid: int, base_fs: int) -> str:
    """neon-accent 词内联样式: 重点词=accent 色轮换+1.18 倍字号+彩辉光, 其余白+白弱光"""
    if _is_emph(text, words, gid):
        c = _NA_ACCENTS[idx % 3]
        return (f"font-size:{round(base_fs * 1.18)}px;color:{c};"
                f"text-shadow:{_na_shadow(c)}")
    c = "#FFFFFF"
    return f"font-size:{base_fs}px;color:{c};text-shadow:{_na_shadow(c)}"

def _is_emph(text: str, words: list, gid: int) -> bool:
    """重读块启发式: 组内含数字/拉丁的块优先, 否则最长块"""
    peers = [w for w in words if w["g"] == gid]
    digits = [w for w in peers if re.search(r"[0-9A-Za-z]", w["text"])]
    pool = digits or peers
    return text == max((w["text"] for w in pool), key=len)

def _hf_title_parts(it: dict, W: int, H: int, accent: str):
    """开场标题贴纸 (1.8s 居中, 衬线) — 三款官方动效 DNA"""
    from html import escape as _hesc
    st = it.get("style") or "tracking-in"
    text = _html_esc(str(it.get("text") or ""))
    n = max(1, len(str(it.get("text") or "")))
    fs = int(min(W * 0.085, W * 0.84 / (n * 1.18), H * 0.115))
    fs = max(22, fs)
    dur = float(it.get("dur") or 1.8)
    out_at = round(dur - 0.32, 3)
    css = (f"#ititle{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;"
           f"z-index:26;pointer-events:none}}")
    if st == "titlecard-lockup":
        css += (f"#itlock{{display:flex;flex-direction:column;align-items:center}}"
                f".itwm{{font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs}px;color:#FAFAFA;"
                f"letter-spacing:.08em;line-height:1.18;text-align:center;"
                f"text-shadow:0 2px 20px rgba(0,0,0,.55),0 1px 0 rgba(0,0,0,.4)}}"
                f".itrule{{width:{round(W*0.30)}px;height:2px;margin-top:{round(H*0.022)}px}}"
                f".itrule line{{stroke:{accent};stroke-width:2;stroke-opacity:.9;"
                f"stroke-dasharray:560;stroke-dashoffset:560}}")
        html = (f'<div id="ititle" class="clip" data-start="0" data-duration="{dur}">'
                f'<div id="itlock"><span class="itwm" id="itwm">{text}</span>'
                f'<svg class="itrule" viewBox="0 0 560 2" preserveAspectRatio="none">'
                f'<line x1="0" y1="1" x2="560" y2="1"/></svg></div></div>')
        js = (f'  tl.fromTo("#itwm",{{autoAlpha:0,scale:0.96}},'
              f'{{autoAlpha:1,scale:1,duration:0.85,ease:"power3.out"}},0.25);\n'
              f'  tl.fromTo(".itrule line",{{strokeDashoffset:560}},'
              f'{{strokeDashoffset:0,duration:0.55,ease:"power2.out"}},0.9);\n'
              f'  tl.to("#itlock",{{autoAlpha:0,duration:0.3,ease:"power2.in"}},{out_at});\n')
    elif st == "per-word-rise":
        css += (f"#itstage{{display:flex;flex-wrap:wrap;justify-content:center;column-gap:.06em;"
                f"font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs}px;color:#FAFAFA;"
                f"line-height:1.24;letter-spacing:.04em;max-width:{round(W*0.86)}px;"
                f"text-shadow:0 2px 18px rgba(0,0,0,.6),0 1px 0 rgba(0,0,0,.4)}}"
                f".pwr-unit{{display:inline-block;filter:blur(14px);will-change:transform,filter,opacity}}")
        chars = list(str(it.get("text") or ""))
        spans = "".join(f'<span class="pwr-unit" id="pw{i}">{_html_esc(ch)}</span>' for i, ch in enumerate(chars))
        html = (f'<div id="ititle" class="clip" data-start="0" data-duration="{dur}">'
                f'<div id="itstage">{spans}</div></div>')
        js = ('  function landEase(p){if(p<=0)return 0;if(p>=1)return 1;var S=0.78,R=0.07,'
              'M=((1-R)/S)*0.5;if(p<S){var x=p/S,inv=1-x;return (1-R)*(0.5*(1-inv*inv*inv)+0.5*x);}'
              'var u=(p-S)/(1-S),b=M*(1-S),c=3*R-2*b,d2=b-2*R;return 1-R+b*u+c*u*u+d2*u*u*u;}\n'
              '  gsap.set("#itstage .pwr-unit",{opacity:0,y:"26px"});\n')
        rise, stag = 0.74, (0.62 / (len(chars) - 1) if len(chars) > 1 else 0.0)
        for i in range(len(chars)):
            landing = round(i * stag + rise, 3)
            start = round(max(0.0, landing - rise), 3)
            d = round(max(0.08, landing - start), 3)
            js += (f'  tl.to("#pw{i}",{{opacity:1,y:0,filter:"blur(0px)",duration:{d},ease:landEase}},{start});\n')
        js += f'  tl.to("#itstage",{{autoAlpha:0,duration:0.3,ease:"power2.in"}},{out_at});\n'
    elif st == "typewriter":
        # 官方 typewriter DNA: 逐字显影(steps 节奏) + 光标收尾闪烁(finite yoyo)
        css += (f"#ittw{{font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs}px;"
                f"letter-spacing:.1em;color:#FAFAFA;background:rgba(15,17,21,.84);"
                f"padding:.3em .6em .36em;border-radius:{round(W*0.018)}px;white-space:nowrap}}"
                f".tw-c{{display:inline-block;opacity:0}}"
                f".tw-caret{{display:inline-block;width:{max(2, round(W*0.0022))}px;height:.9em;"
                f"background:{accent};border-radius:99px;margin-left:.1em;opacity:0;"
                f"vertical-align:-.08em}}")
        chars = list(str(it.get("text") or ""))
        spans = "".join(f'<span class="tw-c">{_html_esc(ch)}</span>' for ch in chars)
        html = (f'<div id="ititle" class="clip" data-start="0" data-duration="{dur}">'
                f'<span id="ittw">{spans}<i class="tw-caret"></i></span></div>')
        step = round(min(0.09, 0.92 / max(1, len(chars))), 3)
        t0 = 0.15
        js = ""
        for i in range(len(chars)):
            js += f'  tl.set("#ittw .tw-c:nth-child({i + 1})",{{opacity:1}},{round(t0 + i * step, 3)});\n'
        t_done = round(t0 + len(chars) * step, 3)
        js += (f'  tl.to("#ittw .tw-caret",{{opacity:1,duration:0.06,ease:"none"}},{round(t0 + step, 3)});\n'
               f'  tl.fromTo("#ittw .tw-caret",{{opacity:1}},{{opacity:0.15,duration:0.2,'
               f'ease:"none",yoyo:true,repeat:3}},{t_done});\n'
               f'  tl.to("#ittw",{{autoAlpha:0,duration:0.3,ease:"power2.in"}},{out_at});\n')
    elif st == "headline-slam":
        # 官方 headline-slam DNA: scale 1.6→1 expo.out 砸落 + 3帧落地震动 + hold 微漂 + 上甩出场
        fs_h = int(max(24, min(W * 0.11, W * 0.86 / (n * 1.05), H * 0.14)))
        css += (f"#hstage{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;"
                f"will-change:transform}}"
                f"#hhead{{font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs_h}px;"
                f"color:#FAFAFA;letter-spacing:.06em;line-height:1.1;text-align:center;"
                f"max-width:{round(W*0.88)}px;white-space:nowrap;"
                f"text-shadow:0 {round(H*0.012)}px {round(H*0.045)}px rgba(0,0,0,.62),0 1px 0 rgba(0,0,0,.4);"
                f"will-change:transform,opacity}}")
        html = (f'<div id="ititle" class="clip" data-start="0" data-duration="{dur}">'
                f'<div id="hstage"><div id="hhead">{text}</div></div></div>')
        dx, dy = round(W * 0.0055, 2), round(H * 0.003, 2)
        js = (f'  tl.fromTo("#hhead",{{scale:1.6,opacity:0}},'
              f'{{scale:1,opacity:1,duration:0.58,ease:"expo.out"}},0);\n'
              f'  tl.to("#hstage",{{x:{dx},y:-{dy},duration:0.0333,ease:"power2.out"}},0.58);\n'
              f'  tl.to("#hstage",{{x:-{round(dx*0.7, 2)},y:{round(dy*0.7, 2)},duration:0.0333,ease:"power2.out"}});\n'
              f'  tl.to("#hstage",{{x:0,y:0,duration:0.0333,ease:"power2.out"}});\n'
              f'  tl.fromTo("#hhead",{{y:0}},{{y:-{round(H*0.006, 2)},duration:0.34,'
              f'ease:"sine.inOut",yoyo:true,repeat:1,immediateRender:false}},0.62);\n'
              f'  tl.to("#hhead",{{y:-{H*1.2},duration:0.46,ease:"power4.in"}},{round(dur - 0.46, 3)});\n')
    elif st == "marker-circle":
        # 官方 marker-highlight DNA: 手绘马克笔 circle 绕题 — getTotalLength dash 描画 + 落笔微弹
        ink = accent
        pc, dk = _parse_css_color(accent), (21, 23, 27)
        if pc and _wcag_ratio(pc[:3], dk) < 3.0:
            ink = "#FAFAFA"
        css += (f"#mhstage{{position:relative;display:inline-block;"
                f"font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs}px;"
                f"color:#FAFAFA;letter-spacing:.08em;line-height:1.2;text-align:center;"
                f"padding:.1em .25em;text-shadow:0 2px 18px rgba(0,0,0,.6)}}"
                f"#mhtx{{position:relative;z-index:1}}"
                f"#mhsvg{{position:absolute;left:-12%;top:-24%;width:124%;height:148%;"
                f"overflow:visible;z-index:0}}")
        circle_d = ("M 22 7 C 58 1, 95 8, 97 20 C 99 31, 74 38, 44 37 "
                    "C 16 36, 2 29, 3 19 C 4 10, 22 4, 52 4.5")
        html = (f'<div id="ititle" class="clip" data-start="0" data-duration="{dur}">'
                f'<div id="mhstage"><span id="mhtx">{text}</span>'
                f'<svg id="mhsvg" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">'
                f'<path id="mhpath" d="{circle_d}" fill="none" stroke="{ink}" stroke-width="2.8" '
                f'stroke-linecap="round" stroke-linejoin="round"/></svg></div></div>')
        js = ('  var mhp=document.getElementById("mhpath"),mhl=mhp.getTotalLength();\n'
              '  gsap.set(mhp,{strokeDasharray:mhl,strokeDashoffset:mhl,opacity:0});\n'
              f'  gsap.set("#mhstage",{{opacity:0,y:"{round(H*0.014)}px"}});\n'
              f'  tl.fromTo("#mhstage",{{opacity:0,y:"{round(H*0.014)}px"}},'
              f'{{opacity:1,y:0,duration:0.4,ease:"power3.out"}},0.05);\n'
              f'  tl.set(mhp,{{opacity:1}},0.5);\n'
              f'  tl.fromTo(mhp,{{strokeDashoffset:mhl}},'
              f'{{strokeDashoffset:0,duration:0.55,ease:"power2.inOut"}},0.5);\n'
              f'  tl.fromTo("#mhstage",{{scale:1}},{{scale:1.04,duration:0.16,'
              f'ease:"power1.out",yoyo:true,repeat:1}},0.88);\n'
              f'  tl.to("#mhstage",{{autoAlpha:0,duration:0.3,ease:"power2.in"}},{out_at});\n')
    elif st == "titlecard-calm":
        # 官方 titlecard-calm DNA: 小标签先淡升, 大字随后淡升, HOLD 极微漂移, 干净淡出 (高级留白)
        # 中文适配: kicker=「今日主题」accent 小字(对比度护栏), 大字衬线 900, 窗口压缩进 1.8s
        fs_c = int(max(20, min(W * 0.072, W * 0.80 / (n * 1.15), H * 0.10)))
        kc = accent
        pc_k, dk_k = _parse_css_color(accent), (21, 23, 27)
        if pc_k and _wcag_ratio(pc_k[:3], dk_k) < 3.0:
            kc = "#FAFAFA"
        css += (f"#itcalm{{display:flex;flex-direction:column;align-items:flex-start;gap:{round(H*0.012)}px}}"
                f"#itck{{font-family:'Microsoft YaHei',sans-serif;font-weight:700;font-size:{max(12, round(W*0.016))}px;"
                f"color:{kc};letter-spacing:.30em;text-indent:.30em}}"
                f"#itch{{font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs_c}px;color:#FAFAFA;"
                f"letter-spacing:.06em;line-height:1.14;white-space:nowrap;"
                f"text-shadow:0 2px 20px rgba(0,0,0,.55),0 1px 0 rgba(0,0,0,.4)}}")
        html = (f'<div id="ititle" class="clip" data-start="0" data-duration="{dur}">'
                f'<div id="itcalm"><span id="itck">今日主题</span>'
                f'<span id="itch">{text}</span></div></div>')
        js = (f'  tl.fromTo("#itck",{{opacity:0,y:{round(H*0.010)}}},'
              f'{{opacity:1,y:0,duration:0.4,ease:"power2.out"}},0);\n'
              f'  tl.fromTo("#itch",{{opacity:0,y:{round(H*0.014)}}},'
              f'{{opacity:1,y:0,duration:0.62,ease:"power3.out"}},0.26);\n'
              f'  tl.to("#itcalm",{{y:-{round(H*0.003)},duration:0.5,ease:"sine.inOut"}},0.9);\n'
              f'  tl.to("#itcalm",{{opacity:0,y:-{round(H*0.009)},duration:0.3,ease:"power2.in"}},{out_at});\n')
    elif st == "text-shimmer":
        # 官方 text-shimmer DNA: 字面定版不动, 一道 spec 高光带扫过 (background-clip:text 渐变位移), 无入场
        # 中文适配: 衬线大字 + Python 预混高光色(避免 color-mix 渲染环境差异), sweep 压缩进 1.8s 窗口
        def _mixw(rgb, w):
            return "#%02X%02X%02X" % tuple(round(c * (1 - w) + 255 * w) for c in rgb)
        pc_s = _parse_css_color(accent)
        if pc_s:
            g1, g2 = _mixw(pc_s[:3], 0.58), _mixw(pc_s[:3], 0.72)
        else:
            g1, g2 = "#F2E7C9", "#FFFDF5"
        css += (f"#itsh{{font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs}px;"
                f"letter-spacing:.06em;line-height:1.14;white-space:nowrap;color:transparent;"
                f"background-image:linear-gradient(110deg,#FAFAFA 0%,#FAFAFA 40%,{g1} 47%,#FFFFFF 50%,{g2} 53%,#FAFAFA 60%,#FAFAFA 100%);"
                f"background-size:300% 100%;background-repeat:no-repeat;"
                f"-webkit-background-clip:text;background-clip:text;"
                f"will-change:background-position}}")
        html = (f'<div id="ititle" class="clip" data-start="0" data-duration="{dur}">'
                f'<span id="itsh">{text}</span></div>')
        sweep_at = 0.55
        sweep_d = round(min(0.9, max(0.3, out_at - sweep_at - 0.03)), 2)
        js = (f'  tl.fromTo("#itsh",{{backgroundPosition:"100% 50%"}},'
              f'{{backgroundPosition:"0% 50%",duration:{sweep_d},ease:"sine.inOut"}},{sweep_at});\n'
              f'  tl.to("#itsh",{{opacity:0,duration:0.3,ease:"power2.in"}},{out_at});\n')
    else:  # tracking-in → 聚焦显字 (HF 规则禁 letter-spacing 布局补间, 用 blur→sharp + 轻收束呈现同款高级感)
        css += (f"#ittx{{font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs}px;"
                f"letter-spacing:.14em;opacity:0;color:#FAFAFA;"
                f"background:rgba(15,17,21,.84);padding:.3em .72em .36em;border-radius:{round(W*0.018)}px;"
                f"white-space:nowrap;will-change:opacity,transform,filter}}")
        html = (f'<div id="ititle" class="clip" data-start="0" data-duration="{dur}">'
                f'<span id="ittx" class="hf-tracking-in">{text}</span></div>')
        js = (f'  tl.fromTo("#ittx",{{opacity:0,scale:1.07,filter:"blur(9px)"}},'
              f'{{opacity:1,scale:1,filter:"blur(0px)",duration:0.85,ease:"power3.out"}},0.08);\n'
              f'  tl.to("#ittx",{{autoAlpha:0,duration:0.3,ease:"power2.in"}},{out_at});\n')
    return css, html, js


def _hf_lt_parts(it: dict, W: int, H: int, accent: str):
    """信息条(人名条)适配: 官方 lt-* DNA — 开场 2.1s 入/4.5s 停, 文字=开场标题, 位置压字幕区上方
    注意: 补间挂主时间轴, 位置必须 +start 偏移(局部时间→绝对时间)"""
    from html import escape as _hesc
    st = it.get("style") or "lt-kicker-name"
    text = _html_esc(str(it.get("text") or ""))
    start = round(float(it.get("start") or 2.1), 2)
    dur = round(float(it.get("dur") or 4.5), 2)
    out_at = round(dur - 0.55, 2)
    fs_name = max(20, round(W * 0.062))
    fs_kick = max(11, round(W * 0.021))
    fs_role = max(11, round(W * 0.019))
    left, bottom = round(W * 0.07), round(H * 0.375)
    pc = _parse_css_color(accent)
    on_accent = "#141518" if (pc and _wcag_ratio(pc[:3], (20, 21, 24)) >= 3.0) else "#FAFAFA"
    role_tx = "AI 生成 · 口播解读"
    css = (f"#ltwrap{{position:absolute;left:{left}px;bottom:{bottom}px;z-index:21;pointer-events:none}}"
           f"#ltwrap .lt-name{{font-family:'SimSun',Georgia,serif;font-weight:900;font-size:{fs_name}px;"
           f"line-height:1.12;letter-spacing:.04em;white-space:nowrap}}"
           f"#ltwrap .lt-role{{font-family:'Microsoft YaHei',sans-serif;font-weight:700;"
           f"font-size:{fs_role}px;letter-spacing:.14em;white-space:nowrap}}")
    html = f'<div id="ltwrap" class="clip" data-start="{start}" data-duration="{dur}">'
    if st == "lt-color-block":
        # 官方 lt-color-block DNA: 色块左滑入(back.out 轻过冲) + 名字/角色行升入; 出场左滑淡出
        css += (f"#ltb{{background:{accent};color:{on_accent};"
                f"padding:{round(W*0.014)}px {round(W*0.026)}px {round(W*0.017)}px {round(W*0.022)}px;"
                f"box-shadow:0 {round(W*0.011)}px {round(W*0.03)}px rgba(0,0,0,.38);"
                f"display:inline-flex;flex-direction:column;gap:{round(W*0.007)}px}}"
                f"#ltb .lt-name{{color:{on_accent}}}"
                f"#ltb .lt-role{{color:{on_accent};opacity:.86}}")
        html += (f'<div id="ltb"><span class="lt-name" id="ltbn">{text}</span>'
                 f'<span class="lt-role" id="ltbr">{role_tx}</span></div></div>')
        js = (f'  var OFF={start};\n'
              f'  gsap.set("#ltb",{{x:-{round(W*0.055)},opacity:0}});\n'
              f'  gsap.set("#ltbn",{{y:{round(W*0.016)},opacity:0}});\n'
              f'  gsap.set("#ltbr",{{y:{round(W*0.012)},opacity:0}});\n'
              f'  tl.to("#ltb",{{x:0,opacity:1,duration:0.5,ease:"back.out(1.4)"}},0.1+OFF);\n'
              f'  tl.to("#ltbn",{{y:0,opacity:1,duration:0.42,ease:"power3.out"}},0.3+OFF);\n'
              f'  tl.to("#ltbr",{{y:0,opacity:1,duration:0.42,ease:"power3.out"}},0.42+OFF);\n'
              f'  tl.to("#ltb",{{x:-{round(W*0.04)},opacity:0,duration:0.35,ease:"power2.in"}},{out_at}+OFF);\n')
    elif st == "lt-side-rule":
        # 官方 lt-side-rule DNA: 竖色条 scaleY 描画 + 名字/角色从左滑入; 出场反向收缩
        css += (f"#lts{{display:flex;align-items:stretch;gap:{round(W*0.016)}px}}"
                f"#ltsb{{width:{max(4, round(W*0.005))}px;background:{accent};border-radius:99px;"
                f"transform-origin:50% 0%}}"
                f"#lts .lt-name{{color:#FAFAFA;text-shadow:0 2px 20px rgba(0,0,0,.55)}}"
                f"#lts .lt-role{{color:#E7EAF0;text-shadow:0 2px 14px rgba(0,0,0,.5)}}"
                f"#ltst{{display:flex;flex-direction:column;justify-content:center;gap:{round(W*0.008)}px}}")
        html += (f'<div id="lts"><div id="ltsb"></div><div id="ltst">'
                 f'<span class="lt-name" id="ltsn">{text}</span>'
                 f'<span class="lt-role" id="ltsr">{role_tx}</span></div></div></div>')
        js = (f'  var OFF={start};\n'
              f'  gsap.set("#ltsb",{{scaleY:0}});\n'
              f'  gsap.set("#ltsn",{{x:-{round(W*0.016)},opacity:0}});\n'
              f'  gsap.set("#ltsr",{{x:-{round(W*0.016)},opacity:0}});\n'
              f'  tl.to("#ltsb",{{scaleY:1,duration:0.5,ease:"power3.out"}},0.1+OFF);\n'
              f'  tl.to("#ltsn",{{x:0,opacity:1,duration:0.5,ease:"power3.out"}},0.26+OFF);\n'
              f'  tl.to("#ltsr",{{x:0,opacity:1,duration:0.5,ease:"power3.out"}},0.38+OFF);\n'
              f'  tl.to("#ltsr",{{opacity:0,duration:0.3,ease:"power2.in"}},{out_at}+OFF);\n'
              f'  tl.to("#ltsn",{{y:-{round(W*0.009)},opacity:0,duration:0.32,ease:"power2.in"}},{out_at}+0.05+OFF);\n'
              f'  tl.to("#ltsb",{{scaleY:0,duration:0.32,ease:"power2.in"}},{out_at}+0.07+OFF);\n')
    elif st == "lt-clean-bar":
        # 官方 lt-clean-bar DNA: 暖白卡 clip 左擦入 + accent 竖标签 scaleY 描画 + 名/角色升入; 出场下沉淡出
        fs_n2 = max(20, round(W * 0.030))
        fs_r2 = max(11, round(W * 0.016))
        css += (f"#ltc{{display:flex;align-items:stretch;border-radius:{round(W*0.010)}px;overflow:hidden;"
                f"box-shadow:0 {round(W*0.012)}px {round(W*0.036)}px rgba(8,9,12,.42);will-change:clip-path}}"
                f"#ltct{{width:{max(6, round(W*0.007))}px;background:{accent};flex-shrink:0;transform-origin:50% 0%}}"
                f"#ltcb{{background:#F5F1E6;padding:{round(W*0.011)}px {round(W*0.020)}px {round(W*0.012)}px {round(W*0.015)}px;"
                f"display:flex;flex-direction:column;gap:{round(W*0.005)}px}}"
                f"#ltc .lt-name{{font-size:{fs_n2}px;color:#141518}}"
                f"#ltc .lt-role{{font-size:{fs_r2}px;color:#565B66;letter-spacing:.10em}}")
        html += (f'<div id="ltc"><div id="ltct"></div><div id="ltcb">'
                 f'<span class="lt-name" id="ltcn">{text}</span>'
                 f'<span class="lt-role" id="ltcr">{role_tx}</span></div></div></div>')
        js = (f'  var OFF={start};\n'
              f'  gsap.set("#ltc",{{clipPath:"inset(0 100% 0 0)"}});\n'
              f'  gsap.set("#ltct",{{scaleY:0}});\n'
              f'  gsap.set("#ltcn",{{y:{round(W*0.011)},opacity:0}});\n'
              f'  gsap.set("#ltcr",{{y:{round(W*0.011)},opacity:0}});\n'
              f'  tl.to("#ltc",{{clipPath:"inset(0 0% 0 0)",duration:0.5,ease:"power3.out"}},0.1+OFF);\n'
              f'  tl.to("#ltct",{{scaleY:1,duration:0.42,ease:"power2.out"}},0.26+OFF);\n'
              f'  tl.to("#ltcn",{{y:0,opacity:1,duration:0.46,ease:"power3.out"}},0.32+OFF);\n'
              f'  tl.to("#ltcr",{{y:0,opacity:1,duration:0.46,ease:"power3.out"}},0.42+OFF);\n'
              f'  tl.to("#ltc",{{y:{round(W*0.016)},opacity:0,duration:0.35,ease:"power2.in"}},{out_at}+OFF);\n')
    elif st == "lt-soft-pill":
        # 官方 lt-soft-pill DNA: 圆pill 左下锚点 back.out(1.7) 弹出 + accent 状态点 back.out(3) + 文字滑入; 出场回落淡出
        fs_n2 = max(19, round(W * 0.028))
        fs_r2 = max(11, round(W * 0.014))
        css += (f"#ltp{{display:flex;align-items:center;gap:{round(W*0.011)}px;background:#F5F1E6;"
                f"border-radius:999px;padding:{round(W*0.010)}px {round(W*0.020)}px {round(W*0.010)}px {round(W*0.013)}px;"
                f"box-shadow:0 {round(W*0.013)}px {round(W*0.038)}px rgba(8,9,12,.42);transform-origin:0% 100%;"
                f"border:1px solid rgba(255,255,255,.16)}}"
                f"#ltpd{{width:{max(8, round(W*0.011))}px;height:{max(8, round(W*0.011))}px;border-radius:50%;"
                f"background:{accent};flex-shrink:0}}"
                f"#ltpt{{display:flex;flex-direction:column;gap:{round(W*0.004)}px}}"
                f"#ltp .lt-name{{font-size:{fs_n2}px;color:#141518}}"
                f"#ltp .lt-role{{font-size:{fs_r2}px;color:#565B66;letter-spacing:.10em}}")
        html += (f'<div id="ltp"><div id="ltpd"></div><div id="ltpt">'
                 f'<span class="lt-name" id="ltpn">{text}</span>'
                 f'<span class="lt-role" id="ltpr">{role_tx}</span></div></div></div>')
        js = (f'  var OFF={start};\n'
              f'  gsap.set("#ltp",{{scale:0.88,y:{round(W*0.016)},opacity:0}});\n'
              f'  gsap.set("#ltpd",{{scale:0}});\n'
              f'  gsap.set("#ltpn",{{x:-{round(W*0.009)},opacity:0}});\n'
              f'  gsap.set("#ltpr",{{x:-{round(W*0.009)},opacity:0}});\n'
              f'  tl.to("#ltp",{{scale:1,y:0,opacity:1,duration:0.55,ease:"back.out(1.7)"}},0.1+OFF);\n'
              f'  tl.to("#ltpd",{{scale:1,duration:0.4,ease:"back.out(3)"}},0.38+OFF);\n'
              f'  tl.to("#ltpn",{{x:0,opacity:1,duration:0.45,ease:"power3.out"}},0.4+OFF);\n'
              f'  tl.to("#ltpr",{{x:0,opacity:1,duration:0.45,ease:"power3.out"}},0.48+OFF);\n'
              f'  tl.to("#ltp",{{scale:0.94,y:{round(W*0.012)},opacity:0,duration:0.35,ease:"power2.in"}},{out_at}+OFF);\n')
    elif st == "lt-stack-bars":
        # 官方 lt-stack-bars DNA: 名条左擦入 + 角色条右擦入(对向), 出场对向擦出
        # 中文适配: 名条 accent 底(on_accent 深字, 黑底段可见), 角色条深底浅字+亮边框
        fs_n2 = max(19, round(W * 0.028))
        fs_r2 = max(11, round(W * 0.013))
        css += (f"#ltk2{{display:flex;flex-direction:column;align-items:flex-start;gap:{round(W*0.005)}px}}"
                f"#ltk2b{{background:{accent};padding:{round(W*0.008)}px {round(W*0.016)}px;will-change:clip-path}}"
                f"#ltk2b .lt-name{{font-size:{fs_n2}px;color:{on_accent};line-height:1.1}}"
                f"#ltk2r{{background:rgba(15,16,20,.88);border:1px solid rgba(255,255,255,.22);"
                f"padding:{round(W*0.005)}px {round(W*0.012)}px;margin-left:{round(W*0.008)}px;will-change:clip-path}}"
                f"#ltk2r .lt-role{{font-size:{fs_r2}px;color:#F2EFE6;letter-spacing:.14em;font-weight:700}}")
        html += (f'<div id="ltk2"><div id="ltk2b"><span class="lt-name" id="ltk2n">{text}</span></div>'
                 f'<div id="ltk2r"><span class="lt-role" id="ltk2rr">{role_tx}</span></div></div></div>')
        js = (f'  var OFF={start};\n'
              f'  gsap.set("#ltk2b",{{clipPath:"inset(0 100% 0 0)"}});\n'
              f'  gsap.set("#ltk2r",{{clipPath:"inset(0 0 0 100%)"}});\n'
              f'  tl.to("#ltk2b",{{clipPath:"inset(0 0% 0 0)",duration:0.5,ease:"power4.out"}},0.1+OFF);\n'
              f'  tl.to("#ltk2r",{{clipPath:"inset(0 0% 0 0%)",duration:0.5,ease:"power4.out"}},0.34+OFF);\n'
              f'  tl.to("#ltk2r",{{clipPath:"inset(0 0 0 100%)",duration:0.3,ease:"power3.in"}},{out_at}+OFF);\n'
              f'  tl.to("#ltk2b",{{clipPath:"inset(0 100% 0 0)",duration:0.34,ease:"power3.in"}},{out_at}+0.06+OFF);\n')
    elif st == "lt-bold-block":
        # 官方 lt-bold-block DNA: 实色块 clip 左擦入 + 大字 back.out 砸升 + 标签自 tagwrap 升出; 出场块右→左擦出
        # 中文适配: 反转配色 — 块 accent 底(on_accent 深字, 黑底段可见), 标签深底浅字
        fs_n2 = max(19, round(W * 0.030))
        fs_t2 = max(11, round(W * 0.014))
        css += (f"#ltbb{{background:{accent};padding:{round(W*0.009)}px {round(W*0.018)}px {round(W*0.011)}px {round(W*0.015)}px;"
                f"display:inline-flex;flex-direction:column;gap:{round(W*0.006)}px;will-change:clip-path}}"
                f"#ltbb .lt-name{{font-size:{fs_n2}px;color:{on_accent};line-height:1.08}}"
                f"#ltbtw{{overflow:hidden}}"
                f"#ltbt{{display:inline-block;background:rgba(15,16,20,.92);"
                f"padding:{round(W*0.004)}px {round(W*0.009)}px}}"
                f"#ltbt .lt-role{{font-size:{fs_t2}px;color:#F2EFE6;letter-spacing:.14em;font-weight:700}}")
        html += (f'<div id="ltbb"><span class="lt-name" id="ltbn2">{text}</span>'
                 f'<div id="ltbtw"><span id="ltbt"><span class="lt-role" id="ltbr2">{role_tx}</span></span></div></div></div>')
        js = (f'  var OFF={start};\n'
              f'  gsap.set("#ltbb",{{clipPath:"inset(0 100% 0 0)"}});\n'
              f'  gsap.set("#ltbn2",{{y:{round(W*0.016)},opacity:0}});\n'
              f'  gsap.set("#ltbt",{{y:{round(W*0.022)}}});\n'
              f'  tl.to("#ltbb",{{clipPath:"inset(0 0% 0 0)",duration:0.5,ease:"power4.out"}},0.1+OFF);\n'
              f'  tl.to("#ltbn2",{{y:0,opacity:1,duration:0.42,ease:"back.out(1.6)"}},0.26+OFF);\n'
              f'  tl.to("#ltbt",{{y:0,duration:0.45,ease:"back.out(2)"}},0.5+OFF);\n'
              f'  tl.to("#ltbb",{{clipPath:"inset(0 0 0 100%)",duration:0.4,ease:"power3.in"}},{out_at}+OFF);\n')
    elif st == "lt-mask-reveal":
        # 官方 lt-mask-reveal DNA: accent 竖光从左扫到右, 名字 clip 揭出, 角色淡入; 出场名字右擦出
        # 无卡最轻 — 白字+阴影黑底段直接可见; sweep 行程=JS 读容器实际宽(官方 SWEEP_TRAVEL 思路)
        fs_n2 = max(20, round(W * 0.034))
        fs_r2 = max(11, round(W * 0.015))
        css += (f"#ltm{{display:flex;flex-direction:column;align-items:flex-start;gap:{round(W*0.008)}px}}"
                f"#ltmw{{position:relative;overflow:hidden;padding:2px 0}}"
                f"#ltmn{{font-size:{fs_n2}px;color:#FAFAFA;"
                f"text-shadow:0 2px 16px rgba(0,0,0,.55);will-change:clip-path}}"
                f"#ltms{{position:absolute;top:0;bottom:0;left:0;width:{max(5, round(W*0.005))}px;"
                f"background:{accent};opacity:0}}"
                f"#ltmr{{font-size:{fs_r2}px;color:#F2EFE6;letter-spacing:.10em;"
                f"text-shadow:0 2px 12px rgba(0,0,0,.5)}}")
        html += (f'<div id="ltm"><div id="ltmw"><span class="lt-name" id="ltmn">{text}</span>'
                 f'<div id="ltms"></div></div>'
                 f'<span class="lt-role" id="ltmr">{role_tx}</span></div></div>')
        js = (f'  var OFF={start};\n'
              f'  var ltSW=document.getElementById("ltmw").clientWidth||300;\n'
              f'  gsap.set("#ltmn",{{clipPath:"inset(0 100% 0 0)"}});\n'
              f'  gsap.set("#ltmr",{{y:{round(W*0.008)},opacity:0}});\n'
              f'  tl.to("#ltms",{{opacity:1,duration:0.12,ease:"none"}},0.1+OFF);\n'
              f'  tl.to("#ltms",{{x:ltSW,duration:0.55,ease:"power2.inOut"}},0.12+OFF);\n'
              f'  tl.to("#ltmn",{{clipPath:"inset(0 0% 0 0)",duration:0.5,ease:"power2.inOut"}},0.16+OFF);\n'
              f'  tl.to("#ltms",{{opacity:0,duration:0.15,ease:"none"}},0.6+OFF);\n'
              f'  tl.to("#ltmr",{{y:0,opacity:1,duration:0.5,ease:"power3.out"}},0.55+OFF);\n'
              f'  tl.to("#ltmr",{{opacity:0,duration:0.3,ease:"power2.in"}},{out_at}+OFF);\n'
              f'  tl.to("#ltmn",{{clipPath:"inset(0 0 0 100%)",duration:0.4,ease:"power2.in"}},{out_at}+0.05+OFF);\n')
    elif st == "lt-accent-underline":
        # 官方 lt-accent-underline DNA: 名字升起 + accent 底线左→右描画 + 角色淡入; 出场角色隐/线收回/字上浮
        # 无标签极简款(kicker-name 去掉 chip 的生态位)
        fs_n2 = max(20, round(W * 0.033))
        fs_r2 = max(11, round(W * 0.015))
        css += (f"#ltau{{display:flex;flex-direction:column;align-items:flex-start;gap:{round(W*0.008)}px}}"
                f"#ltaun{{font-size:{fs_n2}px;color:#FAFAFA;text-shadow:0 2px 16px rgba(0,0,0,.55)}}"
                f"#ltaur{{height:{max(3, round(W*0.0035))}px;align-self:stretch;background:{accent};"
                f"border-radius:99px;transform-origin:0% 50%}}"
                f"#ltaul{{font-size:{fs_r2}px;color:#F2EFE6;letter-spacing:.10em;"
                f"text-shadow:0 2px 12px rgba(0,0,0,.5)}}")
        html += (f'<div id="ltau"><span class="lt-name" id="ltaun">{text}</span>'
                 f'<div id="ltaur"></div>'
                 f'<span class="lt-role" id="ltaul">{role_tx}</span></div></div>')
        js = (f'  var OFF={start};\n'
              f'  gsap.set("#ltaun",{{y:{round(W*0.014)},opacity:0}});\n'
              f'  gsap.set("#ltaur",{{scaleX:0}});\n'
              f'  gsap.set("#ltaul",{{y:{round(W*0.009)},opacity:0}});\n'
              f'  tl.to("#ltaun",{{y:0,opacity:1,duration:0.55,ease:"power3.out"}},0.1+OFF);\n'
              f'  tl.to("#ltaur",{{scaleX:1,duration:0.5,ease:"power4.out"}},0.3+OFF);\n'
              f'  tl.to("#ltaul",{{y:0,opacity:1,duration:0.5,ease:"power3.out"}},0.46+OFF);\n'
              f'  tl.to("#ltaul",{{opacity:0,duration:0.3,ease:"power2.in"}},{out_at}+OFF);\n'
              f'  tl.to("#ltaur",{{scaleX:0,duration:0.3,ease:"power2.in"}},{out_at}+0.05+OFF);\n'
              f'  tl.to("#ltaun",{{y:-{round(W*0.009)},opacity:0,duration:0.32,ease:"power2.in"}},{out_at}+0.1+OFF);\n')
    elif st == "lt-news-ticker":
        # 官方 news-ticker DNA: 横幅 panel 升入 + brand 块滑入 + 扫光 sheen + 爬条 x 线性位移 + 出场
        # 专项设计: 顶部横幅避让字幕带(覆盖 #ltwrap 锚点); liveDot 官方 repeat:11 禁 → 单次 yoyo 脉冲;
        #          爬条单程线性 no repeat, 行程=JS 读窗口宽/轨道宽
        fs_t = max(11, round(W * 0.0145))
        crawl = _html_esc(f"{text} · AI 生成 · 口播解读 · ") * 3
        css += (f"#ltwrap{{top:{round(H*0.055)}px;bottom:auto;left:{round(W*0.045)}px;right:{round(W*0.045)}px}}"
                f"#ltnt{{display:flex;align-items:stretch;border:1px solid rgba(255,255,255,.16);"
                f"border-radius:{round(W*0.012)}px;overflow:hidden;"
                f"background:linear-gradient(180deg,rgba(19,22,30,.96),rgba(9,11,16,.98));"
                f"box-shadow:0 {round(W*0.012)}px {round(W*0.03)}px rgba(0,0,0,.4)}}"
                f"#ltntb{{display:flex;align-items:center;gap:{round(W*0.008)}px;background:{accent};"
                f"padding:{round(W*0.009)}px {round(W*0.014)}px;flex-shrink:0}}"
                f"#ltntd{{width:{max(6, round(W*0.009))}px;height:{max(6, round(W*0.009))}px;border-radius:50%;"
                f"background:{on_accent};flex-shrink:0}}"
                f"#ltntb .lt-role{{font-size:{fs_t}px;color:{on_accent};letter-spacing:.22em;font-weight:700}}"
                f"#ltntw{{flex:1;position:relative;overflow:hidden}}"
                f"#ltntt{{position:absolute;left:{round(W*0.012)}px;top:0;height:100%;display:flex;align-items:center;"
                f"white-space:nowrap;will-change:transform}}"
                f"#ltntt .lt-name{{font-size:{round(fs_t*1.12)}px;color:#F2EFE6;font-weight:500;"
                f"letter-spacing:.06em}}"
                f"#ltntf{{position:absolute;top:0;bottom:0;left:-{round(W*0.14)}px;width:{round(W*0.1)}px;"
                f"background:linear-gradient(90deg,transparent,rgba(255,255,255,.16),transparent);"
                f"transform:skewX(-16deg);z-index:2}}")
        html += (f'<div id="ltnt"><div id="ltntb"><div id="ltntd"></div>'
                 f'<span class="lt-role" id="ltntbl">AI 快讯</span></div>'
                 f'<div id="ltntw"><div id="ltntt"><span class="lt-name" data-layout-allow-overlap>{crawl}</span></div>'
                 f'<div id="ltntf"></div></div></div></div>')
        js = (f'  var OFF={start};\n'
              f'  var ntW=document.getElementById("ltntw").clientWidth||{round(W*0.7)};\n'
              f'  var ntT=document.getElementById("ltntt").scrollWidth||{round(W*2)};\n'
              f'  gsap.set("#ltnt",{{y:-{round(W*0.03)},opacity:0}});\n'
              f'  gsap.set("#ltntb",{{x:-{round(W*0.03)},opacity:0}});\n'
              f'  gsap.set("#ltntt",{{x:ntW}});\n'
              f'  tl.to("#ltnt",{{y:0,opacity:1,duration:0.5,ease:"power4.out"}},0.1+OFF);\n'
              f'  tl.to("#ltntb",{{x:0,opacity:1,duration:0.42,ease:"power3.out"}},0.18+OFF);\n'
              f'  tl.fromTo("#ltntd",{{scale:1}},{{scale:1.35,duration:0.24,ease:"sine.inOut",yoyo:true,repeat:1}},0.5+OFF);\n'
              f'  tl.to("#ltntf",{{x:{round(W*1.1)},duration:0.9,ease:"power2.inOut"}},0.34+OFF);\n'
              f'  tl.to("#ltntt",{{x:-ntT,duration:{round(max(2.0, dur - 1.1), 2)},ease:"none"}},0.62+OFF);\n'
              f'  tl.to("#ltnt",{{y:-{round(W*0.02)},opacity:0,duration:0.34,ease:"power3.in"}},{out_at}+OFF);\n')
    else:  # lt-kicker-name
        # 官方 lt-kicker-name DNA: 标签chip落下(back.out) + 名字升起 + 底线 scaleX 描画; 出场反向
        css += (f"#ltk{{display:flex;flex-direction:column;align-items:flex-start;gap:{round(W*0.009)}px}}"
                f"#ltkw{{overflow:hidden}}"
                f"#ltkk{{display:inline-block;font-family:'Microsoft YaHei',sans-serif;font-weight:700;"
                f"font-size:{fs_kick}px;color:{on_accent};background:{accent};"
                f"padding:{round(W*0.004)}px {round(W*0.009)}px;letter-spacing:.18em;text-indent:.18em;"
                f"white-space:nowrap;border-radius:{round(W*0.0025)}px}}"
                f"#ltkn{{color:#FAFAFA;text-shadow:0 2px 20px rgba(0,0,0,.55)}}"
                f"#ltkb{{height:{max(3, round(W*0.003))}px;width:100%;background:{accent};"
                f"border-radius:99px;transform-origin:0% 50%}}")
        html += (f'<div id="ltk"><div id="ltkw"><span id="ltkk">今日看点</span></div>'
                 f'<span class="lt-name" id="ltkn">{text}</span>'
                 f'<div id="ltkb"></div></div></div>')
        js = (f'  var OFF={start};\n'
              f'  gsap.set("#ltkk",{{y:-{round(W*0.028)}}});\n'
              f'  gsap.set("#ltkn",{{y:{round(W*0.024)},opacity:0}});\n'
              f'  gsap.set("#ltkb",{{scaleX:0}});\n'
              f'  tl.to("#ltkk",{{y:0,duration:0.42,ease:"back.out(2)"}},0.1+OFF);\n'
              f'  tl.to("#ltkn",{{y:0,opacity:1,duration:0.45,ease:"back.out(1.5)"}},0.32+OFF);\n'
              f'  tl.to("#ltkb",{{scaleX:1,duration:0.5,ease:"power4.out"}},0.5+OFF);\n'
              f'  tl.to("#ltkb",{{scaleX:0,duration:0.3,ease:"power2.in"}},{out_at}+OFF);\n'
              f'  tl.to("#ltkn",{{y:-{round(W*0.011)},opacity:0,duration:0.32,ease:"power2.in"}},{out_at}+0.05+OFF);\n'
              f'  tl.to("#ltkk",{{y:-{round(W*0.028)},duration:0.3,ease:"power2.in"}},{out_at}+0.07+OFF);\n')
    return css, html, js


_GRAIN_CSS = ('#grain-overlay{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:100}'
              '#grain-overlay .grain-texture{position:absolute;top:-50%;left:-50%;width:200%;height:200%;opacity:.15;'
              "background:url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E"
              "%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' "
              "stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E\");"
              'animation:hf-grain-noise .5s steps(1) infinite}'
              '@keyframes hf-grain-noise{0%,100%{transform:translate(0,0)}10%{transform:translate(-5%,-5%)}'
              '20%{transform:translate(-10%,5%)}30%{transform:translate(5%,-10%)}40%{transform:translate(-5%,15%)}'
              '50%{transform:translate(-10%,5%)}60%{transform:translate(15%,0)}70%{transform:translate(0,10%)}'
              '80%{transform:translate(-15%,0)}90%{transform:translate(10%,5%)}}')


def _hf_leak_parts(total: float, W: int):
    """官方 organic-light-leak-overlay DNA: 三层暖调 radial-gradient 漏光 + mix-blend-mode:screen,
    包层 opacity 相位随 total 缩放, 三层 xPercent/scale/rotation 全长缓漂 — finite/零体积/screen 模式亮画面天然弱化"""
    d = max(4.0, float(total or 8.0))
    b1, b2, b3 = max(8, round(W * 0.012)), max(12, round(W * 0.018)), max(7, round(W * 0.009))
    css = (f"#hf-leak{{position:absolute;inset:-16%;pointer-events:none;z-index:11;opacity:0;overflow:hidden;"
           f"mix-blend-mode:screen}}"
           f"#hf-leak i{{position:absolute;inset:-20%;display:block;will-change:transform}}"
           f"#hf-leak .lk1{{background:radial-gradient(ellipse 52% 110% at 10% 58%,rgba(255,241,197,.62),transparent 45%),"
           f"radial-gradient(ellipse 72% 120% at 24% 54%,rgba(255,126,57,.50),transparent 66%);filter:blur({b1}px)}}"
           f"#hf-leak .lk2{{background:radial-gradient(ellipse 58% 105% at 78% 34%,rgba(255,79,61,.40),"
           f"rgba(255,44,92,.17) 48%,transparent 76%);filter:blur({b2}px)}}"
           f"#hf-leak .lk3{{background:linear-gradient(104deg,transparent 24%,rgba(255,144,62,.20) 40%,"
           f"rgba(255,247,205,.46) 51%,rgba(255,124,48,.15) 62%,transparent 78%);filter:blur({b3}px)}}")
    html = '<div id="hf-leak"><i class="lk1"></i><i class="lk2"></i><i class="lk3"></i></div>'
    js = (f'  tl.fromTo("#hf-leak",{{opacity:0}},{{opacity:0.62,duration:{round(d*0.24, 2)},ease:"sine.out"}},{round(d*0.08, 2)});\n'
          f'  tl.to("#hf-leak",{{opacity:0.34,duration:{round(d*0.2, 2)},ease:"sine.inOut"}},{round(d*0.42, 2)});\n'
          f'  tl.to("#hf-leak",{{opacity:0,duration:{round(d*0.26, 2)},ease:"sine.in"}},{round(d*0.68, 2)});\n'
          f'  tl.fromTo("#hf-leak .lk1",{{xPercent:-20,scale:0.92,rotation:-4}},'
          f'{{xPercent:18,scale:1.1,rotation:2,duration:{round(d, 2)},ease:"sine.inOut"}},0);\n'
          f'  tl.fromTo("#hf-leak .lk2",{{xPercent:20,yPercent:-8,scale:1.08}},'
          f'{{xPercent:-16,yPercent:8,scale:0.94,duration:{round(d, 2)},ease:"sine.inOut"}},0);\n'
          f'  tl.fromTo("#hf-leak .lk3",{{xPercent:-28,rotation:-6}},'
          f'{{xPercent:24,rotation:4,duration:{round(d, 2)},ease:"sine.inOut"}},0);\n')
    return css, html, js


def _hf_spot_parts(total: float):
    """官方 yt-feather-highlight DNA: 压暗层+羽化椭圆洞(mask radial-gradient), 洞位/洞径 CSS 变量由 GSAP 补间 —
    中文适配: 洞定主体位(中偏上 50%/42%), 全程极缓横漂, 压暗 0.42(官方 0.55 减淡, 口播不喧宾)"""
    css = (f"#hf-spot{{position:absolute;inset:0;pointer-events:none;z-index:11;opacity:0;background:rgba(6,8,12,.42);"
           f"-webkit-mask-image:radial-gradient(ellipse var(--srx) var(--sry) at var(--sx) var(--sy),"
           f"transparent 0%,transparent 52%,black 88%);"
           f"mask-image:radial-gradient(ellipse var(--srx) var(--sry) at var(--sx) var(--sy),"
           f"transparent 0%,transparent 52%,black 88%)}}")
    html = '<div id="hf-spot"></div>'
    out_at = round(max(1.8, float(total or 8.0) - 0.5), 2)
    js = (f'  gsap.set("#hf-spot",{{"--sx":"50%","--sy":"42%","--srx":"30%","--sry":"26%"}});\n'
          f'  tl.fromTo("#hf-spot",{{opacity:0}},{{opacity:1,duration:0.6,ease:"power2.out"}},0.3);\n'
          f'  tl.to("#hf-spot",{{"--sx":"44%",duration:{round(max(2.0, out_at - 1.2), 2)},ease:"sine.inOut"}},0.9);\n'
          f'  tl.to("#hf-spot",{{opacity:0,duration:0.4,ease:"power2.in"}},{out_at});\n')
    return css, html, js


def _hf_index_html(man: dict, cues: list, spec: dict) -> str:
    from html import escape as _hesc
    W, H, total = man["w"], man["h"], man["total"]
    pal, sub, extras = spec["palette"], spec["subtitle"], spec.get("extras", [])
    font = spec.get("font", "Microsoft YaHei")
    fs = max(18, round(W * 0.048 * float(sub.get("size_scale", 1.0))))
    shape = sub.get("shape", "pill")
    if shape == "bar":
        sub_css = (f".sub{{left:0;right:0;bottom:0;display:block}}"
                   f".subbox{{display:block;width:100%;text-align:center;border:none;"
                   f"border-radius:0;background:{pal['bg']};padding:.5em 1em}}")
    elif shape == "bare":
        sub_css = (f".subbox{{background:transparent;border:none;border-radius:0;padding:.1em .3em;"
                   f"-webkit-text-stroke:{max(1, fs // 16)}px rgba(10,10,14,.88);"
                   f"text-shadow:0 2px 0 {pal['accent']}, 0 4px 14px rgba(0,0,0,.55)}}")
    else:
        sub_css = (f".subbox{{background:{pal['bg']};border:1px solid {pal['border']};"
                   f"border-radius:{sub.get('radius', 12)}px;padding:.42em .8em}}")
    extra_css, extra_html = "", ""
    if "cut-marks" in extras:
        acc = pal["accent"]
        extra_css += (f".cm{{position:absolute;width:{round(W*0.03)}px;height:{round(W*0.03)}px;z-index:15;"
                      f"border-color:{acc};border-style:solid;border-width:0;opacity:.85}}"
                      f".cm.tl{{top:12px;left:12px;border-top-width:2px;border-left-width:2px}}"
                      f".cm.tr{{top:12px;right:12px;border-top-width:2px;border-right-width:2px}}"
                      f".cm.bl{{bottom:12px;left:12px;border-bottom-width:2px;border-left-width:2px}}"
                      f".cm.br{{bottom:12px;right:12px;border-bottom-width:2px;border-right-width:2px}}")
        extra_html += ('<div class="cm tl"></div><div class="cm tr"></div>'
                       '<div class="cm bl"></div><div class="cm br"></div>\n      ')
    if "num-badge" in extras:
        extra_css += (f".nb{{position:absolute;top:{round(H*0.025)}px;right:{round(W*0.035)}px;z-index:16;"
                      f"font-family:Georgia,serif;font-size:{round(W*0.09)}px;line-height:1;"
                      f"color:#fff;background:rgba(19,21,26,.5);padding:{round(W*0.012)}px {round(W*0.028)}px;"
                      f"border:2px solid {pal['accent']}}}")
        badges = []
        idx = 0
        for m in man["items"]:
            if m.get("role") != "item":
                continue
            idx += 1
            badges.append(f'<div class="nb" data-start="{m["start"]}" '
                          f'data-duration="{max(m["dur"] - 0.1, 0.5)}">{_roman(idx)}</div>')
        extra_html += "\n      ".join(badges) + "\n      "
    # 进度条: 所有构建必带(不再依赖 extras 勾选)。
    # 作用1: 口播视频的常规进度提示(杂志模板已长期使用); 作用2: 全时长连续补间保证
    # check 布局扫描(sweep)每个采样点几何指纹都变化, 杜绝大 DOM 构建误报 sweep_static。
    extra_css += (f"#pbw{{position:absolute;left:0;right:0;bottom:0;height:4px;z-index:21;background:rgba(0,0,0,.35)}}"
                  f"#pbar{{width:0%;height:100%;background:{pal['accent']}}}")
    extra_html += '<div id="pbw"><div id="pbar"></div></div>'
    if "watermark" in extras and spec.get("watermark_text"):
        # 位置: 右上徽章正下方 — 避开底部字幕带(杂志 bar 全宽)与徽章本体
        extra_css += (f"#wm{{position:absolute;top:{round(H*0.098)}px;right:{round(W*0.035)}px;z-index:22;"
                      f"font-size:{max(12, round(W*0.026))}px;letter-spacing:2px;color:#FFFFFF;"
                      f"background:rgba(19,21,26,.88);padding:{round(W*0.008)}px {round(W*0.02)}px;"
                      f"border-radius:6px;border:1px solid {pal['border']}}}")
        extra_html += f'<div id="wm">{_html_esc(spec["watermark_text"])}</div>'
    menu = spec.get("menu") or {}
    cap_style = menu.get("caption") or "bar"
    t_css = t_html = t_js = cap_css = cap_html = cap_js = a_css = a_html = a_js = mk_css = mk_html = ""
    lt_css = lt_html = lt_js = ""
    it = menu.get("intro_title") or {}
    if it.get("text"):
        t_css, t_html, t_js = _hf_title_parts(it, W, H, pal.get("accent", "#E6C478"))
    ltm = menu.get("lt") or {}
    if ltm.get("style") in _HF_LT_MENU and (ltm.get("text") or it.get("text")):
        lt_css, lt_html, lt_js = _hf_lt_parts(
            {"style": ltm["style"], "text": ltm.get("text") or it.get("text"),
             "start": ltm.get("start") or 2.1, "dur": ltm.get("dur") or 4.5},
            W, H, pal.get("accent", "#E6C478"))
    if menu.get("vignette"):
        # z 序压在所有文字层之下: 只给视频上氛围, 不遮挡/不影响文字对比度采样
        a_css += ("#hf-vignette{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:11;"
                  "background:radial-gradient(ellipse at center,transparent 46%,rgba(0,0,0,.62) 100%)}")
        a_html += '<div id="hf-vignette"></div>'
    if menu.get("grain"):
        a_css += (_GRAIN_CSS.replace("z-index:100", "z-index:12"))
        a_html += '<div id="grain-overlay"><div class="grain-texture"></div></div>'
    if menu.get("leak"):
        lk_css, lk_html, lk_js = _hf_leak_parts(total, W)
        a_css += lk_css
        a_html += lk_html
        a_js += lk_js
    if menu.get("spot"):
        sp_css, sp_html, sp_js = _hf_spot_parts(total)
        a_css += sp_css
        a_html += sp_html
        a_js += sp_js
    if menu.get("trans") in ("flash", "dip-black", "sweep", "wipe"):
        # 转场贴片: 边界来自 manifest.items 的 start(切点), z-14 压视频不压文字
        bounds = [it["start"] for it in (man.get("items") or []) if it.get("start", 0) > 1.5]
        tf_css, tf_html, tf_js = _hf_trans_parts(menu["trans"], bounds, W, H,
                                                 spec.get("palette", {}).get("accent", "#B6A884"))
        a_css += tf_css
        a_html += tf_html
        a_js += tf_js
    if menu.get("bgb") in ("aurora", "mesh"):
        # 黑底段背景: 仅铺在黑底替代段(video:false)窗口, z-3 让位字幕/信息条
        bg_windows = [(it["start"], it["start"] + it["dur"]) for it in (man.get("items") or [])
                      if it.get("role") == "item" and not it.get("video") and it.get("dur", 0) > 1.2]
        gb_css, gb_html, gb_js = _hf_bgb_parts(menu["bgb"], bg_windows, W, H, spec)
        a_css += gb_css
        a_html = gb_html + a_html   # 背景层垫底(同 z-3 无所谓, 语义上先铺背景)
        a_js += gb_js
    # 家族⑧片尾 / 家族⑨数据 / 家族⑩对比 装配
    cta_css = cta_html = cta_js = data_css = data_html = data_js = ""
    cmp_css = cmp_html = cmp_js = ""
    list_css = list_html = list_js = ""
    _ctexts = _cta_texts(spec)
    _dyk = next((x for x in (man.get("items") or [])
                 if x.get("role") == "item" and x.get("video") and x.get("dur", 0) > 3.0), None)
    if menu.get("cta") in _HF_CTA_MENU:
        # 片尾窗口: 优先 outro 段(收尾画面), 否则最后 min(4, total*0.28) 秒
        out_items = [x for x in (man.get("items") or []) if x.get("role") == "outro" and x.get("dur", 0) >= 2.2]
        if out_items:
            win = (out_items[-1]["start"], out_items[-1]["start"] + out_items[-1]["dur"])
        else:
            wd = min(4.0, max(2.6, total * 0.28))
            win = (max(0.0, total - wd), total)
        cta_css, cta_html, cta_js = _hf_cta_parts(menu["cta"], W, H, win, spec)
        a_js += cta_js
    if menu.get("data") in _HF_DATA_MENU:
        # 数据窗口: 首个数字人段内 40% 处, 时长 min(3.4, 段长*0.5)
        if _dyk:
            _ct = spec.get("cta") or {}
            dv, du = _first_number(cues)
            dv = (_ct.get("data_value") or dv or "").strip()
            du = (_ct.get("data_unit") or du or "").strip()
            dd = round(min(3.4, _dyk["dur"] * 0.5), 3)
            _dat = round(_dyk["start"] + _dyk["dur"] * 0.4, 3)
            if menu["data"] == "conic-ring":
                _pg = _num_or_zero(_ct.get("data_progress") or dv or "0")
                data_css, data_html, data_js = _hf_ring_parts(
                    W, H, _dat, dd, _pg, dv or str(int(_pg)), du,
                    (_ct.get("data_label") or "").strip(), pal.get("accent", "#E6C478"))
                a_js += data_js
            elif menu["data"] == "number-wheel" and (dv or "").strip():
                data_css, data_html, data_js = _hf_nwheel_parts(
                    W, H, _dat, dd, dv, du, (_ct.get("data_label") or "").strip(),
                    pal.get("accent", "#E6C478"))
                a_js += data_js
            elif dv:
                data_css, data_html, data_js = _hf_data_parts(
                    W, H, _dat, dd, dv, du,
                    (_ct.get("data_label") or "").strip(), pal.get("accent", "#E6C478"))
                a_js += data_js
    if menu.get("cmp") in _HF_CMP_MENU:
        # 对比窗口: 第二个数字人段(讲变化的常见位置), 无则首个段的 55% 处起
        items2 = [x for x in (man.get("items") or [])
                  if x.get("role") == "item" and x.get("video") and x.get("dur", 0) > 3.0]
        tgt = items2[1] if len(items2) > 1 else _dyk
        if tgt:
            cd = round(min(4.2, max(2.6, tgt["dur"] * 0.7)), 3)
            cstart = round(tgt["start"] + tgt["dur"] * (0.15 if len(items2) > 1 else 0.5), 3)
            # 与数据卡避让: 重叠则顺延到数据卡之后(段尾留 0.4s 余量, 否则贴尾)
            if data_html:
                dend = round(_dat + dd, 3)
                if cstart < dend + 0.3:
                    seg_end = round(tgt["start"] + tgt["dur"], 3)
                    cstart = round(min(dend + 0.3, max(seg_end - cd - 0.2, dend + 0.3)), 3)
            if menu["cmp"] == "split-tilt":
                cmp_css, cmp_html, cmp_js = _hf_tilt_parts(
                    W, H, cstart, cd, _ctexts, pal.get("accent", "#E6C478"))
            else:
                cmp_css, cmp_html, cmp_js = _hf_cmp_parts(
                    W, H, cstart, cd, _ctexts, pal.get("accent", "#E6C478"))
            a_js += cmp_js
    if menu.get("list") in _HF_LIST_MENU:
        # 清单卡窗口: 首个数字人段后段(讲要点), 与数据/对比窗口避让
        if _dyk:
            ld = round(min(5.0, max(3.2, _dyk["dur"] * 0.75)), 3)
            lstart = round(_dyk["start"] + _dyk["dur"] * 0.55, 3)
            busy_ends = []
            if data_html:
                busy_ends.append(_dat + dd)
            if cmp_html:
                busy_ends.append(cstart + cd)
            if busy_ends:
                seg_end = round(_dyk["start"] + _dyk["dur"], 3)
                lstart = round(min(max(busy_ends) + 0.3, max(seg_end - ld - 0.2, max(busy_ends) + 0.3)), 3)
            list_css, list_html, list_js = _hf_list_parts(W, H, lstart, ld, _ctexts, pal.get("accent", "#E6C478"))
            a_js += list_js
    if menu.get("top_mark"):
        mk_css = (f"#tmark{{position:absolute;top:{round(H*0.016)}px;left:0;right:0;display:flex;"
                  f"justify-content:center;z-index:30;pointer-events:none}}"
                  f".tmchip{{background:rgba(15,17,21,.88);color:#FFFFFF;"
                  f"font-size:{max(12, round(W*0.0235))}px;font-weight:500;letter-spacing:.24em;text-indent:.24em;"
                  f"padding:.32em .95em;border-radius:999px}}")
        mk_html = '<div id="tmark"><span class="tmchip">本视频由AI合成</span></div>'
    sub_html_str = ""
    tweens = ""
    if cap_style == "bar":
        sub_html, tween_lines = [], []
        for i, (s, d, c, ii) in enumerate(cues):
            ov = (spec.get("per_item") or {}).get(str(ii)) or {}
            if ov.get("hide"):
                continue
            style_attr = ""
            try:
                scc = float(ov.get("size_scale", 0))
                if 0.7 <= scc <= 1.6:
                    style_attr = f' style="font-size:{max(14, round(fs * scc))}px"'
            except Exception:
                pass
            sub_html.append(f'    <section class="sub" id="sub-{i}" data-start="{s}" '
                            f'data-duration="{max(d - 0.06, 0.4)}">'
                            f'<div class="subbox"{style_attr}>{_html_esc(c)}</div></section>')
            tween_lines.append(f'  tl.fromTo("#sub-{i} .subbox", {{ y: 16, autoAlpha: 0 }}, '
                               f'{{ y: 0, autoAlpha: 1, duration: 0.3, ease: "power2.out" }}, {s + 0.02});')
        sub_html_str = "\n".join(sub_html)
        tweens = "\n".join(tween_lines)
    else:
        cap_css, cap_html, cap_js = _hf_caps_parts(spec, cues, W, H, total)
    # 进度条补间: 与上方必带进度条配套(全时长线性, 保证布局扫描的几何指纹持续变化)
    tweens += f'\n  tl.to("#pbar", {{ width: "100%", duration: {total}, ease: "none" }}, 0);'
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title> koubo refine </title>
    <script src="gsap.min.js"></script><!-- GSAP 3.14.2 本地副本, 构建时由 vendor/ 复制, 渲染零联网 -->
    <style>
      * {{ margin:0; padding:0; box-sizing:border-box }}
      html, body {{ width:{W}px; height:{H}px; overflow:hidden; background:#000 }}
      body {{ font-family:"{font}", "Microsoft YaHei", sans-serif }}
      @font-face {{ font-family:"Microsoft YaHei"; src:local("Microsoft YaHei") }}
      @font-face {{ font-family:"SimSun"; src:local("SimSun") }}
      @font-face {{ font-family:"KaiTi"; src:local("KaiTi") }}
      #root {{ position:relative; width:{W}px; height:{H}px; overflow:hidden }}
      .sub {{ position:absolute; left:5%; right:5%; bottom:6.5%; z-index:20; display:flex; justify-content:center }}
      .subbox {{ display:inline-block; max-width:100%; font-size:{fs}px; line-height:1.4;
        letter-spacing:1px; color:{pal['text']}; font-weight:{sub.get('weight', 600)} }}
      {sub_css}
      {extra_css}
      {cap_css}
      {t_css}
      {lt_css}
      {a_css}
      {cta_css}
      {data_css}
      {cmp_css}
      {list_css}
      {mk_css}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-width="{W}" data-height="{H}" data-duration="{total}">
      <video id="base" class="clip" data-start="0" data-duration="{total}" src="base.mp4" muted playsinline></video>
      <audio id="voice" data-start="0" data-duration="{total}" data-volume="1.0" src="base_audio.m4a"></audio>
      {extra_html}
{sub_html_str}
      {t_html}
      {lt_html}
      {cap_html}
      {a_html}
      {cta_html}
      {data_html}
      {cmp_html}
      {list_html}
      {mk_html}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
{tweens}
{t_js}{lt_js}{cap_js}{a_js}
      window.__timelines = window.__timelines || {{}}
      window.__timelines["main"] = tl
    </script>
  </body>
</html>
"""

def _short_title(s: str, maxlen: int = 14) -> str:
    """选题段落 → 开场短标题: 取第一分句, 超长截断"""
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return ""
    first = re.split(r"[，。！？；：、—…,.;:!?]", s, 1)[0].strip()
    if not first:
        first = s
    return first[:maxlen]

@app.post("/api/edit/refine")
def api_edit_refine(body: dict):
    if _edit_job["running"]:
        raise HTTPException(409, "已有渲染任务在运行")
    d = _find_project(body.get("project") or "")
    if not (d / "output" / "初稿.mp4").exists() or not (d / "output" / "manifest.json").exists():
        raise HTTPException(400, "请先生成视频初稿")
    try:
        man = json.loads((d / "output" / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "manifest.json 损坏 — 请重新渲染初稿")
    style = str(body.get("style") or "").strip()
    prompt = _clean_path(str(body.get("prompt") or "")).strip()[:600]
    feedback = _clean_path(str(body.get("feedback") or "")).strip()[:800]
    intro_title = _short_title(_clean_path(str(body.get("intro_title") or "")), 14)
    menu = _normalize_menu(body.get("menu") or {})
    # 片尾/数据 家族的用户文案(可选, 留空用兜底: 关注我 下期见 / 点个关注 / 每期三分钟讲清一件事)
    cta_texts = {}
    for k, cap in (("action", 24), ("button", 12), ("brand", 16), ("proof", 30), ("data_label", 16), ("data_value", 10), ("data_unit", 6),
                    ("microcopy", 22), ("data_progress", 5), ("left_title", 8), ("left_text", 18), ("right_title", 8), ("right_text", 18),
                    ("list_title", 16), ("list_circled", 8), ("list1", 40), ("list2", 40), ("list3", 40)):
        v = str(body.get("cta_" + k) or "").strip()[:cap]
        if v:
            cta_texts[k] = v
    base_spec = None
    base_version = None
    versions = _hf_versions(d)
    v_next = (versions[-1]["v"] + 1) if versions else 1
    if feedback:
        bv = int(body.get("base_version") or 0)
        if bv > 0:
            if not any(x["v"] == bv for x in versions):
                raise HTTPException(400, f"v{bv} 不存在，无法作为修订基座")
            base_version = bv
            dj = d / "hf-designs" / f"v{base_version}.json"
            if not dj.exists():
                raise HTTPException(400, f"v{base_version} 的设计记录不存在")
            try:
                base_spec = json.loads(dj.read_text(encoding="utf-8"))["spec"]
            except Exception:
                raise HTTPException(500, "设计记录损坏 — 请重新精剪")
        # bv==0 → 基于初稿的反馈重设计: 不继承旧版设计, 反馈并入设计提示词
    else:
        if style not in ("ai", "custom") and style not in _HF_TEMPLATES:
            style = "ai"
    try:
        cfg_f = d / "edit.json"
        cfg = json.loads(cfg_f.read_text(encoding="utf-8")) if cfg_f.exists() else {}
        if not feedback:
            cfg["hf_style"], cfg["hf_prompt"] = style, prompt
        cfg["hf_feedback"] = feedback
        cfg["intro_title"], cfg["menu"] = intro_title, menu
        if cta_texts:
            cfg["cta_texts"] = cta_texts
        cfg_f.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    job = {"v": v_next, "style": style, "prompt": prompt, "feedback": feedback,
           "base_version": base_version, "base_spec": base_spec,
           "intro_title": intro_title, "menu": menu, "cta_texts": cta_texts,
           "topic": _rd_if(d, "选题.txt").strip()}
    _edit_job.update(running=True, stage="设计解析", pct=1, done=False, error="", output="",
                     kind="refine", v=v_next)
    threading.Thread(target=_refine_worker, args=(d, man, job), daemon=True).start()
    return {"ok": True, "v": v_next}

@app.get("/api/edit/refine/history")
def api_edit_refine_history(project: str = ""):
    d = _find_project(project)
    return {"versions": _hf_versions(d)}

def _refine_worker(d: Path, man: dict, job: dict):
    j = _edit_job
    npx = shutil.which("npx")
    try:
        if not npx:
            raise RuntimeError("未找到 npx — 请确认 Node.js 安装")
        v = job["v"]
        hf = d / "hf"
        if hf.exists():
            shutil.rmtree(hf)
        (hf / "renders").mkdir(parents=True)
        j["pct"] = 2
        menu = dict(job.get("menu") or {})
        # 🎲 随机解析(此前 random 从未被解析会静默落到兜底分支 — 修复): 每家族在合法 id 里抽
        _RANDOM_POOLS = {"title": list(_HF_TITLE_MENU), "caption": list(_HF_CAPTION_MENU),
                         "atmo": list(_HF_ATMO_MENU), "lt": list(_HF_LT_MENU),
                         "trans": list(_HF_TRANS_MENU), "bgb": list(_HF_BGB_MENU),
                         "cta": list(_HF_CTA_MENU), "data": list(_HF_DATA_MENU), "cmp": list(_HF_CMP_MENU)}
        _rand_picked = {}
        for fam, pool in _RANDOM_POOLS.items():
            if menu.get(fam) == "random":
                menu[fam] = random.choice(pool)
                _rand_picked[fam] = menu[fam]
        ai_why = {}
        ai_resolved = set()
        picks = None
        job_style = job.get("style")
        if job.get("feedback") and job.get("base_spec"):
            j["stage"] = f"设计修订（基于 v{job['base_version']}）"
            spec, design_note = _design_revise(job["base_spec"], job["feedback"], man,
                                               top_mark=bool(menu.get("top_mark")))
        else:
            if job_style == "ai" or "ai" in (menu.get("title"), menu.get("caption"),
                                             menu.get("atmo"), menu.get("lt"), menu.get("trans"),
                                             menu.get("bgb")):
                j["stage"] = "🤖 AI 选配（按内容挑组件）"
                segs = []
                for m_ in man["items"]:
                    if m_.get("role") == "item":
                        segs.append(re.sub(r"\s+", "", m_.get("text", ""))[:18])
                picks = _ai_menu(job.get("topic", ""), segs)
                ai_why = picks["why"]
                ai_resolved = set()
                for fam in ("title", "caption", "atmo", "lt", "trans", "bgb", "cta", "data", "cmp", "list"):
                    if menu.get(fam) == "ai":
                        # 容错: 池里缺该家族键时(或 LLM 未返回) 回退 none, 绝不 KeyError 中断整批
                        menu[fam] = picks["picks"].get(fam, "none")
                        ai_resolved.add(fam)
            j["stage"] = "设计解析（模板/LLM）"
            p = job["prompt"]
            if job.get("feedback"):
                p = (p + "\n用户反馈要求: " + job["feedback"]).strip()
            style_picked = job_style
            if job_style == "ai" and picks:
                style_picked = picks["picks"].get("style")
            spec, design_note = _design_spec(style_picked, p, man,
                                             top_mark=bool(menu.get("top_mark")))
            if job_style == "ai" and ai_why.get("style"):
                design_note += f"[AI:{ai_why['style']}]"
            if job.get("feedback"):
                design_note += "· 反馈重设计(基于初稿)"
        # 菜单层: 用户可控的官方组件选择, LLM 契约之外, 确定性注入
        spec["menu"] = {"caption": menu.get("caption") or "bar",
                        "grain": menu.get("atmo") == "grain",
                        "vignette": menu.get("atmo") == "vignette",
                        "leak": menu.get("atmo") == "light-leak",
                        "spot": menu.get("atmo") == "spotlight",
                        "trans": menu.get("trans") or "none",
                        "bgb": menu.get("bgb") or "none",
                        "cta": menu.get("cta") or "none",
                        "data": menu.get("data") or "none",
                        "cmp": menu.get("cmp") or "none",
                        "list": menu.get("list") or "none",
                        "top_mark": bool(menu.get("top_mark"))}
        if job.get("cta_texts"):
            spec["cta"] = dict(job["cta_texts"])   # 片尾/数据 用户文案
        if job.get("intro_title"):
            spec["menu"]["intro_title"] = {"text": job["intro_title"],
                                           "style": menu.get("title") or "tracking-in", "dur": 1.8}
            design_note += f"· 开场标题({_HF_TITLE_MENU.get(spec['menu']['intro_title']['style'], '聚焦显字').split('(')[0]})"
            if "title" in ai_resolved and ai_why.get("title"):
                design_note += f"[AI:{ai_why['title']}]"
        if menu.get("caption"):
            design_note += f"· 字幕({_HF_CAPTION_MENU.get(menu['caption'], '现有字幕条').split('(')[0]})"
            if "caption" in ai_resolved and ai_why.get("caption"):
                design_note += f"[AI:{ai_why['caption']}]"
        atmos = [n for n, on in (("胶片颗粒", spec["menu"]["grain"]), ("电影暗角", spec["menu"]["vignette"]),
                                 ("暖调漏光", spec["menu"]["leak"]), ("羽化聚光", spec["menu"]["spot"])) if on]
        if atmos:
            design_note += "· 氛围(" + "+".join(atmos) + ")"
            if "atmo" in ai_resolved and ai_why.get("atmo"):
                design_note += f"[AI:{ai_why['atmo']}]"
        if menu.get("top_mark"):
            design_note += "· 顶部合规标识"
        if menu.get("trans") in ("flash", "dip-black", "sweep", "wipe"):
            design_note += f"· 转场({_TR_NAMES[menu['trans']]})"
            if "trans" in ai_resolved and ai_why.get("trans"):
                design_note += f"[AI:{ai_why['trans']}]"
        if menu.get("bgb") in ("aurora", "mesh"):
            design_note += f"· 黑底段背景({_HF_BGB_MENU[menu['bgb']].split('(')[0]})"
            if "bgb" in ai_resolved and ai_why.get("bgb"):
                design_note += f"[AI:{ai_why['bgb']}]"
        if _rand_picked:
            _MENUS_BY_FAM = {"title": _HF_TITLE_MENU, "caption": _HF_CAPTION_MENU,
                             "atmo": _HF_ATMO_MENU, "lt": _HF_LT_MENU, "trans": _TR_NAMES}
            _rnd = []
            for fam, pid in _rand_picked.items():
                nm = _MENUS_BY_FAM[fam].get(pid, pid)
                nm = pid if fam == "trans" else nm.split("(")[0]
                _rnd.append(f"{fam}={nm}")
            design_note += "｜随机命中:" + "+".join(_rnd)
        lt_text = job.get("intro_title") or _short_title(job.get("topic", ""), 14)
        if menu.get("lt") and menu["lt"] != "none" and lt_text:
            spec["menu"]["lt"] = {"style": menu["lt"], "text": lt_text, "start": 2.1, "dur": 4.5}
            design_note += f"· 信息条({_HF_LT_MENU.get(menu['lt'], '').split('(')[0]})"
            if "lt" in ai_resolved and ai_why.get("lt"):
                design_note += f"[AI:{ai_why['lt']}]"
        if job_style == "ai" or ai_why:
            design_note += "｜想调整→对应家族手选 / 改设计提示词 / 按反馈重剪"
        elif job_style == "custom":
            design_note += "｜自由模式:设计提示词就是全部, 配色对比度护栏仍生效"
        shutil.copy(d / "output" / "初稿.mp4", hf / "base.mp4")
        # GSAP 本地化: 渲染不再联网拉 CDN(断网可渲染, 版本锁定 3.14.2, 兼容将来 exe 打包)
        gsap_src = RES / "vendor" / "gsap.min.js"
        if not gsap_src.exists():
            raise RuntimeError(f"缺少本地 GSAP 库: {gsap_src} (首次部署请从 CDN 下载 gsap@3.14.2/dist/gsap.min.js)")
        shutil.copy(gsap_src, hf / "gsap.min.js")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(d / "output" / "初稿.mp4"),
                        "-vn", "-c:a", "aac", "-b:a", "192k", str(hf / "base_audio.m4a")],
                       check=True, timeout=300)
        cues = []
        idx = 0
        for m in man["items"]:
            if m.get("role") != "item":
                continue
            idx += 1
            cues += [(a, b, c, idx) for (a, b, c) in
                     _split_cues(m.get("text", ""), m["start"] + 0.05, max(m["dur"] - 0.1, 0.8))]
        (hf / "index.html").write_text(_hf_index_html(man, cues, spec), encoding="utf-8")
        (hf / "design.json").write_text(json.dumps(
            {"v": v, "style": job["style"], "prompt": job["prompt"], "feedback": job["feedback"],
             "base_version": job.get("base_version"), "spec": spec, "note": design_note},
            ensure_ascii=False, indent=1), encoding="utf-8")
        (hf / "package.json").write_text(json.dumps({
            "name": "koubo-hf", "private": True, "type": "module",
            "scripts": {"check": f"npx --yes hyperframes@{_HF_VERSION} check",
                        "render": f"npx --yes hyperframes@{_HF_VERSION} render"}}, indent=1), encoding="utf-8")
        (hf / "hyperframes.json").write_text(json.dumps({
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
            "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
            "media": {"autoProxy": True}}, ensure_ascii=False, indent=1), encoding="utf-8")
        j["stage"] = "HF check（lint+布局+对比度）"
        j["pct"] = 8
        r = subprocess.run([npx, "--yes", f"hyperframes@{_HF_VERSION}", "check"],
                           cwd=str(hf), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
        if r.returncode != 0:
            raise RuntimeError("HF check 未通过:\n" + ((r.stdout or "") + (r.stderr or ""))[-900:])
        j["stage"] = "HF 渲染（high 质量）"
        est = max(man.get("total", 30) * 1.1, 40)
        t0 = time.time()
        with open(hf / "render.log", "wb") as logf:
            p = subprocess.Popen([npx, "--yes", f"hyperframes@{_HF_VERSION}", "render",
                                  "--quality", "high", "--output", "renders/final.mp4"],
                                 cwd=str(hf), stdout=subprocess.DEVNULL, stderr=logf)
            while p.poll() is None:
                el = time.time() - t0
                j["pct"] = int(15 + min(82.0, el / est * 82))
                time.sleep(2)
        if p.returncode != 0:
            tail = (hf / "render.log").read_text(encoding="utf-8", errors="replace")[-900:]
            raise RuntimeError("HF render 失败:\n" + tail)
        out = hf / "renders" / "final.mp4"
        if not out.exists() or out.stat().st_size < 10000:
            raise RuntimeError("HF render 未产出有效视频")
        final_v = d / "output" / f"精剪v{v}.mp4"
        shutil.copy(out, final_v)
        final = d / "output" / "成片.mp4"
        shutil.copy(out, final)
        designs = d / "hf-designs"
        designs.mkdir(exist_ok=True)
        (designs / f"v{v}.json").write_text(json.dumps(
            {"v": v, "style": job["style"], "prompt": job["prompt"], "feedback": job["feedback"],
             "base_version": job.get("base_version"), "spec": spec, "note": design_note},
            ensure_ascii=False, indent=1), encoding="utf-8")
        j.update(pct=100, done=True, running=False, stage=f"精剪 v{v} 完成 · {design_note}",
                 output=str(final), v=v)
    except Exception as e:
        j.update(running=False, done=False, stage="精剪失败", error=f"{type(e).__name__}: {e}")

@app.get("/video/{name}/{fname}")
def video(name: str, fname: str):
    d = _find_project(name)
    if "/" in fname or "\\" in fname or ".." in fname:
        raise HTTPException(400, "bad file")
    f = d / "videos" / fname
    if not f.exists():
        raise HTTPException(404, "视频不存在")
    return FileResponse(f, media_type="video/mp4", headers={"Cache-Control": "no-cache"})

@app.get("/api/avatar/preview")
def api_avatar_preview(path: str = ""):
    """形象图缩略预览: 用户手动填的本地路径, 存在且是图片才返回"""
    p = Path(path)
    if not path or not p.exists() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        raise HTTPException(404, "图片不存在")
    return FileResponse(p)

# ---------------- 页面 ----------------
@app.get("/", response_class=HTMLResponse)
def index():
    # no-cache: 界面内嵌在本文件里, 改版后浏览器必须回源拿新 HTML(否则用户验收看不到变化)
    return HTMLResponse(HTML, headers={"Cache-Control": "no-cache"})

HTML = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>口播辅助创作台</title>
<style>
:root{--bg:#0a0a0d;--bg2:#0d0d11;--card:#121217;--card2:#16161b;--line:#232329;--line2:#303039;
  --tx:#eae6dc;--dim:#8d897c;--gold:#b6a884;--gold2:#cdc2a6;--golddim:#8a8069;--goldbg:rgba(182,168,132,.08)}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1100px 520px at 50% -8%,#12121a 0%,var(--bg) 60%);color:var(--tx);
  font-family:"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px}
.wrap{max-width:1020px;margin:0 auto;padding:26px 18px 80px}
a{color:var(--gold2)}
/* ---- 头部 ---- */
.masthead{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid var(--line);
  padding-bottom:14px;margin-bottom:10px}
.masthead h1{font-size:26px;font-weight:600;margin:0;letter-spacing:2px;color:var(--tx);
  font-family:"Noto Serif SC","Source Han Serif SC","SimSun",serif}
.masthead h1 i{font-style:normal;color:var(--gold);font-size:11px;letter-spacing:2px;vertical-align:4px;margin-left:8px;
  font-family:"Segoe UI","Microsoft YaHei",sans-serif}
.sub{color:var(--dim);font-size:12px;margin:8px 0 0}
.sub b{color:var(--golddim);font-weight:500}
.flownav{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 18px}
.flownav a{font-size:11.5px;color:var(--dim);text-decoration:none;border:1px solid var(--line);border-radius:999px;
  padding:3px 11px;cursor:pointer}
.flownav a:hover{color:var(--gold2);border-color:var(--golddim)}
/* ---- 卡片 ---- */
.card{background:linear-gradient(180deg,var(--card) 0%,var(--card2) 100%);border:1px solid var(--line);
  border-radius:12px;padding:18px;margin-bottom:18px}
.card:hover{border-color:var(--line2)}
.card h3{margin:0 0 16px;font-size:16px;font-weight:600;color:var(--gold2);letter-spacing:.5px;
  display:flex;align-items:center;gap:10px;
  font-family:"Noto Serif SC","Source Han Serif SC","SimSun",serif}
.card h3 .n{width:22px;height:22px;border:1px solid var(--golddim);border-radius:50%;display:inline-flex;
  align-items:center;justify-content:center;font-size:12px;color:var(--gold);flex:none;
  font-family:"Segoe UI","Microsoft YaHei",sans-serif}
.card h3 .tail{margin-left:auto;font-weight:400;font-family:"Segoe UI","Microsoft YaHei",sans-serif;font-size:12px}
input[type=text],input[type=password],textarea,select{width:100%;background:#0d0d12;border:1px solid var(--line);
  border-radius:8px;color:var(--tx);padding:9px 12px;font-size:14px;font-family:inherit}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--golddim);box-shadow:0 0 0 1px rgba(182,168,132,.12)}
textarea{min-height:110px;resize:vertical;line-height:1.7}
button{padding:7px 15px;border-radius:8px;border:1px solid var(--line2);background:#191920;color:var(--tx);
  cursor:pointer;font-size:13px;transition:border-color .15s,background .15s}
button:hover{border-color:var(--golddim);background:#1f1f27}
button.sm{padding:4px 10px;font-size:12px}
button.acc{border-color:var(--golddim);color:var(--gold2);background:linear-gradient(180deg,rgba(182,168,132,.12),rgba(182,168,132,.04))}
button.acc:hover{background:rgba(182,168,132,.18);border-color:var(--gold)}
button.primary{background:linear-gradient(180deg,#b6a884,#9d8f6e);color:#17140d;font-weight:600;border:none}
button.primary:hover{background:var(--gold2)}
button.confirm-pending{background:rgba(196,88,88,.06);border:1px solid rgba(196,88,88,.22);color:rgba(224,138,138,.62)}
button.confirm-pending:hover{background:rgba(196,88,88,.12);color:rgba(232,152,152,.9)}
button.confirm-done{background:rgba(46,158,91,.13);border:1px solid rgba(46,158,91,.38);color:#7fcf9f;font-weight:600}
button.confirm-done:hover{background:rgba(46,158,91,.22)}
button.danger{border-color:#4d3a3a;color:#d8a8a8}
button:disabled{opacity:.4;cursor:not-allowed}
/* 合成状态显性化(通用): 生成中(琥珀呼吸) / 生成完毕(绿闪) — ④与工作台共用 */
button.busy{opacity:.75;pointer-events:none;background:linear-gradient(180deg,#8a7a52,#6e6244);color:#fff;
  animation:pulseGold 1.2s ease-in-out infinite}
@keyframes pulseGold{0%,100%{filter:brightness(1)}50%{filter:brightness(1.45)}}
button.done{background:linear-gradient(180deg,#2e9e5b,#1f7a44)!important;color:#fff!important;font-weight:700;
  animation:doneFlash .5s ease 2}
@keyframes doneFlash{0%,100%{filter:brightness(1)}50%{filter:brightness(1.6)}}
.card.working{border-color:var(--gold2);box-shadow:0 0 0 1px var(--gold2),0 0 22px rgba(182,168,132,.18)}
.card.ok{border-color:#2e9e5b;box-shadow:0 0 0 1px #2e9e5b,0 0 22px rgba(46,158,91,.22)}
/* 任务运行中: 四角金色取景角标缓慢呼吸(大卡片与分段小卡片同一套视觉语言) */
.card.stage-running,.segcard.stage-running{position:relative}
.card.stage-running::before,.card.stage-running::after,
.segcard.stage-running::before,.segcard.stage-running::after{
  content:"";position:absolute;width:13px;height:13px;pointer-events:none;
  border:2px solid var(--gold);animation:cornerBreath 3.2s ease-in-out infinite}
.card.stage-running::before,.segcard.stage-running::before{
  top:-1px;left:-1px;border-right:none;border-bottom:none;border-radius:7px 0 0 0}
.card.stage-running::after,.segcard.stage-running::after{
  bottom:-1px;right:-1px;border-left:none;border-top:none;border-radius:0 0 7px 0}
@keyframes cornerBreath{0%,100%{opacity:.25}50%{opacity:.8}}
/* 彩蛋: Live2D 黑猫(长任务时出现陪伴, 不遮挡: pointer-events:none + 贴视口左右留白)
   注: Live2D 运行时会持续重写 canvas 内联样式(left/opacity/z-index/border 等),
       故定位/显隐/层级一律用带 !important 的类控制, 内联样式永远覆盖不了 */
#live2dcanvas{pointer-events:none!important;z-index:5!important;border:none!important;
  width:200px!important;height:260px!important;opacity:0!important;visibility:hidden!important;
  transition:opacity .8s}
#live2dcanvas.cat-show{opacity:.9!important;visibility:visible!important}
#live2dcanvas.cat-lb{left:2%!important;right:auto!important;bottom:0!important;top:auto!important}
#live2dcanvas.cat-rb{right:2%!important;left:auto!important;bottom:0!important;top:auto!important}
#live2dcanvas.cat-lt{left:2%!important;right:auto!important;top:16%!important;bottom:auto!important}
#live2dcanvas.cat-rt{right:2%!important;left:auto!important;top:16%!important;bottom:auto!important}
/* 项目门禁: 未建/未选项目时, 项目卡金框呼吸提醒 */
.card.need-attention{border-color:var(--gold);animation:needPulse 1.8s ease-in-out infinite}
@keyframes needPulse{0%,100%{box-shadow:0 0 0 0 rgba(182,168,132,0)}50%{box-shadow:0 0 20px 3px rgba(182,168,132,.35)}}
/* 猫气泡彩蛋: 流程未就绪时黑猫头上依次冒气泡(位置类与猫联动) */
#catBubble{position:fixed;z-index:7;max-width:270px;padding:10px 14px;background:#14141a;
  border:1px solid var(--gold);border-radius:12px;color:var(--gold2);font-size:13px;line-height:1.55;
  box-shadow:0 6px 24px rgba(0,0,0,.5);opacity:0;visibility:hidden;pointer-events:none;
  transition:opacity .35s,transform .35s;transform:translateY(8px)}
#catBubble.cat-show{opacity:1;visibility:visible;transform:translateY(0)}
#catBubble::after{content:'';position:absolute;bottom:-9px;left:32px;border:9px solid transparent;
  border-top-color:var(--gold);border-bottom:none;border-left-width:6px;border-right-width:6px}
#catBubble.cat-lb{left:calc(2% + 168px);bottom:170px}
#catBubble.cat-rb{right:calc(2% + 168px);bottom:170px}
#catBubble.cat-rb::after{left:auto;right:32px}
#catBubble.cat-lt{left:calc(2% + 168px);top:calc(16% + 6px)}
#catBubble.cat-rt{right:calc(2% + 168px);top:calc(16% + 6px)}
#catBubble.cat-rt::after{left:auto;right:32px}
.avseg.queued{border-style:dashed;border-color:#5a7ea6;background:rgba(90,126,166,.05)}
/* 引擎左右切换: 柔和描边选中(不用实底金), 紧凑尺寸 */
.engbtn{flex:1;text-align:center;padding:5px 8px;border-radius:8px;background:#14141a;border:1px solid var(--line)}
.engbtn:hover{border-color:var(--golddim)}
.engbtn .et{font-weight:700;font-size:13px;color:var(--tx)}
.engbtn .es{font-size:11px;margin-top:1px}
.engbtn.on{border-color:var(--gold);background:var(--goldbg);box-shadow:inset 0 0 0 1px rgba(182,168,132,.18)}
.engbtn.on .et{color:var(--gold2)}
/* 逐段完成反馈: 段卡绿脉冲一次 + 按钮短促绿闪 */
.segcard.flash-ok,.avseg.flash-ok{border-color:#2e9e5b;box-shadow:0 0 16px rgba(46,158,91,.4);transition:box-shadow .3s}
/* 资产条状态淡绿(克制, 按资产各自状态, 不染整卡): fresh 金光优先, 试听后回落淡绿 */
.segcard .asset.audio.tts-done{border-left-color:#2e9e5b;background:rgba(46,158,91,.05)}
.segcard .asset.audio.tts-done .alabel{color:#7fcf9f}
.segcard .asset.video.vid-done{border-left-color:#2e9e5b;background:rgba(46,158,91,.05)}
.segcard .asset.video.vid-done .alabel{color:#7fcf9f}
.segcard .asset.video.running.vid-done{border-left-color:#7ea6d8;box-shadow:none;background:none}
button.tick{animation:doneFlash .45s ease 1}
/* 数字人提交状态行: 提交中(金)/已提交(绿)/失败(红) — 独占一行常驻可见 */
.vinfo{font-size:12px;flex-basis:100%}
.vinfo.sending{color:var(--gold2)}
.vinfo.ok{color:#8fcf9f}
.vinfo.err{color:#e08a8a}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.muted{color:var(--dim);font-size:12px}
.chip{display:inline-flex;align-items:center;gap:5px;background:#101015;border:1px solid var(--line);
  border-radius:999px;padding:4px 12px;font-size:12px;cursor:pointer;user-select:none}
.chip input{accent-color:var(--gold)}
.chip.on{border-color:var(--golddim);background:var(--goldbg);color:var(--gold2)}
.vpill{display:inline-flex;align-items:center;background:#101015;border:1px solid var(--line);
  border-radius:999px;padding:4px 14px;font-size:12px;cursor:pointer;user-select:none;color:var(--fg2)}
.vpill.on{border-color:var(--gold);background:var(--goldbg);color:var(--gold2);font-weight:700}
.vpill:hover{border-color:var(--golddim)}
.pbar{height:4px;background:#0d0d12;border:1px solid var(--line);border-radius:4px;overflow:hidden;margin:10px 0 4px;display:none}
.pbar>div{height:100%;width:0%;transition:width .8s;position:relative;background:linear-gradient(90deg,#877a5c,var(--gold),#877a5c)}
.pbar>div::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.25),transparent);
  animation:shine 1.8s linear infinite}
@keyframes shine{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
.step{font-size:12px;color:var(--golddim);min-height:16px}
.summary{background:#0d0d12;border:1px solid var(--line);border-radius:8px;padding:10px 12px;max-height:76px;
  overflow:hidden;position:relative;font-size:13px;line-height:1.6;color:#c6c1b4;white-space:pre-wrap;margin-top:10px}
.summary::after{content:"";position:absolute;bottom:0;left:0;right:0;height:26px;background:linear-gradient(transparent,var(--card2))}
.summary.empty{color:#54514a}
.summary.empty::after{display:none}
.mrow{display:flex;justify-content:space-between;align-items:center;margin-top:6px;gap:8px;flex-wrap:wrap}
.badge{font-size:11px;padding:2px 9px;border-radius:999px;border:1px solid var(--line);color:var(--dim);flex:none}
.badge.gold{color:var(--gold2);border-color:var(--golddim);background:var(--goldbg)}
.badge.kept{color:#a8c8a0;border-color:#3d5744}
.badge.deleted{color:#d8a0a8;border-color:#4f3038}
/* 状态徽标语义色: 绿=已生成 / 金=生成中 / 蓝=排队 / 灰=未生成 / 红=失败 */
.badge.ok{color:#7fcf9f;border-color:rgba(46,158,91,.45);background:rgba(46,158,91,.08)}
.badge.wait{color:var(--gold2);border-color:var(--golddim);background:var(--goldbg)}
.badge.lineup{color:#8fb2d8;border-color:#46608a;background:rgba(90,126,166,.1)}
.badge.gray{color:var(--dim);border-color:var(--line);background:#101015}
.badge.bad{color:#e08a8a;border-color:#5a3636;background:rgba(196,88,88,.08)}
/* 分段卡片: 每段一张卡, 声音/视频资产条收在同一份子项里 */
#segList{display:grid;grid-template-columns:repeat(auto-fill,minmax(390px,1fr));gap:10px}
.segcard{border:1px solid var(--line);border-radius:10px;background:#0d0d12;padding:10px 11px;
  display:flex;flex-direction:column;gap:8px;transition:border-color .15s,box-shadow .15s}
.segcard.deleted{opacity:.5;border-style:dashed}
.segcard.fresh{border-color:var(--golddim);box-shadow:0 0 16px rgba(182,168,132,.14)}
.segcard .shead{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.segcard .shead .id{font-size:11px;color:var(--golddim);font-family:Consolas,monospace}
.segcard .shead .len{font-size:11px;color:var(--dim);margin-left:auto}
.segcard .stext{font-size:12.5px;line-height:1.6;color:#c9c4b6}
.segcard.deleted .stext{color:#54545e;text-decoration:line-through}
.segcard .asset{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#0c0c10;
  border:1px solid #1c1c23;border-left:2px solid var(--golddim);border-radius:0 8px 8px 8px;padding:7px 9px}
.segcard .asset .alabel{font-size:11px;letter-spacing:.5px;flex:none}
.segcard .asset.audio{border-left-color:var(--gold)}
.segcard .asset.audio .alabel{color:var(--gold2)}
.segcard .asset.video{border-left-color:#5a7ea6}
.segcard .asset.video .alabel{color:#8fb2d8}
.segcard .asset video{width:100%;max-width:100%;flex-basis:100%;border-radius:8px;border:1px solid var(--line)}
.segcard .asset audio{height:36px;width:100%;flex-basis:100%}
.segcard .asset.video.running{border-left-color:#7ea6d8;box-shadow:0 0 12px rgba(90,126,166,.25)}
.freshjump{animation:breathbadge 2.6s ease-in-out infinite}
@keyframes breathbadge{0%,100%{opacity:.55}50%{opacity:1}}
.menufam details{border:1px solid var(--line);border-radius:8px;padding:6px 12px;margin-top:6px}
.menufam summary{cursor:pointer;user-select:none;color:var(--ink2,#c8c8d0)}
.menufam summary b{color:var(--gold2)}
.menufam details label{display:inline-flex;align-items:center;gap:4px;margin:0 6px 6px 0;padding:3px 11px;
  border:1px solid var(--line2);border-radius:999px;cursor:pointer;font-size:12.5px;color:var(--dim);
  background:var(--card2);transition:border-color .15s,color .15s,background .15s;user-select:none}
.menufam details label:hover{border-color:var(--golddim);color:var(--tx)}
.menufam details label:has(input:checked){border-color:var(--gold);background:var(--goldbg);color:var(--gold2);font-weight:600}
.menufam details label input{position:absolute;opacity:0;width:0;height:0;pointer-events:none}
.menufam details .muted{display:block}
.spin{display:inline-block;width:11px;height:11px;border:2px solid #3a3a3a;border-top-color:var(--gold);
  border-radius:50%;animation:sp 1s linear infinite;vertical-align:-2px;margin-right:5px}
@keyframes sp{to{transform:rotate(360deg)}}
#toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:#191920;border:1px solid var(--golddim);
  border-radius:999px;padding:10px 18px;font-size:13px;display:none;z-index:99;max-width:80vw}
.modal{position:fixed;inset:0;background:rgba(5,5,8,.72);backdrop-filter:blur(3px);display:none;z-index:20;  align-items:center;justify-content:center}
.modal .box{background:var(--card);border:1px solid var(--golddim);border-radius:12px;padding:20px;width:470px;max-width:92vw}
/* 页脚: 版权 + 社交图标 */
.site-foot{margin:26px 0 10px;padding:18px 12px 8px;border-top:1px solid var(--line);text-align:center;color:var(--dim);font-size:12.5px}
.site-foot .sf-links{display:flex;justify-content:center;gap:18px;margin-top:12px;flex-wrap:wrap}
.site-foot .sf-links a{color:#7e7a70;display:inline-flex;align-items:center;transition:color .18s,transform .18s}
.site-foot .sf-links a:hover{color:var(--gold2);transform:translateY(-2px)}
.site-foot .sf-links svg{width:24px;height:24px;fill:currentColor}
.site-foot .sf-tip{margin-top:10px;color:#5c594f;font-size:11.5px}
/* 点击黑猫热区(不挡其他内容: 仅猫出现时启用, 位置与 canvas 同步) */
#catHot{position:fixed;z-index:6;width:200px;height:260px;display:none;cursor:pointer;border-radius:14px}
#catHot.cat-show{display:block}
#catHot.cat-lb{left:2%;bottom:0}#catHot.cat-rb{right:2%;bottom:0}
#catHot.cat-lt{left:2%;top:16%}#catHot.cat-rt{right:2%;top:16%}
#catHot::after{content:"点我";position:absolute;left:50%;bottom:6px;transform:translateX(-50%);
  font-size:11px;color:var(--gold2);background:rgba(12,13,17,.86);border:1px solid var(--golddim);
  border-radius:999px;padding:2px 10px;opacity:0;transition:opacity .2s}
#catHot:hover::after{opacity:1}
/* 「关于」弹窗 */
#aboutModal{z-index:1000}
#aboutModal .box{width:min(920px,95vw);max-height:92vh;overflow-y:auto}
#aboutModal h2{margin:2px 0 4px;font-size:20px;color:var(--gold2);letter-spacing:.02em}
#aboutModal .am-sub{color:#b9b3a4;font-size:13px;line-height:1.9;margin:0 0 14px}
#aboutModal .am-p{color:#c6c1b4;font-size:13px;line-height:1.95;margin:0 0 12px}
#aboutModal .am-sec{margin:16px 0 8px;color:var(--gold2);font-size:13.5px;font-weight:700;letter-spacing:.06em;
  display:flex;align-items:center;gap:8px}
#aboutModal .am-sec::after{content:"";flex:1;height:1px;background:var(--line)}
#aboutModal .am-links{display:flex;gap:16px;justify-content:center;margin:18px 0 6px;flex-wrap:wrap}
#aboutModal .am-links a{color:#7e7a70;display:inline-flex;transition:color .18s,transform .18s}
#aboutModal .am-links a:hover{color:var(--gold2);transform:translateY(-2px)}
#aboutModal .am-links svg{width:26px;height:26px;fill:currentColor}
#aboutModal .am-tag{display:inline-block;margin:0 6px 6px 0;padding:3px 10px;border:1px solid var(--line2);
  border-radius:999px;font-size:11.5px;color:#a9a396}
#aboutModal .am-coffee{margin-top:18px;color:#8a8577;font-size:13.5px;text-align:center}
#aboutModal .am-qr{display:flex;gap:34px;justify-content:center;margin:18px 0 6px;flex-wrap:wrap}
#aboutModal .am-qr-item{display:flex;flex-direction:column;align-items:center;gap:9px}
#aboutModal .am-qr-item img{width:min(300px,38vw);height:auto;max-height:52vh;object-fit:contain;background:#fff;
  border-radius:14px;padding:10px;border:1px solid var(--line2);box-shadow:0 6px 22px rgba(0,0,0,.42);
  transition:transform .18s}
#aboutModal .am-qr-item img:hover{transform:translateY(-2px) scale(1.02)}
#aboutModal .am-qr-item span{color:#a29c8e;font-size:12.5px;letter-spacing:.04em}
#aboutModal .am-qr-hint{margin:6px 0 0;color:#6f6b60;font-size:11.5px;text-align:center}
.editor{width:94vw;height:88vh;display:flex;flex-direction:column}
.modal .box.editor{width:min(1400px,94vw);max-width:1500px}
.modal .box.editor .ebody textarea{font-size:15.5px}
@keyframes breathe{0%,100%{box-shadow:0 0 0 0 rgba(182,168,132,0)}50%{box-shadow:0 0 0 3px rgba(182,168,132,.18),0 0 34px rgba(182,168,132,.13)}}
.pulse{animation:breathe 2.4s ease-in-out infinite;border-color:var(--golddim)!important;position:relative}
.pulse-btn{animation:breathe 2.4s ease-in-out infinite;border-color:var(--gold)!important;color:var(--gold2);position:relative}
/* ---- 星河扫光 fx (流程引导, 自动触发; 仅彗星边框) ---- */
@property --fxa{syntax:'<angle>';inherits:false;initial-value:0deg}
.fxwrap{position:absolute;inset:0;border-radius:inherit;overflow:hidden;pointer-events:none;z-index:1}
.fxspark{position:absolute;inset:0;border-radius:inherit;padding:2px;
  background:conic-gradient(from var(--fxa),transparent 0 306deg,rgba(242,233,205,.95) 342deg,rgba(242,233,205,0) 360deg);
  -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;mask-composite:exclude;
  animation:fxspin 3.2s linear infinite}
@keyframes fxspin{to{--fxa:360deg}}
/* ---- 全站 hover 流光 (SVG 描边彗星, 金色调) ---- */
:root{--glow-line-color:#f2e9cd;--glow-blur-color:rgba(182,168,132,.9);
  --glow-line-thickness:2px;--glow-line-length:16;--glow-blur-size:8px;
  --glow-speed:1100ms;--glow-offset:60px}
.card,button{position:relative}
.glowc{pointer-events:none;position:absolute;
  inset:calc(var(--glow-offset)/-2);width:calc(100% + var(--glow-offset));height:calc(100% + var(--glow-offset));opacity:0}
.glow-blur,.glow-line{width:calc(100% - var(--glow-offset));height:calc(100% - var(--glow-offset));
  x:calc(var(--glow-offset)/2);y:calc(var(--glow-offset)/2);fill:transparent;stroke:#000;stroke-width:5px;
  stroke-dasharray:var(--glow-line-length) calc(50 - var(--glow-line-length))}
.glow-line{stroke:var(--glow-line-color);stroke-width:var(--glow-line-thickness)}
.glow-blur{filter:blur(var(--glow-blur-size));stroke:var(--glow-blur-color);stroke-width:var(--glow-blur-size)}
button:hover>.glowc .glow-blur,button:hover>.glowc .glow-line,
.card:hover>.glowc .glow-blur,.card:hover>.glowc .glow-line{
  stroke-dashoffset:-80px;transition:stroke-dashoffset var(--glow-speed) ease-in}
button:hover>.glowc,.card:hover>.glowc{animation:glow-vis var(--glow-speed) ease-in}
@keyframes glow-vis{0%,100%{opacity:0}25%,75%{opacity:1}}
.editor .ebody{flex:1;display:flex;flex-direction:column;margin-top:10px}
.editor textarea{flex:1;font-size:15px;line-height:1.8}
.ok{color:#a8c8a0}
/* ---- 数字人工作台 ---- */
.card.av{border-color:var(--golddim);background:linear-gradient(180deg,#14141a 0%,#111116 100%);
  box-shadow:inset 0 1px 0 rgba(182,168,132,.12)}
.card.av h3 .star{color:var(--gold);font-size:12px;border:1px solid var(--golddim);border-radius:999px;
  padding:1px 9px;font-weight:400;letter-spacing:1px;font-family:"Segoe UI","Microsoft YaHei",sans-serif}
.guide{margin-bottom:12px;border:1px solid var(--line);border-radius:8px;background:#0d0d12}
.guide summary{cursor:pointer;padding:9px 12px;font-size:12.5px;color:var(--gold2);user-select:none}
.guide .gstep{padding:9px 14px;border-top:1px solid #1d1d24}
.guide .gstep b{color:var(--tx);font-size:13px}
.guide p{margin:6px 0 2px;font-size:12.5px;line-height:1.7;color:#c6c1b4}
.guide p.muted{color:var(--dim)}
.avseg{border:1px solid var(--line);border-radius:8px;padding:10px;background:#0d0d12;display:flex;
  flex-direction:column;gap:7px}
.avseg.running{border-color:var(--golddim)}
/* 四态一眼区分: 绿底=已生成 / 金边=生成中 / 蓝虚线=排队 / 红边=失败(未生成=默认灰) */
.avseg.done{border-color:rgba(46,158,91,.38);background:rgba(46,158,91,.045)}
.avseg.failed{border-color:rgba(196,88,88,.4)}
.avseg .id{font-size:11px;color:var(--golddim);font-family:Consolas,monospace}
.avseg .txt{font-size:12px;line-height:1.55;color:#b8b3a6;min-height:34px}
.avseg video{width:100%;border-radius:8px;border:1px solid var(--line)}
.avgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:10px;margin-top:10px}
#avImgPrev{height:52px;width:52px;object-fit:cover;border-radius:8px;border:1px solid var(--line);flex:none;display:none}
.keyrow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
.keyrow input{flex:1;min-width:200px}
.avthumb{display:inline-block;cursor:grab;position:relative;flex:none}
.avthumb:active{cursor:grabbing}
.avthumb.selected{outline:2px solid var(--gold);outline-offset:1px;border-radius:8px}
.droptarget{outline:1px dashed var(--gold);outline-offset:2px;background:rgba(182,168,132,.07)!important;border-radius:8px}
.dhlabel{font-size:11.5px;color:var(--gold2);letter-spacing:.5px;flex:none}
.edrow{display:flex;gap:10px;align-items:center;border-top:1px solid #1d1d24;padding:8px 2px;flex-wrap:wrap}
.edrow .id{font-size:11px;color:var(--golddim);width:64px;flex:none;font-family:Consolas,monospace}
.edrow .txt{flex:1;font-size:12.5px;min-width:160px}
.edrow .len{font-size:11px;color:var(--dim);width:44px;text-align:right;flex:none}
.scrollbox{max-height:460px;overflow-y:auto;border:1px solid var(--line);border-radius:8px;
  padding:6px 10px;background:#0e0e13}
.scrollbox::-webkit-scrollbar{width:9px}
.scrollbox::-webkit-scrollbar-track{background:#101015;border-radius:5px}
.scrollbox::-webkit-scrollbar-thumb{background:#2e2e38;border-radius:5px}
.scrollbox::-webkit-scrollbar-thumb:hover{background:var(--golddim)}
</style></head><body><div class="wrap">
<div style="display:none"><textarea id="m1"></textarea><textarea id="m2"></textarea><textarea id="script"></textarea></div>

<div class="masthead">
  <div><h1>口播辅助创作台<i>STUDIO</i></h1>
  <div class="sub">项目存于 <b id="projPath">…</b> · 流程：灵感 → 选题 → 成稿 → 合成 → 数字人</div></div>
  <div><button id="btnSettings" onclick="openSettings()">⚙ 设置</button></div>
</div>
<div class="flownav">
  <a onclick="goto('card1')">① 灵感</a><a onclick="goto('card2')">② 调研</a><a onclick="goto('card3')">③ 成稿</a>
  <a onclick="goto('card4')">④ 分段合成</a><a onclick="goto('cardAV')">🎬 数字人工作台</a>
  <a onclick="goto('card5')">🎞️ 自动剪辑</a>
</div>

<div class="card" id="cardProj">
  <h3><span class="n">◈</span>创作第一步 · 项目
    <span class="tail"><button class="sm" onclick="openFolder('projects')" title="在资源管理器打开项目根目录">📂</button></span></h3>
  <div class="row">
    <button class="primary" onclick="newProject()">＋ 新建项目</button>
    <select id="projList" onchange="loadProject(this.value)" style="width:auto;min-width:300px"><option value="">— 打开已有项目 —</option></select>
    <button class="sm" onclick="deleteProject()" title="删除下拉中选中的历史项目（含全部文案/音频/成片，不可恢复）">🗑 删除项目</button>
    <span class="muted" id="projInfo"></span>
  </div>
  <div id="projGateTip" style="display:none;margin-top:10px;font-size:13px;color:var(--gold2)">👉 所有素材（文案 / 音频 / 视频）都归档在项目里 — 先<b>新建</b>或<b>选择</b>一个项目，再开始创作</div>
</div>


<div class="card av" id="cardAV">
  <h3><span class="n">✦</span>数字人工作台 <span class="star">重点功能</span>
    <span class="tail"><button class="sm" onclick="openFolder('videos')" title="在资源管理器打开视频目录">📂</button></span></h3>
  <details class="guide">
    <summary>首次使用？注册 / 获取 API Key 指南 · Guide: Register &amp; Get API Key</summary>
    <div class="gstep"><b>1 · 注册账号 Register</b>
      <p>Open the link: <a href="https://www.runninghub.ai?inviteCode=rh-v1655" target="_blank">https://www.runninghub.ai?inviteCode=rh-v1655</a>.
      Register and get <b>1000 RH coins</b>, and you can generate a large number of images and videos for free!!</p>
      <p class="muted">中文：打开上方链接注册账号，即得 1000 RH 币，可免费生成大量图片和视频。</p></div>
    <div class="gstep"><b>2 · 复制 API Key Get your API Key</b>
      <p>Visit <a href="https://www.runninghub.ai/enterprise-api/consumerApi" target="_blank">https://www.runninghub.ai/enterprise-api/consumerApi</a>
      and copy your API Key, then paste it below.</p>
      <p class="muted">中文：打开上方链接，复制你的 API Key，粘贴到下面输入框并点「保存 Key」。</p></div>
  </details>
  <div class="keyrow">
    <input type="password" id="avKey" placeholder="粘贴你的 RunningHub API Key（32位）" autocomplete="off">
    <button class="acc" onclick="saveAvatarKey()">保存 Key</button>
    <span class="muted" id="avKeyStatus">检查中…</span>
  </div>
  <div class="row" style="margin-top:12px">
    <span class="muted" style="flex:none">默认形象图</span>
    <input type="text" id="avatarImg" style="flex:1;min-width:240px" placeholder="未单独绑定图片的段落将使用这张（exe版将换成选图对话框）">
    <img id="avImgPrev" alt="">
    <button class="sm" onclick="updAvImg()">预览</button>
    <button class="sm acc" onclick="avBrowseDefault()">📁 选择</button>
  </div>
  <div class="row" style="margin-top:10px">
    <span class="muted" style="flex:none">⚡ 一键生成</span>
    <button class="acc" onclick="avatarAll()">全部保留段</button>
    <span class="muted">每段优先用拖拽绑定的形象图，未绑定的用默认形象图 · 视频完成后自动下载到项目 videos\（原链接24h过期，故即产即存）</span>
  </div>
  <div class="row" style="margin-top:10px">
    <span class="muted" style="flex:none">自制音频</span>
    <button class="acc sm" onclick="document.getElementById('customAudioInp').click()">📤 上传音频（免TTS直接做数字人视频）</button>
    <input type="file" id="customAudioInp" accept=".wav,.mp3,.m4a,.flac,.ogg" multiple style="display:none" onchange="uploadCustom(this)">
    <span class="muted">wav/mp3/m4a/flac/ogg 可多选 · 上传后与TTS段落一样绑定形象、生成视频</span>
  </div>
  <div class="row" style="margin-top:8px">
    <span class="muted" style="flex:none">文本生成 TTS</span>
    <button class="acc sm" onclick="toggleTtsGen()">📝 上传文本 · 直接生成 TTS 音频</button>
    <span class="muted">每行一段生成一条音频，与④的分段一样绑定形象、生成视频</span>
  </div>
  <div id="ttsGenBox" style="display:none;margin-top:8px;border:1px solid var(--line);border-radius:8px;padding:10px;background:#0d0d12">
    <div class="row" style="flex-wrap:wrap;align-items:center;margin-bottom:6px">
      <span class="muted" style="flex:none">引擎</span>
      <label class="muted" style="flex:none"><input type="radio" name="genEng" value="rh" checked> ☁ 云端 RH</label>
      <label class="muted" style="flex:none"><input type="radio" name="genEng" value="local"> 🖥 本地 soar</label>
      <span class="muted">云端：每行建议 ≤40 字，超长音色保持度会下降</span>
    </div>
    <textarea id="ttsGenText" rows="4" style="width:100%;background:#101016;color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:8px;font-size:13px;line-height:1.6" placeholder="每行一段，一行生成一条音频：&#10;第一段的台词…&#10;第二段的台词…"></textarea>
    <div class="row" style="margin-top:8px;align-items:center">
      <button class="primary sm" id="btnTtsGen" onclick="runTtsGen()">开始生成</button>
      <span class="muted" id="ttsGenInfo"></span>
    </div>
  </div>
  <div style="margin-top:12px">
    <div class="muted" style="margin-bottom:6px">形象图库 — 添加多张图片，<b style="color:var(--gold2)">拖动小图</b>到下方段落卡或④的段落行上即可绑定；Image library — drag a thumbnail onto a segment to bind it</div>
    <div class="row" style="margin-bottom:8px">
      <input type="text" id="avLibPath" style="flex:1;min-width:240px" placeholder="图片完整路径（带引号 / \ \ 或 / 均可）" autocomplete="off">
      <button class="acc sm" onclick="avBrowseAdd()">📁 选择文件</button>
      <button class="sm" onclick="avAddImage('avLibPath')">＋ 添加到图库</button>
    </div>
    <div class="row" id="avLib" style="gap:10px"></div>
  </div>
  <div class="muted" style="margin-top:8px" id="avTip">💡 小贴士：AI 生成偶有惊喜——若视频出现怪异字幕、口型不自然或画面瑕疵，点「重生成视频」再试一次通常即可 <span style="opacity:.75">If a video comes out with odd subtitles or visual artifacts, simply regenerate it.</span></div>
  <div class="scrollbox" style="margin-top:8px"><div class="avgrid" id="avSegs" style="margin-top:0"></div></div>
</div>

<div class="card" id="card1">
  <h3><span class="n">1</span>初步灵感收集
    <span class="tail"><button class="sm" onclick="openFolder('project')" title="在资源管理器打开项目目录">📂</button></span></h3>
  <input type="text" id="direction" placeholder="方向提示（可选）：例如 AI 编程 / 端侧模型 / 留空=全域热点" style="margin-bottom:10px" autocomplete="off">
  <div class="row" id="collectChips" style="margin-bottom:8px"></div>
  <div class="muted" style="margin-bottom:8px">自定义技能：把 .md 提示词放进 skills\collect\ 后点刷新 · 技能会真联网检索</div>
  <div class="row">
    <button class="primary" id="btnCollect" onclick="runCollect()">▶ 开始灵感收集</button>
    <button onclick="refreshSkills()">刷新技能</button>
  </div>
  <div class="pbar" id="pb1"><div></div></div><div class="step" id="st1"></div>
  <div id="m1box" class="summary empty">（尚未收集）</div>
  <div class="mrow">
    <span class="muted" id="m1meta"></span>
    <span class="row">
      <button id="btnM1edit" onclick="openEditor('m1')" disabled>展开编辑</button>
      <button id="btnM1ok" class="confirm-pending" onclick="confirmM1()" disabled>审核确认中</button>
    </span>
  </div>
</div>

<div class="card" id="card2">
  <h3><span class="n">2</span>选题与深度调研
    <span class="tail"><button class="sm" onclick="openFolder('project')" title="在资源管理器打开项目目录">📂</button></span></h3>
  <input type="text" id="topic" autocomplete="off" placeholder="输入选题或提示词（可从上面的灵感里挑一个）" style="margin-bottom:10px">
  <div class="row" id="researchChips" style="margin-bottom:8px"></div>
  <div class="muted" style="margin-bottom:8px">自定义技能放 skills\research\ · 涉及时效的判断会先检索验证</div>
  <div class="row">
    <button class="primary" id="btnResearch" onclick="runResearch()">▶ 开始深度调研</button>
    <span class="muted" id="rHint"></span>
  </div>
  <div class="pbar" id="pb2"><div></div></div><div class="step" id="st2"></div>
  <div id="m2box" class="summary empty">（尚未调研）</div>
  <div class="mrow">
    <span class="muted" id="m2meta"></span>
    <span class="row">
      <button id="btnM2edit" onclick="openEditor('m2')" disabled>展开编辑</button>
      <button id="btnM2ok" class="confirm-pending" onclick="confirmM2()" disabled>审核确认中</button>
    </span>
  </div>
</div>

<div class="card" id="card3">
  <h3><span class="n">3</span>统一成稿
    <span class="tail"><button class="sm" onclick="openFolder('project')" title="在资源管理器打开项目目录">📂</button></span></h3>
  <div class="row" id="unifySkills" style="margin-bottom:8px"></div>
  <div class="muted" style="margin-bottom:8px">自动加前缀「请根据以下选题和资料，给我生成文案」 · 自定义技能放 skills\unify\</div>
  <div class="row">
    <button class="primary" id="btnUnify" onclick="runUnify()">▶ 生成文案</button>
    <button id="btnScriptImport" onclick="importScript()" title="已有现成稿(txt/md)？在项目内导入后直接进④分段合成">📄 上传自定义口播稿</button>
  </div>
  <div class="muted" style="margin-top:4px">已有手写或别处准备好的底稿？上传 .txt/.md 即可跳过①②（须先新建项目，资产统一归档）</div>
  <div class="pbar" id="pb3"><div></div></div><div class="step" id="st3"></div>
  <div id="scriptbox" class="summary empty">（尚未成稿）</div>
  <div class="mrow">
    <span class="muted" id="smeta"></span>
    <span class="row">
      <button id="btnSedit" onclick="openEditor('script')" disabled>展开编辑</button>
      <button id="btnSok" class="primary" onclick="saveProject()" disabled>💾 保存文案</button>
    </span>
  </div>
</div>

<div class="card" id="card4">
  <h3><span class="n">4</span>分段合成
    <span class="tail"><button class="sm" onclick="openFolder('tts')" title="在资源管理器打开音频目录">📂</button> <span class="muted">30-40字/段 · 标点优先不断句不切数字</span></span></h3>
  <div class="row" style="gap:10px;margin-top:8px;align-items:stretch">
    <button id="engRh" class="engbtn" onclick="setTtsEng('rh')">
      <div class="et">☁ 云端 TTS</div>
      <div class="es muted">RunningHub 在线应用 · 无需本地模型 · 可自定义音色</div>
    </button>
    <button id="engLocal" class="engbtn" onclick="setTtsEng('local')">
      <div class="et">🖥 本地 TTS <span class="muted" id="engLocalTag" style="font-size:11px"></span></div>
      <div class="es muted">本机 dots.soar · 免费无限 · 可自定义音色</div>
    </button>
  </div>
  <div id="voiceRow" style="display:none">
    <div class="row" style="margin-top:10px;flex-wrap:wrap;align-items:center">
      <span class="muted" style="flex:none">自定义音色</span>
      <input id="voiceRefText" placeholder="参考音频对应文本（必填，它说了什么）" style="flex:1;min-width:230px">
      <button class="sm" id="btnVoiceRef" onclick="setVoiceRef()">🎤 上传音色</button>
      <button class="sm" id="btnVoiceClr" onclick="clearVoiceRef()" style="display:none" title="移除自定义音色，恢复内置">✕</button>
      <span class="muted" id="voiceRefInfo">内置音色（兜底）</span>
    </div>
    <div class="muted" style="margin-top:3px">可选：上传 10~15 秒干净、无噪声的纯人声并填写对应文本，本项目的分段合成将克隆该音色；不上传 = 内置默认音色</div>
  </div>
  <div id="rhTtsBox" style="display:none">
    <div class="row" style="margin-top:10px;flex-wrap:wrap;align-items:center">
      <span class="muted" style="flex:none">API Key</span><b class="muted" id="rhKeyInfo">…</b>
      <span class="muted" id="rhRefInfo"></span>
    </div>
    <div class="row" style="margin-top:6px;flex-wrap:wrap;align-items:center">
      <span class="muted" style="flex:none">自定义音色（可选）</span>
      <input id="rhRefText" placeholder="参考音频对应文本（它说了什么）" style="flex:1;min-width:200px">
      <input id="rhRefPath" style="width:190px" placeholder="参考音频路径(.wav)" autocomplete="off">
      <button class="sm" id="btnRhRef" onclick="upRhRef()">上传 / 保存</button>
      <button class="sm" id="btnRhClr" onclick="clrRhRef()" style="display:none" title="移除参考音频，恢复默认音色">✕</button>
    </div>
    <div class="muted" style="margin-top:3px">不上传参考音色 = 使用内置默认音色（<b>梁秘书</b>，仅首次自动上传一次）。API Key 与数字人共用（⚙ 设置里填一次即可）。云端合成约 10~60 秒/段（走 RH 队列，串行逐段提交），成片与本地管线完全一致。</div>
  </div>
  <div class="row" style="margin:10px 0 6px;flex-wrap:wrap">
    <button class="primary" id="btnSplit" onclick="splitSynth(false)">按文案分段并全部合成</button>
    <button id="btnResynth" onclick="splitSynth(true)">重合成保留段</button>
    <span class="muted" id="engHint"></span>
  </div>
  <div class="muted" id="segSummary" style="margin-bottom:4px"></div>
  <div class="row" style="margin:6px 0 6px;justify-content:space-between">
    <span class="muted">形象图库 · <b style="color:var(--gold2)">点击小图选中</b>，再点各数字人行里的「📌 绑选中图」（也可直接拖）</span>
    <span class="row">
      <input type="text" id="avLibPath2" style="width:220px" placeholder="或粘贴图片路径（带引号/正反斜杠均可）" autocomplete="off">
      <button class="sm" onclick="avBrowseAdd()">📁 选择文件</button>
      <button class="sm" onclick="avAddImage('avLibPath2')">＋ 添加</button>
    </span>
  </div>
  <div class="row" id="avLib2" style="gap:8px;margin-bottom:10px;flex-wrap:wrap"></div>
  <div class="scrollbox" style="max-height:min(82vh,1100px)"><div id="segList"></div></div>
</div>

<div class="card" id="card5">
  <h3><span class="n">5</span>自动剪辑 · 视频初稿
    <span class="tail"><button class="sm" onclick="openFolder('output')" title="在资源管理器打开成片目录">📂</button> <span class="muted">硬切拼接 576×1024 · 缺视频段自动黑底+音频替代 · 字幕/设计化剪辑留给 HyperFrames</span></span></h3>
  <div class="muted" style="margin-bottom:8px">勾选拼进初稿的素材并排序（缺视频的段自动用「黑底＋该段 TTS」替代，声音不缺内容）— 拼好即<b style="color:var(--gold2)">视频初稿</b>，下一步交给 HyperFrames 做字幕与设计化精剪</div>
  <div class="row" style="margin:6px 0 8px"><button class="sm" onclick="editSelectAll(true)">☑ 全选</button><button class="sm" onclick="editSelectAll(false)">☐ 清空</button><span class="muted">默认全选；用每行的 ↑ ↓ 调整拼接顺序（不勾选的素材不进初稿）</span></div>
  <div class="scrollbox" style="max-height:300px"><div id="editList"></div></div>
  <div class="row" style="margin-top:10px;flex-wrap:wrap">
    <span class="muted" style="flex:none">开场标题</span>
    <input type="text" id="editTitle" style="width:170px" placeholder="默认用项目选题" autocomplete="off">
    <label class="muted" style="flex:none"><input type="checkbox" id="editOutro" checked> 片尾AI声明卡</label>
  </div>
  <div class="row" style="margin-top:6px;flex-wrap:wrap">
    <span class="muted" style="flex:none">BGM</span>
    <input type="text" id="editBgm" style="flex:1;min-width:220px" placeholder="可选：本地音乐路径，自动 ducking + 循环补齐 + 首尾淡化" autocomplete="off">
    <button class="sm" onclick="pickBgm()">📁</button>
    <span class="muted" style="flex:none">音量</span>
    <input type="number" id="editBgmVol" step="0.02" min="0" max="1" value="0.12" style="width:74px">
  </div>
  <div class="row" style="margin-top:12px">
    <button class="acc" id="btnEdit" onclick="startEdit()">🎬 生成视频初稿</button>
    <button class="primary" id="btnRefine" onclick="startRefine()" disabled title="需要已生成的初稿 — HyperFrames 设计化字幕+合成">✨ HyperFrames 精剪</button>
    <span class="muted" id="editStatus"></span>
  </div>
  <div class="row" style="margin-top:10px;flex-wrap:wrap;align-items:flex-start">
    <span class="muted" style="flex:none;margin-top:4px">设计风格</span>
    <select id="hfStyle" style="flex:none;min-width:150px">
      <option value="ai">🤖 AI 选模板（默认）</option>
      <option value="custom">无模板 · 我自己写</option>
      <option value="gold">黑金简约</option>
      <option value="magazine">杂志编辑感</option>
      <option value="variety">综艺花字</option>
      <option value="minimal">极简白</option>
    </select>
    <span class="muted" style="flex:none;margin-top:4px">设计提示词（AI 选模板=口味方向 · 无模板=完全按它设计 · 手选模板=在其上定制）</span>
  </div>
  <textarea id="hfPrompt" rows="2" style="width:100%;margin-top:6px" placeholder="例：明黄+品红撞色，字幕条左侧加宋体大编号，右上加罗马数字徽章，加进度条和「DSH出品」水印。选「AI选模板」时留空=AI全权决定"></textarea>
  <div class="row" style="margin-top:6px;flex-wrap:wrap">
    <span class="muted" style="flex:none;margin-top:4px">💾 我的模板</span>
    <select id="hfTpl" style="flex:none;min-width:170px"><option value="">— 选择模板一键套用 —</option></select>
    <button class="sm" onclick="applyTpl()">套用</button>
    <button class="sm" onclick="saveTpl()">存为模板</button>
    <button class="sm" onclick="delTpl()" title="删除下拉中选中的模板">🗑</button>
    <input type="text" id="hfTplName" placeholder="模板名" style="width:120px;flex:none" autocomplete="off">
  </div>
  <div class="menufam">
    <details open>
      <summary>🏷 标题动效 · <b id="labTitle">🤖 AI 选配</b></summary>
      <label><input type="radio" name="fTitle" value="ai" checked> 🤖 AI 选配（按内容挑）</label>
      <label><input type="radio" name="fTitle" value="random"> 🎲 随机</label>
      <label><input type="radio" name="fTitle" value="tracking-in"> 聚焦显字</label>
      <label><input type="radio" name="fTitle" value="titlecard-lockup"> 定版细线</label>
      <label><input type="radio" name="fTitle" value="per-word-rise"> 逐字升起</label>
      <label><input type="radio" name="fTitle" value="typewriter"> 打字机</label>
      <label><input type="radio" name="fTitle" value="headline-slam"> 重锤标题</label>
      <label><input type="radio" name="fTitle" value="marker-circle"> 手绘圈题</label>
      <label><input type="radio" name="fTitle" value="titlecard-calm"> 冷静定版</label>
      <label><input type="radio" name="fTitle" value="text-shimmer"> 扫光标题</label>
      <div class="muted" style="margin-top:4px">已适配 8/货架约40 · 会持续扩库</div>
    </details>
    <details>
      <summary>💬 字幕风格 · <b id="labCap">🤖 AI 选配</b></summary>
      <label><input type="radio" name="fCap" value="ai" checked> 🤖 AI 选配（按内容挑）</label>
      <label><input type="radio" name="fCap" value="random"> 🎲 随机</label>
      <label><input type="radio" name="fCap" value="bar"> 现有字幕条</label>
      <label><input type="radio" name="fCap" value="pill-karaoke"> 卡拉OK药丸</label>
      <label><input type="radio" name="fCap" value="editorial-emphasis"> 杂志重读</label>
      <label><input type="radio" name="fCap" value="highlight"> 高亮扫过</label>
      <label><input type="radio" name="fCap" value="blend-difference"> 反色显字</label>
      <label><input type="radio" name="fCap" value="clip-wipe"> 逐词擦入</label>
      <label><input type="radio" name="fCap" value="weight-shift"> 字重焦点</label>
      <label><input type="radio" name="fCap" value="matrix-decode"> 矩阵解码</label>
      <label><input type="radio" name="fCap" value="glitch-rgb"> RGB故障</label>
      <label><input type="radio" name="fCap" value="gradient-fill"> 渐变扫色</label>
      <label><input type="radio" name="fCap" value="neon-glow"> 霓虹辉光</label>
      <label><input type="radio" name="fCap" value="kinetic-slam"> 动力猛砸</label>
      <label><input type="radio" name="fCap" value="neon-accent"> 霓虹强调</label>
      <div class="muted" style="margin-top:4px">已适配 12/货架17 · 会持续扩库</div>
    </details>
    <details>
      <summary>🎞 氛围 · <b id="labAtmo">🤖 AI 选配</b></summary>
      <label><input type="radio" name="fAtmo" value="ai" checked> 🤖 AI 选配（按内容挑）</label>
      <label><input type="radio" name="fAtmo" value="none"> 无</label>
      <label><input type="radio" name="fAtmo" value="vignette"> 电影暗角</label>
      <label><input type="radio" name="fAtmo" value="light-leak"> 暖调漏光</label>
      <label><input type="radio" name="fAtmo" value="spotlight"> 羽化聚光</label>
      <label><input type="radio" name="fAtmo" value="grain"> 胶片颗粒（⚠️成片体积暴涨）</label>
      <div class="muted" style="margin-top:4px">已适配 4/货架约10 · 零体积款均可进 AI 选配池 · 极光/网格背景属「黑底段背景」，与插播卡同批（第三阶段）</div>
    </details>
    <details>
      <summary>🔖 信息条 · <b id="labLt">🤖 AI 选配</b></summary>
      <label><input type="radio" name="fLt" value="ai" checked> 🤖 AI 选配（按内容挑）</label>
      <label><input type="radio" name="fLt" value="none"> 不加</label>
      <label><input type="radio" name="fLt" value="lt-kicker-name"> 看点签条</label>
      <label><input type="radio" name="fLt" value="lt-color-block"> 色块签条</label>
      <label><input type="radio" name="fLt" value="lt-side-rule"> 竖线签条</label>
      <label><input type="radio" name="fLt" value="lt-clean-bar"> 白卡签条</label>
      <label><input type="radio" name="fLt" value="lt-soft-pill"> 圆牌签条</label>
      <label><input type="radio" name="fLt" value="lt-stack-bars"> 双条签条</label>
      <label><input type="radio" name="fLt" value="lt-bold-block"> 重块签条</label>
      <label><input type="radio" name="fLt" value="lt-mask-reveal"> 扫光签条</label>
      <label><input type="radio" name="fLt" value="lt-accent-underline"> 底线签条</label>
      <label><input type="radio" name="fLt" value="lt-news-ticker"> 新闻爬条</label>
      <div class="muted" style="margin-top:4px">已适配 9/货架约11 · 开场后 2.1s 滑入停留 4.5s，文字=开场标题，位置压字幕区上方（新闻爬条=顶部横幅）</div>
    </details>
    <details>
      <summary>✨ 转场 · <b id="labTrans">无转场</b></summary>
      <label><input type="radio" name="fTrans" value="ai"> 🤖 AI 选配</label>
      <label><input type="radio" name="fTrans" value="random"> 🎲 随机</label>
      <label><input type="radio" name="fTrans" value="none" checked> 无转场</label>
      <label><input type="radio" name="fTrans" value="flash"> 切点闪光</label>
      <label><input type="radio" name="fTrans" value="dip-black"> 黑场过渡</label>
      <label><input type="radio" name="fTrans" value="sweep"> 光带扫场</label>
      <label><input type="radio" name="fTrans" value="wipe"> 色板擦除</label>
      <div class="muted" style="margin-top:4px">已适配 4 款 · 落在每段素材拼接边界，压视频不压文字 · 效果重可组合氛围层（闪光/扫场配漏光更编辑感）</div>
    </details>
    <details>
      <summary>🌌 黑底段背景 · <b id="labBgb">AI 选配</b></summary>
      <label><input type="radio" name="fBgb" value="ai" checked> 🤖 AI 选配</label>
      <label><input type="radio" name="fBgb" value="random"> 🎲 随机</label>
      <label><input type="radio" name="fBgb" value="none"> 无背景</label>
      <label><input type="radio" name="fBgb" value="aurora"> 极光漂移</label>
      <label><input type="radio" name="fBgb" value="mesh"> 网格渐变</label>
      <div class="muted" style="margin-top:4px">已适配 2 款 · 只铺在黑底替代段（删掉数字人的分段），数字人画面不受影响 · 字幕之下 z 序让位</div>
    </details>
    <details>
      <summary>🎬 片尾 · <b id="labCta">不加</b></summary>
      <label><input type="radio" name="fCta" value="ai"> 🤖 AI 选配</label>
      <label><input type="radio" name="fCta" value="random"> 🎲 随机</label>
      <label><input type="radio" name="fCta" value="none" checked> 不加</label>
      <label><input type="radio" name="fCta" value="cta-close"> 行动号召</label>
      <label><input type="radio" name="fCta" value="social-card"> 口碑收尾</label>
      <label><input type="radio" name="fCta" value="logo-sting"> 品牌落版</label>
      <div class="muted" style="margin-top:4px">已适配 2 款（新家族）· 覆盖在片尾段（无 outro 则用最后约 3~4 秒），深底遮罩压住字幕 · 行动号召=大字逐词落定+胶囊按钮弹出；口碑收尾=品牌名+五星逐颗弹入+证明行+三项</div>
    </details>
    <details>
      <summary>📊 数据数字 · <b id="labData">不加</b></summary>
      <label><input type="radio" name="fData" value="ai"> 🤖 AI 选配</label>
      <label><input type="radio" name="fData" value="none" checked> 不加</label>
      <label><input type="radio" name="fData" value="number-pop"> 数字弹入</label>
      <label><input type="radio" name="fData" value="conic-ring"> 环形进度</label>
      <label><input type="radio" name="fData" value="number-wheel"> 滚动计数</label>
      <div class="muted" style="margin-top:4px">已适配 2 款（新家族）· 数字弹入=自动从文案提取第一个数字与单位；环形进度=圆环填充+中心数字同步计数（可手填进度%）· 均落在第一段数字人画面中上部，避开底部字幕带</div>
    </details>
    <details>
      <summary>⚖ 前后对比 · <b id="labCmp">不加</b></summary>
      <label><input type="radio" name="fCmp" value="ai"> 🤖 AI 选配</label>
      <label><input type="radio" name="fCmp" value="none" checked> 不加</label>
      <label><input type="radio" name="fCmp" value="before-after"> 前后对比条</label>
      <label><input type="radio" name="fCmp" value="split-tilt"> 双卡倾斜</label>
      <div class="muted" style="margin-top:4px">已适配 2 款（新家族，第 10 族）· 前后对比条=中部两栏面板右栏擦入揭示；双卡倾斜=两卡从两侧飞入带 3D 镜像倾斜+眉标弹入（文案取下方「对比·左/右」输入）· 落在第二个数字人段，避开底部字幕带</div>
    </details>
    <details>
      <summary>📋 要点清单 · <b id="labList">不加</b></summary>
      <label><input type="radio" name="fList" value="ai"> 🤖 AI 选配</label>
      <label><input type="radio" name="fList" value="none" checked> 不加</label>
      <label><input type="radio" name="fList" value="checklist" disabled> 要点清单卡（⚠ 受 HF check 对比度口径阻塞，暂缓）</label>
      <div class="muted" style="margin-top:4px">已适配 1 款（新家族，第 11 族）· 纸卡上手写风标题(中文楷体)+下划线+红笔圈词，三行要点逐条落地、每行绿勾自绘，落在首个数字人段后半，避开底部字幕带</div>
    </details>
    <details>
      <summary>✍ 片尾/数据/对比/清单文案（可选，留空用默认）</summary>
      <div class="row" style="flex-wrap:wrap;margin-top:4px">
        <input id="ctaAction" style="width:200px" placeholder="片尾行动语（默认:关注我 下期见）" autocomplete="off">
        <input id="ctaButton" style="width:170px" placeholder="按钮文字（默认:点个关注）" autocomplete="off">
        <input id="ctaBrand" style="width:150px" placeholder="品牌名（默认:开场标题）" autocomplete="off">
        <input id="ctaProof" style="width:230px" placeholder="证明行（默认:每期三分钟 讲清一件事）" autocomplete="off">
        <input id="ctaMicro" style="width:200px" placeholder="微文案（标准收尾用）" autocomplete="off"><input id="ctaDataLabel" style="width:150px" placeholder="数字标签（可留空）" autocomplete="off"><input id="ctaDataValue" style="width:110px" placeholder="数值（留空=自动提取）" autocomplete="off"><input id="ctaDataUnit" style="width:90px" placeholder="单位" autocomplete="off"><input id="ctaDataProgress" style="width:120px" placeholder="环形进度%" autocomplete="off">
        <input id="cmpLeftTitle" style="width:110px" placeholder="对比·左标题（以前）" autocomplete="off">
        <input id="cmpLeftText" style="width:190px" placeholder="对比·左文字" autocomplete="off">
        <input id="cmpRightTitle" style="width:110px" placeholder="对比·右标题（现在）" autocomplete="off">
        <input id="cmpRightText" style="width:190px" placeholder="对比·右文字" autocomplete="off">
        <input id="listTitle" style="width:190px" placeholder="清单标题（默认:今天讲清三件事）" autocomplete="off">
        <input id="list1" style="width:220px" placeholder="要点1（格式:标签|内容）" autocomplete="off">
        <input id="list2" style="width:220px" placeholder="要点2（格式:标签|内容）" autocomplete="off">
        <input id="list3" style="width:220px" placeholder="要点3（格式:标签|内容）" autocomplete="off">
      </div>
    </details>
    <label class="muted" style="display:block;margin-top:6px"><input type="checkbox" id="mTopMark"> 顶部常驻·本视频由AI合成（合规双保险之一，与片尾卡并存）</label>
    <div class="muted" style="margin-top:3px">AI 选配 = LLM 阅读选题与各段内容后从该家族挑选，挑选理由写在版本备注里 · 随机 = 从本家族抽一款 · 想自己定就手选</div>
  </div>
  <div id="refineFbRow" style="display:none;margin-top:10px">
    <div class="muted" style="margin-bottom:4px">👁 预览后不满意？写修改意见，在当前版本设计基础上重剪（素材仍用粗剪 <b>初稿.mp4</b>）</div>
    <textarea id="hfFeedback" rows="2" style="width:100%" placeholder="例：第2段字幕太大改小些；隐藏第4段字幕；进度条去掉；强调色换品红；水印改成「AI生成」"></textarea>
    <div class="row" style="margin-top:6px">
      <button class="acc" id="btnReRefine" onclick="startRefineRevise()">🔄 按反馈重剪（出新版本）</button>
      <span class="muted" id="refineBase"></span>
    </div>
  </div>
  <div class="row" id="refineVers" style="display:none;margin-top:8px;flex-wrap:wrap"></div>
  <div class="pbar" id="pb5" style="display:none"><div></div></div><div class="step" id="st5"></div>
  <video id="editPreview" controls preload="none" style="display:none;margin-top:10px;max-width:300px;border-radius:8px;border:1px solid var(--line)"></video>
  <video id="refinePreview" controls preload="none" style="display:none;margin-top:10px;max-width:300px;border-radius:8px;border:2px solid var(--gold)"></video>
</div>

<div class="modal" id="editorModal"><div class="box editor">
  <div class="row" style="justify-content:space-between">
    <h3 style="margin:0" id="editorTitle">编辑资料</h3>
    <span class="muted" id="editorCount"></span>
  </div>
  <div class="ebody"><textarea id="editorText" style="min-height:0"></textarea></div>
  <div class="row" style="margin-top:12px;justify-content:flex-end">
    <span class="muted" style="margin-right:auto" id="editorHint"></span>
    <button onclick="closeEditor()">取消</button>
    <button class="primary" onclick="saveEditor()">✓ 确认资料并关闭</button>
  </div>
</div></div>

<div class="modal" id="npModal"><div class="box">
  <h3 style="margin-top:0"><span class="n">＋</span>新建项目</h3>
  <input type="text" id="npName" placeholder="项目名，例如：年轻人攒钱" style="margin-bottom:12px" autocomplete="off">
  <div class="row" style="justify-content:flex-end">
    <button onclick="closeNp()">取消</button>
    <button class="primary" onclick="createProject()">创建</button>
  </div>
</div></div>

<div class="modal" id="settingsModal"><div class="box">
  <h3 style="margin-top:0"><span class="n">⚙</span>设置</h3>
  <div style="margin-bottom:8px"><div class="muted">LLM Base URL</div><input type="text" id="setBase" placeholder="https://api.xxx.com/v1"></div>
  <div style="margin-bottom:8px"><div class="muted">模型</div><input type="text" id="setModel" placeholder="deepseek-flash">
    <div class="muted" style="margin-top:4px;line-height:1.7">当前推荐 <b>deepseek-flash</b>（DeepSeek-V4.1-Flash，官方主力：性能/费用/速度全面优于 V4 Pro）。<br>
      注：<code>deepseek-v4-pro</code> 官方将于 2026-09-14 后路由至 Flash；<code>deepseek-chat</code>/<code>deepseek-reasoner</code> 为已下线的旧名，请勿再用。</div></div>
  <div style="margin-bottom:8px"><div class="muted">思考模式</div>
    <select id="setThinking" style="width:100%">
      <option value="off">关闭（更快更省，写稿/选配推荐）</option>
      <option value="on">开启思考（复杂推理更准，更慢更贵）</option>
      <option value="low">开启·低强度</option>
    </select>
    <div class="muted" style="margin-top:4px;line-height:1.7">DeepSeek 思考模式默认开启：会先输出思维链再作答，实测两个字的回答也要先花约 260 字推理。
      本程序默认<b>关闭</b>（生成型任务质量无差别）。思考模式下 <code>temperature</code> 不生效。</div></div>
  <div style="margin-bottom:8px"><div class="muted">API Key（留空 = 保持不变）</div>
    <input type="password" id="setKey" placeholder="sk-..." autocomplete="off"> <div class="muted" id="keyHint"></div></div>
  <div style="margin-bottom:8px"><div class="muted">项目保存目录（留空 = 软件目录\projects；exe 版将提供系统选目录对话框）</div>
    <input type="text" id="setDir" placeholder="D:\我的口播"></div>
  <div style="margin-bottom:8px"><div class="muted">数字人 RunningHub API Key（留空 = 保持不变 · 也可在「数字人工作台」保存）</div>
    <input type="password" id="setAvatarKey" autocomplete="off"> <div class="muted" id="avatarKeyHint"></div></div>
  <div style="margin-bottom:10px"><div class="muted">数字人形象图默认路径（RunningHub plus 48G 实例）</div>
    <input type="text" id="setAvatarImage" placeholder="D:\素材图\数字人.jpg"></div>
  <div style="margin-bottom:10px"><div class="muted">数字人视频并行数（同时提交给 RunningHub 的任务数；API 不支持多线时，超出部分自动排队，前序完成自动续交）</div>
    <select id="setMaxParallel" style="width:100%">
      <option value="1">1 — 串行排队（最稳，默认）</option>
      <option value="2">2 — 双线并行</option>
      <option value="3">3 — 三线并行</option>
      <option value="4">4 — 四线并行</option>
    </select></div>
  <div class="row">
    <button class="primary" onclick="saveSettings()">保存</button>
    <button onclick="testLLM()">测试连通</button>
    <button onclick="closeSettings()">关闭</button>
    <span class="step" id="setMsg"></span>
  </div>
</div></div>

  <div class="site-foot">
    <div>© <span id="yearNow"></span> 天工开帧 · 口播辅助创作台 · 由兴趣驱动，为动手而做</div>
    <div class="sf-links">
      <a href="https://v.douyin.com/BgCJXnllitM/" target="_blank" rel="noopener noreferrer" aria-label="抖音" title="抖音">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64 2.93 2.93 0 0 1 .88.13V9.4a6.84 6.84 0 0 0-1-.05A6.33 6.33 0 0 0 5 20.1a6.34 6.34 0 0 0 10.86-4.43v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-1-.1z"/></svg>
      </a>
      <a href="https://x.com/yijt1339303" target="_blank" rel="noopener noreferrer" aria-label="X" title="X (Twitter)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>
      </a>
      <a href="https://www.youtube.com/@jintaoyi" target="_blank" rel="noopener noreferrer" aria-label="YouTube" title="YouTube">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>
      </a>
      <a href="https://space.bilibili.com/11946287" target="_blank" rel="noopener noreferrer" aria-label="哔哩哔哩" title="哔哩哔哩">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.813 4.653h.854c1.51.054 2.769.578 3.773 1.574 1.004.995 1.524 2.249 1.56 3.76v7.36c-.036 1.51-.556 2.769-1.56 3.773s-2.262 1.524-3.773 1.56H5.333c-1.51-.036-2.769-.556-3.773-1.56S.036 18.858 0 17.347v-7.36c.036-1.511.556-2.765 1.56-3.76 1.004-.996 2.262-1.52 3.773-1.574h.854l-.853-.827a1.253 1.253 0 0 1-.374-.92c0-.356.125-.662.374-.92l.16-.16c.284-.276.599-.414.947-.414.347 0 .656.138.933.414l2.187 2.16h5.213l2.133-2.16a1.3 1.3 0 0 1 .947-.414c.321 0 .63.138.933.414l.16.16c.249.258.374.564.374.92 0 .356-.125.662-.374.92zM6.4 8.24c-.618 0-1.134.213-1.547.64-.412.427-.636.958-.667 1.587v7.44c.03.62.255 1.146.667 1.586.413.427.929.64 1.547.64h11.2c.618 0 1.134-.213 1.546-.64.413-.427.638-.953.667-1.587v-7.44c-.03-.628-.254-1.159-.667-1.586-.412-.427-.928-.64-1.546-.64zm1.546 1.6c.355 0 .638.11.853.333.215.223.323.502.323.84 0 .347-.108.626-.323.84-.215.222-.498.333-.853.333-.355 0-.638-.111-.853-.333a1.12 1.12 0 0 1-.323-.84c0-.338.108-.617.323-.84.215-.223.498-.333.853-.333zm8.107 0c.355 0 .638.11.853.333.215.223.323.502.323.84 0 .347-.108.626-.323.84-.215.222-.498.333-.853.333-.355 0-.638-.111-.853-.333a1.12 1.12 0 0 1-.323-.84c0-.338.108-.617.323-.84.215-.223.498-.333.853-.333z"/></svg>
      </a>
      <a href="https://github.com/beiyege-01" target="_blank" rel="noopener noreferrer" aria-label="GitHub" title="GitHub">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
      </a>
    </div>
  </div>
<div id="toast"></div>
</div>
<div id="catHot" title="点我看看作者是谁"></div>
<div id="catBubble" aria-live="polite"></div>
<div class="modal" id="aboutModal"><div class="box">
  <h2>你好呀，我是天工开帧 🐈</h2>
  <p class="am-sub">一个兴趣使然的 AI 动画师，<br>一个不入流的 AI 时代探索者。</p>
  <p class="am-p">这个项目是我在本地 AI 世界里摸索的产物之一——从 GitHub 上淘来的开源工具，到本地大模型、
    TTS 语音合成、GPU 加速这些折腾人的东西，一步步拼成一条能跑通的口播视频流水线。
    每处都是自己踩过坑、跑通之后才写进来的，希望能让你少走弯路。</p>
  <p class="am-p">我做的东西大多是用 ComfyUI、HyperFrames 这类工具在本地渲染出来的。
    <b style="color:#d8d2c4">技术不是为了炫技，而是为了让想象落地。</b>
    这里不追热点、不贩卖焦虑，只有能照着做的流程，和一点关于动手的朴素快乐。</p>
  <div class="am-sec">这个程序能做什么</div>
  <p class="am-p">「口播辅助创作台 · 云端版」把一条口播视频拆成五步，串成一条流水线（每一步都由你审核决定，AI 只是助手）：</p>
  <div>
    <span class="am-tag">① 选题</span><span class="am-tag">② 深度调研（联网取证）</span>
    <span class="am-tag">③ 统一成稿</span><span class="am-tag">④ 分段合成 TTS</span>
    <span class="am-tag">⑤ 数字人视频 + 自动剪辑</span>
  </div>
  <p class="am-p" style="margin-top:10px">云端 TTS 与数字人走 RunningHub（本机无需显卡），
    精剪用 HyperFrames 组件货架做包装：标题、字幕、氛围、信息条、转场、片尾、数据、对比……
    全部可 AI 选配或手动指定，版本可回退、可按反馈重剪。</p>
  <p class="am-p">如果你也在折腾本地 AI，欢迎常来看看。愿我们都能在这个时代里，找到属于自己的那份好奇心。</p>
  <div class="am-sec">找到我</div>
  <div class="am-links">
    <a href="https://v.douyin.com/BgCJXnllitM/" target="_blank" rel="noopener noreferrer" title="抖音">
      <svg viewBox="0 0 24 24"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64 2.93 2.93 0 0 1 .88.13V9.4a6.84 6.84 0 0 0-1-.05A6.33 6.33 0 0 0 5 20.1a6.34 6.34 0 0 0 10.86-4.43v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-1-.1z"/></svg>
    </a>
    <a href="https://x.com/yijt1339303" target="_blank" rel="noopener noreferrer" title="X (Twitter)">
      <svg viewBox="0 0 24 24"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>
    </a>
    <a href="https://www.youtube.com/@jintaoyi" target="_blank" rel="noopener noreferrer" title="YouTube">
      <svg viewBox="0 0 24 24"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>
    </a>
    <a href="https://space.bilibili.com/11946287" target="_blank" rel="noopener noreferrer" title="哔哩哔哩">
      <svg viewBox="0 0 24 24"><path d="M17.813 4.653h.854c1.51.054 2.769.578 3.773 1.574 1.004.995 1.524 2.249 1.56 3.76v7.36c-.036 1.51-.556 2.769-1.56 3.773s-2.262 1.524-3.773 1.56H5.333c-1.51-.036-2.769-.556-3.773-1.56S.036 18.858 0 17.347v-7.36c.036-1.511.556-2.765 1.56-3.76 1.004-.996 2.262-1.52 3.773-1.574h.854l-.853-.827a1.253 1.253 0 0 1-.374-.92c0-.356.125-.662.374-.92l.16-.16c.284-.276.599-.414.947-.414.347 0 .656.138.933.414l2.187 2.16h5.213l2.133-2.16a1.3 1.3 0 0 1 .947-.414c.321 0 .63.138.933.414l.16.16c.249.258.374.564.374.92 0 .356-.125.662-.374.92zM6.4 8.24c-.618 0-1.134.213-1.547.64-.412.427-.636.958-.667 1.587v7.44c.03.62.255 1.146.667 1.586.413.427.929.64 1.547.64h11.2c.618 0 1.134-.213 1.546-.64.413-.427.638-.953.667-1.587v-7.44c-.03-.628-.254-1.159-.667-1.586-.412-.427-.928-.64-1.546-.64zm1.546 1.6c.355 0 .638.11.853.333.215.223.323.502.323.84 0 .347-.108.626-.323.84-.215.222-.498.333-.853.333-.355 0-.638-.111-.853-.333a1.12 1.12 0 0 1-.323-.84c0-.338.108-.617.323-.84.215-.223.498-.333.853-.333zm8.107 0c.355 0 .638.11.853.333.215.223.323.502.323.84 0 .347-.108.626-.323.84-.215.222-.498.333-.853.333-.355 0-.638-.111-.853-.333a1.12 1.12 0 0 1-.323-.84c0-.338.108-.617.323-.84.215-.223.498-.333.853-.333z"/></svg>
    </a>
    <a href="https://github.com/beiyege-01" target="_blank" rel="noopener noreferrer" title="GitHub">
      <svg viewBox="0 0 16 16"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
    </a>
  </div>
  <p class="am-coffee">当然，如果你愿意的话，也可以请我喝杯咖啡 ☕</p>
  <div class="am-qr">
    <div class="am-qr-item">
      <img src="/assets/thanks/wx-pay.jpg" alt="微信收款码" loading="lazy">
      <span>微信支付</span>
    </div>
    <div class="am-qr-item">
      <img src="/assets/thanks/okx-wallet.jpg" alt="OKX Wallet 收款码" loading="lazy">
      <span>OKX Wallet</span>
    </div>
  </div>
  <p class="am-qr-hint">扫码即可（点图可看大图）</p>
  <div class="row" style="justify-content:flex-end;margin-top:14px">
    <button class="primary" onclick="closeAbout()">收起</button>
  </div>
</div></div>
<!-- 🐈 彩蛋: Live2D 黑猫引擎(长任务时出现陪伴) -->
<script src="/assets/hijiki/live2d/L2Dwidget.min.js"></script>
<script>
const $=id=>document.getElementById(id);
function toast(t,ms=2600){const e=$('toast');e.textContent=t;e.style.display='block';clearTimeout(e._h);e._h=setTimeout(()=>e.style.display='none',ms)}
function goto(id){$(id).scrollIntoView({behavior:'smooth'});
  /* 进入⑤时确保素材列表已加载(否则未走「打开项目」入口会空白, 需手动刷新) */
  if(id==='card5'){
    if(projName){try{loadEditItems()}catch(e){}}
    else{const _b=$('editList');if(_b)_b.innerHTML='<span class="muted">请先在顶部「打开已有项目」选一个项目</span>'}
  }
}
async function api(path,body,noTimeout){
  const ctl=new AbortController();
  const h=setTimeout(()=>ctl.abort(),noTimeout?900000:300000);
  try{
    const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},
      body:body?JSON.stringify(body):undefined,signal:ctl.signal});
    const txt=await r.text();
    let j;
    try{ j=JSON.parse(txt) }catch(e){ throw new Error('服务器错误 '+r.status+': '+txt.slice(0,100)) }
    if(!r.ok) throw new Error(j.detail||('HTTP '+r.status));
    return j;
  } finally{ clearTimeout(h) }
}
/* ---- 进度条 ---- */
let pbTimer=null;
function pbarStart(pb,st,phases){
  const bar=$(pb);bar.style.display='block';bar.firstElementChild.style.width='3%';
  let p=3,pi=0;
  $(st).textContent='▸ '+phases[0];
  clearInterval(pbTimer);
  pbTimer=setInterval(()=>{ p=Math.min(92,p+Math.random()*4+1);bar.firstElementChild.style.width=p+'%';
    const target=Math.min(phases.length-1,Math.floor(p/100*phases.length));
    if(target!==pi){pi=target;$(st).textContent='▸ '+phases[pi]}
  },900);
}
function pbarDone(pb,st){clearInterval(pbTimer);$(pb).firstElementChild.style.width='100%';
  setTimeout(()=>{$(pb).style.display='none';$(st).textContent=''},1200)}
function pbarFail(pb,st,msg){clearInterval(pbTimer);$(pb).style.display='none';$(st).textContent='✗ '+msg}
/* ---- 技能 chips ---- */
function renderChips(stage,contId,mode){
  return api('/api/skills/'+stage).then(j=>{
    const box=$(contId);box.innerHTML='';
    if(mode==='single'){
      const s=document.createElement('select');s.style.width='auto';s.id=stage+'Sel';
      (j.skills.length?j.skills:['khazix-writer']).forEach(n=>{
        const o=document.createElement('option');o.value=n;o.textContent=n;s.appendChild(o)});
      if(j.defaults[0])s.value=j.defaults[0];
      box.appendChild(s);
    } else {
      j.skills.forEach(n=>{
        const l=document.createElement('label');l.className='chip'+(j.defaults.includes(n)?' on':'');
        const c=document.createElement('input');c.type='checkbox';c.checked=j.defaults.includes(n);
        c.onchange=()=>l.classList.toggle('on',c.checked);
        l.appendChild(c);l.appendChild(document.createTextNode(' '+n));box.appendChild(l);
      });
    }
  });
}
function refreshSkills(){Promise.all([renderChips('collect','collectChips'),renderChips('research','researchChips'),renderChips('unify','unifySkills','single')]).then(()=>toast('技能列表已刷新'))}
function sel(stage,contId){const b=$(contId);return [...b.querySelectorAll('input:checked')].map(x=>x.parentElement.textContent.trim())}
/* ---- 资料 summary + 编辑弹窗 ---- */
const MATERIALS={m1:{title:'灵感资料',meta:'m1meta'},m2:{title:'调研资料',meta:'m2meta'},script:{title:'口播文案',meta:'smeta'}};
function refreshSummary(key){
  const v=$(key).value;
  const box=$(key+'box');
  if(!v){box.className='summary empty';box.textContent=({m1:'（尚未收集）',m2:'（尚未调研）',script:'（尚未成稿）'})[key];
    $(MATERIALS[key].meta).textContent='';$(key==='script'?'btnSedit':'btnM'+(key==='m1'?'1':'2')+'edit').disabled=true;return}
  box.className='summary';box.textContent=v.length>120?v.slice(0,120)+'…':v;
  $(MATERIALS[key].meta).textContent=v.length+' 字';
  $(key==='script'?'btnSedit':'btnM'+(key==='m1'?'1':'2')+'edit').disabled=false;
  if(window.updateFlowHint)updateFlowHint();
}
let editorKey=null;
function openEditor(key){
  editorKey=key;
  $('editorTitle').textContent='编辑 · '+MATERIALS[key].title;
  $('editorText').value=$(key).value;
  updCount();
  $('editorModal').style.display='flex';
  $('editorText').focus();
}
function updCount(){$('editorCount').textContent=$('editorText').value.length+' 字'}
$('editorText').addEventListener('input',updCount);
function closeEditor(){$('editorModal').style.display='none';editorKey=null}
function saveEditor(){
  if(!editorKey)return;
  const key=editorKey;            /* 先捕获: closeEditor 会把 editorKey 置 null(原版在此处判断恒走 else 分支的隐蔽bug) */
  $(key).value=$('editorText').value;
  refreshSummary(key);
  closeEditor();
  /* 两步引导: 编辑确认资料 → 引导外部的「审核确认」/「保存文案」按钮(不再自动替用户确认) */
  if(key==='m1'){guide1='confirm';updateFlowHint()}
  else if(key==='m2'){guide2='confirm';updateFlowHint()}
  else{guide3='save';updateFlowHint()}
}
/* ---- ③ 导入自定义口播稿 ---- */
async function importScript(){
  if(!projName){toast('请先新建或打开一个项目 — 稿件资产统一归档在项目内',4500);return}
  toast('正在打开文件选择窗口…',1800);
  try{
    const p=await api('/api/dialog/pick-image?kind=script',{});
    if(!p.path)return;
    const r=await api('/api/script/import',{project:projName,path:p.path});
    $('script').value=r.script;
    refreshSummary('script');
    setM1ok(true);setM2ok(true);          // 外稿=用户已定稿, 前置确认视为通过
    $('btnSok').disabled=false;$('btnSedit').disabled=false;
    updateFlowHint();
    toast('✅ 已导入《'+r.name+'》('+r.chars+'字) — 前面步骤已跳过，可直接进④分段合成',6000);
  }catch(e){toast('导入失败: '+e.message,6000)}
}
/* ---- ① 灵感收集 ---- */
async function runCollect(){
  if(!gateCheckSync())return;
  const skills=sel('collect','collectChips');
  if(!skills.length){toast('至少勾选一个技能');return}
  $('btnCollect').disabled=true;
  pbarStart('pb1','st1',skills.flatMap(s=>[s+': 准备…',s+': 联网检索…',s+': 生成中…']).concat('汇总…'));
  try{
    const j=await api('/api/skill/collect',{skills,direction:$('direction').value.trim()},true);
    $('m1').value=j.output;refreshSummary('m1');
    pbarDone('pb1','st1');$('btnM1ok').disabled=false;guide1='edit';updateFlowHint();
    toast('灵感收集完成（'+skills.join(' + ')+'）');
  }catch(e){pbarFail('pb1','st1',e.message);toast('失败: '+e.message,5000)}
  $('btnCollect').disabled=false;
}
let m1ok=false,m2ok=false;
function setM1ok(v){m1ok=v;const b=$('btnM1ok');
  b.textContent=v?'✓ 资料我已审核，传递给下一个环节使用。':'审核确认中';
  b.classList.toggle('confirm-done',v);b.classList.toggle('confirm-pending',!v);
  updRHint()}
function confirmM1(){setM1ok(true);autoSave();toast('灵感资料已确认');guide1=null;updateFlowHint();
  $('card2').scrollIntoView({behavior:'smooth'});
  if(!$('topic').value.trim()){setTimeout(()=>$('topic').focus(),450)}}
function updRHint(){
  const t=$('topic').value.trim();
  $('rHint').textContent=t?(m1ok?'':'灵感资料未确认，将不带资料调研'):'';
}
/* ---- ② 深度调研 ---- */
function syncTopicGate(){ $('btnResearch').disabled=!$('topic').value.trim(); updRHint() }
$('topic').addEventListener('input',syncTopicGate);
async function runResearch(){
  if(!gateCheckSync())return;
  const topic=$('topic').value.trim();
  if(!topic){toast('先输入选题');return}
  const skills=sel('research','researchChips');
  if(!skills.length){toast('至少勾选一个技能');return}
  $('btnResearch').disabled=true;
  $('card2').classList.add('stage-running');
  pbarStart('pb2','st2',['联网搜索证据…'].concat(skills.flatMap(s=>[s+': 准备…',s+': 检索分析…'])).concat('汇总…'));
  try{
    const j=await api('/api/skill/research',{topic,material1:m1ok?$('m1').value:'',skills},true);
    $('m2').value=j.output;refreshSummary('m2');
    pbarDone('pb2','st2');$('btnM2ok').disabled=false;guide2='edit';updateFlowHint();
    toast('深度调研完成（'+j.backend+' 搜到 '+j.sources_found+' 条）');
  }catch(e){pbarFail('pb2','st2',e.message);toast('失败: '+e.message,5000)}
  $('btnResearch').disabled=false;
  $('card2').classList.remove('stage-running');
}
function setM2ok(v){m2ok=v;const b=$('btnM2ok');
  b.textContent=v?'✓ 资料我已审核，传递给下一个环节使用。':'审核确认中';
  b.classList.toggle('confirm-done',v);b.classList.toggle('confirm-pending',!v)}
function confirmM2(){setM2ok(true);autoSave();toast('调研资料已确认');guide2=null;updateFlowHint();
  $('card3').scrollIntoView({behavior:'smooth'})}
/* ---- ③ 统一成稿 ---- */
async function runUnify(){
  if(!gateCheckSync())return;
  const topic=$('topic').value.trim();
  if(!topic){toast('先输入选题');return}
  const skill=$('unifySel').value;
  $('btnUnify').disabled=true;
  $('card3').classList.add('stage-running');
  pbarStart('pb3','st3',['套用前缀: 请根据以下选题和资料，给我生成文案…',skill+': 统稿中…',skill+': 自检打磨…']);
  try{
    const j=await api('/api/skill/unify',{topic,material1:m1ok?$('m1').value:'',material2:m2ok?$('m2').value:$('m2').value,skill},true);
    $('script').value=j.script;refreshSummary('script');
    pbarDone('pb3','st3');$('btnSok').disabled=false;guide3='edit';updateFlowHint();
    toast('文案已生成（'+skill+'）');
    autoSave();
  }catch(e){pbarFail('pb3','st3',e.message);toast('失败: '+e.message,5000)}
  $('btnUnify').disabled=false;
  $('card3').classList.remove('stage-running');
}
/* ---- 项目 ---- */
let projName='';
async function autoSave(){
  if(!projName){toast('请先在顶部新建或打开项目');return}
  try{ await api('/api/project/save',{project:projName,topic:$('topic').value.trim(),
    material1:$('m1').value,material2:$('m2').value,script:$('script').value,direction:$('direction').value.trim()});
    refreshProjects();
  }catch(e){toast('自动保存失败: '+e.message,4000)}
}
function saveProject(){autoSave();toast('已保存到项目 '+(projName||'(未建)'));guide3=null;updateFlowHint()}
function newProject(){$('npName').value='';$('npModal').style.display='flex';$('npName').focus()}
function closeNp(){$('npModal').style.display='none'}
async function createProject(){
  const n=$('npName').value.trim();
  if(!n){toast('先起个项目名');return}
  try{
    const j=await api('/api/project/create',{name:n});
    projName=j.project;
    ['m1','m2','script'].forEach(k=>{$(k).value='';refreshSummary(k)});
    setM1ok(false);setM2ok(false);$('btnSok').disabled=true;
    $('topic').value='';$('direction').value='';
    closeNp();await refreshProjects();
    $('projInfo').textContent='当前: '+projName;
    avImages=[];renderAvLib();
    customItems=[];
    editItems=[];editSel=new Set();editHasDraft=false;renderEditList();
    $('editPreview').style.display='none';
    renderSegs([]);
    loadVoiceRef();
    toast('项目已创建: '+projName);syncProjGate();
  }catch(e){toast('创建失败: '+e.message,5000)}
}
async function refreshProjects(){
  const j=await api('/api/projects');
  const s=$('projList');s.innerHTML='<option value="">— 打开已有项目 —</option>';
  (j.projects||[]).forEach(p=>{const o=document.createElement('option');o.value=p.name;
    o.textContent=p.name+'（保留'+p.kept+'/'+p.segments+'）';s.appendChild(o)});
  if(projName)s.value=projName;
}
async function deleteProject(){
  const name=$('projList').value||projName;
  if(!name){toast('先在下拉里选中要删除的项目');return}
  if(!confirm('确定删除项目「'+name+'」？\n其全部文案 / 音频 / 视频 / 成片将一并删除，不可恢复！'))return;
  try{
    await api('/api/project/delete',{project:name});
    toast('已删除 '+name);
    if(name===projName){location.reload();return}
    await refreshProjects();syncProjGate();
  }catch(e){toast('删除失败: '+e.message,6000)}
}
/* ---- 自定义设计模板: 风格+提示词+全家族菜单快照 ---- */
let hfTpls=[];
async function refreshTpls(){
  try{const j=await api('/api/hf/templates');hfTpls=j.templates||[]}catch(e){hfTpls=[]}
  const s=$('hfTpl');s.innerHTML='<option value="">— 选择模板一键套用 —</option>';
  hfTpls.forEach(t=>{const o=document.createElement('option');o.value=t.name;
    o.textContent=t.name+'（'+t.ts+'）';s.appendChild(o)});
}
function applyTpl(){
  const t=hfTpls.find(x=>x.name===$('hfTpl').value);
  if(!t){toast('先在下拉里选一个模板');return}
  if(t.style)$('hfStyle').value=t.style;
  if(t.prompt!=null)$('hfPrompt').value=t.prompt;
  const mn=t.menu||{};let atmo=mn.atmo;
  if(!atmo)atmo=mn.grain?'grain':(mn.vignette?'vignette':'none');
  const sf=(n,v,d)=>{const el=document.querySelector('input[name="'+n+'"][value="'+(v||d)+'"]');if(el)el.checked=true};
  sf('fTitle',mn.title,'ai');sf('fCap',mn.caption,'ai');sf('fAtmo',atmo,'ai');
  sf('fLt',mn.lt,'ai');sf('fTrans',mn.trans,'none');sf('fBgb',mn.bgb,'none');
  $('mTopMark').checked=!!mn.top_mark;famLab();
  toast('模板已套用: '+t.name);
}
async function saveTpl(){
  const name=($('hfTplName').value||'').trim();
  if(!name){toast('先在右侧输入框填模板名');return}
  try{
    await api('/api/hf/templates/save',{name:name,style:$('hfStyle').value,prompt:$('hfPrompt').value,
      menu:refineMenu(),top_mark:$('mTopMark').checked,...refineCtaTexts()});
    $('hfTplName').value='';await refreshTpls();$('hfTpl').value=name;
    toast('模板已保存: '+name+'（重名自动覆盖）');
  }catch(e){toast('保存失败: '+e.message,6000)}
}
async function delTpl(){
  const name=$('hfTpl').value;
  if(!name){toast('先在下拉里选中要删除的模板');return}
  if(!confirm('删除模板「'+name+'」？'))return;
  try{await api('/api/hf/templates/delete',{name:name});await refreshTpls();toast('模板已删除')}
  catch(e){toast('删除失败: '+e.message,6000)}
}
async function loadProject(name){
  if(!name)return;
  const j=await api('/api/project/'+encodeURIComponent(name));
  projName=name;
  $('topic').value=j.topic;$('m1').value=j.material1;$('m2').value=j.material2;$('script').value=j.script;
  $('direction').value=j.direction||'';
  ['m1','m2','script'].forEach(k=>refreshSummary(k));
  setM1ok(!!j.material1);setM2ok(!!j.material2);
  if(j.script && !j.material1 && !j.material2){setM1ok(true);setM2ok(true)}  // 外稿导入型项目: 前置视为已确认
  $('btnSok').disabled=!j.script;
  $('projInfo').textContent='当前: '+name;
  syncTopicGate();
  avImages=j.images||[];renderAvLib();
  customItems=j.custom||[];
  renderSegs(j.segments||[]);
  loadVoiceRef();
  loadEditItems();
  toast('已载入 '+name);syncProjGate();catHintIfTtsDone();resumeVideoPoll();
}
/* ---- ④ 分段合成 ---- */
/* TTS 引擎左右切换: 左=云端 RH, 右=本地 soar; 两边音色配置各自独立 */
let ttsEng = localStorage.getItem('koub.ttsEng') || 'local';
function setTtsEng(e){
  ttsEng = e; localStorage.setItem('koub.ttsEng', e);
  $('engRh').className = 'engbtn' + (e === 'rh' ? ' on' : '');
  $('engLocal').className = 'engbtn' + (e === 'local' ? ' on' : '');
  $('rhTtsBox').style.display = e === 'rh' ? '' : 'none';
  $('voiceRow').style.display = e === 'local' ? '' : 'none';
  $('engHint').textContent = e === 'rh' ? '云端合成约 10~60 秒/段（走 RH 队列，串行逐段提交）' : '首次本地合成需加载模型（约40秒）';
  if(e === 'rh')refreshRhTts();
}
async function refreshTtsCap(){
  try{
    const j = await api('/api/tts/capability');
    if(!j.local){$('engLocalTag').textContent = '（未检出）';$('engLocalTag').title = j.local_error || '本地 TTS 不可用'}
  }catch(e){}
}
async function refreshRhTts(){
  try{
    const j=await api('/api/tts/rh/config');
    $('rhKeyInfo').textContent=j.has_key?('Key '+j.key_hint):'未配 Key(⚙设置·与数字人共用)';
    if(!$('rhRefText').value)$('rhRefText').value=j.ref_text||'';
    $('rhRefInfo').textContent=j.has_ref?'｜当前: 自定义音色':'｜当前: 内置默认音色(梁秘书)';
    $('rhRefInfo').textContent=j.has_ref?('参考音色已上传'):'';
    $('btnRhClr').style.display=j.has_ref?'':'none';
  }catch(e){}
}
async function upRhRef(){
  const p=$('rhRefPath').value.trim();
  if(!p){  /* 只填了文本未选文件 → 仅保存参考文本 */
    try{await api('/api/tts/rh/config',{ref_text:$('rhRefText').value});toast('参考文本已保存')}
    catch(e){toast('保存失败: '+e.message,6000)}
    return;
  }
  $('btnRhRef').disabled=true;
  try{
    const j=await api('/api/tts/rh/config',{ref_audio_path:p,ref_text:$('rhRefText').value});
    $('btnRhClr').style.display='';
    toast('参考音频已上传 RH: '+j.ref_audio,5000);
    refreshRhTts();
  }catch(e){toast('上传失败: '+e.message,6000)}
  $('btnRhRef').disabled=false;
}
async function clrRhRef(){
  try{await api('/api/tts/rh/config',{clear_ref:true});
    $('btnRhClr').style.display='none';$('rhRefInfo').textContent='';
    toast('已恢复应用默认音色')}
  catch(e){toast('失败: '+e.message,6000)}
}
/* 合成状态显性化(通用): busy=按钮琥珀呼吸+卡片金框, done=按钮绿闪「生成完毕」+卡片绿框 */
function busyBtn(btn, card, on, busyText){
  btn.disabled=on;
  card.classList.toggle('working',on);
  if(on){if(!btn.dataset.t)btn.dataset.t=btn.textContent;btn.textContent=busyText;btn.classList.add('busy')}
  else{btn.classList.remove('busy');if(btn.dataset.t){btn.textContent=btn.dataset.t;delete btn.dataset.t}}
}
function doneBtn(btn, card){
  const orig = btn.dataset.t || btn.textContent;   /* 先存原文(busyBtn(false)会清 dataset.t) */
  busyBtn(btn,card,false,'');
  btn.classList.add('done');btn.textContent='✓ 生成完毕';
  card.classList.add('ok');
  setTimeout(()=>{btn.classList.remove('done');btn.textContent=orig;card.classList.remove('ok')},5000);
}
const BTN_SPLIT=()=>$('btnSplit'), CARD4=()=>$('card4'), BTN_GEN=()=>$('btnTtsGen'), CARD_AV=()=>$('cardAV');
function synthBusy(on){
  busyBtn(BTN_SPLIT(),CARD4(),on,'⏳ 合成中…');
  CARD4().classList.toggle('stage-running',on);   /* 本地同步合成期间也亮金线+出猫 */
  if(on)setCat(true,'card4');
}
function synthDone(){doneBtn(BTN_SPLIT(),CARD4());catHintIfTtsDone()}
/* TTS 全部合成且还没有任何视频 → 黑猫气泡引导去数字人工作台生成视频(拼初稿的硬前提) */
function catHintIfTtsDone(){
  if(!projName||!lastSegs.length)return;
  const allKept=lastSegs.every(s=>s.status==='kept');
  const anyVideo=lastSegs.some(s=>s.video_status==='running'||s.video_status==='queued'||s.video_status==='kept'||s.video);
  if(allKept&&!anyVideo)catShowBubble(['音频已全部合成！下一步去「数字人工作台」至少生成一段视频，才能拼出视频初稿哦。']);
}
/* 逐段/单段成功反馈: 卡片绿脉冲 1.8s + 主按钮短促绿闪 (④分段卡与工作台avseg卡通用) */
function flashCard(id){
  const els=document.querySelectorAll('#segList .segcard[data-id="'+id+'"], #avSegs .avseg[data-id="'+id+'"]');
  els.forEach(el=>{el.classList.add('flash-ok');setTimeout(()=>el.classList.remove('flash-ok'),1800)});
}
function tickBtn(){
  const b=$('btnSplit');b.classList.add('tick');setTimeout(()=>b.classList.remove('tick'),500);
}
async function splitSynth(resynth){
  if(!gateCheckSync())return;
  const eng = ttsEng;   /* 左右切换当前选中的引擎, 无自动魔法 */
  synthBusy(true);
  try{
    if(eng==='rh'){
      if(!resynth){
        const j=await api('/api/segments/split',{project:projName,script:$('script').value});
        renderSegs(j.segments);toast('已分 '+j.count+' 段，云端合成中…');
      }
      $('segSummary').innerHTML='<span class="spin"></span>云端合成中…';
      await api('/api/tts/rh/synth',{project:projName});
      const seenIds=new Set();
      await new Promise((res,rej)=>{
        const poll=setInterval(async()=>{try{
          const s=await api('/api/tts/rh/status');
          busySegId=(s.cur||'').split(' ')[0]||'';   /* 正在合成的段亮角标 */
          refreshSegCards();
          $('segSummary').innerHTML='<span class="spin"></span>云端合成中… 已完成 '+s.finished+'/'+s.total+(s.cur?(' · '+s.cur):'');
          /* 逐段完成反馈: 新完成的段卡绿脉冲一次 + 按钮短促绿闪 */
          (s.done_ids||[]).forEach(id=>{
            if(seenIds.has(id))return;
            seenIds.add(id);
            flashCard(id);tickBtn();
          });
          if(!s.running){clearInterval(poll);busySegId='';refreshSegCards();s.error?rej(new Error(s.error)):res()}
        }catch(e){}},4000);
      });
      await reloadSegs();
      toast('云端合成完成 — 🔊 可试听',4500);
      catHintIfTtsDone();synthDone();
    }else{
      if(!resynth){
        const j=await api('/api/segments/split',{project:projName,script:$('script').value});
        renderSegs(j.segments);toast('已分 '+j.count+' 段，开始合成…');
      }
      $('segSummary').innerHTML='<span class="spin"></span>合成中（soar 每段约10-80秒）…';
      /* 渐进刷新: 每段完成立即点亮徽标+新音频特效, 不等整批结束 */
      const prevKept=new Set(resynth?[]:lastSegs.filter(s=>s.status==='kept').map(s=>s.id));
      let lastKey='';
      const poll=setInterval(async()=>{try{
        const j=await api('/api/project/'+encodeURIComponent(projName));
        const nxt=(j.segments||[]).find(x=>x.status!=='kept'&&x.status!=='deleted');   /* 本地逐段合成: 当前正在做的段 */
        busySegId=nxt?nxt.id:'';refreshSegCards();
        j.segments.forEach(s=>{if(s.status==='kept'&&!prevKept.has(s.id))freshAudio.add(s.id)});
        const key=j.segments.map(s=>s.id+s.status+(s.audio_ok?'1':'0')).join();
        if(key!==lastKey){
          lastKey=key;renderSegs(j.segments);
          /* 本地整批同样逐段反馈: 新完成的段卡绿脉冲 + 按钮绿闪 */
          j.segments.forEach(s=>{if(s.status==='kept'&&!prevKept.has(s.id)){flashCard(s.id);tickBtn()}});
          const done=j.segments.filter(s=>s.status==='kept').length;
          $('segSummary').innerHTML='<span class="spin"></span>合成中… 已完成 '+done+'/'+j.segments.length+' 段（🔊 边完成边可试听）';
        }
      }catch(e){}},4000);
      try{
        const r=await api('/api/segments/synth',{project:projName,id:resynth?null:undefined},true);
        (r.synthed||[]).forEach(id=>freshAudio.add(id));
      }finally{
        clearInterval(poll);
        await reloadSegs();
      }
      toast('合成完成 — 🔊 新音频段会跳动提示，试听后消失',4500);
      synthDone();
    }
  }catch(e){toast('失败: '+e.message,5000);reloadSegs().catch(()=>{})}
  synthBusy(false);
}
/* 新合成未试听的分段(会话级): 播放即解除 */
let freshAudio=new Set();
async function reloadSegs(){
  if(!projName)return;
  const j=await api('/api/project/'+encodeURIComponent(projName));
  renderSegs(j.segments||[]);
  resumeVideoPoll();   /* 数据刷新后检查是否有需要恢复轮询的任务 */
}
function renderSegs(segs){
  lastSegs=segs||[];
  const box=$('segList');
  /* 重渲染不打断正在播放的音频 */
  const playing=[...box.querySelectorAll('audio')].find(a=>!a.paused&&a.src);
  const ptime=playing?playing.currentTime:0;
  const psrc=playing?playing.src.split('/').pop():'';
  box.innerHTML='';
  let kept=0,deleted=0,pending=0;
  (segs||[]).forEach(s=>{
    if(s.status==='kept')kept++;else if(s.status==='deleted')deleted++;else pending++;
    /* ---- 段卡片: 文本 + 声音资产条 + 数字人资产条, 同一份子项收在一张卡 ---- */
    const c=document.createElement('div');c.className='segcard'+(s.status==='deleted'?' deleted':'');
    c.dataset.id=s.id;
    c.ondragover=allowDrop;c.ondragleave=unDrop;c.ondrop=e=>dropBind(e,s.id);
    const hasAudio=s.status!=='deleted'&&s.wav&&s.audio_ok;
    const stTag=s.status==='kept'?'<span class="badge kept">已合成</span>'
      :s.status==='deleted'?'<span class="badge deleted">已删除·可恢复</span>':'<span class="badge pending">待合成</span>';
    c.innerHTML=`<div class="shead"><span class="id">${s.id}</span>${stTag}<span class="len">${s.text.length}字</span></div>
      <div class="stext">${s.text.replace(/</g,'&lt;')}</div>`;
    /* 🎤 声音资产条 */
    const a=document.createElement('div');a.className='asset audio'+((s.status==='kept'&&s.wav&&s.audio_ok)?' tts-done':'');   /* TTS已合成: 声音条淡绿 */
    a.innerHTML='<span class="alabel">🎤 声音</span>';
    if(hasAudio){
      const au=document.createElement('audio');au.controls=true;au.preload='none';
      /* ?v=gen_at: 重合成后URL变化, 强制浏览器拉新音频(否则回放缓存里的旧声音) */
      au.src='/audio/'+encodeURIComponent(projName)+'/'+s.wav+'?v='+(s.gen_at||0);a.appendChild(au);
    } else {
      a.insertAdjacentHTML('beforeend','<span class="muted">未合成</span>');
    }
    if(s.status==='kept'&&freshAudio.has(s.id)){
      /* 新合成未试听: 卡金光呼吸+头徽标, 点播放即解除 */
      c.classList.add('fresh');
      const fb=document.createElement('span');fb.className='badge gold freshjump';fb.textContent='🔊 新音频·待试听';
      fb.title='播放试听这段音频后，提示自动消失';
      c.querySelector('.shead').appendChild(fb);
      const au=a.querySelector('audio');
      if(au)au.addEventListener('play',()=>{freshAudio.delete(s.id);renderSegs(lastSegs)},{once:true});
    }
    if(s.status!=='deleted'){
      const bK=document.createElement('button');bK.className='sm';bK.textContent='保留';
      bK.title='保留这段音频';
      bK.onclick=async()=>{await api('/api/segments/status',{project:projName,id:s.id,action:'keep'});reloadSegs()};
      const bD=document.createElement('button');bD.className='danger sm';bD.textContent='删除';
      bD.title='删除这段音频（移入回收目录，卡内可♻恢复，不会丢）';
      bD.onclick=async()=>{await api('/api/segments/status',{project:projName,id:s.id,action:'delete'});
        toast(s.id+' 已删除 — 反悔可点该卡的 ♻ 恢复',4000);reloadSegs()};
      const bR=document.createElement('button');bR.className='sm';bR.textContent='重新合成';
      bR.title='用当前引擎重新合成这段音频';
      bR.onclick=async()=>{bR.disabled=true;bR.innerHTML='<span class="spin"></span>';
        busySegId=s.id;refreshSegCards();
        try{
          if(ttsEng==='rh'){   /* 云端引擎: 走 RH 单段 job + 轮询 */
            await api('/api/tts/rh/synth',{project:projName,id:s.id});
            await new Promise((res,rej)=>{const poll=setInterval(async()=>{try{
              const st=await api('/api/tts/rh/status');
              if(!st.running){clearInterval(poll);st.error?rej(new Error(st.error)):res()}
            }catch(e){}},4000)});
          }else{
            await api('/api/segments/synth',{project:projName,id:s.id},true);
          }
          freshAudio.add(s.id);
          busySegId='';refreshSegCards();
          await reloadSegs();
          flashCard(s.id);tickBtn();   /* 单段成功: 同样绿脉冲 */
          toast(s.id+' 音频已重新合成（当前引擎）');
        }catch(e){busySegId='';refreshSegCards();toast(e.message,5000)}
      };
      a.appendChild(bK);a.appendChild(bD);a.appendChild(bR);
    } else {
      /* 已删除卡: 可后悔 */
      const bRes=document.createElement('button');bRes.className='sm';bRes.textContent='♻ 恢复';
      bRes.title='误删找回：音频从回收目录还原为保留状态；文件已不在则回到待合成（文本不会丢）';
      bRes.onclick=async()=>{await api('/api/segments/status',{project:projName,id:s.id,action:'restore'});
        toast(s.id+' 已恢复');reloadSegs()};
      const bRe=document.createElement('button');bRe.className='sm';bRe.textContent='恢复并重合成';
      bRe.title='找回该段并立即用当前引擎重新合成（觉得原音频效果不好时用这个）';
      bRe.onclick=async()=>{bRe.disabled=true;bRe.innerHTML='<span class="spin"></span>';
        busySegId=s.id;refreshSegCards();
        try{await api('/api/segments/status',{project:projName,id:s.id,action:'restore'});
          if(ttsEng==='rh'){
            await api('/api/tts/rh/synth',{project:projName,id:s.id});
            await new Promise((res,rej)=>{const poll=setInterval(async()=>{try{
              const st=await api('/api/tts/rh/status');
              if(!st.running){clearInterval(poll);st.error?rej(new Error(st.error)):res()}
            }catch(e){}},4000)});
          }else{
            await api('/api/segments/synth',{project:projName,id:s.id},true);
          }
          freshAudio.add(s.id);
          busySegId='';refreshSegCards();
          await reloadSegs();
          flashCard(s.id);tickBtn();
          toast(s.id+' 已恢复并重新合成，试听后提示自动消失')}
        catch(e){busySegId='';refreshSegCards();toast(e.message,5000)}
      };
      a.appendChild(bRes);a.appendChild(bRe);
    }
    c.appendChild(a);
    /* 🎬 数字人资产条 */
    if(s.status!=='deleted'){
      const vs=s.video_status||'';
      const hasVideo=s.video&&vs!=='deleted';
      const r=document.createElement('div');r.className='asset video'+(vs==='running'?' running':'')+(hasVideo?' vid-done':'');   /* 视频已生成: 视频条淡绿 */
      r.innerHTML='<span class="alabel">🎬 数字人视频</span>';
      if(s.img){
        r.appendChild(avThumb(s.img,26,false));
        const ub=document.createElement('button');ub.className='sm';ub.textContent='解绑';
        ub.title='清除该段绑定的形象图，回落默认形象图';
        ub.onclick=async()=>{await api('/api/avatar/bind',{project:projName,seg:s.id,img:''});reloadSegs()};
        r.appendChild(ub);
      } else {
        r.insertAdjacentHTML('beforeend','<span class="muted">未绑图·将用默认形象图</span>');
      }
      if(selectedAvImg){
        const bb=document.createElement('button');bb.className='acc sm';bb.textContent='📌 绑选中图';
        bb.title='把当前选中的图库形象绑定到该段: '+selectedAvImg.split('\\').pop();
        bb.onclick=async()=>{await api('/api/avatar/bind',{project:projName,seg:s.id,img:selectedAvImg});
          toast(s.id+' 已绑定选中形象图');reloadSegs()};
        r.appendChild(bb);
      }
      if(hasVideo){
        const v=document.createElement('video');v.controls=true;v.preload='none';
        v.title='若出现怪异字幕、口型不自然或画面瑕疵，点「重新生成」再试一次';
        v.src='/video/'+encodeURIComponent(projName)+'/'+s.video;
        r.appendChild(v);
        r.insertAdjacentHTML('beforeend','<span class="badge gold">视频已生成</span>');
      } else if(vs==='running'){
        r.insertAdjacentHTML('beforeend','<span class="badge gold"><span class="spin"></span>数字人生成中… 预计 300 秒左右</span>');
      } else if(vs==='deleted'){
        r.insertAdjacentHTML('beforeend','<span class="badge">视频已删除</span>');
      } else {
        r.insertAdjacentHTML('beforeend','<span class="badge">未生成</span>');
        if(s.video_error){   /* 上次失败原因常驻卡片, 不再只靠 8 秒 toast */
          const ev=document.createElement('span');ev.className='vinfo err';
          ev.textContent='上次失败: '+s.video_error;ev.title=s.video_error;
          r.appendChild(ev);
        }
      }
      if(vs!=='running'){
        const bG=document.createElement('button');bG.className='acc sm';
        bG.textContent=hasVideo?'重新生成':'生成视频';
        bG.title='本段音频 + 绑定形象图（未绑定则用默认）→ RunningHub 工作流生成口播视频';
        bG.onclick=()=>avatarSynth(s.id);
        r.appendChild(bG);
      }
      if(hasVideo){
        const bVK=document.createElement('button');bVK.className='sm';bVK.textContent='保留视频';
        bVK.title='保留这段数字人视频';
        bVK.onclick=async()=>{await api('/api/avatar/status',{project:projName,seg:s.id,action:'keep'});reloadSegs()};
        const bVD=document.createElement('button');bVD.className='danger sm';bVD.textContent='删除视频';
        bVD.title='删除这段数字人视频（移入回收目录）';
        bVD.onclick=async()=>{await api('/api/avatar/status',{project:projName,seg:s.id,action:'delete'});reloadSegs()};
        r.appendChild(bVK);r.appendChild(bVD);
      }
      c.appendChild(r);
    }
    box.appendChild(c);
  });
  $('segSummary').textContent=segs&&segs.length?`共 ${segs.length} 段 · 保留 ${kept} · 待合成 ${pending} · 已删除 ${deleted}`:'（尚未分段）';
  /* 恢复被重渲染打断的播放 */
  if(psrc){const na=[...box.querySelectorAll('audio')].find(a=>a.src.endsWith(psrc));
    if(na){try{na.currentTime=ptime;na.play().catch(()=>{})}catch(e){}}}
  refreshSegCards();   /* 重建 DOM 后恢复「正在执行」角标 */
  renderAvatarPanel(segs);
  updateFlowHint();
}
/* 分段卡片任务角标: 该段正在合成 TTS 或正在生成数字人视频 → 四角角标呼吸 */
let busySegId='';
function refreshSegCards(){
  document.querySelectorAll('#segList .segcard, #avSegs .avseg').forEach(c=>{
    const s=(lastSegs||[]).find(x=>x.id===c.dataset.id)||{};
    const on=(s.video_status==='running')||(c.dataset.id&&c.dataset.id===busySegId);
    c.classList.toggle('stage-running',!!on);
  });
}
/* ---- 数字人工作台 (RunningHub) ---- */
let runningSegs=[], pvTimer=null, customItems=[];
function renderAvatarPanel(segs){
  const grid=$('avSegs');grid.innerHTML='';
  const kept=(segs||[]).filter(s=>s.status==='kept');
  const cards=[
    ...kept.map(s=>({...s,_kind:'seg'})),
    ...customItems.map(c=>({...c,_kind:'cus'}))
  ];
  if(!cards.length){grid.innerHTML='<span class="muted">暂无可用音频 — 完成④分段合成，或在上方「自制音频」直接上传</span>';return}
  cards.forEach(s=>{
    /* 四态: done(绿) / running(金) / queued(蓝虚线) / failed(红) / 未生成(默认灰) */
    const vst=s.video_status||'', isDone=!!s.video, isFail=!!s.video_error&&vst!=='running'&&!isDone;
    const stBadge=isDone?'<span class="badge ok">✓ 已生成</span>'
      :vst==='running'?'<span class="badge wait"><span class="spin"></span>生成中 · 约300秒</span>'
      :vst==='queued'?'<span class="badge lineup">⏸ 排队中 · 自动续交</span>'
      :isFail?'<span class="badge bad">✗ 失败</span>'
      :'<span class="badge gray">○ 未生成</span>';
    const c=document.createElement('div');c.className='avseg'
      +(vst==='running'?' running':'')+(vst==='queued'?' queued':'')
      +(isDone?' done':'')+(isFail?' failed':'');
    c.dataset.id=s.id;
    c.ondragover=allowDrop;c.ondragleave=unDrop;c.ondrop=e=>dropBind(e,s.id);
    const top=document.createElement('div');top.className='row';top.style.justifyContent='space-between';
    const tag=s._kind==='cus'?('<span class="badge gold">'+(s.text?'文本生成':'自制音频')+'</span>'):'';
    top.innerHTML=`<span class="id">${s.id}</span>${tag}${stBadge}`;
    c.appendChild(top);
    if(s.video_error&&s.video_status!=='running'&&!s.video){   /* 上次失败原因常驻可见 */
      const ev=document.createElement('div');ev.className='vinfo err';
      ev.textContent='上次失败: '+s.video_error;ev.title=s.video_error;
      c.appendChild(ev);
    }
    if(s._kind==='cus'){
      const nm=document.createElement('div');nm.className='txt';nm.style.fontWeight='600';
      nm.textContent=s.text?(s.text.length>44?s.text.slice(0,44)+'…':s.text):(s.name||s.id);c.appendChild(nm);
      const a=document.createElement('audio');a.controls=true;a.preload='none';a.style.width='100%';
      a.src='/media/'+encodeURIComponent(projName)+'/'+s.wav;c.appendChild(a);
    }
    const brow=document.createElement('div');brow.className='row';brow.style.flexWrap='nowrap';
    if(s.img){
      brow.appendChild(avThumb(s.img,40,false));
      const ub=document.createElement('button');ub.className='sm';ub.textContent='解绑';ub.title='清除绑定，回落默认形象图';
      ub.onclick=async()=>{await api('/api/avatar/bind',{project:projName,seg:s.id,img:''});reloadSegs()};
      brow.appendChild(ub);
    } else {
      brow.innerHTML='<span class="muted">未绑定 — 从图库拖一张图到此卡</span>';
    }
    c.appendChild(brow);
    if(s._kind==='seg'){
      const tx=document.createElement('div');tx.className='txt';
      tx.textContent=s.text.length>40?s.text.slice(0,40)+'…':s.text;
      c.appendChild(tx);
    }
    const b=document.createElement('button');b.className='acc sm';b.textContent=s.video?'重生成视频':'生成视频';
    b.onclick=()=>avatarSynth(s.id);
    c.appendChild(b);
    if(s.video){
      const v=document.createElement('video');v.controls=true;v.preload='none';
      v.title='若出现怪异字幕、口型不自然或画面瑕疵，点「重生成视频」再试一次';
      v.src='/video/'+encodeURIComponent(projName)+'/'+s.video;
      c.appendChild(v);
      const db=document.createElement('button');db.className='danger sm';db.textContent='删除视频';
      db.onclick=async()=>{await api('/api/avatar/status',{project:projName,seg:s.id,action:'delete'});reloadSegs()};
      c.appendChild(db);
    }
    if(s._kind==='cus'){
      const rm=document.createElement('button');rm.className='danger sm';rm.textContent='删除此音频';
      rm.title='删除该自制音频及其视频';
      rm.onclick=async()=>{await api('/api/avatar/custom-remove',{project:projName,id:s.id});await reloadCustom();reloadSegs()};
      c.appendChild(rm);
    }
    grid.appendChild(c);
  });
}
async function reloadCustom(){
  if(!projName)return;
  const j=await api('/api/project/'+encodeURIComponent(projName));
  customItems=j.custom||[];
}
function toggleTtsGen(){
  const b=$('ttsGenBox');b.style.display=b.style.display==='none'?'':'none';
}
async function runTtsGen(){
  if(!projName){toast('先新建或打开项目');return}
  const lines=$('ttsGenText').value.split('\n').map(l=>l.trim()).filter(Boolean);
  if(!lines.length){toast('先填写要合成的文本（每行一段）');return}
  const eng=(document.querySelector('input[name="genEng"]:checked')||{}).value||'rh';
  if(eng==='rh'){
    const long=lines.filter(l=>l.length>40).length;
    if(long&&!confirm(`${long} 行超过 40 字\n云端 TTS 超长行音色保持度会下降，建议每行 ≤40 字。\n仍要继续吗？`))return;
  }
  $('btnTtsGen').disabled=true;$('ttsGenInfo').innerHTML='<span class="spin"></span>提交中…';
  busyBtn(BTN_GEN(),CARD_AV(),true,'⏳ 生成中…');
  CARD_AV().classList.add('stage-running');setCat(true,'cardAV');
  try{
    await api('/api/avatar/tts-gen',{project:projName,lines,engine:eng});
    await new Promise((res,rej)=>{
      const poll=setInterval(async()=>{try{
        const s=await api('/api/avatar/tts-gen/status');
        $('ttsGenInfo').innerHTML='<span class="spin"></span>生成中… '+s.finished+'/'+s.total+(s.cur?(' · '+s.cur):'');
        if(!s.running){clearInterval(poll);s.error?rej(new Error(s.error)):res()}
      }catch(e){}},4000);
    });
    await reloadCustom();renderAvatarPanel(lastSegs);
    $('ttsGenInfo').textContent='';
    toast('✓ 已生成 '+lines.length+' 条音频，进入工作台素材，可直接生成视频',5000);
    doneBtn(BTN_GEN(),CARD_AV());
  }catch(e){toast('失败: '+e.message,6000);$('ttsGenInfo').textContent='';busyBtn(BTN_GEN(),CARD_AV(),false,'')}
  CARD_AV().classList.remove('stage-running');refreshStageRunning();
}
async function uploadCustom(inp){
  if(!projName){toast('先新建或打开项目');inp.value='';return}
  const files=[...inp.files||[]];
  if(!files.length)return;
  for(const f of files){
    try{
      toast('上传中: '+f.name,1500);
      const buf=await f.arrayBuffer();
      const r=await fetch('/api/avatar/custom-upload?project='+encodeURIComponent(projName)+'&name='+encodeURIComponent(f.name),
        {method:'POST',body:buf});
      const j=await r.json();
      if(!r.ok)throw new Error(j.detail||('HTTP '+r.status));
    }catch(e){toast(f.name+' 失败: '+e.message,6000)}
  }
  inp.value='';
  await reloadCustom();renderAvatarPanel(lastSegs);
  toast('自制音频已就绪 — 绑定形象后即可生成视频');
}
async function openFolder(scope){
  try{
    const j=await api('/api/open-folder',{scope,project:projName});
    toast('已打开: '+j.path,3500);
  }catch(e){toast('失败: '+e.message,4000)}
}
/* ---- 全站 hover 流光注入 (按钮+卡片; 动态按钮经 mouseover 懒注入) ---- */
const SVGNS='http://www.w3.org/2000/svg';
function injectGlow(el){
  if(!el||el.querySelector(':scope>svg.glowc'))return;
  const svg=document.createElementNS(SVGNS,'svg');svg.setAttribute('class','glowc');
  ['glow-blur','glow-line'].forEach(cls=>{
    const r=document.createElementNS(SVGNS,'rect');
    r.setAttribute('pathLength','100');r.setAttribute('stroke-linecap','round');r.setAttribute('class',cls);
    svg.appendChild(r);
  });
  el.appendChild(svg);
  const rx=getComputedStyle(el).borderRadius;
  svg.querySelectorAll('rect').forEach(r=>r.setAttribute('rx',rx));
}
document.addEventListener('mouseover',e=>{
  const t=e.target.closest&&e.target.closest('button,.avseg');
  if(t)injectGlow(t);
},{passive:true});
document.querySelectorAll('.card,.avseg').forEach(injectGlow);

function applyPulseFX(el){
  if(!el||el.querySelector(':scope>.fxwrap'))return;
  const w=document.createElement('span');w.className='fxwrap';
  w.appendChild(Object.assign(document.createElement('span'),{className:'fxspark'}));
  el.appendChild(w);
}
function removePulseFX(el){if(!el)return;const w=el.querySelector(':scope>.fxwrap');if(w)w.remove()}
function setPulse(id,on){
  const e=$(id);if(!e)return;
  if(on){e.classList.add('pulse');applyPulseFX(e)}
  else{e.classList.remove('pulse');removePulseFX(e)}
}
let guide1=null,guide2=null,guide3=null;   /* 按钮级引导: 'edit'(展开编辑) → 'confirm'/'save'(审核确认/保存文案) → null */
function pulseBtn(id,on){
  const b=$(id);if(!b)return;
  if(on===false){b.classList.remove('pulse-btn');removePulseFX(b);return}
  b.classList.add('pulse-btn');applyPulseFX(b);
}
function updateFlowHint(){
  ['card1','card2','card3','card4','cardProj','cardAV'].forEach(id=>setPulse(id,false));
  ['btnM1edit','btnM1ok','btnM2edit','btnM2ok','btnSedit','btnSok'].forEach(id=>pulseBtn(id,false));
  $('btnSettings').classList.remove('pulse-btn');removePulseFX($('btnSettings'));
  api('/api/settings').then(j=>{
    if(!j.has_key){$('btnSettings').classList.add('pulse-btn');applyPulseFX($('btnSettings'));return}  // 1. 未配Key → 设置
    if(!projName){setPulse('cardProj',true);return}                             // 2. 未建项目 → 项目
    if(!$('script').value){                                                     // 3. 无文稿 → ①/②/③(有稿即跳过)
      if(!$('m1').value){setPulse('card1',true);return}
      if(guide1==='edit'){pulseBtn('btnM1edit');return}       /* 收集完成 → 引导展开编辑 */
      if(guide1==='confirm'){pulseBtn('btnM1ok');return}      /* 编辑保存过 → 引导审核确认 */
      if(!$('m2').value){setPulse('card2',true);return}
      if(guide2==='edit'){pulseBtn('btnM2edit');return}
      if(guide2==='confirm'){pulseBtn('btnM2ok');return}
      setPulse('card3',true);return
    }
    if(guide3==='edit'){pulseBtn('btnSedit');return}         /* 成稿完成 → 引导展开编辑 */
    if(guide3==='save'){pulseBtn('btnSok');return}           /* 编辑保存过 → 引导保存文案 */
    if(!lastSegs.some(s=>s.status==='kept')){setPulse('card4',true);return}     // 6. 无音频 → ④
    if(editHasDraft!==true){setPulse('card5',true);return}                      // 7. 无初稿 → ⑤（缺视频段用黑底替代）
  }).catch(()=>{});
}
/* ---- ⑤ 自动剪辑 ---- */
let editItems=[],editSel=new Set(),editHasDraft=false,editHasVideo=false,editTimer=null;
async function loadEditItems(){
  if(!projName)return;
  try{
    const j=await api('/api/edit/items?name='+encodeURIComponent(projName));
    editItems=j.items||[];editHasDraft=!!j.has_draft;editHasVideo=!!j.has_video;
    let cfg={};
    try{cfg=await api('/api/edit/config?name='+encodeURIComponent(projName))}catch(e){}
    const order=cfg.order||[];
    editItems.sort((a,b)=>{const ia=order.indexOf(a.id),ib=order.indexOf(b.id);
      return (ia>=0||ib>=0)?(ia<0?1:ib<0?-1:ia-ib):(a.id<b.id?-1:1)});
    /* 默认全选(含自制音频); 修复: cfg.selected 为空数组时 JS 空数组为 truthy → 误判为"已选"导致无素材 */
    const _sel0=(Array.isArray(cfg.selected)&&cfg.selected.length)?cfg.selected:editItems.map(x=>x.id);
    editSel=new Set(_sel0);
    renderEditList();
    if(cfg.title!=null)$('editTitle').value=cfg.title;
    else if(!$('editTitle').value&&j.topic)$('editTitle').value=j.topic;   // 开场标题默认=选题
    if(cfg.bgm!=null)$('editBgm').value=cfg.bgm;
    if(cfg.bgm_vol!=null)$('editBgmVol').value=cfg.bgm_vol;
    if(cfg.outro!=null)$('editOutro').checked=!!cfg.outro;
    const mn=cfg.menu||{};
    /* 旧版 cfg 兼容: grain/vignette 布尔 → atmo */
    let atmo=mn.atmo;
    if(!atmo)atmo=mn.grain?'grain':(mn.vignette?'vignette':'none');
    const setFam=(name,val,def)=>{const el=document.querySelector('input[name="'+name+'"][value="'+(val||def)+'"]');if(el)el.checked=true;};
    setFam('fTitle',mn.title,'ai');setFam('fCap',mn.caption,'ai');setFam('fAtmo',atmo,'ai');
    setFam('fLt',mn.lt,'ai');setFam('fTrans',mn.trans,'none');setFam('fBgb',mn.bgb,'none');
    setFam('fCta',mn.cta,'none');setFam('fData',mn.data,'none');setFam('fCmp',mn.cmp,'none');setFam('fList',mn.list,'none');
    try{const ct=(cfg&&cfg.cta_texts)||{};
      if($('ctaAction'))$('ctaAction').value=ct.action||'';
      if($('ctaButton'))$('ctaButton').value=ct.button||'';
      if($('ctaBrand'))$('ctaBrand').value=ct.brand||'';
      if($('ctaProof'))$('ctaProof').value=ct.proof||'';
      if($('ctaDataLabel'))$('ctaDataLabel').value=ct.data_label||'';
      if($('ctaDataValue'))$('ctaDataValue').value=ct.data_value||'';
      if($('ctaDataUnit'))$('ctaDataUnit').value=ct.data_unit||'';
      if($('ctaMicro'))$('ctaMicro').value=ct.microcopy||'';
      if($('ctaDataProgress'))$('ctaDataProgress').value=ct.data_progress||'';
      if($('cmpLeftTitle'))$('cmpLeftTitle').value=ct.left_title||'';
      if($('cmpLeftText'))$('cmpLeftText').value=ct.left_text||'';
      if($('cmpRightTitle'))$('cmpRightTitle').value=ct.right_title||'';
      if($('cmpRightText'))$('cmpRightText').value=ct.right_text||'';
      if($('listTitle'))$('listTitle').value=ct.list_title||'';
      if($('list1'))$('list1').value=ct.list1||'';
      if($('list2'))$('list2').value=ct.list2||'';
      if($('list3'))$('list3').value=ct.list3||'';}catch(e){}
    $('mTopMark').checked=!!mn.top_mark;
    famLab();
    if(cfg.hf_style)$('hfStyle').value=cfg.hf_style;
    if(cfg.hf_prompt!=null)$('hfPrompt').value=cfg.hf_prompt;
    const s=await api('/api/edit/status');
    if(s.running){
      $('btnEdit').disabled=true;$('pb5').style.display='block';
      clearInterval(editTimer);editTimer=setInterval(pollEdit,1500);
    }else if(s.done&&s.output){
      showPreview('edit','/media/'+encodeURIComponent(projName)+'/output/'+encodeURIComponent('初稿.mp4')+'?t='+Date.now());
    }
    $('btnRefine').disabled=!editHasDraft;
    loadRefineHistory();
    updateFlowHint();
  }catch(e){}
}
function editSelectAll(v){
  editSel = v ? new Set(editItems.map(it=>it.id)) : new Set();
  renderEditList();
  toast(v ? ('已全选 '+editItems.length+' 个素材') : '已清空选择 — 记得至少勾一个才能合成');
}
function renderEditList(){
  const box=$('editList');box.innerHTML='';
  if(!editItems.length){box.innerHTML='<span class="muted">（暂无素材 — 先完成④分段合成，或在工作台上传自制音频）</span>';return}
  if(!editHasVideo){const w=document.createElement('div');w.className='callout';w.style.marginBottom='8px';
    w.textContent='⚠ 至少需要一段已生成的数字人视频才能合成初稿 — 缺视频的段会用「黑底＋该段音频」替代（尺寸跟随已生成视频）';
    box.appendChild(w)}
  editItems.forEach((it,i)=>{
    const r=document.createElement('div');r.className='edrow';r.style.marginBottom='6px';
    const cb=document.createElement('input');cb.type='checkbox';cb.checked=editSel.has(it.id);
    cb.title='勾选拼进初稿';
    cb.onchange=()=>{cb.checked?editSel.add(it.id):editSel.delete(it.id)};
    r.appendChild(cb);
    const id=document.createElement('span');id.className='id';id.textContent=(i+1)+'. '+it.id;
    r.appendChild(id);
    const nm=document.createElement('span');nm.className='txt';nm.textContent=it.name;
    nm.style.cssText='max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
    r.appendChild(nm);
    const b=document.createElement('span');b.className='badge'+(it.video?' gold':'');
    b.textContent=it.video?'有视频':'黑底替代';b.title=it.video?'使用已生成的数字人视频':'该段无视频 — 渲染时自动生成黑底+本段音频替代';
    r.appendChild(b);
    const len=document.createElement('span');len.className='len';len.textContent=it.dur.toFixed(1)+'s';
    r.appendChild(len);
    const up=document.createElement('button');up.className='sm';up.textContent='↑';up.disabled=i===0;up.title='上移';
    up.onclick=()=>{[editItems[i-1],editItems[i]]=[editItems[i],editItems[i-1]];renderEditList()};
    r.appendChild(up);
    const dn=document.createElement('button');dn.className='sm';dn.textContent='↓';dn.disabled=i===editItems.length-1;dn.title='下移';
    dn.onclick=()=>{[editItems[i+1],editItems[i]]=[editItems[i],editItems[i+1]];renderEditList()};
    r.appendChild(dn);
    box.appendChild(r);
  });
}
async function pickBgm(){
  toast('正在打开文件选择窗口…',1800);
  try{
    const j=await api('/api/dialog/pick-image?kind=audio',{});
    if(j.path)$('editBgm').value=j.path;
  }catch(e){toast('失败: '+e.message,4000)}
}
async function loadVoiceRef(){
  if(!projName){return}
  try{
    const j=await api('/api/tts/ref?project='+encodeURIComponent(projName));
    const v=j.voice;
    if(v){$('voiceRefInfo').textContent='已启用: '+v.name+' ('+v.dur+'s)';
      $('btnVoiceClr').style.display='';$('btnVoiceRef').textContent='🔄 换音色';}
    else{$('voiceRefInfo').textContent='内置音色（兜底）';
      $('btnVoiceClr').style.display='none';$('btnVoiceRef').textContent='🎤 上传音色';}
  }catch(e){}
}
async function setVoiceRef(){
  if(!projName){toast('先新建或打开项目');return}
  const txt=$('voiceRefText').value.trim();
  if(!txt){toast('先填写参考音频对应的文本（它说了什么）',4000);return}
  toast('正在打开文件选择窗口…',1800);
  try{
    const p=await api('/api/dialog/pick-image?kind=audio',{});
    if(!p.path)return;
    const j=await api('/api/tts/ref',{project:projName,path:p.path,ref_text:txt});
    toast('✅ 自定义音色已启用 ('+j.voice.dur+'s)，下次合成生效',5000);
    loadVoiceRef();
  }catch(e){toast('失败: '+e.message,6000)}
}
async function clearVoiceRef(){
  if(!projName)return;
  try{
    await api('/api/tts/ref',{project:projName,action:'remove'});
    toast('已移除自定义音色，恢复内置音色');
    $('voiceRefText').value='';loadVoiceRef();
  }catch(e){toast('失败: '+e.message,4000)}
}
async function startEdit(){
  if(!projName){toast('先新建或打开项目');return}
  const sel=editItems.filter(it=>editSel.has(it.id)).map(it=>({id:it.id,kind:it.kind}));
  if(!sel.length){toast('请至少勾选一个素材 — 可点上方「全选」（默认应为全选）',5000);return}
  if(!editHasVideo){toast('至少需要一段已生成的数字人视频才能合成初稿（缺视频的段才用黑底替代）',4500);return}
  const cfg={selected:[...editSel],order:editItems.map(it=>it.id),
    title:$('editTitle').value,outro:$('editOutro').checked,
    bgm:$('editBgm').value,bgm_vol:parseFloat($('editBgmVol').value)||0.12};
  try{await api('/api/edit/config',{project:projName,config:cfg})}catch(e){}
  try{
    await api('/api/edit/render',{project:projName,items:sel,title:cfg.title,
      intro:cfg.intro,outro:cfg.outro,bgm:cfg.bgm,bgm_vol:cfg.bgm_vol});
    $('btnEdit').disabled=true;
    $('pb5').style.display='block';$('pb5').firstElementChild.style.width='3%';
    clearInterval(editTimer);editTimer=setInterval(pollEdit,1500);pollEdit();
  }catch(e){toast('失败: '+e.message,6000)}
}
let editPollFails=0;
async function pollEdit(){
  try{
    const j=await api('/api/edit/status');
    editPollFails=0;
    $('st5').textContent=j.running?('▸ '+j.stage+' '+j.pct+'%'):'';
    $('pb5').firstElementChild.style.width=Math.max(j.running||j.done?j.pct:0,3)+'%';
    if(j.done){
      clearInterval(editTimer);$('btnEdit').disabled=false;
      if(j.kind==='refine'){
        $('btnRefine').disabled=false;$('btnReRefine').disabled=false;$('hfFeedback').value='';
        setTimeout(()=>{$('pb5').style.display='none';$('st5').textContent=''},1400);
        await loadRefineHistory();refineCur=j.v||refineCur;renderRefineVers();
        showPreview('refine','/media/'+encodeURIComponent(projName)+'/output/'+encodeURIComponent('精剪v'+refineCur+'.mp4')+'?t='+Date.now());        toast('✨ 精剪 v'+refineCur+' 完成 — '+j.stage.replace(/^精剪 v\d+ 完成 · /,''),6000);
      }else{
        editHasDraft=true;updateFlowHint();$('btnRefine').disabled=false;
        setTimeout(()=>{$('pb5').style.display='none';$('st5').textContent=''},1400);
        showPreview('edit','/media/'+encodeURIComponent(projName)+'/output/'+encodeURIComponent('初稿.mp4')+'?t='+Date.now());
        toast('✅ 视频初稿已生成 — output\\初稿.mp4',5000);
      }
    }else if(!j.running&&j.error){
      clearInterval(editTimer);$('btnEdit').disabled=false;$('btnRefine').disabled=!editHasDraft;$('btnReRefine').disabled=false;
      $('pb5').style.display='none';$('st5').textContent='';
      toast((j.kind==='refine'?'精剪失败: ':'剪辑失败: ')+j.error,8000);
    }
  }catch(e){
    /* 服务中断/任务丢失(如后端重启): 连续失败后停止轮询并明确告知 */
    if(++editPollFails>=5){
      clearInterval(editTimer);editPollFails=0;
      $('pb5').style.display='none';$('st5').textContent='';
      $('btnEdit').disabled=false;$('btnRefine').disabled=!editHasDraft;$('btnReRefine').disabled=false;
      toast('渲染任务中断（服务重启或网络中断）— 请重新点击生成',8000);
    }
  }
}
let refineVers=[],refineCur=0;
/* 预览互斥: 初稿预览与精剪版预览是同一个位置的两个元素, 显示其一必须隐藏另一个(否则两视频叠着) */
function showPreview(which, src){
  const a=$('editPreview'), b=$('refinePreview');
  if(!a||!b)return;
  const off=(el)=>{el.pause&&el.pause();el.style.display='none';el.removeAttribute('src');if(el.load)el.load()};
  if(which==='edit'){off(b);a.style.display='block';a.src=src}
  else{off(a);b.style.display='block';b.src=src}
}
async function loadRefineHistory(){
  if(!projName){refineVers=[];refineCur=0;renderRefineVers();return}
  try{
    const j=await api('/api/edit/refine/history?project='+encodeURIComponent(projName));
    refineVers=j.versions||[];
    if(!refineVers.find(x=>x.v===refineCur))refineCur=refineVers.length?refineVers[refineVers.length-1].v:0;
    renderRefineVers();
    /* 预览强同步: 页面加载/刷新后预览必须等于当前选中版本 */
    if(refineCur>0){showPreview('refine','/media/'+encodeURIComponent(projName)+'/output/'+encodeURIComponent('精剪v'+refineCur+'.mp4')+'?t='+Date.now());}
  }catch(e){refineVers=[];renderRefineVers()}
}
function renderRefineVers(){
  const r=$('refineVers'),fb=$('refineFbRow');
  if(!refineVers.length&&!editHasDraft){r.style.display='none';fb.style.display='none';r.innerHTML='';return}
  r.style.display='flex';fb.style.display='block';
  const cur=refineVers.find(x=>x.v===refineCur);
  r.innerHTML=`<button class="vpill${refineCur===0?' on':''}" onclick="pickRefineV(0)" title="粗剪素材版 — 反馈重设计不继承旧版">初稿</button>`+
    refineVers.map(v=>`<button class="vpill${v.v===refineCur?' on':''}" onclick="pickRefineV(${v.v})" title="${(v.note||'').replace(/"/g,'')}">v${v.v}</button>`).join('')+
    (refineCur===0?'<span class="muted">将基于初稿重新设计（不继承旧版设计）</span>':(cur?`<span class="muted">${cur.note||''}</span>`:''));
  $('refineBase').textContent=refineCur===0?'将基于初稿重新设计（不继承旧版设计）':(cur?('将基于 v'+cur.v+' 的设计修订'):'');
}
function pickRefineV(v){
  refineCur=v;renderRefineVers();
  const name=v===0?'初稿.mp4':'精剪v'+v+'.mp4';
  showPreview(v===0?'edit':'refine','/media/'+encodeURIComponent(projName)+'/output/'+encodeURIComponent(name)+'?t='+Date.now());
}
function famLab(){
  const map={fTitle:'labTitle',fCap:'labCap',fAtmo:'labAtmo',fLt:'labLt',fTrans:'labTrans',fBgb:'labBgb',
             fCta:'labCta',fData:'labData'};
  for(const [n,id] of Object.entries(map)){
    const el=document.querySelector('input[name="'+n+'"]:checked');
    if(el&&$(id)){const t=el.parentNode.textContent.trim();$(id).textContent=t.replace(/^[🤖🎲🎞💬🏷✨🌌🎬📊]\s*/,'');}
  }
}
function refineMenu(){
  return {title:(document.querySelector('input[name="fTitle"]:checked')||{}).value||'ai',
          caption:(document.querySelector('input[name="fCap"]:checked')||{}).value||'ai',
          atmo:(document.querySelector('input[name="fAtmo"]:checked')||{}).value||'ai',
          lt:(document.querySelector('input[name="fLt"]:checked')||{}).value||'ai',
          trans:(document.querySelector('input[name="fTrans"]:checked')||{}).value||'none',
          bgb:(document.querySelector('input[name="fBgb"]:checked')||{}).value||'none',
          cta:(document.querySelector('input[name="fCta"]:checked')||{}).value||'none',
          data:(document.querySelector('input[name="fData"]:checked')||{}).value||'none',
          cmp:(document.querySelector('input[name="fCmp"]:checked')||{}).value||'none',
          list:(document.querySelector('input[name="fList"]:checked')||{}).value||'none',
          top_mark:$('mTopMark').checked};
}
function refineCtaTexts(){
  return {cta_action:($('ctaAction')||{}).value||'', cta_button:($('ctaButton')||{}).value||'',
          cta_brand:($('ctaBrand')||{}).value||'', cta_proof:($('ctaProof')||{}).value||'',
          cta_data_label:($('ctaDataLabel')||{}).value||'',
          cta_data_value:($('ctaDataValue')||{}).value||'',
          cta_data_unit:($('ctaDataUnit')||{}).value||'',
          cta_microcopy:($('ctaMicro')||{}).value||'',
          cta_data_progress:($('ctaDataProgress')||{}).value||'',
          cta_left_title:($('cmpLeftTitle')||{}).value||'',
          cta_left_text:($('cmpLeftText')||{}).value||'',
          cta_right_title:($('cmpRightTitle')||{}).value||'',
          cta_right_text:($('cmpRightText')||{}).value||'',
          cta_list_title:($('listTitle')||{}).value||'',
          cta_list1:($('list1')||{}).value||'',
          cta_list2:($('list2')||{}).value||'',
          cta_list3:($('list3')||{}).value||''};
}
async function startRefineRevise(){
  if(!projName){toast('先新建或打开项目');return}
  const fb=$('hfFeedback').value.trim();
  if(!fb){toast('先填写修改意见');return}
  try{
    await api('/api/edit/refine',{project:projName,feedback:fb,base_version:refineCur||undefined,
      intro_title:$('editTitle').value,menu:refineMenu(),...refineCtaTexts()});
    $('btnReRefine').disabled=true;$('btnEdit').disabled=true;$('btnRefine').disabled=true;
    $('pb5').style.display='block';$('pb5').firstElementChild.style.width='2%';
    clearInterval(editTimer);editTimer=setInterval(pollEdit,2000);pollEdit();
  }catch(e){toast('失败: '+e.message,6000)}
}
async function startRefine(){
  if(!projName){toast('先新建或打开项目');return}
  if(!editHasDraft){toast('请先生成视频初稿');return}
  try{
    await api('/api/edit/refine',{project:projName,style:$('hfStyle').value,prompt:$('hfPrompt').value,
      intro_title:$('editTitle').value,menu:refineMenu(),...refineCtaTexts()});
    $('btnRefine').disabled=true;$('btnEdit').disabled=true;
    $('refinePreview').style.display='none';
    $('pb5').style.display='block';$('pb5').firstElementChild.style.width='2%';
    clearInterval(editTimer);editTimer=setInterval(pollEdit,2000);pollEdit();
  }catch(e){toast('失败: '+e.message,6000)}
}
async function saveAvatarKey(){
  const k=$('avKey').value.trim();
  if(!k){toast('先粘贴 Key');return}
  await api('/api/settings',{avatar_key:k});
  $('avKey').value='';
  await refreshAvatarKeyStatus();
  toast('Key 已保存');
}
async function refreshAvatarKeyStatus(){
  try{
    const j=await api('/api/settings');
    $('avKeyStatus').textContent=j.avatar_has_key?('✓ Key 已配置 ('+j.avatar_key_hint+') · 实例 '+j.avatar_instance):'⚠ 未配置 Key（见上方指南）';
    if(j.avatar_image&&!$('avatarImg').value)$('avatarImg').value=j.avatar_image;
    updateFlowHint();
  }catch(e){}
}
function updAvImg(){
  const p=$('avatarImg').value.trim();
  const im=$('avImgPrev');
  if(!p){im.style.display='none';return}
  im.onerror=()=>{im.style.display='none';toast('图片读取失败：检查路径',2500)};
  im.onload=()=>{im.style.display='block'};
  im.src='/api/avatar/preview?path='+encodeURIComponent(p)+'&t='+Date.now();
}
$('avatarImg').addEventListener('change',updAvImg);
/* ---- 图片库 + 拖拽/点选绑定 ---- */
let avImages=[], selectedAvImg='';
function cleanPath(p){return (p||'').trim().replace(/^["'""''`]+|["'""''`]+$/g,'').trim()}
function avThumb(path,size,removable){
  const w=document.createElement('span');
  w.className='avthumb'+(selectedAvImg===path?' selected':'');
  w.draggable=true;w.title=path+(selectedAvImg===path?'（已选中）':'（点击选中 / 拖动到段落绑定）');
  const im=document.createElement('img');
  im.src='/api/avatar/preview?path='+encodeURIComponent(path);
  im.style.cssText=`width:${size}px;height:${size}px;object-fit:cover;border-radius:8px;border:1px solid var(--line);display:block;pointer-events:none`;
  w.appendChild(im);
  w.ondragstart=e=>{e.dataTransfer.setData('text/avimg',path);e.dataTransfer.effectAllowed='copy'};
  w.onclick=e=>{e.stopPropagation();
    selectedAvImg=(selectedAvImg===path?'':path);
    renderAvLib();
    if(projName)reloadSegs();
    toast(selectedAvImg?'已选中形象图 — 点各数字人行「📌 绑选中图」':'已取消选中',2000);
  };
  if(removable){
    const x=document.createElement('span');x.textContent='×';
    x.style.cssText='position:absolute;top:-6px;right:-6px;width:16px;height:16px;border-radius:50%;background:#2a2a32;border:1px solid var(--line2);color:var(--dim);font-size:11px;line-height:14px;text-align:center;cursor:pointer;display:none';
    w.appendChild(x);
    w.onmouseenter=()=>x.style.display='block';
    w.onmouseleave=()=>x.style.display='none';
    x.onclick=async e=>{e.stopPropagation();
      const j=await api('/api/avatar/images',{project:projName,action:'remove',path});
      avImages=j.images;if(selectedAvImg===path)selectedAvImg='';
      renderAvLib();if(projName)reloadSegs();toast('已从图库移除')};
  }
  return w;
}
function renderAvLib(){
  ['avLib','avLib2'].forEach(id=>{
    const box=$(id);if(!box)return;box.innerHTML='';
    if(!avImages.length){box.innerHTML='<span class="muted">（图库为空 — 粘贴路径或点「📁 选择文件」添加）</span>';return}
    avImages.forEach(p=>{const w=avThumb(p,52,id==='avLib');w.style.cssText='position:relative;display:inline-block;cursor:pointer';box.appendChild(w)});
  });
}
async function avAddImage(inputId){
  if(!projName){toast('先新建或打开项目');return}
  const p=cleanPath($(inputId||'avLibPath').value);
  if(!p){toast('先填图片路径或点「📁 选择文件」');return}
  try{
    const j=await api('/api/avatar/images',{project:projName,action:'add',path:p});
    avImages=j.images;
    const inp=$(inputId||'avLibPath');if(inp)inp.value='';
    renderAvLib();if(projName)reloadSegs();
    toast('已加入图库（'+avImages.length+'张）— 点击小图选中后绑定到段落');
  }catch(e){toast('失败: '+e.message,5000)}
}
async function avBrowseAdd(){
  if(!projName){toast('先新建或打开项目');return}
  toast('正在打开文件选择窗口…',1800);
  try{
    const j=await api('/api/dialog/pick-image',{});
    if(!j.path){toast('未选择文件');return}
    const r=await api('/api/avatar/images',{project:projName,action:'add',path:j.path});
    avImages=r.images;renderAvLib();if(projName)reloadSegs();
    toast('已加入图库: '+j.path.split('\\').pop());
  }catch(e){toast('失败: '+e.message,5000)}
}
async function avBrowseDefault(){
  toast('正在打开文件选择窗口…',1800);
  try{
    const j=await api('/api/dialog/pick-image',{});
    if(!j.path){toast('未选择文件');return}
    $('avatarImg').value=j.path;updAvImg();
    await api('/api/settings',{avatar_image:j.path});
    toast('默认形象图已设置');
  }catch(e){toast('失败: '+e.message,5000)}
}
function dropBind(e,segId){
  e.preventDefault();
  const path=cleanPath(e.dataTransfer.getData('text/avimg'));
  if(!path)return;
  if(!projName){toast('先新建或打开项目');return}
  api('/api/avatar/bind',{project:projName,seg:segId,img:path}).then(()=>{
    toast(segId+' 已绑定形象图 — 点「生成数字人口播视频」即用此图');
    reloadSegs();
  }).catch(err=>toast('绑定失败: '+err.message,5000));
}
function allowDrop(e){e.preventDefault();e.currentTarget.classList.add('droptarget')}
function unDrop(e){e.currentTarget.classList.remove('droptarget')}
/* 数字人卡片定位: ④分段卡 + 工作台avseg卡(两处都可能同时存在, 都要给反馈) */
function videoCards(id){
  return [...document.querySelectorAll('#segList .segcard[data-id="'+id+'"], #avSegs .avseg[data-id="'+id+'"]')];
}
async function avatarSynth(segId){
  if(!projName){toast('先新建或打开项目');return}
  const cards=videoCards(segId);
  const btns=[], infos=[];
  cards.forEach(card=>{
    card.querySelectorAll('.vinfo').forEach(e=>e.remove());   /* 清掉上次的失败/提示行 */
    const btn=[...card.querySelectorAll('button')].find(b=>/生成视频|重生成视频|重新生成/.test(b.textContent));
    if(btn){btn.disabled=true;btn.classList.add('busy');btn.dataset.t=btn.textContent;btn.textContent='⏳ 提交中…';btns.push(btn)}
    const info=document.createElement('span');info.className='vinfo sending';
    info.textContent='正在向云端发送数据（上传形象图与音频）…';
    (card.querySelector('.asset.video')||card).appendChild(info);
    infos.push(info);
  });
  try{
    await api('/api/avatar/synth',{project:projName,seg:segId,image:$('avatarImg').value.trim()});
    if(!runningSegs.includes(segId))runningSegs.push(segId);
    infos.forEach(i=>{i.className='vinfo ok';i.textContent='已提交 ✓ 数字人生成中 · 预计合成时间 300 秒左右'});
    toast(segId+' 数字人任务已提交（预计 300 秒左右）');
    pollVideos();
    setTimeout(()=>reloadSegs(),1200);   /* 让「已提交 ✓」提示可见 1.2 秒, 再刷新为「数字人生成中…」 */
  }catch(e){
    /* 提交失败(网络/参数): 按钮复位, 卡片内常驻原因, 可直接再点重试 */
    btns.forEach(btn=>{btn.disabled=false;btn.classList.remove('busy');
      if(btn.dataset.t){btn.textContent=btn.dataset.t;delete btn.dataset.t}});
    infos.forEach(i=>{i.className='vinfo err';i.textContent='提交失败: '+e.message});
    toast('提交失败: '+e.message,6000);
  }
}
async function avatarAll(){
  if(!gateCheckSync())return;
  let n=0;
  for(const segId of avKeptIds()){
    if(runningSegs.includes(segId))continue;
    if(n){await new Promise(r=>setTimeout(r,1500))}
    try{
      await api('/api/avatar/synth',{project:projName,seg:segId,image:$('avatarImg').value.trim()});
      runningSegs.push(segId);n++;
    }catch(e){toast(segId+' 失败: '+e.message,6000);break}
  }
  if(n){toast('已提交 '+n+' 段数字人任务');pollVideos();reloadSegs()}
  else toast('没有可生成的保留段');
}
let lastSegs=[];
function avKeptIds(){
  return (lastSegs||[]).filter(s=>s.status==='kept').map(s=>s.id);
}
function pollVideos(){
  if(pvTimer)return;
  pvTimer=setInterval(async()=>{
    for(const segId of [...runningSegs]){
      try{
        const j=await api('/api/avatar/status',{project:projName,seg:segId,action:'poll'});
        const vst=j.seg.video_status;
        if(vst!=='running'&&vst!=='queued'){   /* queued 段留在轮询里等后端补位转 running */
          runningSegs=runningSegs.filter(x=>x!==segId);
          if(j.seg.video_status==='kept'&&j.seg.video){
            toast(segId+' 数字人视频已生成 ✓');
            await reloadSegs();
            flashCard(segId);   /* 生成成功: 克制的绿脉冲 */
          }
          else if(j.seg.video_error){toast(segId+' 失败: '+j.seg.video_error,8000);reloadSegs()}
          else reloadSegs();
        }
        if(j.poll_error)console.warn(j.poll_error);
      }catch(e){console.warn(e)}
    }
    if(!runningSegs.length){clearInterval(pvTimer);pvTimer=null}
  },6000);
}
function resumeVideoPoll(){   /* 页面刷新/重进项目后恢复轮询: running/queued 的段重新纳入 pollVideos(否则状态卡在"生成中") */
  if(!projName)return;
  const need=[...lastSegs,...customItems]
    .filter(x=>x.video_status==='running'||x.video_status==='queued')
    .map(x=>x.id);
  let added=0;
  need.forEach(id=>{if(!runningSegs.includes(id)){runningSegs.push(id);added++}});
  if(added){console.log('[resume] 恢复视频轮询:',need.join(','));pollVideos()}
}
/* ---- 设置 ---- */
function openSettings(){
  api('/api/settings').then(j=>{
    $('setBase').value=j.base_url;$('setModel').value=j.model;if($('setThinking'))$('setThinking').value=j.thinking||'off';$('setDir').value=j.projects_dir_custom?j.projects_dir:'';
    $('setDir').placeholder='留空 = '+j.projects_dir;
    $('keyHint').textContent=j.has_key?('当前已配置 ('+j.key_hint+')'):'尚未配置';
    $('setAvatarImage').value=j.avatar_image||'';if($('setMaxParallel'))$('setMaxParallel').value=String(j.max_parallel||1);
    $('avatarKeyHint').textContent=j.avatar_has_key?('当前已配置 ('+j.avatar_key_hint+') · 实例: '+j.avatar_instance):'尚未配置';
    $('settingsModal').style.display='flex';
  });
}
function closeSettings(){$('settingsModal').style.display='none'}
async function saveSettings(){
  await api('/api/settings',{base_url:$('setBase').value,model:$('setModel').value,api_key:$('setKey').value,
    projects_dir:$('setDir').value,avatar_image:cleanPath($('setAvatarImage').value),avatar_key:$('setAvatarKey').value,
    max_parallel:parseInt(($('setMaxParallel')||{}).value||'1',10)});
  $('setKey').value='';$('setAvatarKey').value='';openSettings();loadSub();refreshAvatarKeyStatus();refreshGate();toast('设置已保存');
}
async function testLLM(){
  $('setMsg').innerHTML='<span class="spin"></span>保存并测试中…';
  try{
    /* 先落盘当前表单(否则测的是上次保存的旧配置 — 常见的"填了却连不上"） */
    await api('/api/settings',{base_url:$('setBase').value,model:$('setModel').value,api_key:$('setKey').value,
      projects_dir:$('setDir').value,avatar_image:cleanPath($('setAvatarImage').value),avatar_key:$('setAvatarKey').value});
    $('setKey').value='';$('setAvatarKey').value='';
    const j=await api('/api/settings/test',{});
    $('setMsg').innerHTML=j.ok?('<span class="ok">✓ 连通正常 · 回复: '+(j.reply||'')+'</span>')
                              :('<span style="color:#d8a0a8">✗ '+j.error+'</span>');
  }catch(e){$('setMsg').textContent='✗ '+e.message}
}
async function loadSub(){
  try{const j=await api('/api/settings');
    $('projPath').textContent=j.projects_dir;
    if(j.avatar_image)$('avatarImg').value=j.avatar_image;
  }catch(e){}
}
$('btnResearch').disabled=true;
/* ---- 🐈 彩蛋: Live2D 黑猫 hijiki (长任务陪伴; 各阶段不同落点, 不遮挡内容) ---- */
let catReady=false;
const CAT_SPOTS={card1:'cat-lb',   /* ①选题: 左下 */
                 card2:'cat-rb',   /* ②调研: 右下 */
                 card3:'cat-lb',   /* ③成稿: 左下 */
                 card4:'cat-rb',   /* ④分段合成: 右下 */
                 cardAV:'cat-rt',  /* 工作台: 右上 */
                 card5:'cat-lt'};  /* ⑤剪辑: 左上 */
const CAT_CLASSES=['cat-lb','cat-rb','cat-lt','cat-rt','cat-show'];
function initCat(){
  if(catReady)return true;
  if(typeof L2Dwidget==='undefined')return false;
  try{
    L2Dwidget.init({model:{jsonPath:'/assets/hijiki/models/hijiki/hijiki.model.json',scale:1},
      display:{position:'left',width:200,height:260,hOffset:0,vOffset:0},
      mobile:{show:false,scale:0.4},
      react:{opacityDefault:1,opacityOnHover:1}});
    catReady=true;
    return true;
  }catch(e){console.warn('黑猫彩蛋初始化失败',e);return false}
}
function setCat(on,cardId,retry){
  retry=retry||0;
  if(on&&window.innerWidth<1360)return;   /* 窄屏无侧边留白, 不显示免得挡内容 */
  const el=document.getElementById('live2dcanvas');
  if(!el){                                  /* canvas 由引擎异步创建, 未就绪则稍后重试 */
    if(on&&initCat()&&retry<15)setTimeout(()=>setCat(on,cardId,retry+1),300);
    return;
  }
  CAT_CLASSES.forEach(k=>el.classList.remove(k));   /* 先清位置/显隐类 */
  if(on)el.classList.add(CAT_SPOTS[cardId]||'cat-rb','cat-show');
  /* 点击热区跟随猫的位置(猫本体 pointer-events:none, 由热区接收点击) */
  const hot=document.getElementById('catHot');
  if(hot){
    CAT_CLASSES.forEach(k=>hot.classList.remove(k));
    if(on)hot.classList.add(CAT_SPOTS[cardId]||'cat-rb','cat-show');
  }
}
/* ---- 🐈 流程门禁彩蛋: 缺项目 / 未填 LLM Key / 未填 RH Key 时, 黑猫出现并依次冒气泡提醒 ---- */
let gateLLM=true,gateRH=true,catTalking=false;
function gateMissing(){
  const m=[];
  if(!projName)m.push('请先创建或选择已有项目哦。');
  if(!gateLLM)m.push('请在设置里填入大语言模型的API。');
  if(!gateRH)m.push('请前往RunningHub AI注册账号并填入你的API。');
  return m;
}
function gateCheckSync(){       /* 同步检查: 缺失时猫弹气泡并拦截, 返回是否放行 */
  const m=gateMissing();
  if(m.length)catShowBubble(m);
  return !m.length;
}
function catShowBubble(lines){
  if(catTalking||!lines.length)return;
  const b=$('catBubble');
  if(!b)return;
  catTalking=true;
  setCat(true,'card1');         /* 猫出现, 落点与①一致 */
  const b2=$('catBubble');
  ['cat-rb','cat-lb','cat-lt','cat-rt'].forEach(k=>b2.classList.remove(k));
  b2.classList.add(CAT_SPOTS.card1||'cat-rb');   /* 气泡位置类与猫联动 */
  let i=0;
  const step=()=>{
    if(i>=lines.length){
      setTimeout(()=>{b2.classList.remove('cat-show');catTalking=false},2600);
      return;
    }
    b2.textContent=lines[i++];
    b2.classList.add('cat-show');
    setTimeout(step,3800);      /* 每条 3.8s, 依次出现 */
  };
  step();
}
async function refreshGate(){   /* 拉取 Key 配置状态 + 同步项目门禁特效 */
  try{
    const j=await api('/api/gate/status');
    gateLLM=!!j.llm_key;gateRH=!!j.rh_key;
  }catch(e){}
  syncProjGate();
}
function syncProjGate(){        /* 项目门禁: 未建/未选项目时项目卡金框呼吸 + 提示条 */
  const need=!projName;
  const cp=$('cardProj');
  if(cp){cp.classList.toggle('need-attention',need);
    const tip=$('projGateTip');if(tip)tip.style.display=need?'':'none';}
}
/* 🐈 点猫 → 关于弹窗(个人介绍 + 项目简介) */
function openAbout(){const m=$('aboutModal');if(m)m.style.display='flex'}
function closeAbout(){const m=$('aboutModal');if(m)m.style.display='none'}
document.addEventListener('DOMContentLoaded',()=>{
  const y=$('yearNow');if(y)y.textContent=String(new Date().getFullYear());
  const hot=$('catHot');
  if(hot)hot.onclick=openAbout;
  const m=$('aboutModal');
  if(m)m.addEventListener('click',e=>{if(e.target===m)closeAbout()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAbout()});
  /* 隐藏入口: 地址栏加 ?about=1 (或 #about) 直接打开关于弹窗 */
  try{
    if(/[?&]about=1/.test(location.search)||location.hash==='#about')setTimeout(openAbout,300);
  }catch(e){}
  /* 收款码点开看大图(新标签显示原图, 便于扫) */
  document.querySelectorAll('#aboutModal .am-qr-item img').forEach(im=>{
    im.style.cursor='zoom-in';
    im.title='点击查看大图';
    im.onclick=()=>window.open(im.getAttribute('src'),'_blank');
  });
});
/* 阶段运行中: 顶部金线扫描 + 黑猫换位陪伴 */
async function refreshStageRunning(){
  const busy={card1:false,card2:false,card3:false,card4:false,cardAV:false,card5:false};
  try{
    const [rh,gen,ed]=await Promise.all([
      api('/api/tts/rh/status').catch(()=>({})),
      api('/api/avatar/tts-gen/status').catch(()=>({})),
      api('/api/edit/status').catch(()=>({}))]);
    if(rh.running)busy.card4=true;
    if(gen.running)busy.cardAV=true;
    if(ed.running)busy.card5=true;
    if(projName){
      const p=await api('/api/project/'+encodeURIComponent(projName)).catch(()=>null);
      if(p){
        const vrun=(p.segments||[]).some(s=>s.video_status==='running');
        const crun=(p.custom||[]).some(c=>c.video_status==='running');
        if(vrun||crun){busy.card4=true;busy.cardAV=true}
      }
    }
  }catch(e){}
  /* ②③ 是同步长请求, 由 runResearch/runUnify 直接挂类, 这里只保留类不清除 */
  ['card2','card3'].forEach(id=>{if($(id).classList.contains('stage-running'))busy[id]=true});
  Object.entries(busy).forEach(([id,on])=>{const el=$(id);if(el)el.classList.toggle('stage-running',on)});
  const order=['card1','card2','card3','card4','cardAV','card5'];
  const active=order.find(id=>busy[id]);
  setCat(!!active,active||'card4');
}
setInterval(refreshStageRunning,6000);
loadSub();refreshSkills();refreshProjects();refreshAvatarKeyStatus();refreshTpls();refreshRhTts();refreshTtsCap();setTtsEng(ttsEng);refreshGate();refreshStageRunning();
setTimeout(syncTopicGate, 200);
setTimeout(()=>{const m=gateMissing();if(m.length)catShowBubble(m)},2600);   /* 首次进入: 流程未就绪则猫气泡引导 */
</script></body></html>"""

# 发行默认 TTS 引擎(单点差异): 云端版='rh' 开箱即云端合成; 本地源码版把本行改为 "local" 开箱即本地 dots.soar。
# 只影响首次打开(localStorage 无 koub.ttsEng 记录)的新用户; 用户手动切换引擎后以浏览器 localStorage 为准。
DEFAULT_TTS_ENG = "rh"
if DEFAULT_TTS_ENG != "local":
    HTML = HTML.replace("localStorage.getItem('koub.ttsEng') || 'local'",
                        "localStorage.getItem('koub.ttsEng') || '" + DEFAULT_TTS_ENG + "'")


# ---------------- 启动器: exe 双击即用 ----------------
def _pick_port(preferred: int = 8795) -> int:
    import socket
    for p in range(preferred, preferred + 12):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return preferred


def _prep_runtime():
    """打包版运行准备(启动即执行):
    ① 控制台 UTF-8 —— Nuitka 下 sys.stdout 可能没有 reconfigure, 故直接包装 buffer,
       否则会出现「GBK 输出 + UTF-8 代码页 = 乱码」
    ② 内置 ffmpeg/ffprobe 目录前置到 PATH
    ③ 若本机有 Chrome, 指定 PUPPETEER_EXECUTABLE_PATH 给精剪用(省去下载 Chromium)
    """
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    import io
    for _name in ("stdout", "stderr"):
        _st = getattr(sys, _name, None)
        if _st is None:
            continue
        try:
            _buf = getattr(_st, "buffer", None)
            if _buf is not None:
                setattr(sys, _name, io.TextIOWrapper(_buf, encoding="utf-8", errors="replace",
                                                     line_buffering=True, write_through=True))
            else:
                _st.reconfigure(encoding="utf-8", errors="replace")   # 兼容有 reconfigure 的实现
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for _c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
               r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(_c):
            os.environ.setdefault("PUPPETEER_EXECUTABLE_PATH", _c)
            break
    # 内置 Node.js(精剪 HyperFrames 依赖 node/npx): 随包优先
    for _nc in (ROOT / "node", RES / "assets" / "node", RES / "node"):
        if (_nc / "node.exe").exists():
            os.environ["PATH"] = str(_nc) + os.pathsep + os.environ.get("PATH", "")
            break
    for _cand in (ROOT / "assets" / "bin", RES / "assets" / "bin", ROOT):
        if (_cand / "ffmpeg.exe").exists():
            os.environ["PATH"] = str(_cand) + os.pathsep + os.environ.get("PATH", "")
            return str(_cand / "ffmpeg.exe")
    return ""


def _net_check(quiet: bool = False):
    """启动自检: 能否访问 RunningHub(云端 TTS/数字人 的唯一入口)"""
    ok, detail = False, ""
    for url in ("https://www.runninghub.ai/", "https://www.runninghub.cn/"):
        try:
            r = httpx.get(url, timeout=8, follow_redirects=True,
                          headers={"User-Agent": "koubo-studio/1.0"})
            if r.status_code < 500:
                ok, detail = True, url
                break
        except Exception as e:
            detail = f"{type(e).__name__}"
    if ok:
        if not quiet:
            print(f"  网络自检: 通过（{detail}）", flush=True)
        return True
    print("", flush=True)
    print("  ⚠ 网络自检未通过 — 无法访问 RunningHub", flush=True)
    print("    本程序的【云端 TTS】与【数字人视频】必须能访问以下站点：", flush=True)
    print("      · www.runninghub.ai  (任务提交与查询)", flush=True)
    print("      · *.myqcloud.com     (生成结果下载)", flush=True)
    print("    请开启可正常访问上述网站的网络环境（或换一台能访问的机器）后重启本程序。", flush=True)
    print("    其它功能不受影响：①选题 ②调研 ③成稿 走 LLM 接口；⑤精剪为本地渲染。", flush=True)
    print("", flush=True)
    return False


def _node_check():
    """启动自检: ⑤精剪需要 Node.js（一次性安装）"""
    node = shutil.which("node")
    if node:
        print(f"  精剪依赖: Node.js 已就绪（{node}）", flush=True)
        return True
    print("  ⚠ 未检测到 Node.js —— ⑤自动剪辑(HyperFrames 精剪)将不可用", flush=True)
    print("    安装一次即可（https://nodejs.org/ 下载 LTS 版，装完重启本程序）。", flush=True)
    return False


def main():
    _ff_bundled = _prep_runtime()
    import uvicorn
    port = _pick_port(int(os.environ.get("KOUBO_PORT") or 0) or 8795)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 58)
    print("  口播辅助创作台 · 云端版")
    print(f"  控制台地址: {url}")
    _ff = shutil.which("ffmpeg")
    print("  ffmpeg: " + ("已就绪 " + _ff if _ff else "⚠ 未检测到 — 混音/转码/剪辑会失败"))
    if not _ff:
        print("     请装 ffmpeg 并加入 PATH, 或把 ffmpeg.exe 放到本程序目录后重启")
    _net_check()
    _node_check()
    print("  关闭本窗口即退出程序")
    print("=" * 58)
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()