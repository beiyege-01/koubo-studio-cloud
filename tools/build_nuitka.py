# -*- coding: utf-8 -*-
"""构建辅助: 把 app.py 里内嵌的整块前端 HTML 抽出并加密, 生成 Nuitka 编译用的 app_build.py。
   - 源码 app.py 保持不变(开发期仍可读)
   - 产物 app_build.py 里没有任何明文界面代码, 运行时从 assets/ui.enc 解密还原
   - 加密: 逐字节 XOR(密钥派生自固定盐) + base64, 目的是提高"strings 直接抄界面"的成本
"""
import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # koubo-studio-cloud
SRC = ROOT / "app.py"
BUILD = ROOT / "app_build.py"
ENC_DIR = ROOT / "assets" / "enc"
ENC_FILE = ENC_DIR / "ui.enc"
SALT = b"koubo-studio-cloud/2026/tgkz"


def key_stream(n: int) -> bytes:
    """确定性密钥流(与运行时同一算法)"""
    ks = bytearray()
    x = 0x5D
    for i in range(n):
        x = (x * 1103515245 + 12345 + (SALT[i % len(SALT)] << 3)) & 0xFFFFFFFF
        ks.append((x >> 16) & 0xFF)
    return bytes(ks)


def xor(data: bytes) -> bytes:
    ks = key_stream(len(data))
    return bytes(b ^ k for b, k in zip(data, ks))


def extract_html(src: str):
    """提取 HTML = r\"\"\"...\"\"\" 整块(按行定位: 起始行以 `HTML = r\"\"\"` 开头, 结束行为首个以 \"\"\" 收尾的行)"""
    lines = src.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith('HTML = r"""')), None)
    if start is None:
        return None, None, None
    end = None
    for j in range(start, len(lines)):
        if lines[j].rstrip().endswith('"""'):
            end = j
            break
    if end is None:
        return None, None, None
    first = lines[start][len('HTML = r"""'):]
    html = "\n".join([first] + lines[start + 1:end]) + lines[end].rstrip()[:-3]
    return html, start, end


def main() -> int:
    src = SRC.read_text(encoding="utf-8")
    html, start, end = extract_html(src)
    if html is None:
        print('✗ 未找到 HTML = r"""...""" 块')
        return 1
    print(f"✓ 定位 HTML 块: 第 {start+1}~{end+1} 行, {len(html)} 字符")
    ENC_DIR.mkdir(parents=True, exist_ok=True)
    ENC_FILE.write_bytes(base64.b64encode(xor(html.encode("utf-8"))))
    print(f"✓ 界面代码已加密: {ENC_FILE.name} ({ENC_FILE.stat().st_size} 字节)")

    loader = (
        '# -*- coding: utf-8 -*-\n'
        '# 构建产物(由 tools/build_nuitka.py 生成): 界面代码从加密文件运行时还原, 无明文\n'
        'import base64 as _b64\n'
        'from pathlib import Path as _P\n'
        '\n'
        'def _ui_key(n):\n'
        '    _s = b"koubo-studio-cloud/2026/tgkz"\n'
        '    ks = bytearray(); x = 0x5D\n'
        '    for i in range(n):\n'
        '        x = (x * 1103515245 + 12345 + (_s[i % len(_s)] << 3)) & 0xFFFFFFFF\n'
        '        ks.append((x >> 16) & 0xFF)\n'
        '    return bytes(ks)\n'
        '\n'
        'def _load_ui():\n'
        '    import sys as _sys\n'
        '    base = _P(getattr(_sys, "_MEIPASS", _P(__file__).resolve().parent))\n'
        '    blob = (base / "assets" / "enc" / "ui.enc").read_bytes()\n'
        '    raw = _b64.b64decode(blob)\n'
        '    ks = _ui_key(len(raw))\n'
        '    return bytes(a ^ b for a, b in zip(raw, ks)).decode("utf-8")\n'
        '\n'
        'HTML = _load_ui()\n'
    )
    out = "\n".join(src.split("\n")[:start]) + "\n" + loader + "\n" + "\n".join(src.split("\n")[end + 1:])
    BUILD.write_text(out, encoding="utf-8")
    print(f"✓ 已生成构建入口: {BUILD.name} ({len(out)} 字符)")
    print("  下一步: python -m nuitka --standalone ... app_build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
