#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""observe.py — 进新层时的**唯一入口**：截图 → 认层 → 认构型 → 求解 → 判词

为什么要有这个
--------------
2026-10-28 复盘：一轮 18 分钟里，有 9 分钟花在「推理与编排」，其中很大一部分不是思考，
而是**把一件事拆成七八次工具调用**：

    截图(1) → label_ocr(1) → 数格子匹配构型(1) → 截图裁 AP(1)
    → 再看图确认 AP(1) → 想 grants(1) → route.py(1)

每次调用都有进程启动 + 我重新读一遍输出的开销。这里把它们合成**一次**。

用法
----
  python tools/observe.py --root 1,2 --items "标准引擎:3;老妈妈的融雪:1"
  python tools/observe.py --probe                        # 只探测截图链路（adb/设备/最新图龄）
  python tools/observe.py --image 截图.png --allow-stale  # 复盘用：显式放行旧图
  python tools/observe.py --layer 3 --config L4 --ap 6 ... # 全手动（跳过识别）

它会自己搞定：**是哪一层、起点在哪、属于哪个构型、行动力几点**。
玩家只需要报「理想源坐标」和「道具库存」——这两样图上没有
（普通分队模式下理想源可不报，不报 = 按无理想源算；N16文明开化下是「追猎进入时大概率出现」，
不是必定——真没有就照常求解。模式用 --mode 传）。

⛔ 两条输入源纪律（2026-10-29 事故后加，见 adb_find.py 头部）
  1. adb 不再写死：显式 --adb → 环境变量 → 缓存 → 常见路径/通配 → PATH；
     设备为空会自动 connect 常见模拟器端口，找不到就**列全试过的地方**。
  2. `--image` 必须过**旧图闸**：超过 5 分钟、或 runs/shots 里有明显更新的一张，
     一律拒绝（--allow-stale 显式放行）。抓图失败时**不许**改用旧的 runs/shots 图。
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np                       # noqa: E402
from PIL import Image                    # noqa: E402
import label_ocr as LO                   # noqa: E402
import build_scenario as BS              # noqa: E402
import apply_config as AC                # noqa: E402
import check_configs as CC               # noqa: E402
import adb_find as AF                    # noqa: E402
import grab as G                         # noqa: E402  （只用 map_likeness，不跑它的 main）
import rules as R                        # noqa: E402  （三/二层问询的状态与校验）

RUNS = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "runs", "shots"))

# 旧图闸：截图超过这么多秒就算「过期」，不许拿来规划（2026-10-29 事故后新增）
FRESH_SECONDS = 300
# 允许「不是 runs/shots 里最新的一张」的宽限：这么短之内截的，先后顺序无所谓
NEWER_TOLERANCE = 60


# 层名关键词 → 层号（截图标题里的中文）
LAYER_TITLE = {
    "玻利瓦尔": 1, "甜美的伤口": 2, "血色空脉": 3,
    "受害者腐殖": 4, "卡德霍之颅": 5,
    "盲目者所见": 6,       # 四结局六层（5×4）
    "源流交汇处": 6,       # 三结局六层（6×5，构型名=源流）
}


def _fmt_age(sec):
    if sec < 90:
        return f"{sec:.0f} 秒"
    if sec < 5400:
        return f"{sec / 60:.1f} 分钟"
    return f"{sec / 3600:.1f} 小时"


def shot_index():
    """runs/shots 里的**正式截图**（排除 _title / _annotated / _b 这类派生图），按新→旧。"""
    if not os.path.isdir(RUNS):
        return []
    out = []
    for name in os.listdir(RUNS):
        if not name.lower().endswith(".png"):
            continue
        if "_title" in name or "annotated" in name or name.endswith("_b.png"):
            continue
        p = os.path.join(RUNS, name)
        try:
            out.append((p, os.path.getmtime(p)))
        except OSError:
            pass
    return sorted(out, key=lambda x: x[1], reverse=True)


def newest_shot():
    idx = shot_index()
    return idx[0] if idx else (None, 0.0)


def check_image_freshness(path, max_age=None, allow_stale=False):
    """**旧图闸**（2026-10-29 事故的直接修补）。

    那天 observe.py 因 adb 路径写死而抓图失败，执行方退回去读了 runs/shots 里
    19:42 / 19:55 的两张「血色空脉」三层图，把上一局当成当前局出方案。
    识别没错，是**输入源取错**。所以这里对 `--image` 加两道机械判据：

      ① 太旧：拍摄时间超过 FRESH_SECONDS（默认 300s）→ 拒绝；
      ② 不是最新：runs/shots 里存在比它新 NEWER_TOLERANCE 秒以上的截图 → 拒绝
         （正常用法是「刚截完立刻跑」，那文件本来就是最新的）。

    拒绝时**给出最新那张的路径和年龄**，并明确说该怎么做（现场重截 / --allow-stale）。
    `--allow-stale` 只放行、不静默：仍打印 ⚠️，提醒这是人工担责的决定。
    """
    if not os.path.exists(path):
        raise SystemExit(f"❌ 截图不存在：{path}")
    max_age = FRESH_SECONDS if max_age is None else float(max_age)
    age = time.time() - os.path.getmtime(path)
    newest_p, newest_t = newest_shot()
    newest_age = (time.time() - newest_t) if newest_p else None
    problems = []
    if age > max_age:
        problems.append(f"它拍摄于 {_fmt_age(age)} 前（阈值 {_fmt_age(max_age)}）")
    if newest_p and os.path.abspath(newest_p) != os.path.abspath(path) \
            and newest_age is not None and newest_age + NEWER_TOLERANCE < age:
        problems.append(f"runs/shots 里有更新的截图 {os.path.basename(newest_p)}"
                        f"（{_fmt_age(newest_age)} 前），你传的是它之前的")
    if problems:
        msg = ["❌ 旧图闸：这张图不能用来规划——" + "；".join(problems),
               f"   你传的：{path}（{_fmt_age(age)} 前）"]
        if newest_p:
            msg.append(f"   最新一张：{newest_p}（{_fmt_age(newest_age)} 前）")
        msg += [
            "   正确做法（按顺序）：",
            "     ① 不带 --image 直接跑 → 现场截模拟器（推荐，adb 现在会自动找）；",
            "     ② 或先 `python tools/grab.py` 再传它刚存的那张；",
            "     ③ 真要用旧图（例如复盘上一局）必须显式 --allow-stale，且结论必须标注「基于旧图」。",
            "   ⛔ 不许换一张更旧的图绕过——旧图会把整层路线算错（2026-10-29 实际发生）。",
        ]
        if not allow_stale:
            raise SystemExit("\n".join(msg))
        print("\n".join("⚠️ " + m if i == 0 else m for i, m in enumerate(msg)))
        print("⚠️ 已按 --allow-stale 放行：结论只能当参考，不能当本局当前状态。")
    return path


def grab(adb="", device=""):
    """现场截一张图，返回本地路径。

    2026-10-29 修复：以前这里把 adb 路径/设备写死，找不到就一句「adb 没连上？」。
    现在全交给 adb_find（显式参数 → 环境变量 → 缓存 → 常见路径/通配 → PATH，
    设备为空会自动 connect 常见端口），并把**用了哪个 adb / 哪个设备**打出来，
    让「到底截的是哪台模拟器」有据可查。
    """
    os.makedirs(RUNS, exist_ok=True)
    try:
        adb_path = AF.find_adb(adb)
        dev = AF.pick_device(adb_path, device)
    except SystemExit as e:
        raise SystemExit(f"{e}\n\n💡 抓图链路断了也不许去读 runs/shots 里的旧图。"
                         "先用 `python tools/observe.py --probe` 看探测报告。")
    out = os.path.join(RUNS, time.strftime("%Y%m%d-%H%M%S") + ".png")
    AF.capture(adb_path, dev, out)
    print(f"现场截图：adb={adb_path}　device={dev}")
    return out


def raw_ocr(path):
    """不经词表过滤的原始 OCR。

    `LO.read_image` 会把「不是节点名」的文字丢掉（那是它的本职），
    所以**层标题、行动力数字**必须用原始识别，否则永远读不到。
    """
    res, _ = LO.engine()(path)
    out = []
    for line in res or []:
        txt = line[1] if isinstance(line, (list, tuple)) and len(line) > 1 else ""
        if txt:
            out.append(str(txt).strip())
    return out


def read_layer(im):
    """从标题区认层。认不出返回 None。"""
    w, h = im.size
    crop = im.crop((int(w * 0.35), 0, int(w * 0.65), int(h * 0.12)))
    tmp = os.path.join(RUNS, "_title_ocr.png")
    crop.save(tmp)
    try:
        texts = raw_ocr(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    txt = "".join(texts)
    for k, v in LAYER_TITLE.items():
        if k in txt:
            return v, txt
    return None, txt


def read_ap(im):
    """右上角行动力数字。取最大的那个数字块。"""
    w, h = im.size
    crop = im.crop((int(w * 0.76), int(h * 0.05), int(w * 0.95), int(h * 0.25)))
    tmp = os.path.join(RUNS, "_ap_ocr.png")
    crop.save(tmp)
    try:
        texts = raw_ocr(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    nums = [int(t) for t in texts if re.fullmatch(r"\d{1,2}", t)]
    return max(nums) if nums else None


def detect_config(layer, starts, grid, landmarks=frozenset()):
    """定构型：**起点 + 险路恶敌/险路尽头地标（主） → 节点集合求差（仅复核）**。

    玩家 2026-10-28 定下的标准流程（硬规则）：
      收到指令 → 截图 → **顶部文字定层** → **起点与已探明的尽头/恶敌坐标定构型**。
    节点集合求差在进层初期大多数据格未点亮时证据不足：二层 19 格只亮 10 格时，
    差分曾把构型7 推上首位、反把屏上真尽头 (4,1) 无视——**钥匙与差分冲突时以钥匙为准**。

    判据顺序：
      1. 地标硬过滤：观察到的尽头/恶敌格必须**全部**落在构型声明的地标集合里（obs ⊆ want）；
         want 为空（未收录/未学）的构型不因此排除，交给后两把钥匙；
      2. 起点命中：构型声明起点在检测候选里者优先（未声明起点不排除）；
      3. `obs - cells`（读到的格子在构型里不存在 → 越界数）仅作并列复核。
    返回 (config, cfg_start)。
    """
    files = {1: "17-一层构型.md", 2: "09-二层构型.md",
             3: "10-三层左区构型.md", 4: "11-四层左区构型.md", 5: "13-五层左区构型.md"}
    if layer == 6:
        # 六层只有 1 个构型（15-六层构型），且文件里没有「构型N（」标题可解析。
        # 旧代码在这里返回 None → main 直接 SystemExit("定不出构型")，
        # 逼玩家手传 --config——单构型层根本不用猜（2026-10-29 修复）。
        return "唯一", BS.config_start(6, "唯一")
    fn = files.get(layer)
    if not fn:
        return None, None
    txt = open(os.path.join(BS.REF, fn), encoding="utf-8").read()
    segs = re.split(r"(?m)^## ", txt)
    obs = {tuple(q) for q in grid if q[0] <= LO.LEFT_MAX.get(layer, 99)}
    lm_set = {tuple(q) for q in landmarks}
    best = None
    for seg in segs:
        m = re.match(r"(?:构型)?([A-Za-z]?\d+)（", seg)
        if not m:
            continue
        name = m.group(1)
        edges, _w, cfg_start, _e = CC.parse_section(seg)
        cells = {p for e in edges for p in e}
        miss = len(obs - cells)
        hit_start = 1 if (cfg_start and any(tuple(s) == cfg_start for s in starts)) else 0
        lm_ok = 1
        if lm_set:
            want = BS.config_landmarks(layer, name)
            if want and not (lm_set <= want):
                lm_ok = 0               # 地标硬过滤：观察到的尽头/恶敌必须在声明集合里
        key = (0 if lm_ok else 1, 0 if hit_start else 1, miss)
        if best is None or key < best[0]:
            best = (key, name, cfg_start, miss)
    if best is None:
        return None, None
    _key, name, cfg_start, miss = best
    return name, (cfg_start if miss == 0 else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=None,
                    help="用已有截图。**必须是刚截的**：太旧/不是 runs/shots 里最新的一张会被旧图闸拒绝"
                         "（--allow-stale 显式放行）")
    ap.add_argument("--latest", action="store_true",
                    help="用 runs/shots 里最新的一张（同样过旧图闸）")
    ap.add_argument("--adb", default="", help="显式 adb.exe 路径（默认自动找：缓存→常见路径→PATH）")
    ap.add_argument("--device", default="", help="显式设备序列号，如 127.0.0.1:16384")
    ap.add_argument("--probe", action="store_true",
                    help="只探测截图链路（adb 从哪来/有哪些设备/最新截图多旧），不求解")
    ap.add_argument("--max-age", type=float, default=None,
                    help=f"旧图闸的年龄阈值（秒），默认 {FRESH_SECONDS}")
    ap.add_argument("--allow-stale", dest="allow_stale", action="store_true",
                    help="允许用旧图（复盘用）；会打 ⚠️，结论不得当本局当前状态")
    ap.add_argument("--no-map-check", dest="no_map_check", action="store_true",
                    help="跳过「这张图是不是树海地图」的亮度检查（界面配色异常时才用）")
    ap.add_argument("--layer", type=int, default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--ap", type=int, default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--mode", default=None,
                    help="N16文明开化（默认；内部名「文明开化」）｜普通分队"
                         "（不保证有理想源；不传 --root 即按无算）")
    ap.add_argument("--end-mode", dest="end_mode", default=None,
                    help="全程追猎（默认）｜险路尽头（一/四层最后一步进险路尽头**或险路小径**；"
                         "二层默认只能进险路尽头（四结局=不要，或 要且已获取前置藏品时，"
                         "小径与尽头同分、可作可选终点）；三/五/六层最后一步进险路恶敌）")
    ap.add_argument("--final", default=None,
                    help="终点坐标手报（仅险路尽头终点模式），如 3,0")
    ap.add_argument("--four-ending", dest="four_ending", default=None,
                    metavar="要|不要",
                    help="**二层必答**（截到二层/玩家报二层都要先问）：本局是否要打四结局？"
                         "全程追猎：要=尽头 8；不要=尽头 0、不再必走。"
                         "险路尽头：要→再问 --four-ending-pre；不要→小径=尽头、可作可选终点")
    ap.add_argument("--four-ending-pre", dest="four_ending_pre", default=None,
                    metavar="已获取|未获取",
                    help="二层·险路尽头模式·四结局=要 的必答问题二：是否已获取四结局前置藏品？")
    ap.add_argument("--three-ending", dest="three_ending", default=None,
                    metavar="要|不要",
                    help="**三层必答（仅本层无右区/熄日余径时）**：本局是否打三结局？"
                         "不要→普通逻辑；要→再问 --three-ending-pre")
    ap.add_argument("--three-ending-pre", dest="three_ending_pre", default=None,
                    metavar="已获取|未获取",
                    help="三层·三结局=要 的必答问题二：是否已获取三结局前置收藏品？"
                         "未获取→先行一步 15 分必走点（藏着就列候选坐标）")
    ap.add_argument("--second-ending", dest="second_ending", default=None,
                    metavar="要|不要",
                    help="**五层必答（仅左区出现 3 个「命运所指」时）**：本局是否打二结局？"
                         "不要→常规逻辑、终点=右区命运所指；要→右区命运所指必走（尽量排最后），"
                         "最后一步=左区带徽章的命运所指")
    ap.add_argument("--second-ending-node", dest="second_ending_node", default=None,
                    metavar="c,r",
                    help="五层·二结局=要 时，左区**带徽章**那个「命运所指」的坐标（手报，如 3,1）")
    ap.add_argument("--right-zone", dest="right_zone", default="自动",
                    choices=("自动", "有", "无"),
                    help="三/四/五层右区（熄日余径）有无：默认按格点宽度自动判；"
                         "拿不准时先问玩家「本局是否存在熄日余径？」再传 有/无")
    ap.add_argument("--items", default="")
    ap.add_argument("--grants", default="")
    ap.add_argument("--max-time", type=float, default=240.0)
    ap.add_argument("--explain", action="store_true",
                    help="透传给 route.py：判词前附「转述文案」（【结论】【下一步】【备选】【再看】【风险】），"
                         "2026-10-29 起的标准输出——执行方逐字转达给玩家")
    a = ap.parse_args()

    if a.probe:
        # 环境一行：这个入口最容易撞上「pip 的 Python ≠ 跑脚本的 Python」
        print(f"解释器：{sys.executable}（Python {sys.version.split()[0]}）")
        try:
            import pyenv as PY
            _miss = PY.missing()
            print("依赖：" + ("齐全 ✅" if not _miss
                            else "缺 " + "、".join(_miss) + " ❌ → 跑 `python tools/pyenv.py`"))
            if not PY.have("scipy"):
                print("可选：scipy ❌（滚动阶段「站在带标签节点上」判定会降级，见 pyenv.py）")
        except Exception:
            pass
        AF.describe(a.adb, a.device)
        newest_p, newest_t = newest_shot()
        if newest_p:
            print(f"最新截图：{newest_p}（{_fmt_age(time.time() - newest_t)} 前）"
                  f"{'' if time.time() - newest_t <= (a.max_age or FRESH_SECONDS) else '  ← 已过期，别用'}")
        else:
            print(f"最新截图：runs/shots 里没有（{RUNS}）")
        return 0

    if a.latest:
        newest_p, _t = newest_shot()
        if not newest_p:
            raise SystemExit(f"❌ --latest：{RUNS} 里没有截图")
        a.image = newest_p

    if a.image:
        img = check_image_freshness(a.image, a.max_age, a.allow_stale)
    else:
        img = grab(a.adb, a.device)
        check_image_freshness(img, a.max_age, a.allow_stale)
    age = time.time() - os.path.getmtime(img)
    print(f"图源：{img}（{_fmt_age(age)} 前截的）")
    im = Image.open(img).convert("RGB")

    if not a.no_map_check:
        is_map, mean, p90 = G.map_likeness(img)
        if not is_map:
            why = ("画面太黑（p90 偏低）＝ 还在 Loading / 转场"
                   if p90 < 25.0 else "模拟器现在多半停在战斗 / 结算 / 主菜单 / 开局界面")
            raise SystemExit(
                f"❌ 这张图不是树海地图界面（中央均值 {mean:.0f} / p90 {p90:.0f}；地图应 均值<60 且 p90 在 25~110）\n"
                f"   {why}。\n"
                "   · 回到树海地图那一屏再跑；\n"
                "   · ⛔ 别改拿 runs/shots 里的旧地图截图凑数（旧图 = 别的对局）。\n"
                "   · 确有特殊情况（比如界面配色异常）才用 --no-map-check 跳过此检查。")
        print(f"画面判断：像树海地图（均值 {mean:.0f} / p90 {p90:.0f}）")

    layer = a.layer
    title = ""
    if layer is None:
        layer, title = read_layer(im)
        if layer is None:
            # 认不出层时**先分辨「是不是根本没在地图上」**：Loading/战斗/结算画面也是暗的，
            # 能骗过亮度检查（2026-10-29 实测撞上「OPERATION ISW-NO 急不可耐 / Loading..」）。
            # 这时一句「请手传 --layer」会诱导执行方硬套层号去分析垃圾输入，所以改成先数标签。
            n_labels = len(LO.read_image(img))
            if n_labels < 4:
                raise SystemExit(
                    f"❌ 认不出层（标题读到「{title}」），而且只认出 {n_labels} 个节点标签\n"
                    "   这张图多半**不是树海地图**：Loading / 战斗中 / 结算 / 主菜单。\n"
                    "   · 等回到树海地图那一屏再跑 observe.py；\n"
                    "   · ⛔ 不要手传 --layer 硬套（会把垃圾输入当局面算），更不要翻旧图。")
            raise SystemExit(f"❌ 认不出层（标题读到「{title}」）——图上确实有节点，请手传 --layer")
        print(f"层：{layer}（标题「{title}」）")
    else:
        # --layer 是手传的：仍然读一眼标题做**交叉核对**。标题说的层跟手传不一致，
        # 说明图根本不对（或传错图）——这正是「读旧图」最容易露馅的地方。
        t_layer, title = read_layer(im)
        if t_layer is not None and t_layer != layer:
            print(f"⚠️ 手传 --layer {layer}，但标题「{title}」说的是第 {t_layer} 层"
                  f"——确认这张图是不是当前这局的？")
        elif title:
            print(f"层：{layer}（标题「{title}」核对一致）")

    # 三结局参数只在三层（且无右区）有意义：**读格子之前**先挡「用错层」。
    try:
        R.set_three_ending(a.three_ending)
        R.set_three_ending_pre(a.three_ending_pre)
    except ValueError as exc:
        raise SystemExit(f"❌ {exc}")
    _e3 = R.layer3_scope_errors(layer)
    if _e3:
        raise SystemExit("❌ " + "\n   - ".join(_e3))

    # 五层「二结局」问询（2026-10-31）：先挡「用错层」，是否真有 3 个命运所指要等建格
    try:
        R.set_second_ending(a.second_ending)
    except ValueError as exc:
        raise SystemExit(f"❌ {exc}")
    _e5 = R.layer5_scope_errors(layer)
    if _e5:
        raise SystemExit("❌ " + "\n   - ".join(_e5))

    # 六层有两个完全不同的图（2026-10-30）：
    #   四结局「盲目者所见」5×4；三结局「源流交汇处」6×5（构型名=源流）。
    # 标题能直接分出来；手传 --layer 6 时若标题是源流，也按源流处理。
    _six = "源流" if (layer == 6 and "源流" in (title or "")) else None
    if _six:
        print("六层变体：源流交汇处（三结局，6×5；起点 (0,2)）")

    cols, rows = LO.layer_dims(layer, _six or a.config)

    labels = LO.read_image(img)
    # 兜底：不管层号是怎么来的（自动/手传），图上没节点就不是局面。
    # 狠一点拒绝，比拿一张 Loading 图算出「构型 X、18 分」好得多。
    if len(labels) < 4:
        raise SystemExit(
            f"❌ 这张图上只认出 {len(labels)} 个节点标签——不是可规划的树海地图。\n"
            "   常见原因：还在 Loading / 战斗中 / 结算页 / 主菜单，或画面被遮挡。\n"
            "   · 回到地图那一屏重新截（observe.py 不带 --image 就是现场截）；\n"
            "   · ⛔ 不要手传 --layer / --config 硬套，更不要翻 runs/shots 里的旧图。")
    # 三/四/五层可能有/没有右区（2026-10-30）：按「完整宽度 vs 左区宽度」A/B 选列。
    # 没有右区时左区在屏幕居中，仍按完整宽度套格点会把列顶出地图区（尤其 1596 宽）。
    cols, gx, ginfo = LO.choose_layer_cols([l[0] for l in labels], layer, im.size[0], cfg=a.config)
    if _six:
        # 六层「源流交汇处」不套 3/4/5 层的左右区逻辑：6 列全宽、无右区
        cols = LO.layer_dims(6, "源流")[0]
        gx = sorted(LO.choose_lattice([l[0] for l in labels], cols, center=im.size[0] / 2.0))
        ginfo = {"has_right": False, "bounds_ok": True}
    rows = LO.layer_dims(layer, _six or a.config)[1]
    _has_right = LO.has_right_cols(layer, cols)
    if a.right_zone != "自动" and layer not in (3, 4, 5):
        raise SystemExit(f"❌ --right-zone {a.right_zone} 只对三/四/五层有意义")
    if a.right_zone == "有" and not _has_right:
        raise SystemExit("❌ 你指定本层有右区，但截图按左区宽度拟合（可能只截了左区）——"
                         "重截完整宽度，或改 --right-zone 无")
    if a.right_zone == "无" and _has_right:
        raise SystemExit("❌ 你指定本层无右区，但截图是完整宽度（含右区列）——"
                         "先确认是否已获取四结局前置藏品/截图是否完整")
    # 三层·无右区 → 三结局问询（2026-10-30 玩家新增，强制）。
    # 这里先拦一次，免得白跑一趟 route.py；route.py 里还有同一道闸（其它入口也走它）。
    _ask3 = R.layer3_answer_errors(layer, _has_right)
    if _ask3:
        # 返回码与 route.py 一致：2 = 「先去问玩家」，不是「工具坏了」（1）。
        print("❌ 不可用：" + R.layer3_error_headline(_ask3, "跑 observe")
              + "：\n   - " + "\n   - ".join(_ask3)
              + "\n   · 本题只在**本层无右区（熄日余径）**时问；Q1=要 再问 "
                "Q2「是否已获取三结局前置收藏品？」"
              + "\n   · 参数：--three-ending 要|不要；要 再加 --three-ending-pre 已获取|未获取")
        return 2
    if layer in (3, 4, 5):
        print("右区：有（熄日余径）" if _has_right
              else "右区：无（本层无熄日余径；左区居中，不预留 AP、右区必走点失效）")
    gy = sorted(LO.choose_lattice([l[1] for l in labels], rows, center=0.49 * im.size[1]), reverse=True)
    # 小人锚点自证：最左列只有起点（无标签）时整张图会右移一列。放在地标/起点判据之前，
    # 否则「已探明地标」会读成 (3,0) 这种错列坐标，构型钥匙跟着被带偏（2026-10-29 一层）。
    gx, gy, _align_note = LO.align_lattice(im, labels, gx, gy, cols, rows)
    if _align_note:
        print(_align_note)
    grid = LO.assign(labels, gx, gy, cols, rows)
    # 地标（险路尽头/险路恶敌）从**全图 OCR 标签**直接捕获，不依赖图标检测：
    # 尽头球大、标签比普通格低 50+px，assign 的吸附容差会把它丢掉
    # （2026-10-28 二层 (4,1) 因此漏检 → 地标钥匙空转 → 构型被差分带偏）。
    gy_asc = sorted(gy)
    dx_med = float(np.median(np.diff(gx))) if len(gx) > 1 else 0.0
    dy_med = float(np.median(np.diff(gy_asc))) if len(gy_asc) > 1 else 0.0
    obs_landmark = {tuple(q) for q, t in grid.items()
                    if str(t[0]) in ("险路恶敌", "险路尽头")}
    for lab in labels:
        cx, cy, txt = lab[0], lab[1], str(lab[2])
        if txt not in ("险路恶敌", "险路尽头"):
            continue
        ci = int(np.argmin([abs(cx - g) for g in gx]))
        ri = int(np.argmin([abs(cy - g) for g in gy_asc]))
        if abs(cx - gx[ci]) <= 0.55 * dx_med and abs(cy - gy_asc[ri]) <= 0.55 * dy_med:
            obs_landmark.add((ci, rows - 1 - ri))
    if obs_landmark:
        print(f"已探明地标：{sorted(obs_landmark)}")
    # 起点：候选列表（小人偏半格是常态）。**别把第一个当答案**——
    # 构型定出来之后，若该构型声明的起点在候选里，就用它（2026-10-28 三层偏了一列）。
    cands, on_labeled = LO.detect_start_candidates_ex(im, gx, list(reversed(gy)), labels)
    cand_cells = [(c, rows - 1 - r) for c, r in cands]
    start = cand_cells[0] if cand_cells else None
    print(f"起点候选：{cand_cells[:3]}　格点列 {len(gx)}")
    if on_labeled:
        # 2026-10-29：小人正站在一个**带标签**的节点上 → 这不是进层那一刻（进层起点是空地）。
        # observe.py 是按「进层」口径出方案的（会把起点强行套成构型声明的起点），
        # 滚动阶段必须改走 route.py 的 --start/--visited。
        # 与其给一份错位的方案，不如**停下来说明该用哪条命令**（与输出门禁一致）。
        print(f"  ⚠️ 玩家正站在**带标签**的节点 {cand_cells[0] if cand_cells else '?'} 上"
              f"→ 这是**滚动阶段**，不是进层。")
        print("     observe.py 按进层口径出方案会错位；请改用：")
        print(f"     python tools/route.py 截图 --layer {layer} --config <构型> \\")
        print(f"         --start {cand_cells[0][0]},{cand_cells[0][1]} --ap N "
              f"--visited \"<已走格>\" --root <树根> --items \"...\"")
        raise SystemExit("❌ observe.py 只用于**进层那一刻**；滚动阶段请按上面的 route.py 命令算")

    if _six:
        cfg = _six          # 六层源流：唯一构型，标题已经定死
    elif a.config:
        cfg = a.config
    else:
        cfg, _cfg_start_from_diff = detect_config(layer, cand_cells, grid, obs_landmark)
    if cfg is None:
        raise SystemExit("❌ 定不出构型——请手传 --config")

    # 起点以**构型表**为准：小人偏半格是常态，构型定下来之后起点就基本已知了。
    # 但三/四/五层的构型表**不声明起点**，所以再用两条硬判据从候选里挑：
    #   ① 在该构型的格子集合里；② 是无标签格（起点=空地，不写字）。
    # 三层那次 (1,2) 读到了「曲折密道」→ 被排除，正确落到 (0,2)。
    cfg_start = BS.config_start(layer, cfg)
    cells = {p for e in BS.config_edges(layer, cfg) for p in e} if cfg else set()
    start_warned = False
    if cfg_start:
        if start and tuple(start) != cfg_start:
            print(f"  !! 起点检测 {tuple(start)}，按构型 {cfg} 校正为 {cfg_start}")
        start = cfg_start
    elif cells and start and (tuple(start) not in cells or tuple(start) in grid):
        ok = [q for q in cand_cells if q in cells and q not in grid]
        if ok:
            print(f"  !! 起点检测 {tuple(start)} 不满足「格内 + 无标签」，校正为 {ok[0]}")
            start = ok[0]
        else:
            # 起点是**猜的**：交给 route.py 的 --start 仍要传（否则它自己再猜一次），
            # 但必须带上 --no-learn-start，不许把猜出来的坐标写成构型的硬事实
            start_warned = True
            print(f"  ⚠️ 候选 {cand_cells[:3]} 没有一个满足「格内 + 无标签」，起点可能读错"
                  f"（本次不写入起点表）")
    if cfg_start is None and cells and start:
        ok_multi = [q for q in cand_cells if q in cells and q not in grid]
        if len(ok_multi) > 1:
            # 2026-10-28 四层 L1 事故：角格起点 (0,0) 的小人画在节点**上方**（左侧出画面），
            # 「小人左侧⇒节点在右」的候选序把 (1,1) 排第一并被学习进起点表——污染硬事实。
            # 多候选 = 猜测：照样求解（玩家可当场纠正），但**禁止写入起点表**。
            start_warned = True
            print(f"  ⚠️ {len(ok_multi)} 个候选同时满足「格内+无标签」：{ok_multi[:4]}"
                  f"——起点按序取 {tuple(start)}，本次**不写入起点表**（需玩家确认或查起点表）")
    print(f"构型：{cfg}　起点：{start}")

    # ── 五层·二结局问询（2026-10-31）──────────────────────────────────
    # 判据：左区读到 3 个「命运所指」= 拿了二结局相关藏品 → 必须先问「本局是否打二结局？」。
    # 这里与 route.py 是同一道闸（都走 rules.layer5_answer_errors），提前拦下免得白跑。
    if layer == 5:
        _lm5 = BS.left_max(layer, cols, cfg)
        _se_left = sorted(q for q, t in grid.items()
                          if str(t[0]) == R.SECOND_FATE_NODE and q[0] <= _lm5)
        _se_marker = len(_se_left) >= R.SECOND_FATE_MARKER_COUNT
        _ask5 = R.layer5_answer_errors(layer, _se_marker)
        if _ask5:
            print("❌ 五层的「二结局」问询还没完成——**先问玩家，再跑 observe**：")
            for _e in _ask5:
                print(f"   - {_e}")
            print("   · 参数：--second-ending 要|不要；"
                  "要 时可用 --second-ending-node c,r 锁定带徽章那个")
            return 2
        if _se_marker:
            print("二结局：左区读到 3 个「命运所指」"
                  + "、".join(f"({q[0]},{q[1]})" for q in _se_left)
                  + " → 已按答案透传给 route.py")

    cur_ap = a.ap if a.ap is not None else read_ap(im)
    if cur_ap is None:
        raise SystemExit("❌ 读不出行动力——请手传 --ap")
    print(f"行动力：{cur_ap}")

    cmd = [sys.executable, os.path.join(HERE, "route.py"), img,
           "--layer", str(layer), "--config", str(cfg), "--ap", str(cur_ap),
           "--max-time", str(a.max_time)]
    if start:
        cmd += ["--start", f"{start[0]},{start[1]}"]
    if start_warned:
        cmd += ["--no-learn-start"]      # 起点是猜的 → 别写进 起点表.json
    if a.mode:
        cmd += ["--mode", a.mode]        # 分队模式透传给 route.py（默认 N16文明开化）
    if a.end_mode:
        cmd += ["--end-mode", a.end_mode]  # 终点模式透传（默认全程追猎）
    if a.right_zone != "自动":
        cmd += ["--right-zone", a.right_zone]   # 三/四/五层右区有无（拿不准时问玩家）
    if a.final:
        cmd += ["--final", a.final]      # 手报终点坐标（仅险路尽头模式）
    if a.four_ending:
        cmd += ["--four-ending", a.four_ending]   # 二层四结局问询（必答）
    if a.four_ending_pre:
        cmd += ["--four-ending-pre", a.four_ending_pre]  # 二层·险路尽头·Q2
    if a.three_ending:
        cmd += ["--three-ending", a.three_ending]        # 三层·三结局 Q1（仅无右区）
    if a.three_ending_pre:
        cmd += ["--three-ending-pre", a.three_ending_pre]  # 三层·三结局 Q2
    if a.second_ending:
        cmd += ["--second-ending", a.second_ending]        # 五层·二结局 Q1（3 个命运所指时必答）
    if a.second_ending_node:
        cmd += ["--second-ending-node", a.second_ending_node]  # 五层·带徽章命运所指坐标
    if a.root:
        cmd += ["--root", a.root]
    if a.items:
        cmd += ["--items", a.items]
    if a.grants:
        cmd += ["--grants", a.grants]
    if a.explain:
        cmd += ["--explain"]             # 转述文案透传（2026-10-29 起 SKILL.md 标准输出）
    print("\n" + "─" * 60)
    sys.stdout.flush()
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
