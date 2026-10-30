#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pyenv.py — 解释器/依赖自检：这个技能到底该用哪个 Python

为什么要有这个
--------------
2026-10-30 事故：会话用 **Dsh 自带的 Python 3.12**
（`...\\dsh-runtimes\\...\\dependencies\\python\\python.exe`，只有 numpy/Pillow）跑 `tools/*.py`，
报「缺 `rapidocr_onnxruntime`」→ 结论写成「本机缺 OCR，三层实图回归留待下次」；
同一解释器缺 `scipy`，又让 `selftest_lattice` 两项失败被误当成代码问题。
而玩家系统里的 `Python 3.11`（**PATH 上的 `python`，`pip` 装的就是它**）两样都齐。

**`pip` 的 Python ≠ 跑脚本的 Python。** 所以报「缺依赖」之前，先跑本工具：
它打印当前解释器、缺什么，以及本机还有哪些解释器是齐全的（可直接复制命令）。

用法
----
  python tools/pyenv.py                  # 当前解释器 + 推荐
  python tools/pyenv.py --all            # 连本机其它解释器一起探测
  python tools/pyenv.py --python PATH    # 探测指定解释器
  python tools/pyenv.py --json           # 给脚本读

退出码：0 = 当前解释器齐全；2 = 当前缺、但本机另有齐全的；1 = 都不齐全
"""
import argparse
import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 跑完整链路（截图 → 识别 → 求解）必需的模块。NAME → 人话说明
REQUIRED = {
    "numpy": "数值/栅格",
    "PIL": "Pillow：读截图",
    "rapidocr_onnxruntime": "OCR：读节点标签",
    "onnxruntime": "OCR 推理后端",
}
# 可选但**缺了会降级**的模块（label_ocr 会打 ⚠️，不再静默）
OPTIONAL = {
    "scipy": "滚动阶段「玩家站在带标签节点上」的起点判定",
}

_PROBE = (
    "import importlib.util,json,sys\n"
    "mods=sys.argv[1:]\n"
    "out={'version':sys.version.split()[0],'exe':sys.executable,'found':{}}\n"
    "for n in mods:\n"
    "    try: out['found'][n]=importlib.util.find_spec(n) is not None\n"
    "    except Exception: out['found'][n]=False\n"
    "print(json.dumps(out))\n"
)


def have(name):
    """当前解释器有没有这个模块（不 import，避免加载 onnxruntime 拖慢）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def missing(mods=None):
    mods = list(mods or REQUIRED)
    return [m for m in mods if not have(m)]


def probe(path):
    """让**另一个**解释器自报版本与依赖（子进程，不污染当前进程）。"""
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(path):
        return {"exe": path, "error": "不存在"}
    try:
        r = subprocess.run(
            [path, "-c", _PROBE, *REQUIRED, *OPTIONAL],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"exe": path, "error": str(exc)[:200]}
    out = (r.stdout or "").strip()
    line = out.splitlines()[-1] if out else ""
    try:
        info = json.loads(line)
    except Exception:
        err = (r.stderr or "无输出").strip().splitlines()
        return {"exe": path, "error": (err[-1] if err else "无法解析探测结果")[:200]}
    info["exe"] = path
    info["missing"] = [m for m in REQUIRED if not info["found"].get(m)]
    info["missing_optional"] = [m for m in OPTIONAL if not info["found"].get(m)]
    info["ok"] = not info["missing"]
    return info


def _key(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def candidates():
    """本机可能装了依赖的解释器（去重、只留真实存在的文件）。"""
    seen, out = set(), []

    def add(p):
        if not p or not isinstance(p, str):
            return
        p = os.path.abspath(os.path.expanduser(p))
        if not os.path.isfile(p):
            return
        k = _key(p)
        if k not in seen:
            seen.add(k)
            out.append(p)

    add(os.environ.get("ARKNIGHTS_PYTHON"))
    add(sys.executable)
    for name in ("python", "python3"):
        add(shutil.which(name))
    if os.name == "nt":
        home = os.path.expanduser("~")
        pats = [
            os.path.join(home, "AppData", "Local", "Programs", "Python",
                         "Python3*", "python.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                         "Python3*", "python.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                         "Python3*", "python.exe"),
            # Dsh 自带运行时也列出来：它**通常没有** OCR/scipy，列出来是为了让人一眼
            # 看出「我刚才用的就是这个」，而不是再去猜「本机为什么缺」。
            os.path.join(home, ".dsh", "dsh-runtimes", "*", "dependencies",
                         "python", "python.exe"),
        ]
        for pat in pats:
            for p in sorted(glob.glob(pat)):
                add(p)
    else:
        for p in ("/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"):
            add(p)
    return out


def alternative_hint(mods=None, limit=3):
    """给报错文案用：本机还有哪些解释器不缺 `mods`（默认必需依赖）。

    只在**当前解释器缺依赖**时调用（会 spawn 子进程，约 0.3–0.5s/个）。
    """
    need = list(mods or REQUIRED)
    cur = _key(sys.executable)
    lines = []
    for cand in candidates():
        if _key(cand) == cur:
            continue
        info = probe(cand)
        if info.get("ok") and not [m for m in need if not info["found"].get(m)]:
            lines.append(f"   · {cand}（Python {info['version']}）")
            if len(lines) >= limit:
                break
    if not lines:
        return ""
    return ("   本机另有依赖齐全的解释器（用它重跑本命令）：\n"
            + "\n".join(lines)
            + "\n   选解释器：python tools/pyenv.py")


def _fmt_found(info, mods):
    return "  ".join(f"{m}{'✅' if info['found'].get(m) else '❌'}" for m in mods)


def _print_info(info, indent="  "):
    head = f"{indent}{info.get('exe', '?')}（Python {info.get('version', '?')}）"
    if info.get("error"):
        print(f"{head}  ⚠️ {info['error']}")
        return
    mark = "✅" if info.get("ok") else "❌"
    print(f"{head}  {mark}")
    print(f"{indent}  必需：{_fmt_found(info, REQUIRED)}")
    print(f"{indent}  可选：{_fmt_found(info, OPTIONAL)}")
    if info.get("missing_optional"):
        print(f"{indent}  ⚠️ 缺 {'、'.join(info['missing_optional'])}："
              f"{OPTIONAL[info['missing_optional'][0]]}会降级")


def main():
    ap = argparse.ArgumentParser(
        description="打印当前解释器与依赖；缺依赖时给出本机可用的其他解释器")
    ap.add_argument("--all", action="store_true", help="把本机候选解释器全部探测一遍")
    ap.add_argument("--python", default=None, help="探测指定解释器路径")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args()

    # 当前解释器用**进程内**探测：这就是脚本真正看到的依赖
    current = {
        "exe": os.path.abspath(sys.executable),
        "version": sys.version.split()[0],
        "found": {m: have(m) for m in list(REQUIRED) + list(OPTIONAL)},
    }
    current["missing"] = [m for m in REQUIRED if not current["found"][m]]
    current["missing_optional"] = [m for m in OPTIONAL if not current["found"][m]]
    current["ok"] = not current["missing"]

    target = None
    if a.python:
        target = probe(a.python)

    others = []
    if a.all or not current["ok"]:
        others = [probe(c) for c in candidates() if _key(c) != _key(current["exe"])]

    if a.json:
        print(json.dumps({"current": current, "target": target, "others": others},
                         ensure_ascii=False, indent=1))
        return 0 if current["ok"] else (2 if any(o.get("ok") for o in others) else 1)

    print("== 黑流树海 · 环境自检（该用哪个 Python）==")
    print(f"当前解释器：{current['exe']}")
    print(f"  版本：Python {current['version']}")
    print(f"  必需：{_fmt_found(current, REQUIRED)}")
    print(f"  可选：{_fmt_found(current, OPTIONAL)}")
    if current["ok"]:
        print("✅ 当前解释器依赖齐全 —— 直接用它跑 `python tools/xxx.py` 即可。")
        if current["missing_optional"]:
            print("⚠️ 可选依赖缺失：滚动阶段「玩家站在带标签节点上」的起点判定会降级"
                  "（`label_ocr` 会打 ⚠️）。要补："
                  f"{current['exe']} -m pip install " + " ".join(current["missing_optional"]))
    else:
        print(f"❌ 当前解释器缺必需依赖：{'、'.join(current['missing'])}")
        print(f"   装依赖：{current['exe']} -m pip install "
              + " ".join(current["missing"]) + " " + " ".join(current["missing_optional"]))

    if target:
        print("\n-- 指定解释器 --")
        _print_info(target)

    if others:
        print("\n-- 本机其他解释器 --")
        for info in others:
            _print_info(info)
        ready = [o for o in others if o.get("ok")]
        if ready:
            best = ready[0]
            print("\n✅ 推荐用依赖齐全的解释器重跑：")
            print(f"   {best['exe']} tools{os.sep}observe.py ...")
            print("   （或把它放到 PATH 最前 / 设环境变量 ARKNIGHTS_PYTHON 指向它）")
        elif not current["ok"]:
            print("\n❌ 本机没有依赖齐全的解释器 —— 先给某一个装依赖（见上），"
                  "再跑 `python tools/pyenv.py --all` 复核。")

    if current["ok"]:
        return 0
    return 2 if any(o.get("ok") for o in others) else 1


if __name__ == "__main__":
    sys.exit(main())
