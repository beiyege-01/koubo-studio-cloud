# -*- coding: utf-8 -*-
"""口播生成器 CLI — 给 Agent 直接调用(无服务器依赖, 跑完退出自动释放显存)

用法:
  python koubo_cli.py synth <项目目录> [文案文件路径]   # 分段+合成所有pending段, 输出JSON
  python koubo_cli.py status <项目目录> --keep seg01 --delete seg02
"""
import json, os, sys, wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app as K  # 复用 split_segments / _tts_one / 配置


def _load(dirp: Path) -> dict:
    f = dirp / "segments.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"segments": []}


def _save(dirp: Path, data: dict):
    (dirp / "segments.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_synth(dirp: Path, script_file: str | None):
    (dirp / "tts").mkdir(parents=True, exist_ok=True)
    data = _load(dirp)
    # 给了文案(或还没有分段) → 重新分段
    if script_file or not data["segments"]:
        script = Path(script_file).read_text(encoding="utf-8") if script_file else (dirp / "文案.md").read_text(encoding="utf-8")
        import re
        script = re.sub(r"^#.*$", "", script, flags=re.M).strip()
        mn = int(K.CONF["tts"]["segment"]["min"]); mx = int(K.CONF["tts"]["segment"]["max"])
        segs = K.split_segments(script, mn, mx)
        data = {"model": os.path.basename(K.CONF["tts"]["model_path"]),
                "segments": [{"id": f"seg{i+1:02d}", "text": s, "wav": f"seg{i+1:02d}.wav", "status": "pending"}
                             for i, s in enumerate(segs)]}
        _save(dirp, data)
    for s in data["segments"]:
        if s["status"] != "pending":
            continue
        dur, sr = K._tts_one(s["text"], dirp / "tts" / s["wav"])
        s["dur_sec"] = round(dur, 2); s["sr"] = sr; s["status"] = "kept"
        _save(dirp, data)
    print(json.dumps({"project": str(dirp), "segments": data["segments"]}, ensure_ascii=False, indent=1))


def cmd_status(dirp: Path, actions: dict):
    data = _load(dirp)
    for s in data["segments"]:
        a = actions.get(s["id"])
        if not a:
            continue
        if a == "delete":
            w = dirp / "tts" / s["wav"]
            if w.exists():
                trash = dirp / "tts" / "_trash"; trash.mkdir(exist_ok=True)
                os.replace(w, trash / s["wav"])
            s["status"] = "deleted"
        else:
            s["status"] = "kept" if a == "keep" else a
    _save(dirp, data)
    print(json.dumps({"project": str(dirp), "segments": data["segments"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    dirp = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
    if cmd == "synth" and dirp and dirp.is_dir():
        cmd_synth(dirp, sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "status" and dirp and dirp.is_dir():
        actions = {}
        for arg in sys.argv[3:]:
            if arg.startswith("--"):
                cur = arg[2:]
            elif cur:
                actions[arg] = cur; cur = None
        cmd_status(dirp, actions)
    else:
        print(__doc__)
        sys.exit(1)
