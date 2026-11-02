#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""label_ocr.py — 读节点下方的中文字，判定节点类型（RapidOCR 版）

为什么是这条路
--------------
2026-10-27：四层的深青「未知的诡秘」和暗背景色度几乎相同，色度规则怎么调都漏检 1/3。
但**每个节点下方都写着类型**——直接读字。

试过但放弃的方案：自己用系统字体做模板匹配（`tools/label_ocr_template.py` 的思路）。
实测 2/8，因为游戏字的宽高比、描边、抗锯齿和字体渲染都对不齐，且每个候选词字数不同，
归一化方式一变结论就变。**有成熟 OCR 就不要自己写。**

现在的做法
----------
1. `rapidocr-onnxruntime` 对**整张截图**跑一次 OCR（约 3 秒），拿到所有文字 + 方框
2. 按方框中心把文字分配给最近的格子
3. 用编辑距离把 OCR 结果**校正**到已知词表（`未知的凶房` → `未知的凶戾`，OCR 常把「戾」认成「房」）
4. 没文字 = 空地 / 起点 / 汇点（这几类本来就不写字）

用法
----
  python label_ocr.py 截图.png --cols 12 --rows 5 --x0 290 --y0 620 --dx 120 --dy 115
  python label_ocr.py 截图.png --cols 11 --rows 5        # 不给原点则自动标定
"""
import argparse
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 版本戳：打印在输出首行。别的会话若跑的是旧版（没有这一行），一眼就能看出来。
# 2026-10-27c 修了：行序翻转、标签偏移写死 70px、暗背景误判、定点覆盖并入输出
# **层数决定网格，是硬事实，不是可调的猜测。**
# 自标定只负责「格点在哪」，绝不能改「几列几行」——2026-10-27 就是因为让自标定
# 覆盖了维度，把一层报成了 6 列 3 行。
LAYER_DIMS = {1: (5, 3), 2: (5, 4), 3: (11, 5), 4: (12, 5), 5: (14, 5), 6: (5, 4)}
# 五层有 L8 变体（13 列，x 0-12），这是唯一允许的例外
LAYER_VARIANT = {(5, 13): (13, 5)}

# 六层有两个**完全不同的图**（2026-10-30 玩家口径）：
#   四结局「盲目者所见」= 5×4（默认 LAYER_DIMS[6]）
#   三结局「源流交汇处」= **6×5**，cfg 名用 `源流`
SIX_VARIANT_DIMS = {"源流": (6, 5)}


def layer_dims(layer, cfg=None):
    """该层该构型的（列, 行）。六层「源流」= 6×5，其余走 LAYER_DIMS。

    2026-10-30：六层三结局（源流交汇处）是 6 列 × 5 行、起点 (0,2)；
    四结局（盲目者所见）是 5 列 × 4 行、起点 (2,0)。**两张图不能混用**。
    """
    if int(layer) == 6 and cfg is not None and "源流" in str(cfg):
        return SIX_VARIANT_DIMS["源流"]
    return LAYER_DIMS[int(layer)]
# 左区最右列（右区不属于构型表，自检时必须排除，否则会误报「越界」）
LEFT_MAX = {1: 4, 2: 4, 3: 6, 4: 7, 5: 9, 6: 5}

VERSION = "2026-10-29b"

# 黑流树海会出现的节点标签
CANDIDATES = [
    "未知的诡秘", "未知的凶戾", "紧急作战", "作战", "不期而遇", "得偿所愿",
    "先行一步", "曲折密道", "羽瞰点", "诡意行商", "秘境行商", "险路尽头",
    "险路恶敌", "失与得", "安全的角落", "应急助力", "命运所指", "居民据点",
    "误入奇境", "狭路相逢", "险路小径",
]

_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            # 2026-10-29：缺 OCR 依赖时给可执行的安装命令，不再裸 traceback。
            # 2026-10-30：**先确认是哪个解释器**——`pip` 的 Python ≠ 跑脚本的 Python
            # （事故：Dsh 自带 3.12 缺依赖，系统 3.11 齐全，却被误判成「本机缺 OCR」）。
            hint = ""
            try:
                import pyenv as PY
                hint = PY.alternative_hint(["rapidocr_onnxruntime", "onnxruntime"])
            except Exception:
                pass
            raise SystemExit(
                "❌ 缺少 OCR 依赖 `rapidocr_onnxruntime`（label_ocr 必需）。\n"
                f"   **本解释器**：{sys.executable}（Python {sys.version.split()[0]}）\n"
                f"   安装（装到**这个**解释器）：{sys.executable} -m pip install "
                "rapidocr_onnxruntime onnxruntime\n"
                + (hint + "\n" if hint else "")
                + "   · 只是手工读坐标：用 --start/--root 手报（见 23-操作流程）。\n"
                "   · ⛔ 别把「这个解释器缺」说成「本机缺」：先跑 `python tools/pyenv.py`。\n"
                f"   原始错误：{exc}") from None
        _ENGINE = RapidOCR()
    return _ENGINE


def _dist(a, b):
    """编辑距离，用于把 OCR 错字校正到词表。"""
    la, lb = len(a), len(b)
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, lb + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return dp[lb]


def snap(text, cands=CANDIDATES, maxd=1):
    """把 OCR 文本校正到词表里最接近的一项；差太远就原样返回。"""
    if text in cands:
        return text, 0
    best = (text, 99)
    for t in cands:
        if abs(len(t) - len(text)) > maxd:
            continue
        d = _dist(text, t)
        if d < best[1]:
            best = (t, d)
    return best if best[1] <= maxd else (text, best[1])


def read_image(path, min_conf=0.6):
    """整图 OCR → [(cx, cy, text, conf)]（已校正词表）。"""
    res, _ = engine()(path)
    out = []
    for item in (res or []):
        box, txt, conf = item
        if conf is None or conf < min_conf:
            continue
        raw = txt.strip()
        # 行动力徽章上的数字会被当成文字读出来（如「7」），必须丢掉
        if len(raw) < 2 or raw.isdigit():
            continue
        xs = [q[0] for q in box]
        ys = [q[1] for q in box]
        cx, cy = sum(xs) / 4.0, sum(ys) / 4.0
        # 游戏里的标签会带引号和空格：「居民」据点 / “居民” 据点 / 「居民」 据点
        # 直接和词表比对必然对不上，会被当成"词表外文字"丢掉（2026-10-27 二层漏读 (2,2)）
        norm = raw
        for ch in '「」“”"\'‘’ \u3000':
            norm = norm.replace(ch, "")
        snapped, d = snap(norm if norm else raw)
        # 词表之外的一律丢掉：YOU ARE HERE / [HUMAN] / 行动力 这些不是节点标签，
        # 留着会把行聚类拉偏（一层的 YOU ARE HERE 曾把整行中心从 270 拖到 300）
        if snapped not in CANDIDATES:
            continue
        out.append([cx, cy, snapped, float(conf), raw])
    # 只保留地图区域内的文字：顶部标题/等级栏、底部资源栏都会污染行聚类
    if out:
        from PIL import Image as _I
        W, H = _I.open(path).size
        out = [t for t in out if 0.15 * H <= t[1] <= 0.88 * H and 0.05 * W <= t[0] <= 0.96 * W]
    # 标签被别的图标挡住时会断成两截（如「未」+「秘」），拼接后再试一次
    merged = []
    used = [False] * len(out)
    for i in range(len(out)):
        if used[i]:
            continue
        xi, yi, ti, ci, ri = out[i]
        if len(ri) < 4:
            for j in range(len(out)):
                if j == i or used[j]:
                    continue
                xj, yj, tj, cj, rj = out[j]
                if abs(yj - yi) < 18 and 0 < xj - xi < 95:
                    cat = ri + rj if xi < xj else rj + ri
                    s, d = snap(cat)
                    if d == 0:
                        merged.append([(xi + xj) / 2, (yi + yj) / 2, s, min(ci, cj), cat])
                        used[i] = used[j] = True
                        break
        if not used[i]:
            merged.append([xi, yi, ti, ci, ri])
    return merged


def choose_lattice(vals, want, center=None):
    """模块级均匀格点搜索（与 auto_lattice 内部的 uniform_axis 同算法）。

    单独暴露出来，是为了让 build_scenario.py 能在不进入 main() 的情况下复用。

    center（2026-10-29b 新增）：屏幕/地图中心的像素坐标（x 轴传 W/2，y 轴传 0.49*H）。
    作用：**噪声级平局判决**。当「最左列/最右列没有标签」（起点列天生无字）时，
    「最左标签=第 0 列」和「最左标签=第 1 列」两套格点的代价只差标签抖动的几个像素
    （2026-10-29 三层实测 18 vs 22，差 0.13px/标签），旧代码按遍历顺序取前者 → 整图偏一列，
    只能靠小人锚点自愈补救。现在代价差在噪声带内（平均每标签 ≤2px）时，
    取**格点中心更接近 center** 的那套。两层实测：一层 [548..1372] 中心 960、
    三层 [356..1566] 中心 961，都精确落在 1920 宽的屏幕中心上，判据可靠。
    若地图真的不居中，错判也只发生在「标签自己分不出」的平局里，小人自愈仍在兜底。
    """
    v = sorted(vals)
    lo, hi = v[0], v[-1]
    cands = []
    for dx in range(60, 271):
        for anchor in (lo, hi):
            if anchor == lo:
                grid = [lo + dx * i for i in range(want)]
            else:
                grid = [hi - dx * (want - 1 - i) for i in range(want)]
            if grid[-1] < lo - 1 or grid[0] > hi + 1:
                continue
            cost = sum(min(abs(y - g) for g in grid) for y in v)
            cost += 40 * sum(1 for y in v if min(abs(y - g) for g in grid) > 0.45 * dx)
            cands.append((cost, grid))
    if not cands:
        return []
    # 稳定排序 + 严格小于 → 与旧版「首个最小代价胜出」完全一致（不传 center 时零行为变化）
    cands.sort(key=lambda t: t[0])
    best_cost = cands[0][0]
    if center is not None:
        eps = 2.0 * max(1, len(v))      # 平均每标签 2px = 抖动级差距，不算真差异
        tied = [g for c, g in cands if c <= best_cost + eps]
        if len(tied) > 1:
            tied.sort(key=lambda g: abs(sum(g) / len(g) - center))
            return tied[0]
    return cands[0][1]


def choose_layer_cols(xs, layer, width, cfg=None):
    """三/四/五层：在「完整宽度」与「只有左区」之间选列数，并拟合 gx。

    2026-10-30 玩家口径：**未获取四结局前置藏品时，三/四/五层没有右区**，
    左区直接在屏幕/截图里居中。此时若仍按完整宽度（11/12/14 列）套格点：
      · 1920 宽可能勉强不越界，但格点中心偏右；
      · 1596 宽会直接顶出地图区（`board_read` 报「格点越出地图区域」）。
    所以这里把「完整宽度」和「左区宽度（LEFT_MAX+1，含/不含缝列）」都拟一遍，
    用「是否越界 + 标签残差 + 格点中心是否贴近屏幕中心」三条件选。

    返回 `(cols, gx, info)`；`info["has_right"]` = 选中的列数是否达到完整宽度
    （用于判定本层该不该有右区；**不是**看右区节点有没有被 OCR 到）。
    """
    layer = int(layer)
    xs = [float(x) for x in xs]
    rows = LAYER_DIMS[layer][1]
    if not xs or layer not in (3, 4, 5):
        cols = LAYER_DIMS[layer][0]
        gx = sorted(choose_lattice(xs, cols, center=width / 2.0)) if xs else []
        return cols, gx, {"has_right": layer in (3, 4, 5), "bounds_ok": True,
                          "res": 0.0, "cols": cols, "candidates": []}

    if layer == 5:
        if cfg and str(cfg).upper() == "L8":
            fulls, lefts = [13], [9, 10]
        else:
            fulls, lefts = [14, 13], [9, 10]
    else:
        fulls = [LAYER_DIMS[layer][0]]
        lefts = [LEFT_MAX[layer] + 1, LEFT_MAX[layer] + 2]

    cands = []
    for cols in sorted(set(fulls + lefts)):
        gx = sorted(choose_lattice(xs, cols, center=width / 2.0))
        if len(gx) != cols:
            continue
        res = sum(min(abs(x - g) for g in gx) for x in xs) / len(xs)
        bounds_ok = gx[0] >= 0.04 * width and gx[-1] <= 0.96 * width
        ctr = abs((gx[0] + gx[-1]) / 2.0 - width / 2.0)
        cands.append({"cols": cols, "gx": gx, "res": res,
                      "bounds_ok": bounds_ok, "ctr": ctr})
    if not cands:
        cols = LAYER_DIMS[layer][0]
        return cols, [], {"has_right": True, "bounds_ok": False,
                          "res": 0.0, "cols": cols, "candidates": []}

    pool = [c for c in cands if c["bounds_ok"]] or cands
    best = min(c["res"] for c in pool)
    # 标签残差差 2px 以内算同一档（标签抖动级），再按格点中心贴近屏幕中心选。
    near = [c for c in pool if c["res"] <= best + 2.0]
    pick = min(near, key=lambda c: (c["ctr"], c["cols"]))
    info = {
        "has_right": pick["cols"] > LEFT_MAX[layer] + 2,
        "bounds_ok": pick["bounds_ok"],
        "res": round(pick["res"], 2),
        "cols": pick["cols"],
        "candidates": [(c["cols"], round(c["res"], 1), c["bounds_ok"], round(c["ctr"], 1))
                       for c in cands],
    }
    return pick["cols"], pick["gx"], info


def has_right_cols(layer, cols):
    """三/四/五层：按选中的列数判断本层是否**有右区**（熄日余径）。

    右区存在 ⇔ 未获取四结局前置藏品 **不**成立；截图里左区居中、没有右区时，
    格点会选左区宽度（三层 7/8、四层 8/9、五层 9/10），到不了完整宽度（11/12/13/14）。
    """
    return int(layer) in (3, 4, 5) and int(cols) > LEFT_MAX.get(int(layer), 99) + 2


def lattice_residual(labels, resp, gx, gy):
    """用「标签正上方的图标」校验格点：图标才是节点的真实坐标。

    列距估大/估小是**最隐蔽**的错法——前几列还对得上，越往后偏得越多，
    于是 (1,1) 被读成 (2,1) 这种「整体错一列」（2026-10-27 二层）。
    只看标签是发现不了的（标签自己就是按错误的列聚出来的），必须拿图标验。
    """
    rs = []
    H, W = resp.shape
    for cx, cy, _t, _c, _raw in labels:
        x0, x1 = int(max(0, cx - 60)), int(min(W, cx + 61))
        y0, y1 = int(max(0, cy - 150)), int(min(H, cy - 8))
        if y1 <= y0 or x1 <= x0:
            continue
        win = resp[y0:y1, x0:x1]
        if win.size == 0:
            continue
        iy, ix = np.unravel_index(int(np.argmax(win)), win.shape)
        if win[iy, ix] < 8:
            continue
        icx = x0 + ix
        c = min(range(len(gx)), key=lambda k: abs(gx[k] - icx))
        rs.append(icx - gx[c])
    return rs

_WARNED = set()


def _warn_missing_scipy(exc):
    """scipy 是**运行时**依赖（不是「仅建表时用」）：缺了会静默关掉「站在带标签节点上」判定。

    2026-10-30 事故：缺 scipy → `except Exception: pass` 把 ImportError 吞了 →
    滚动阶段起点判定退化（玩家站在行商/作战上时会被罚到旁边的空地），
    `selftest_lattice` 10/11 失败，看起来像代码坏了。
    现在**缺一次打一次 ⚠️**（每进程一次），并给出解释器与安装/换解释器的方法。
    """
    if "scipy" in _WARNED:
        return
    _WARNED.add("scipy")
    msg = ["⚠️ 缺 scipy：滚动阶段「玩家站在带标签节点上」的起点判定**已关闭**"
           "（起点可能偏格，尤其玩家站在行商/作战上时）。",
           f"   本解释器：{sys.executable}（Python {sys.version.split()[0]}）",
           f"   安装：{sys.executable} -m pip install scipy"]
    try:
        import pyenv as PY
        hint = PY.alternative_hint(["scipy"])
        if hint:
            msg.append(hint)
    except Exception:
        pass
    msg.append("   或先跑 `python tools/pyenv.py` 选依赖齐全的解释器。")
    print("\n".join(msg), file=sys.stderr)


def _sprite_evidence(im, gx, gy, labels=None):
    """小人像素证据 + 「玩家是否正站在一个**带标签**的节点上」。

    返回 `(xs, ys, on_labeled_node)`；暖色像素不足 40 个返回 None。

    `on_labeled_node` 的判据（2026-10-29 新增，修「玩家在 (2,4) 秘境行商被识别成 (3,3)」）：
    **每一块暖色证据的正下方都有 OCR 文字** → 这团暖色是「节点图标 + 小人」的融合体，
    玩家正站在一个有标签的节点上（行商 / 作战 / 紧急作战 / 诡秘…）。
    进层的起点是不写字的空地，**不会**出现这种情况，所以该标志只在滚动阶段为真。

    实测（5 张真截图）：只有站在带标签节点上的那张 keep=0，其余都留有干净小人块
    （keep = 26 / 38 / 48 / 103）。
    """
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    H, W = R.shape
    m = (R > 150) & (G > 120) & (B < 120) & (R - B > 60)
    m[: int(0.12 * H), :] = False
    m[int(0.90 * H):, :] = False
    m[:, : int(0.03 * W)] = False
    ys, xs = np.nonzero(m)
    if len(xs) < 40:
        return None

    on_labeled = False
    # 2026-10-28 玩家口径（四层 L1 事故）：小人**必定**在起点左侧、且**没有标签**；
    # 节点图标（曲折密道等）同样过亮色掩码、也亮——不剔除的话中位数会被拖到节点上。
    # 判据：亮块**下方 0.05~0.95 格内有 OCR 文字 = 节点**，整块从证据里剔除。
    gxs0, gys0 = list(gx), list(gy)
    cw0 = (gxs0[1] - gxs0[0]) if len(gxs0) > 1 else 90.0
    ch0 = (gys0[1] - gys0[0]) if len(gys0) > 1 else 90.0
    if labels:
        try:
            from scipy import ndimage
            lab_img, n_blob = ndimage.label(m)
            if n_blob:
                keep = np.zeros_like(m)
                tagged_n = 0
                for i in range(1, n_blob + 1):
                    bys, bxs = np.nonzero(lab_img == i)
                    if len(bxs) < 8:
                        continue                    # 噪点小块
                    bx, by = float(bxs.mean()), float(bys.mean())
                    tagged = any(abs(lb[0] - bx) < 0.7 * cw0
                                 and 0.05 * ch0 < lb[1] - by < 0.95 * ch0
                                 for lb in labels)
                    if not tagged:
                        keep[bys, bxs] = True       # 下方无文字 → 可能是小人
                    else:
                        tagged_n += 1
                if keep.sum() >= 12:
                    ys, xs = np.nonzero(keep)       # 全被剔除则退回原证据（别空转）
                    # 注意：小人本体很小（四层实测仅 ~36 个暖色像素），阈值别设回 40。
                elif tagged_n:
                    # 一块都没留下、但确实有带标签的亮块 → 暖色证据**全部**是节点图标
                    # （含融进去的小人）→ 玩家正站在那个节点上。
                    # 此时「起点不写字」这条判据不能用：它会把正确答案重罚掉。
                    on_labeled = True
        except ImportError as exc:
            # 2026-10-30：scipy 缺失**不许静默**（旧代码 `except Exception: pass` 把它吞了，
            # 于是「站在带标签节点上」判定悄悄关闭，还让自检看起来像代码坏了）。
            _warn_missing_scipy(exc)
        except Exception:
            pass                                    # 其它异常 → 退回原行为
    return xs, ys, on_labeled


def _sprite_pixels(im, gx, gy, labels=None):
    """小人（暖黄白）的像素证据，返回 (xs, ys)；不足 40 个返回 None。

    从 detect_start_candidates 抽出来共用：小人是**唯一「必定站在节点上」的像素证据**，
    格点对齐要靠它（2026-10-29 一层：最左列只有起点、没有文字标签，5 列被套在 4 列
    标签上整体右移一列；标签→图标残差仍然为 0，所以那种校验发现不了）。
    """
    ev = _sprite_evidence(im, gx, gy, labels)
    if ev is None:
        return None
    return ev[0], ev[1]


def _axis_shift(value, coords, spacing):
    """value 离最近格点超过 0.85 格 → 返回整格平移量（列/行错位自愈）。

    正常的小人绘制偏移是 0.3~0.6 格（站在节点左侧/右侧），不能当错位处理；
    1 格以上的偏差只可能是「整列被漏掉/多算」。
    """
    if not coords or spacing <= 0:
        return 0.0
    nearest = min(coords, key=lambda g: abs(g - value))
    off = (value - nearest) / spacing
    if abs(off) <= 0.85 or abs(off) > 2.4:
        return 0.0
    return float(round(off)) * spacing


def _sprite_clusters(xs, ys, cell):
    """把暖色像素按距离聚成簇（小人常断成 2–3 个小块：帽/身/脚）。

    返回 [(n, x0, x1, y0, y1, cx, cy), …]，按像素数降序。
    不能用连通域：实测小人会被拆成 12+9+13 三个互不相邻的域，
    按域取会得出三个"小簇"而不是一个小人。这里用**单链聚类**（间距 < 0.45 格并簇）。
    """
    pts = list(zip(xs, ys))
    if not pts:
        return []
    if len(pts) > 4000:                     # 证据面异常大（大片误检）→ 抽样防卡死
        step = len(pts) // 4000 + 1
        pts = pts[::step]
    thr = 0.45 * abs(cell)
    cl = []                                 # [n, x0, x1, y0, y1, sx, sy]
    for x, y in pts:
        best, bd = None, thr
        for c in cl:
            dx0 = max(c[1] - x, 0, x - c[2])
            dy0 = max(c[3] - y, 0, y - c[4])
            d = (dx0 * dx0 + dy0 * dy0) ** 0.5
            if d <= bd:
                best, bd = c, d
        if best is None:
            cl.append([1, x, x, y, y, float(x), float(y)])
        else:
            best[0] += 1
            best[1] = min(best[1], x); best[2] = max(best[2], x)
            best[3] = min(best[3], y); best[4] = max(best[4], y)
            k = best[0]
            best[5] += (x - best[5]) / k; best[6] += (y - best[6]) / k
    out = [tuple(c) for c in cl]
    out.sort(key=lambda t: -t[0])
    return out


def _sprite_plausible(c, gx, gy, dx, dy):
    """这簇暖色像素像不像「站在节点上的小人」。

    小人本体极小（121px 格距下实测 34px；207px 格距下按面积放大约百来 px），
    盒子 ~30px 见方，且**必定落在某个节点行上**（横可以偏 0.3–0.6 格，竖不能偏）。
    违反任意一条 = 图标/装饰/噪声，不许当锚点（2026-10-29b）。
    """
    n, x0, x1, y0, y1, cx, cy = c
    dx, dy = abs(dx), abs(dy)
    if not (16 <= n <= max(120.0, 3.0 * dx)):
        return False                        # 太小=噪点，太大=图标（金色行商/得偿所愿一大坨）
    if (x1 - x0) > 0.55 * dx or (y1 - y0) > 0.60 * dy:
        return False
    if min(abs(cy - g) for g in gy) > 0.45 * dy:
        return False                        # 不在任何节点行上 → 不是站在节点上
    return True


def align_lattice(im, labels, gx, gy, cols, rows):
    """用小人像素把整列/整行平移回正确位置，返回 (gx, gy, note)。

    note 为空串 = 没动。判据：小人**必定站在节点上**，到最近格点列的距离不可能
    超过约 0.6 格；超过 0.85 格只能是整列错位——最常见的是最左/最右列没有文字
    标签，choose_lattice 只按标签聚类时把它漏掉，于是整张图平移一列。

    2026-10-29b 加固（三层 19:02 截图触发「平移 x-121」被当成 bug 上报的那次——
    平移本身是对的，但流程无法自证）：
      1. 小人证据先聚簇并校验（大小 / 盒尺寸 / 是否落在节点行上），
         不合格的单簇直接不平移，不再拿裸中位数赌；
      2. 合格簇 ≥2 处 = 证据歧义 → **拒绝平移**并大声说明（宁可让人核对，不赌）；
      3. note 自报「平移后小人距格心的残差 + 哪侧边缘列无标签」，
         执行方和玩家不用复跑就能确认这次平移可不可信。
    """
    gx, gy = list(gx), list(gy)
    from PIL import Image as _Img
    pil = _Img.open(im).convert("RGB") if isinstance(im, str) else im
    sp = _sprite_pixels(pil, gx, list(reversed(gy)), labels)
    if sp is None:
        return gx, gy, ""
    xs, ys = sp
    dx = float(np.median(np.diff(sorted(gx)))) if len(gx) > 1 else 120.0
    dy = float(np.median(np.diff(sorted(gy)))) if len(gy) > 1 else 120.0
    clusters = [c for c in _sprite_clusters(xs, ys, max(dx, abs(dy)))
                if _sprite_plausible(c, gx, gy, dx, dy)]
    if not clusters:
        return gx, gy, ""                   # 证据不合格（噪点/图标/装饰）→ 不动
    if len(clusters) > 1:
        spots = " ".join(f"({c[5]:.0f},{c[6]:.0f})" for c in clusters[:4])
        return gx, gy, (f"格点对齐：发现 {len(clusters)} 处小人级暖色证据（{spots}）"
                        f"→ 证据歧义，**不平移**，请人工核对起点/格点")
    _n, _x0, _x1, _y0, _y1, sx, sy = clusters[0]
    shx = _axis_shift(sx, sorted(gx), dx)
    shy = _axis_shift(sy, sorted(gy), dy)
    if not shx and not shy:
        return gx, gy, ""
    nx = [x + shx for x in gx]
    ny = [y + shy for y in gy]
    W, H = pil.size
    if min(nx) < 0.02 * W or max(nx) > 0.98 * W or min(ny) < 0.05 * H or max(ny) > 0.95 * H:
        return gx, gy, ""          # 平移出界 → 不动，让常规校验去报错
    nearest = min(nx, key=lambda g: abs(g - sx))
    post = abs(sx - nearest) / dx if dx else 0.0
    hint = ""
    if labels:
        dl = min((abs(lb[0] - nx[0]) for lb in labels), default=1e9)
        dr = min((abs(lb[0] - nx[-1]) for lb in labels), default=1e9)
        if dl > 0.5 * dx >= dr:
            hint = "（最左列无标签）"
        elif dr > 0.5 * dx >= dl:
            hint = "（最右列无标签）"
        elif dl > 0.5 * dx and dr > 0.5 * dx:
            hint = "（两侧边缘列都无标签）"
    return nx, ny, (f"格点对齐：标签格点疑似{hint}整体偏 {abs(sx - min(sorted(gx), key=lambda g: abs(g - sx))) / dx:.2f} 格"
                    f" → 小人锚点整格平移 x{shx:+.0f} y{shy:+.0f}"
                    f"（平移后小人距格心 {post:.2f} 格 ✓）")


def detect_start_candidates(im, gx, gy, labels=None):
    """定位起点小人，返回**按可能性排序的候选格** [(c, r), …]。

    这是 `detect_start_candidates_ex` 的薄包装（只要候选）。要看「玩家是否站在
    带标签节点上」请用 `_ex` 版本——**所有入口的「必须无标签」过滤都应据此关闭**。
    """
    return detect_start_candidates_ex(im, gx, gy, labels)[0]


def detect_start_candidates_ex(im, gx, gy, labels=None):
    """定位起点小人，返回 `(候选格列表, on_labeled_node)`。

    为什么不是一个答案
    ------------------
    小人画在节点**左侧**，所以「小人像素 → 节点格」本身就有一个半格左右的横向偏差。
    2026-10-28 三层那次：小人偏了一列，检测出 (1,2)，而 (1,2) 那格是**曲折密道**
    （有标签），真实起点是 (0,2) —— 单答案函数没有任何自证能力，只能靠人发现。

    这里改成给候选 + 打分：
      1. 小人像素的**中位数**和**包围盒中心**分别套格，得两个候选列；
      2. 再加左右各一列、上下各一行（小人可能画在节点左侧**也可能右侧**，
         2026-10-28 那次就是偏右一列）。**方向不做假设**；
      3. **有标签的格子重罚**：起点本身是空地/无标签，若候选格上读到了文字
         （如「曲折密道」），几乎一定是偏了 —— 这条正好能拦住三层那次的错。
         ⛔ 但**只在进层阶段成立**：玩家走到 (2,4) 秘境行商后再截图，他正站在一个
         **带标签**的节点上，这条罚则会把正确答案重罚 +10、反而选中旁边的空地格
         （2026-10-29 实测：真实 (2,4) 几何分 0.037 却因 +10 输给几何分 5.409 的 (3,3)）。
         所以 `_sprite_evidence` 报告 `on_labeled_node` 时，**本条罚则关闭**。
    调用方（observe/route）应拿候选去和构型表核对，**不要盲取第一个**。
    """
    ev = _sprite_evidence(im, gx, gy, labels)
    if ev is None:
        return [], False
    xs, ys, on_labeled = ev

    cx_med, cy_med = float(np.median(xs)), float(np.median(ys))
    cx_box = float((xs.min() + xs.max()) / 2.0)
    cy_box = float((ys.min() + ys.max()) / 2.0)

    def col_at(cx, extra=0):
        c = next((i for i, x in enumerate(gx) if x >= cx - 6), None)
        if c is None:
            c = len(gx) - 1
        return min(len(gx) - 1, max(0, c + extra))

    def row_at(cy, extra=0):
        r = min(range(len(gy)), key=lambda i: abs(gy[i] - cy))
        return min(len(gy) - 1, max(0, r + extra))

    cand = []
    for cx in (cx_med, cx_box):
        for ex_c in (-1, 0, 1):        # 左右都试：小人可能在节点左侧，也可能右侧
            for cy in (cy_med, cy_box):
                for ex_r in (0, 1, -1):
                    q = (col_at(cx, ex_c), row_at(cy, ex_r))
                    if q not in cand:
                        cand.append(q)

    # 打分：**离小人像素越近越可能是它站的格**（这是主证据），
    # 有标签的格子重罚（起点=空地，不写字），越晚生成的候选轻微降权。
    box = {}
    if labels:
        for lb in labels:
            box[(round(lb[0]), round(lb[1]))] = lb[2] if len(lb) > 2 else ""
    gxs, gys = list(gx), list(gy)
    cw = (gxs[1] - gxs[0]) if len(gxs) > 1 else 90.0
    ch = (gys[1] - gys[0]) if len(gys) > 1 else 90.0
    # 小人画在节点左/右侧，横偏差 ≈ 0.3~0.6 格；行应该基本对齐
    cx_ref = (cx_med + cx_box) / 2.0
    cy_ref = (cy_med + cy_box) / 2.0

    def score(q):
        c, r = q
        dx = (gxs[c] - cx_ref) / cw
        dy = (gys[r] - cy_ref) / ch
        s = dx * dx * 2.0 + dy * dy * 3.0         # 行偏比列偏更该罚
        s += 0.01 * cand.index(q)                 # 稳定偏好
        if box and not on_labeled:
            # on_labeled=True（玩家正站在带标签节点上）时本罚则**整条关闭**：
            # 否则正确答案自己被罚 +10，反而输给旁边的无标签格（2026-10-29 事故）。
            x0, x1 = gxs[c] - cw * 0.5, gxs[c] + cw * 0.5
            y0, y1 = gys[r] - ch * 0.5, gys[r] + ch * 0.5
            for (lx, ly) in box:
                if x0 <= lx <= x1 and y0 <= ly <= y1:
                    s += 10.0                     # 这格有文字 → 本不该是起点
                    break
        return s

    return sorted(cand, key=score), on_labeled


def detect_start(im, gx, gy, labels=None):
    """定位起点小人，返回它所在的最优格 (c, r)。

    小人是**黄白色**的，画在它所在节点的**左侧**——不能直接拿小人的像素坐标当节点。
    真正的定位靠 `detect_start_candidates`，这里只取第一名；
    **需要可靠时必须用候选列表**（见那个函数的说明）。
    """
    cand = detect_start_candidates(im, gx, gy, labels)
    return cand[0] if cand else None
def pair_nodes(labels, resp, max_up=140, half_w=55):
    """把每个标签和它**正上方**的图标配成一对，返回 [(node_x, node_y, label, ok)]。

    标签一定在节点下方，但偏移量随层不同（一层 85px、五层 25px）。
    只按标签自己的 y 聚行，一旦行偏移估计差了，标签就会挂到**上一行**的节点上
    ——2026-10-27 新会话把 (3,0) 读成未知的诡秘、(3,1) 读成空地，就是差了一整行。

    这里改成：在每个标签正上方的一竖条里找最强的图标响应，用它的位置当节点坐标。
    找不到（响应太弱）就退回「标签上方固定偏移」，并标记 ok=False 供调用方告警。
    """
    out = []
    H, W = resp.shape
    for cx, cy, txt, conf, raw in labels:
        x0, x1 = int(max(0, cx - half_w)), int(min(W, cx + half_w + 1))
        y0, y1 = int(max(0, cy - max_up)), int(min(H, cy - 10))
        if y1 <= y0 or x1 <= x0:
            out.append((cx, cy - 45, txt, False))
            continue
        win = resp[y0:y1, x0:x1]
        if win.size == 0:
            out.append((cx, cy - 45, txt, False))
            continue
        iy, ix = np.unravel_index(int(np.argmax(win)), win.shape)
        v = float(win[iy, ix])
        if v >= 10:
            out.append((x0 + ix, y0 + iy, txt, True))
        else:
            out.append((cx, cy - 45, txt, False))
    return out


def auto_lattice(labels, cols, rows, H=None, center_x=None, center_y=None):
    """**从标签位置本身推格点**，不依赖外部传参，也不依赖 fit_lattice。

    每个节点下面都有一行字，所以标签的中心 x 就落在列上、y 就落在行上。
    先按最近邻间距的一半做聚类，如果簇数和期望的列数/行数不符，就二分调整间距阈值。

    center_x / center_y（2026-10-29b）：屏幕中心与地图区中心（像素），
    透传给 uniform_axis 做噪声级平局判决（见 choose_lattice 的说明）。

    返回 (gx, gy)：gx 是各列中心，gy 是各行中心（**自上而下**）。
    失败返回 (None, None)。
    """
    if not labels:
        return None, None
    xs = sorted(l[0] for l in labels)
    ys = sorted(l[1] for l in labels)

    def median(v):
        v = sorted(v)
        n = len(v)
        return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])

    def spread(v):
        d = [b - a for a, b in zip(v, v[1:]) if (b - a) > 8]
        return median(d) if d else 1.0

    def cluster(vals, gap):
        out, cur = [], [vals[0]]
        for t in vals[1:]:
            if t - cur[-1] <= gap:
                cur.append(t)
            else:
                out.append(sum(cur) / len(cur)); cur = [t]
        out.append(sum(cur) / len(cur))
        return out

    def fit(vals, want):
        """二分找能让簇数 == want 的间距阈值。"""
        base = spread(vals)
        lo, hi = 0.15 * base, 1.6 * base
        for _ in range(28):
            mid = (lo + hi) / 2
            k = len(cluster(vals, mid))
            if k > want:
                lo = mid
            else:
                hi = mid
        return cluster(vals, (lo + hi) / 2)

    def merge_to(centers, want):
        """簇数多于目标时，反复合并**间隔最近**的两簇，直到正好 want。

        为什么要这步：同一行的标签 y 会有 20px 左右的抖动
        （`险路尽头` 的标签就比同行低 20px），按间距聚类会把它拆成单独一行，
        于是一层 3 行被判成 4 行、二层 4 行被判成 5 行。
        """
        c = list(centers)
        while len(c) > want:
            gaps = [c[i + 1] - c[i] for i in range(len(c) - 1)]
            i = gaps.index(min(gaps))
            c[i] = (c[i] + c[i + 1]) / 2.0
            del c[i + 1]
        return c

    def uniform_axis(vals, want, center=None):
        """找一个**均匀格距**，让所有标签都贴近某个格点。

        为什么不用纯聚类：某一整行/整列可能一个标签都没有（二层第 2 行就是空的），
        聚类只能给出 3 簇，强行凑成 4 会把相邻两行拆开，格点反而全错。
        这里反过来做——穷举格距，取「所有标签到最近格点的距离之和」最小的那一组。

        center（2026-10-29b）：与 choose_lattice 同一套噪声级平局判决——
        边缘整列无标签时两套锚点只差抖动噪声，取格点中心更接近屏幕中心的那套。
        """
        v = sorted(vals)
        lo, hi = v[0], v[-1]
        cands = []
        for dx in range(60, 271):
            for anchor in (lo, hi):
                if anchor == lo:
                    grid = [lo + dx * i for i in range(want)]
                else:
                    grid = [hi - dx * (want - 1 - i) for i in range(want)]
                if grid[-1] < lo - 1 or grid[0] > hi + 1:
                    continue
                cost = sum(min(abs(y - g) for g in grid) for y in v)
                # 惩罚「标签离格点太远」的解
                cost += 40 * sum(1 for y in v if min(abs(y - g) for g in grid) > 0.45 * dx)
                cands.append((cost, grid))
        if not cands:
            return sorted(fit(vals, want))
        cands.sort(key=lambda t: t[0])
        if center is not None:
            eps = 2.0 * max(1, len(v))
            tied = [g for c, g in cands if c <= cands[0][0] + eps]
            if len(tied) > 1:
                tied.sort(key=lambda g: abs(sum(g) / len(g) - center))
                return tied[0]
        return cands[0][1]

    def settle(vals, want):
        """先把簇数调成 want：多了就合并，少了才补缺口。

        顺序不能反——`fill_gaps` 会把「同一行被拆开」当成「缺了一行」再插回来，
        于是 merge 白做（2026-10-27 二层就因此一直报 5 行）。
        """
        c = fit(vals, want)
        if len(c) > want:
            c = merge_to(c, want)
        elif len(c) < want:
            c = merge_to(fill_gaps(c), want)
        return c

    gx = uniform_axis(xs, cols, center=center_x)
    gy = uniform_axis(ys, rows, center=center_y)
    # 某一列/某一行可能**一个标签都没有**（五层的接缝列就是这样），聚类会把它整条跳过。
    # 这里按中位间距把缺口补回来，否则整张图会左移一列。
    def fill_gaps(centers):
        if len(centers) < 3:
            return centers
        d = [b - a for a, b in zip(centers, centers[1:])]
        base = median(d)
        if base <= 2:
            return centers
        out = [centers[0]]
        for a, b in zip(centers, centers[1:]):
            gap = b - a
            k = int(round(gap / base))
            if k >= 2:
                for j in range(1, k):
                    out.append(a + gap * j / k)
            out.append(b)
        return out

    # 注意：**这里不能再跑 fill_gaps**。settle() 已经处理过缺口了，
    # 再补一次会把「刚合并掉的拆分行」当成缺口插回去（2026-10-27 二层因此一直报 5 行）。
    if len(gx) < 3 or len(gy) < 2:
        return None, None
    return gx, gy


def assign(labels, gx, gy, cols, rows, max_off=None):
    # 偏移上限必须随格距放大：一层图标大、标签离节点 85px，
    # 硬编码 70 会把整排标签丢掉（2026-10-27 一层实测漏掉最上面一整排）。
    if max_off is None:
        max_off = max(70.0, 0.55 * abs(gy[0] - gy[-1]) / max(1, rows - 1))
    """把文字按最近的格心分配到 (c,r)；gy 自上而下。"""
    grid = {}
    for cx, cy, txt, conf, raw in labels:
        best = None
        # 用「归一化到格距的最近距离」配对，**不假设标签一定在节点下方**：
        # 自标定模式下 gy 本身就是标签行中心，硬加「必须在下方」会把同一行的标签排除掉
        # （2026-10-27 (2,0) 就是这么丢的：标签 y=685 比行中心 697 高了 12px）。
        sx = abs(gx[1] - gx[0]) if cols > 1 else 120.0
        sy = abs(gy[1] - gy[0]) if rows > 1 else 120.0
        for c in range(cols):
            for r in range(rows):
                nx = abs(cx - gx[c]) / (sx / 2.0)
                ny = abs(cy - gy[r]) / (sy / 2.0)
                if nx > 1.2 or ny > 1.2:      # 超出半格多就判为不属于该格
                    continue
                d = nx + ny
                if best is None or d < best[0]:
                    best = (d, (c, r))
        if best is not None:
            q = best[1]
            # 同名冲突时保留置信度高的
            if q not in grid or grid[q][1] < conf:
                grid[q] = (txt, conf, raw)
    return grid


# 「线人」只在这些层存在（五/六层没有，见 03-节点收益）
LINE_REN_LAYERS = (2, 3, 4)


def make_right_edge_probe(img_or_im, gx, gy, dy_frac=-0.16, half=0.16):
    """造一个「两格之间到底有没有连线」的探针：`probe(edges) -> {edge: 灰度对比}`。

    为什么需要（2026-11-02 实拍标定）：`16-四层全图` 的**构型2 与构型3 格集完全相同**
    （都是 3×3、2 空地），只在两条边上不同 —— 光靠「有标签格 + 空地圆点」永远分不开。
    但边是**画在屏幕上**的，能量出来：实拍「有线 8–21、无线 −2.5–0.2」，阈值 4.5 分得开。

    采样：`gy` 是文字基线，图标中心在它上方约 0.16 格距；沿连线取中段
    t∈[0.32,0.68] 避开两端图标，对照带取法线 ±0.16 / ±0.24 格距（避开连线本身）。
    """
    from PIL import Image as _Image
    im = (_Image.open(img_or_im).convert("RGB")
          if isinstance(img_or_im, (str, bytes)) else img_or_im)
    g = np.asarray(im, dtype=np.float32)[..., :3].mean(axis=2)
    sx = sorted(float(v) for v in gx)
    sy = [float(v) for v in gy]
    if len(sx) < 2 or len(sy) < 2:
        return lambda edges: {}
    sp = (sx[-1] - sx[0]) / (len(sx) - 1)
    h, w = g.shape

    def _one(e):
        (x1, y1), (x2, y2) = e
        p1 = np.array([sx[x1], sy[y1] + dy_frac * sp], dtype=np.float32)
        p2 = np.array([sx[x2], sy[y2] + dy_frac * sp], dtype=np.float32)
        d = p2 - p1
        n = float(np.linalg.norm(d))
        if n < 1:
            return 0.0
        u = d / n
        nrm = np.array([-u[1], u[0]], dtype=np.float32)
        vals, ctrls = [], []
        for t in np.linspace(0.32, 0.68, 19):
            pt = p1 + d * t
            for s in (-2, -1, 0, 1, 2):
                q = pt + u * s
                xi, yi = int(q[0]), int(q[1])
                if 0 <= xi < w and 0 <= yi < h:
                    vals.append(g[yi, xi])
            for s in (-1, 1):
                for off in (half * sp, 0.24 * sp):
                    q = pt + nrm * off * s
                    xi, yi = int(q[0]), int(q[1])
                    if 0 <= xi < w and 0 <= yi < h:
                        ctrls.append(g[yi, xi])
        if not vals or not ctrls:
            return 0.0
        return float(np.mean(vals) - np.mean(ctrls))

    def probe(edges):
        return {tuple(sorted(e)): _one(tuple(sorted(e))) for e in edges}

    return probe


def _grid_put(grid, q, new_type, why):
    """改写 grid[q] 的类型，保留置信度/原始文字；不在 grid 里就新建一条。"""
    cur = grid.get(q)
    conf = cur[1] if cur else 1.0
    raw = (cur[2] if cur else "") or ""
    grid[q] = (new_type, conf, (raw + "；" + why) if raw else why)


# ── 右区「空地小圆点」检测（2026-11-02 玩家要求）─────────────────────────
# 为什么需要：三/四层右区的形状表里，**空地格不写字**，OCR 的 grid 里根本没有它。
# 于是 `build_scenario.match_right_shape()` 只能拿到「有标签的节点」，
# 三层永远 6 格、四层永远 7 格 —— 多种形状都说得通（1↔4、2↔3 的格集互相包含），
# 结果只能是 `ambiguous` + ⚠️。把空地格**测出来**，形状就能唯一锁定 → ✅。
#
# 判据（不写字的小圆点 vs 节点图标 vs 背景星点）：
#   · 节点图标：整块发亮，**外圈也亮** → `ring_dark` 低；
#   · 背景星点：亮但**面积太小**，中心圆盘里占比 <5% → `core_frac` 低；
#   · 空地圆点：中心一小块灰白、外圈是暗底 → 两个条件都满足。
DOT_MIN = 0.55          # 判「这一格有小圆点」的阈值（0–1）
DOT_RAD = 0.10          # 采样半径 = 0.10 × 格距（圆点本体半径约 0.05）
DOT_LO, DOT_HI = 80.0, 150.0   # 圆点「最亮像素」区间：低于 = 纯背景，高于 = 节点图标辉光


def cell_dot_score(im, cx, cy, sp):
    """单格「空地小圆点」得分（0–1）。`cx,cy` = 像素中心，`sp` = 格距（像素）。

    实拍形态（2026-11-02 在 20261029-050317 上量的）：空地 = **一圈亮边 + 暗心**的小圆
    （直径约 0.09 格距），整块最亮像素 100–130；节点图标是**实心辉光**（最亮 220–255）；
    纯背景最亮 < 60。所以判据是「有亮环（但不够亮）+ 中心不是实心 + 外圈暗」：

      · `DOT_LO ≤ max ≤ DOT_HI`：既不是背景也不是图标；
      · `mean / max ≤ 0.80`：中间是暗心（实心图标接近 1）；
      · 外圈中位数 < 45：周围是夜底。

    返回 (score, maxv, ratio, bg)；`score = 1 − |max−中值| / 幅度`，够 0.55 就算圆点。
    """
    try:
        rad = max(8, int(round(DOT_RAD * float(sp))))
        box = (int(cx - rad), int(cy - rad), int(cx + rad), int(cy + rad))
        if box[0] < 0 or box[1] < 0 or box[2] > im.width or box[3] > im.height:
            return 0.0, 0.0, 0.0, 0.0
        a = np.asarray(im.crop(box), dtype=np.float32)
    except Exception:
        return 0.0, 0.0, 0.0, 0.0
    if a.ndim < 3 or a.shape[0] < 8 or a.shape[1] < 8:
        return 0.0, 0.0, 0.0, 0.0
    g = a[..., :3].mean(axis=2)
    h, w = g.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cyy, cxx = (h - 1) / 2.0, (w - 1) / 2.0
    r = np.sqrt((xx - cxx) ** 2 + (yy - cyy) ** 2) / max(1.0, rad)   # 0–1（rad = 0.10 格距）
    disc = r <= 0.75
    ring = (r >= 1.5) & (r <= 2.6)      # 0.15–0.26 格距：外圈（背景）
    maxv = float(g[disc].max())
    meanv = float(g[disc].mean())
    bg = float(np.median(g[ring])) if ring.any() else 0.0
    if not (DOT_LO <= maxv <= DOT_HI):
        return 0.0, maxv, (meanv / maxv if maxv else 0.0), bg
    if meanv / maxv > 0.80 or bg > 45.0:
        return 0.0, maxv, meanv / maxv, bg
    # 落在区间中段 + 中心够暗 → 分越高
    band = 1.0 - abs(maxv - 0.5 * (DOT_LO + DOT_HI)) / (0.5 * (DOT_HI - DOT_LO))
    score = 0.5 * float(np.clip(band, 0.0, 1.0)) + 0.5 * (1.0 - meanv / maxv)
    return float(np.clip(score, 0.0, 1.0)), maxv, meanv / maxv, bg


def _best_dot_offset(im, cx, cy, sp):
    """空地圆点在**节点图标中心**，而 OCR 的 gy 是文字基线（约低 0.2 格距）。

    所以竖向扫一段偏移取最好的一格（返回 (score, maxv, ratio, bg, dy_frac)）。
    """
    best = (0.0, 0.0, 0.0, 0.0, 0.0)
    for k in range(-9, 2):
        dy = k * 0.03 * sp
        s, mv, rt, bg = cell_dot_score(im, cx, cy + dy, sp)
        if s > best[0]:
            best = (s, mv, rt, bg, k * 0.03)
    return best


def detect_empty_dots(img_or_im, gx, gy, cells, lm, rows=None, min_score=DOT_MIN,
                      max_report=6):
    """在**右区**（x > lm）找「没有标签、但格心有小圆点」的格子 = 空地候选。

    `cells`：已经有标签的格子（{q: label} 或 set/dict 的 key 都用得上）。
    `gx,gy`：像素格点（gy **自下而上**，gy[0] = 最下一行，与路线工具一致）。
    返回 (dots, notes)：dots = [(x,y), …]（按分数降序）；notes = 证据/诊断行。

    只做**证据采集**，不判断这些格子能不能走——能不能走由建场景那边按
    「空地=硬墙」处理（玩家 2026-11-02 裁决）。真正让它有用的是
    `build_scenario.match_right_shape()`：**只有能让形状唯一对上的圆点才会被采纳**，
    所以这里宁可放宽阈值，误检由形状表兜住。
    """
    from PIL import Image as _Image
    im = (_Image.open(img_or_im).convert("RGB")
          if isinstance(img_or_im, (str, bytes)) else img_or_im)
    try:
        keys = set(cells.keys()) if hasattr(cells, "keys") else {tuple(q) for q in cells}
    except Exception:
        keys = set()
    gx = sorted(float(v) for v in gx)
    # ⚠️ gy **保持调用方的顺序**：`assign()` 约定 cells 的 r 直接索引 gy（gy[0] = 最下一行）。
    # 2026-11-02 踩过：这里之前又 sorted() 了一次 → 上下翻个个儿，圆点全标到对称的另一行。
    gy = [float(v) for v in gy]
    if len(gx) < 2:
        return [], []
    sp = (gx[-1] - gx[0]) / max(1, len(gx) - 1)
    hits = []
    for xi, px in enumerate(gx):
        if xi <= int(lm):
            continue
        for yi, py in enumerate(gy):
            q = (xi, yi)                        # gy 自下而上 → y 就是索引
            if q in keys:
                continue
            s, mv, rt, bg, dy = _best_dot_offset(im, px, py, sp)
            if s >= min_score:
                hits.append((q, s, mv, rt, dy))
    hits.sort(key=lambda z: -z[1])
    notes = []
    if hits:
        notes.append(
            f"右区空地圆点：{len(hits)} 格候选（阈值 {min_score:g}）→ "
            + "、".join(f"({q[0]},{q[1]})={s:.2f}" for q, s, _m, _r, _d in hits[:max_report])
            + ("…" if len(hits) > max_report else ""))
    return [q for q, _s, _m, _r, _d in hits], notes


def apply_badges(im, grid, gx, gy, cols, rows, right_from=None, layer=None,
                 empty_label="未探明节点（小圆点）"):
    """白圈徽章 → 节点类型/提示（2026-10-30，就地改写 grid）。

    徽章文字里没有，只能看像素；**同一个白圈压在不同节点上是完全不同的东西**，
    所以必须拿 `grid` 里 OCR 读到的类型 + **字形模板**再解释（模板见 `pixel_probe.badge_glyph`）：

    | 徽章压在 + 字形 | 含义 | 处理 |
    |---|---|---|
    | 不期而遇 + `tears`（骷髅面罩） | **泪之聚落**（三结局特殊事件） | 类型改 `泪之聚落`：0 分、默认不进，可作 0 分过路中继 |
    | 不期而遇 + `informant`（蜘蛛） | **线人** | 类型改 `不期而遇线人`（二/三/四层 4/10/10 分） |
    | 失与得 + `ram`（羊角骷髅） | 三结局特殊失与得 | **不改类型、不改价**（与普通失与得一致） |
    | 作战 + `wolf`（狼头） | **无效验尸** | **不改类型、不改价**（与普通作战一致） |
    | 任意节点 + `acorn`（橡果） | **迷藏** | 不改类型；坐标进 `acorn` 列表，**所在节点收益 +12**（带橡果的空地放行） |
    | 空地 + `raven`（乌鸦） | 流窜居民 | 不改类型（供 `resident_infer`） |
    | **命运所指 + 任意徽章** | **五层·二结局关卡入口**（2026-10-31） | 不改类型；坐标进 `fate_badge`，供二结局问询/终点判定 |

    其它组合（未收录字形、字形不认识）只提示、**不改类型不改价**。
    ⚠️ `gy` 必须是**自下而上**（gy[0] = 最下一行像素），与路线工具一致。
    返回 `{"notes": [...], "acorn": [(x,y), …], "fate_badge": [(x,y), …]}`；
    调用方打印 notes、把 acorn 写进 scenario、把 fate_badge 交给二结局逻辑（route.py/build_scenario.py）。
    """
    try:
        import pixel_probe as PP
    except Exception as exc:                      # numpy 缺失等
        return {"notes": [f"!! 徽章检测不可用：{exc}"], "acorn": [], "fate_badge": []}
    sp = ((float(gx[-1]) - float(gx[0])) / max(1, len(gx) - 1)) if len(gx) > 1 else 124.0
    try:
        PP.set_scale(sp)
        items = PP.detect_badges(im, gx, gy, cols, rows, right_from=right_from)
    except Exception as exc:
        return {"notes": [f"!! 徽章检测失败：{exc}（不期而遇按普通值算）"], "acorn": [],
                "fate_badge": []}
    if not items:
        return {"notes": ["未检测到白圈徽章"], "acorn": [], "fate_badge": []}
    notes, acorn, fate_badge = [], [], []
    for it in items:
        q = it["cell"]
        gly = (it.get("glyph") or {}).get("glyph", "unknown")
        score = (it.get("glyph") or {}).get("score")
        node = (grid.get(q) or (None,))[0]
        tag = f"({q[0]},{q[1]})"
        node_txt = node if node else empty_label
        # 橡果 = 藏品类徽章「迷藏」：与底下节点无关，**任何节点都可能带**。
        # 不改类型，只记坐标 —— 收益由建场景工具并入（所在节点 +12），见 rules.ACORN_VALUE。
        if gly == "acorn":
            if q not in acorn:
                acorn.append(q)
            notes.append(f"{tag} 「{node_txt}」+ 橡果徽章（匹配 {score}）→ **迷藏**："
                         "所在节点收益 +12（带橡果的空地放行）")
            continue
        if node == "不期而遇":
            if gly == "tears":
                _grid_put(grid, q, "泪之聚落", "不期而遇+骷髅面罩徽章=泪之聚落")
                notes.append(f"{tag} 不期而遇 + 骷髅面罩（{score}）→ "
                             "**泪之聚落**：三结局特殊事件，0 分、默认不进，可作 0 分过路中继")
            elif gly == "informant":
                if layer is not None and int(layer) not in LINE_REN_LAYERS:
                    notes.append(f"⚠️ {tag} 不期而遇 + 蜘蛛徽章（{score}），"
                                 f"但第 {layer} 层没有线人 → 请人工确认，暂按普通不期而遇算")
                else:
                    _grid_put(grid, q, "不期而遇线人", "不期而遇+蜘蛛徽章=线人")
                    notes.append(f"{tag} 不期而遇 + 蜘蛛徽章（{score}）→ **线人**（正常线人分值）")
            else:
                notes.append(f"⚠️ {tag} 不期而遇 + 未收录字形「{gly}」（{score}）"
                             "→ 暂按普通不期而遇算，请人工确认")
        elif node == "失与得":
            if gly == "ram":
                notes.append(f"{tag} 失与得 + 羊角骷髅徽章（{score}）→ 三结局特殊失与得"
                             "（**收益不变**，仍按普通失与得）")
            else:
                notes.append(f"⚠️ {tag} 失与得 + 未收录字形「{gly}」（{score}）→ 请人工确认")
        elif node in ("作战", "普通作战"):
            if gly == "wolf":
                notes.append(f"{tag} 作战 + 狼头徽章（{score}）→ **无效验尸**"
                             "（收益同普通作战，不改价）")
            else:
                notes.append(f"⚠️ {tag} 作战 + 未收录字形「{gly}」（{score}）→ 请人工确认")
        elif node == "命运所指":
            # 2026-10-31 玩家口径：五层「命运所指」右上角有徽章 = **二结局关卡入口**
            # （拿了二结局相关藏品后，五层左区会多出 3 个命运所指，其中带徽章的那个是入口）。
            # 不改类型（它仍是 0 分非作战节点），只把坐标交给二结局逻辑：
            # 二结局=要 → 它是本层最后一步；=不要 → 它有徽章也说明是入口，不该进。
            if q not in fate_badge:
                fate_badge.append(q)
            if layer == 5:
                notes.append(f"{tag} 命运所指 + 徽章（{gly}，{score}）→ **二结局关卡入口**"
                             "（五层：二结局=要 时它是本层最后一步）")
            else:
                notes.append(f"⚠️ {tag} 命运所指 + 徽章（{gly}，{score}）→ "
                             "三/四层命运所指本来是墙，不该带徽章；请人工确认")
        elif node in ("空地", "空地?", None):
            if gly == "raven":
                notes.append(f"{tag} 空地 + 乌鸦徽章（{score}）→ 流窜居民"
                             "（据点产物，不改节点类型）")
            else:
                notes.append(f"⚠️ {tag} 空地 + 未收录字形「{gly}」（{score}）→ 请人工确认")
        else:
            notes.append(f"{tag} 「{node}」+ 字形「{gly}」（{score}，未收录组合）→ 请人工确认")
    return {"notes": notes, "acorn": acorn, "fate_badge": fate_badge}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--cols", type=int, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--x0", type=float)
    ap.add_argument("--y0", type=float, help="最下一行的 y")
    ap.add_argument("--dx", type=float)
    ap.add_argument("--dy", type=float)
    ap.add_argument("--layer", type=int, choices=[1, 2, 3, 4, 5, 6],
                    help="给了就顺手做定点覆盖（尽头禁踩 / 一层行商位）")
    ap.add_argument("--config", help="构型名，例如 1/2/3 或 L6")
    ap.add_argument("--start", default=None, help="玩家口报的起点，如 0,0（优先于自动检测）")
    ap.add_argument("--badges", action="store_true",
                    help="检测白圈徽章：把「不期而遇 + 线人」标成 不期而遇线人（价值 4）")
    a = ap.parse_args()

    # **层数优先**：给了 --layer 就用层表里的固定维度，覆盖手动传的 --cols/--rows。
    # 层数是硬事实（一层 5x3、四层 12x5…），不允许被参数或自标定改写。
    # 例外：徽章模式（三/四/五层）要按「本层有没有右区」定列，见下面 choose_layer_cols。
    if a.layer in LAYER_DIMS and not (a.badges and a.layer in (3, 4, 5)):
        want = layer_dims(a.layer, a.config)
        if (a.cols, a.rows) != want:
            print(f"!! 层 {a.layer}（构型 {a.config or '默认'}）固定是 {want[0]}列×{want[1]}行，"
                  f"你传的是 {a.cols}×{a.rows} → 以层表为准")
            a.cols, a.rows = want

    labels = read_image(a.image)
    # 版本戳：别的会话如果跑的是旧版，输出里没有这一行，一眼就能看出来
    print(f"label_ocr v{VERSION}")
    print(f"OCR 识别 {len(labels)} 条文字")

    # 徽章模式（三/四/五层）：先按格点宽度判本层有没有右区，再定列数。
    # 旧版直接按 11/12/14 列套——无右区局（左区居中 7/8/10 列）会整体错列，
    # 4 枚徽章全映射到错的格子（2026-10-30 实测）。
    if a.badges and a.layer in (3, 4, 5) and a.x0 is None:
        try:
            from PIL import Image as _Iw
            _W, _H = _Iw.open(a.image).size
            _cols, _gx, _info = choose_layer_cols([l[0] for l in labels], a.layer,
                                                  _W, cfg=a.config)
            print(f"徽章模式：格点宽度判 {'有' if _info.get('has_right') else '无'}右区 → {_cols} 列")
            a.cols = _cols
            a.rows = LAYER_DIMS[a.layer][1]
        except Exception as exc:
            print(f"!! 徽章模式：列数判定失败（{exc}），按传入的 {a.cols} 列继续")

    if a.x0 is not None:
        gx = [a.x0 + a.dx * c for c in range(a.cols)]
        gy = [a.y0 - a.dy * r for r in range(a.rows)]     # gy[0] = 最下一行
        src_note = "手动给定"
    else:
        # 格点只按标签位置聚（稳）。旧的 --pair「标签↔图标配对」分支 2026-10-30 删除：
        # 它在四层会多出整行噪声，收益只有一层偶发；图标响应图现在统一由 read_scene 提供。
        from PIL import Image as _Isize
        _Wm, _Hm = _Isize.open(a.image).size
        gx, gy = auto_lattice(labels, a.cols, a.rows,
                              center_x=_Wm / 2.0, center_y=0.49 * _Hm)
        if gx is not None:
            got = (len(gx), len(gy))
            want = (a.cols, a.rows)
            if got != want:
                # 只允许「五层 L8 变体（13 列）」这一个例外
                if a.layer == 5 and got == (13, 5) and want == (14, 5):
                    print("!! 五层 13 列 → 判为 L8 变体，按 13 列处理")
                    a.cols, a.rows = 13, 5
                else:
                    print(f"❌ 自标定得到 {got[0]} 列 × {got[1]} 行，但该层固定是 {want[0]} 列 × {want[1]} 行。")
                    print("   维度不可能变 → 说明标签聚类有问题（漏读/误读/混入非节点文字）。")
                    print("   **这张读数不可用**，请重截或检查截图，不要继续求解。")
                    return 2
        if gx is None:
            print("!! 自标定失败（标签簇数与层尺寸不符），退回 fit_lattice——结果不可靠，请核对")
            from read_scene import fit_lattice, response_map
            gx, gy = fit_lattice(response_map(a.image), a.cols, a.rows)
            if gx is None:
                print("❌ 格点拟合失败（响应图上找不到可行解）——这张读数不可用，请重截")
                return 2
            gy = list(reversed(gy))          # fit_lattice 是自上而下，assign 要 gy[0]=最下一行
            src_note = "fit_lattice 兜底"
        else:
            gy = list(reversed(gy))      # auto_lattice 给的 gy 是自上而下
            src_note = "自标定"
    # 格点对齐自证：小人必定站在节点上（2026-10-29 一层：最左列无标签 → 整体右移一列）。
    # 手动给定 --x0/--dx 时不插手（那是玩家的硬指定）。
    if a.x0 is None and gx:
        gx, gy, _align_note = align_lattice(a.image, labels, gx, gy, a.cols, a.rows)
        if _align_note:
            print(_align_note)
    # ★ 起点优先输出：它决定构型（二层构型1/2/3/7 的格子集合完全相同，只有起点不同）
    if getattr(a, "start", None):
        sc = tuple(int(v) for v in a.start.split(","))
        print(f"★ 起点（玩家口报）：({sc[0]},{sc[1]})")
    else:
        try:
            from PIL import Image as _Is
            # gy 是自下而上；detect_start 收到的是自上而下的列表，行号要翻回来
            st = detect_start(_Is.open(a.image).convert("RGB"), gx, list(reversed(gy)))
            if st:
                row = a.rows - 1 - st[1]
                print(f"★ 起点（自动检测）：({st[0]},{row})")
            else:
                print("★ 起点：**未检测到小人** → 必须问玩家，禁止猜（起点决定构型）")
        except Exception as exc:
            print(f"★ 起点检测失败：{exc} → 必须问玩家")
    print(f"格点来源：{src_note}  x={[round(v) for v in gx]}")
    print(f"                 y={[round(v) for v in gy]}")
    # 格点校验：用图标位置验列距（列距估错会导致「整体错一列」，只看标签发现不了）
    try:
        from read_scene import response_map
        rs = lattice_residual(labels, response_map(a.image), gx, gy)
        if rs:
            dx_now = abs(gx[1] - gx[0]) if a.cols > 1 else 120.0
            ar = [abs(r) for r in rs]
            med = float(np.median(ar))
            over = sum(1 for r in ar if r > 0.25 * dx_now)
            # 区分两种失败：
            #   整体拉偏 → 中位数就大，或大多数标签都超差
            #   个别噪点 → 中位数正常，只有一两个标签的图标峰值抓歪
            ok = (med <= 0.15 * dx_now) and (over <= max(1, len(ar) * 0.2))
            mark = "✅" if ok else "❌"
            print(f"格点校验 {mark} 图标残差 中位 {med:.1f}px / 最大 {max(ar):.1f}px / "
                  f"超差 {over}/{len(ar)}（阈值 中位≤{0.15*dx_now:.0f}px、超差≤20%）")
            if not ok:
                print("   → 列距/原点被估偏，**这张读数的列号不可信**，请重截或手传 --x0/--dx")
    except Exception as exc:
        print(f"!! 格点校验跳过：{exc}")

    grid = assign(labels, gx, gy, a.cols, a.rows)

    # 定点覆盖：OCR 只认得「地图上写了什么」，认不出「构型表规定这格是什么」。
    # 一层 (4,1) 地图上写的是「未知的诡秘」，但构型3 规定它是诡意行商——
    # 这里直接覆盖并标 (定点)，省得每个新会话都纠结「谁优先」。
    forced = {}
    # 2026-10-29 修复：定点覆盖原来只支持 1/3/4 层（且各自手写解析）——
    # 二层尽头、六层定点节点的命行路径不会覆盖，与 23-操作流程「--layer N --config X
    # 会自动覆盖」的承诺不一致。现在统一走 apply_config.config_fixed（与 route.py 同一份表），
    # 支持与 route.py 相同的 1/2/3/4/6 层。
    if a.layer and a.config and a.layer in (1, 2, 3, 4, 6):
        try:
            import apply_config as AC
            ends, fixed = AC.config_fixed(a.layer, a.config)
            for q in ends:
                forced[q] = "险路尽头"
            for q, t in fixed.items():
                forced[q] = t
        except Exception as exc:
            print(f"!! 定点覆盖失败：{exc}")

    # 白圈徽章（2026-10-30 三结局口径）：含义取决于**徽章底下是什么节点**，
    # 所以先拿到 grid 再翻译——不期而遇+窄/宽 = 泪之聚落/线人；失与得/作战不改价。
    if getattr(a, "badges", False):
        from PIL import Image as _Ib
        _im = _Ib.open(a.image).convert("RGB")
        # main 里的 gy 已经是自下而上（上面 `gy = list(reversed(gy))`），直接用。
        # ⚠️ 旧代码在这里又 reversed 了一次 → 徽章行号上下镜像（2026-10-30 修）。
        _bd = apply_badges(_im, grid, gx, gy, a.cols, a.rows, layer=a.layer)
        for _n in _bd["notes"]:
            print("徽章：" + _n)
        if _bd["acorn"]:
            print("迷藏（橡果）：" + "、".join(f"({q[0]},{q[1]})" for q in _bd["acorn"])
                  + f" → 所在节点收益 +12（rules.ACORN_VALUE；建场景时并入）")

    for r in range(a.rows - 1, -1, -1):
        cells = []
        for c in range(a.cols):
            if (c, r) in forced:
                cells.append(f"{c}:{forced[(c, r)]}(定点)")
            elif (c, r) in grid:
                t, conf, raw = grid[(c, r)]
                mark = "" if raw == t else "*"
                cells.append(f"{c}:{t}{mark}")
            else:
                cells.append(f"{c}:∅")
        print(f" y={r}  " + "  ".join(cells))
    print("\n节点类型以**文字标签**为准；图标形态只能定位坐标，不能判类型"
          "（肺/胚胎/兽首各对应一类而非一个类型）。")
    print("∅ = 无文字（空地/起点/汇点）；* = OCR 文字已按词表校正；"
          "(定点) = 按构型表强制覆盖（OCR 读到的可能是伪装名）")

    # ── 自检闸门 ──────────────────────────────────────────────
    # 判据：**读到的每一格都必须是该构型的合法格子**。
    # 只要有一格越界，说明格点/行列错位，结果一律不可信。
    if a.layer and a.config and a.layer in (1, 2, 3, 4, 6):
        try:
            import apply_config as AC
            legal = AC.config_cells(a.layer, a.config)
            if legal:
                lm = LEFT_MAX.get(a.layer, 99)
                if a.layer == 5 and a.cols == 13:
                    lm = 8                     # 五层 L8 变体：左区只到 x=8
                seen = {q for q in grid} | set(forced)
                # 只校验左区（右区有独立构型表，不在这份表里）
                outside = sorted(q for q in seen if q[0] <= lm and q not in legal)
                uncovered = sorted(q for q in legal if q not in seen)
                print(f"\n自检：构型 {a.config} 共 {len(legal)} 格；"
                      f"读到 {len(seen)} 格，其中无标签(空地/起点) {len(uncovered)} 格")
                if outside:
                    print(f"❌ 有 {len(outside)} 格不在该构型里：{outside}")
                    print("   → 格点或行列错位，这张读数**不可用**，请重新标定（别继续求解）")
                else:
                    print("✅ 所有读到的格子都在构型内 → 可以进入求解")
        except Exception as exc:
            print(f"!! 自检跳过：{exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
