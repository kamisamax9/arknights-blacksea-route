#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grab.py — 从正在运行的 MuMu 模拟器截图，并可选地直接跑场景识别

本来是为了替代"每走一步手动上传一张截图"。
用户只要在对话里说一句"我到了三层，起点(a,b)，理想源(c,d)"，Agent 调这个脚本即可。

**层数不用传**：`--scene` 会先截图，再用 board_read.py 从标题 OCR 认层
（认不出就报错，绝不默认 11×5）。`--layer/--cols/--rows` 只是兜底。

用法
----
  python grab.py                       # 截图，存到 <workspace>/runs/shots/<时间>.png
  python grab.py --scene               # 截图 + 自动认层 + 节点表（推荐）
  python grab.py --scene --force-grid --layer 3   # 调试：强制按三层 11×5 读
  python grab.py --device emulator-5554

自动发现
--------
1. 依次尝试常见 adb 路径（MuMu 12 / MuMu 12 Global / 系统 PATH）；
2. `adb devices` 里取第一个 state=device 的设备（多开时可用 --device 指定）；
3. 截图走 `adb exec-out screencap -p`，二进制直接重定向到文件（不能用 shell 管道，会损坏 PNG）。
"""
import argparse
import os
import re
import subprocess
import sys
import time


# 控制台编码兜底：避免 GBK 下打印箭头/符号直接崩
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ADB_CANDIDATES = [
    r"E:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
]


# adb 的定位/设备选择/截图统一由 adb_find.py 负责（2026-10-29：observe.py 曾经自己
# 硬编码 adb 路径，路径一换就抓不到图，于是退回旧截图——见 adb_find.py 头部注释）。
# 这里保留同名薄包装，外部旧调用（find_adb/list_devices/capture）不受影响。
import adb_find as AF                                    # noqa: E402

find_adb = AF.find_adb
list_devices = AF.list_devices


def capture(adb, device, out_path):
    return AF.capture(adb, device, out_path)


def pick_device(adb, explicit=""):
    return AF.pick_device(adb, explicit)


# 层名 → 层号（画面顶部中间那行字）
TITLE_TO_LAYER = {
    "玻利瓦尔肤层": 1,
    "甜美的伤口": 2,
    "血色空脉": 3,
    "受害者腐殖": 4,
    "卡德霍之颅": 5,
    "盲目者所见": 6,      # 四结局六层（5×4）
    "源流交汇处": 6,      # 三结局六层（6×5，构型名=源流）
}


def crop_title(path):
    """裁出顶部层名区域，返回小图路径（给模型看，比读整张图便宜）"""
    from PIL import Image
    im = Image.open(path)
    W, H = im.size
    box = (int(0.30 * W), 0, int(0.72 * W), int(0.10 * H))
    out = path.replace(".png", "_title.png")
    im.crop(box).save(out)
    return out


def _diff(p1, p2):
    """两帧差异（缩到 200px 宽后比较平均绝对差）"""
    from PIL import Image
    import numpy as np
    a = Image.open(p1).convert("L").resize((200, 112))
    b = Image.open(p2).convert("L").resize((200, 112))
    return float(np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).mean())


# 各层网格（仅在 --layer 覆盖或 OCR 不可用时兜底用）
LAYER_GRID = {1: (5, 3), 2: (5, 4), 3: (11, 5), 4: (12, 5), 5: (14, 5), 6: (5, 4)}


def _legacy_read_scene(path, cols, rows):
    """老路径：按**给定** cols/rows 跑响应图拟合（无 OCR 时兜底）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "read_scene.py"), path,
           "--cols", str(cols), "--rows", str(rows)]
    print("--- read_scene（按给定网格）---")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out2 = (r.stdout or "") + (r.stderr or "")
    print(out2.rstrip())
    m = re.search(r"检测到节点 (\d+) 个", out2)
    n = int(m.group(1)) if m else 0
    print(f"识别到 {n} 个节点（按给定网格 {cols}×{rows}）")
    return n


def _board_python():
    """找一个能跑 board_read 的解释器（要 rapidocr）。

    本进程优先（最省事），其次 PATH 上的 python/python3、py 启动器、
    以及 Windows 常见安装目录——DSH 的宿主进程 PATH 里经常没有 python。
    返回命令前缀 list（如 [sys.executable] 或 ['py', '-3']），找不到返回 None。
    """
    import glob
    # 快路径：本进程就能 import（省掉一次子进程 + 一次重复加载 onnxruntime）
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
        return [sys.executable]
    except Exception:
        pass

    probe = "import numpy, PIL, rapidocr_onnxruntime"
    cands = [["python"], ["python3"], ["py", "-3"]]
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES"),
                 os.environ.get("PROGRAMFILES(X86)")):
        if not base:
            continue
        pats = [os.path.join(base, "Programs", "Python", "Python3*", "python.exe"),
                os.path.join(base, "Python3*", "python.exe")]
        for pat in pats:
            for p in sorted(glob.glob(pat), reverse=True):
                cands.append([p])
    seen = set()
    for c in cands:
        key = tuple(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            r = subprocess.run(c + ["-c", probe], capture_output=True, timeout=90)
            if r.returncode == 0:
                return c
        except Exception:
            continue
    return None


def _board_inproc(path):
    import board_read as BR
    res = BR.read_board(path, None)
    print(BR.format_board(res))
    if not res.get("ok"):
        return False, 0, 0, 0
    return True, len(res["cells"]), res["cols"], res["rows"]


def _board_subproc(path, python):
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = list(python) + [os.path.join(here, "board_read.py"), path]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out.rstrip())
    m = re.search(r"识别到 (\d+) 个节点", out)
    if r.returncode != 0 or not m:
        return False, 0, 0, 0
    m2 = re.search(r"格点 (\d+)×(\d+)", out)
    cols, rows = (int(m2.group(1)), int(m2.group(2))) if m2 else (11, 5)
    return True, int(m.group(1)), cols, rows


def read_board(path, a):
    """截图 → 认层 + 节点表。返回 (ok, n, cols, rows)。

    **默认永远先自动认层**（board_read：标题 OCR 认层 → 标签 OCR 建格）。
    --cols/--rows/--layer 只是自动失败时的兜底网格，不再能抢在自动前面
    ——否则老调用方随手传的 `--cols 11 --rows 5` 又会把一层读成三层。
    想强制按给定网格读，加 --force-grid（调试用）。

    本进程缺 rapidocr 时（DSH 自带 runtime 就没装）自动换 PATH 上的 python 跑，
    这样调用方用哪个解释器都不会退回 11×5。
    """
    force = bool(getattr(a, "force_grid", False))

    if not force:
        python = _board_python()
        if python is None:
            print("!! 找不到带 OCR 的解释器（缺 rapidocr_onnxruntime）——层数无法自动识别")
        else:
            print("--- 场景识别（自动认层）---")
            try:
                same = python == [sys.executable]
                ok, n, cols, rows = _board_inproc(path) if same else _board_subproc(path, python)
                if ok:
                    return True, n, cols, rows
            except SystemExit as e:                 # 缺 rapidocr 时 label_ocr 会这么抛
                print(str(e))
            except Exception as e:                  # noqa: BLE001
                print(f"!! 读图异常：{e}")
            print("   自动认层失败，退回兜底网格")

    cols = a.cols if a.cols is not None else LAYER_GRID.get(a.layer, (11, 5))[0]
    rows = a.rows if a.rows is not None else LAYER_GRID.get(a.layer, (11, 5))[1]
    if not force and a.layer is None and a.cols is None and a.rows is None:
        print("   层数认不出就不猜——确认画面停在地图上，或手传 --layer 覆盖。")
        return False, 0, 0, 0
    print("   按给定网格读（兜底）")
    return True, _legacy_read_scene(path, cols, rows), cols, rows



def map_likeness(path):
    """判断这张图像不像「战斗地图」界面。

    实测三类界面的中央区（x 10%~90%, y 15%~80%）：
        战斗地图 : 均值 21~30, p90 灰度 38~58
        Loading  : 均值 ~16,  p90 ~17（黑屏/转场——只靠上界会误判成地图，2026-10-29 实测）
        开始界面 : 均值 ~139, p90 ~203
        主界面   : 均值 ~80,  p90 ~198
    所以用 p90 做成**双界**判定：太暗（<25，没加载完）和太亮（≥110，别的界面）都不算地图。
    返回 (是否像地图, 均值, p90)。
    """
    from PIL import Image
    import numpy as np
    g = np.asarray(Image.open(path).convert("L")).astype(float)
    H, W = g.shape
    box = g[int(0.15 * H):int(0.80 * H), int(0.10 * W):int(0.90 * W)]
    m = float(box.mean())
    p90 = float(np.percentile(box, 90))
    return (m < 60.0 and 25.0 <= p90 < 110.0), m, p90


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adb", default="")
    ap.add_argument("--device", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--scene", action="store_true")
    # 层数默认**不用传**：从截图标题自动认（board_read.py）。
    # --layer / --cols / --rows 只在想强行覆盖或 OCR 不可用时才用。
    ap.add_argument("--layer", type=int, default=None,
                    help="兜底层号（自动认层失败时才用；正常不需要）")
    ap.add_argument("--cols", type=int, default=None)
    ap.add_argument("--rows", type=int, default=None)
    ap.add_argument("--force-grid", dest="force_grid", action="store_true",
                    help="跳过自动认层，强制按 --layer/--cols/--rows 读（调试用）")
    ap.add_argument("--pair", action="store_true",
                    help="连拍两张比对，检测画面是否稳定（默认开）")
    ap.add_argument("--no-pair", dest="pair", action="store_false")
    ap.set_defaults(pair=True)
    a = ap.parse_args()

    adb = find_adb(a.adb)
    dev = pick_device(adb, a.device)        # 空列表时自动 connect 常见模拟器端口
    devs = list_devices(adb)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = a.out or os.path.join(os.getcwd(), "runs", "shots", f"{ts}.png")
    capture(adb, dev, out)
    if a.pair:
        tmp = out.replace(".png", "_b.png")
        time.sleep(0.35)
        capture(adb, dev, tmp)
        d = _diff(out, tmp)
        if d < 1.5:
            print(f"稳定检测 : 差异 {d:.2f} → 画面静止 [OK]")
            os.remove(tmp)
        else:
            print(f"稳定检测 : 差异 {d:.2f} → 画面在动！，建议等动画结束重截（已保留两张：{os.path.basename(out)} / {os.path.basename(tmp)}）")
    size = os.path.getsize(out)
    print(f"adb      : {adb}")
    print(f"device   : {dev}" + (f"（共 {len(devs)} 个）" if len(devs) > 1 else ""))
    print(f"saved    : {out}  ({size//1024} KB)")

    tpath = crop_title(out)
    print(f"title_image : {tpath}")

    is_map, brightness, p90 = map_likeness(out)
    if not is_map:
        hint = ("画面太黑（p90 偏低）＝ 还在 Loading / 转场"
                if p90 < 25.0 else
                "多半还在开始界面 / 主界面 / 结算页")
        print(f"界面判断 : 中央均值 {brightness:.0f} / p90 {p90:.0f} -> 不是战斗地图界面"
              f"（地图应 均值<60 且 p90 在 25~110）")
        print(f"           {hint}。等回到地图那一屏再让我截。")
        return
    print(f"界面判断 : 中央均值 {brightness:.0f} / p90 {p90:.0f} -> 像战斗地图 [OK]")

    if not a.scene:
        return

    ok, n, cols, rows = read_board(out, a)
    if not ok:
        # 认不出层 = 硬失败。以前这里会退回 11×5 硬读，把一层读成 8 个节点
        # 再报「不是战斗地图」，正是 2026-10-29 那次误判的根因。
        raise SystemExit(2)

    need = max(4, int(cols * rows * 0.2))
    print()
    if n < need:
        print("!! 只识别到 %d 个节点（本层 %d×%d 至少应有 %d）——多为暗图标漏检" % (n, cols, rows, need))
        print("   看标注图/标题图确认；必要时手传 --layer 覆盖或人工补格。")
    else:
        print("→ 识别到 %d 个节点，可用于规划。" % n)


if __name__ == "__main__":
    main()


