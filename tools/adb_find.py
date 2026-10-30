#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adb_find.py — 找 adb.exe / 找设备 / 截图：把「模拟器在哪」一次解决

为什么要有这个文件（2026-10-29 事故）
------------------------------------
`observe.py` 把 adb 路径**写死**成 `E:\\MuMuPlayer\\nx_device\\12.0\\shell\\adb.exe`，
而那天 adb 既不在 PATH、写死的路径也不保证存在 → 抓图直接失败，
失败信息只有一句「adb 没连上？」。执行方于是**退回去翻 runs/shots/ 里的旧截图**
（19:42 / 19:55 的「血色空脉」三层），把上一局的局面当成当前局来分析。

这不是识别算错，是**输入源取错**。防复发要三层，缺一不可：

  A. 找 adb 不许靠猜：显式参数 → 环境变量 → 上次成功的缓存 → 常见安装路径与通配
     → 系统 PATH（which/where）。找不到就把**试过哪些**全部列出，而不是一句「截图失败」。
  B. 找设备不许靠运气：`adb devices` 为空时自动 `adb connect` 常见模拟器端口
     （MuMu 16384 / 夜神 62001 / 雷电 5555 …），并记住上次成功的设备。
  C. **取图不许用旧图**：这是 `observe.py` 的旧图闸职责（`check_image_freshness`）——
     本文件只负责「让现场截图这一步几乎不可能失败」，从而没有借口去翻旧图。

对外 API（grab.py / observe.py 共用）
------------------------------------
  find_adb(explicit="")          -> adb 路径（找到即写缓存）
  list_devices(adb)              -> ["127.0.0.1:16384", ...]
  pick_device(adb, explicit="")  -> 设备序列号（必要时自己 adb connect）
  capture(adb, device, out_path) -> out_path（exec-out 直写；失败退 pull）
  describe(explicit="", device="") -> 人类可读探测报告（observe --probe 用）
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# 控制台编码兜底：GBK 终端下打印 ✅/❌ 会直接崩（2026-10-29 实测）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "adb.local.json")

# 环境变量：优先 ARKNIGHTS_ADB，其次通用的 ADB / ANDROID_ADB
ENV_KEYS = ("ARKNIGHTS_ADB", "BS_ADB", "ADB", "ANDROID_ADB")

# 已知安装位置（MuMu 12 是当前这台机器的实际路径，其余是其他机器的常见形态）
STATIC_CANDIDATES = [
    r"E:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
    r"D:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
    r"C:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-6.0\shell\adb.exe",
    r"C:\Program Files\Nox\bin\adb.exe",
    r"C:\Program Files (x86)\Nox\bin\adb.exe",
    r"C:\LDPlayer\LDPlayer9\adb.exe",
    r"D:\LDPlayer\LDPlayer9\adb.exe",
    r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    r"C:\Program Files\Microvirt\MEmu\adb.exe",
]

# 通配：覆盖「装在别的盘 / 版本号不同」的情况。模式要窄，别整盘扫。
GLOB_CANDIDATES = [
    r"?:\\MuMuPlayer*\\nx_device\\*\\shell\\adb.exe",
    r"?:\\Program Files\\Netease\\MuMuPlayer*\\shell\\adb.exe",
    r"?:\\Program Files\\Netease\\MuMuPlayer*\\nx_device\\*\\shell\\adb.exe",
    r"?:\\LDPlayer*\\adb.exe",
    r"?:\\Nox\\bin\\adb.exe",
    r"?:\\BlueStacks_nxt\\HD-Adb.exe",
]

# 常见模拟器 adb 端口：devices 为空时逐个 connect 试试
CONNECT_HINTS = [
    "127.0.0.1:16384",   # MuMu 12（本机）
    "127.0.0.1:16416",   # MuMu 12 多开第二实例
    "127.0.0.1:7555",    # MuMu 6
    "127.0.0.1:62001",   # 夜神
    "127.0.0.1:62025",   # 夜神多开
    "127.0.0.1:5555",    # 雷电 / BlueStacks
    "emulator-5554",     # 通用 AVD
]

NO_OLD_IMAGE_HINT = (
    "⛔ 不要改用 runs/shots/ 里的旧截图（那是别的对局/别的层），"
    "旧图会把整条路线算错。见 SKILL.md「旧图闸」。"
)


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(**kw):
    data = _load_cache()
    # 防污染：临时目录里的 adb（单测造的假文件、解包临时目录）不许写进缓存——
    # 2026-10-29 实测被 selftest 写进去一次，之后 grab.py 一直拿它当 adb 找设备，全盘失败。
    adb = kw.get("adb")
    if adb:
        try:
            tmp_root = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
            if os.path.normcase(os.path.abspath(adb)).startswith(tmp_root):
                kw = {k: v for k, v in kw.items() if k != "adb"}
        except Exception:
            pass
    data.update({k: v for k, v in kw.items() if v})
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass          # 缓存写不了不影响主流程


def candidate_paths():
    """按优先级返回 [(来源, 路径)]，含环境变量 / 缓存 / 静态 / 通配。"""
    out = []
    for k in ENV_KEYS:
        v = os.environ.get(k)
        if v:
            out.append((f"环境变量 {k}", v.strip('"')))
    cached = _load_cache().get("adb")
    if cached:
        out.append(("上次成功的缓存", cached))
    for p in STATIC_CANDIDATES:
        out.append(("常见安装路径", p))
    for pat in GLOB_CANDIDATES:
        try:
            for p in sorted(glob.glob(pat)):
                out.append(("通配扫描", p))
        except Exception:
            pass
    for name in ("adb", "adb.exe"):
        p = shutil.which(name)
        if p:
            out.append(("系统 PATH", p))
            break
    # Windows 上 where 有时能命中 which 找不到的（PATH 里有但无扩展名）
    if os.name == "nt":
        try:
            r = subprocess.run(["where", "adb"], capture_output=True, text=True, timeout=10)
            for line in (r.stdout or "").splitlines():
                if line.strip():
                    out.append(("where adb", line.strip()))
        except Exception:
            pass
    return _dedup_paths(out)


def _dedup_paths(pairs):
    seen, out = set(), []
    for src, p in pairs:
        if not p:
            continue
        try:
            key = os.path.normcase(os.path.abspath(p))
        except Exception:
            key = p
        if key in seen:
            continue
        seen.add(key)
        out.append((src, p))
    return out


def find_adb(explicit=""):
    """返回可用的 adb.exe 绝对路径；找不到时**列出所有尝试过的地方**再退出。

    显式路径写错不算致命：打 ⚠️ 后继续自动找（用户贴错盘符很常见，不该因此卡死）；
    但自动找也找不到时，报错里必须带「不要改用 runs/shots 旧图」。
    """
    tried = []
    if explicit:
        if os.path.exists(explicit):
            _save_cache(adb=os.path.abspath(explicit))
            return os.path.abspath(explicit)
        print(f"⚠️ --adb 指定的路径不存在：{explicit}\n   继续自动查找……", file=sys.stderr)
        tried.append(("--adb 显式传入", explicit + "（不存在）"))
    for src, p in candidate_paths():
        if os.path.exists(p):
            _save_cache(adb=os.path.abspath(p))
            return os.path.abspath(p)
        tried.append((src, p))
    lines = [
        "❌ 找不到 adb.exe（模拟器的 adb 不在 PATH，常见安装路径也没有）",
        "   已尝试：",
    ]
    for src, p in tried[(1 if explicit else 0):]:
        lines.append(f"     · [{src}] {p}")
    lines += [
        "   怎么办（按顺序）：",
        "     ① 显式指定：--adb \"<MuMu 安装目录>\\shell\\adb.exe\"",
        "        （MuMu 12 例：E:\\MuMuPlayer\\nx_device\\12.0\\shell\\adb.exe）",
        "     ② 或设环境变量 ARKNIGHTS_ADB=<该路径>，之后会自动记住",
        NO_OLD_IMAGE_HINT,
    ]
    raise SystemExit("\n".join(lines))


def _run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as e:                      # 超时 / 无权限 / 文件被锁
        return type("R", (), {"returncode": -1, "stdout": "", "stderr": str(e)})()


def list_devices(adb):
    r = _run([adb, "devices"], timeout=25)
    devs = []
    for line in (r.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devs.append(parts[0])
    return devs


def _connect(adb, serial):
    _run([adb, "connect", serial], timeout=15)


def pick_device(adb, explicit=""):
    """挑一台设备。空列表时自动 start-server + connect 常见端口。"""
    cached = _load_cache().get("device", "")
    if explicit:
        if ":" in explicit and explicit not in list_devices(adb):
            _connect(adb, explicit)
    devs = list_devices(adb)
    if not devs:
        _run([adb, "start-server"], timeout=30)
        devs = list_devices(adb)
    if not devs and not explicit:
        for serial in CONNECT_HINTS:
            _connect(adb, serial)
            devs = list_devices(adb)
            if devs:
                break
    if explicit:
        if explicit in devs or not devs:
            _save_cache(device=explicit)
            return explicit
        raise SystemExit(
            f"❌ 指定的设备 {explicit} 不在 adb devices 里（当前：{devs}）\n"
            "   先启动模拟器，或去掉 --device 让它自动挑。\n" + NO_OLD_IMAGE_HINT)
    if not devs:
        raise SystemExit(
            "❌ adb 找到了（%s），但一台设备都没有：模拟器没开 / 没连上 adb。\n"
            "   · 先把 MuMu 启动到游戏地图界面，再重跑；\n"
            "   · 多开或非默认端口：--device 127.0.0.1:<端口>（MuMu 12 默认 16384）。\n"
            "%s" % (adb, NO_OLD_IMAGE_HINT))
    dev = cached if cached in devs else devs[0]
    _save_cache(device=dev)
    return dev


def capture(adb, device, out_path, retries=2):
    """截图。先 exec-out（二进制直写，不经 shell 管道，避免 PNG 损坏）；
    失败再退回 `screencap -> pull`。"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    last = ""
    for attempt in range(max(1, retries)):
        if attempt:
            time.sleep(0.4)
        try:
            with open(out_path, "wb") as f:
                r = subprocess.run([adb, "-s", device, "exec-out", "screencap", "-p"],
                                   stdout=f, stderr=subprocess.PIPE, timeout=60)
            size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
            if r.returncode == 0 and size >= 10000:
                return out_path
            last = f"exec-out rc={r.returncode} size={size}"
        except Exception as e:
            last = f"exec-out 异常：{e}"
        # 退路：写到设备再 pull
        try:
            _run([adb, "-s", device, "shell", "screencap", "-p", "/sdcard/_dsh_shot.png"], timeout=60)
            r2 = subprocess.run([adb, "-s", device, "pull", "/sdcard/_dsh_shot.png", out_path],
                                capture_output=True, timeout=60)
            _run([adb, "-s", device, "shell", "rm", "-f", "/sdcard/_dsh_shot.png"], timeout=20)
            size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
            if r2.returncode == 0 and size >= 10000:
                return out_path
            last += f"；pull rc={r2.returncode} size={size}"
        except Exception as e:
            last += f"；pull 异常：{e}"
    raise SystemExit(
        f"❌ 截图失败（adb={adb} 设备={device}）：{last}\n"
        "   已知可行的处置顺序：\n"
        "     ① 确认模拟器停在地图界面，重跑（多数情况重跑就好）；\n"
        "     ② 换设备：--device 127.0.0.1:<端口>；\n"
        "     ③ 让玩家现场截图后传 --image（必须是**刚刚**截的）。\n" + NO_OLD_IMAGE_HINT)


def describe(explicit="", device=""):
    """给 --probe 用的探测报告：adb 从哪来、有哪些设备、缓存是什么。"""
    lines = ["── adb 探测 ──"]
    src = ""
    try:
        adb = find_adb(explicit)     # 这一步会顺手刷新缓存，所以缓存状态在它之后打印
        cache = _load_cache()
        lines.append(f"缓存文件：{CACHE}"
                     + (f"（{cache.get('updated', '?')} 更新）" if cache else "（还没有）"))
        for s, p in candidate_paths():
            if os.path.exists(p) and os.path.normcase(os.path.abspath(p)) == os.path.normcase(adb):
                src = s
                break
        lines.append(f"adb     ：{adb}" + (f"　[{src}]" if src else ""))
        devs = list_devices(adb)
        lines.append(f"设备    ：{devs if devs else '（空 → 会尝试自动 connect）'}")
        dev = pick_device(adb, device)
        lines.append(f"选用设备：{dev}")
        lines.append("✅ 截图链路可用（下一步直接跑 observe.py，不带 --image）")
    except SystemExit as e:
        lines.append(str(e))
        lines.append("❌ 截图链路不可用")
        raise SystemExit("\n".join(lines))
    print("\n".join(lines))
    return adb


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="探测 adb / 设备；也可只打印会选哪个 adb")
    ap.add_argument("--adb", default="", help="显式 adb.exe 路径")
    ap.add_argument("--device", default="", help="显式设备序列号，如 127.0.0.1:16384")
    ap.add_argument("--which", action="store_true", help="只打印 adb 路径")
    _a = ap.parse_args()
    if _a.which:
        print(find_adb(_a.adb))
    else:
        describe(_a.adb, _a.device)
