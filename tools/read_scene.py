#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_scene.py — 图标响应图 + 格点拟合（**库**，不是命令行工具）

2026-10-30 起本文件只提供三个函数，供其它工具 import：
  * `response_map(path)`：图标响应图（内盘均值 − 外环均值，已裁 HUD / 抹行动力徽章）；
  * `fit_lattice(resp, cols, rows, …)`：全局拟合格子，返回 (gx, gy)；
  * `iconness(g, ii)` / `load_gray` / `integral` / `box_sum`：上面的底层实现。

谁在用：
  * `board_read.py`：标签↔图标配对（`pair_nodes`）、一/二层弱响应补「空地」格；
  * `label_ocr.py`：自标定失败时的 fit_lattice 兜底、图标残差校验；
  * `tools/offline/grid_fit.py`：标定 + 吸附 + 残差（离线建表）。

**原来的命令行 main()（找「可见圆点」当节点）已删除**：圆点是汇点不是节点，
不能当坐标来源（`23-操作流程` 明令禁止）；要读一局的节点表请用
`board_read.py`（自动认层）或 `label_ocr.py`。

与 `offline/edge_builder.py` 的分工：
  * `offline/edge_builder.py`：从**构型图**（无 HUD、平铺、纯色背景）重建「节点+边」总表，建资料用；
  * `read_scene.py` 的响应图：从**实战截图**（有 HUD、带光晕）给格点与图标定位，当场规划用。
"""
import sys

import numpy as np
from PIL import Image

HUD_TOP_RATIO = 0.13     # 顶部 HUD 占比
HUD_BOT_RATIO = 0.15     # 底部道具栏占比
R_IN = 15                # 内盘半径
R_OUT = 55               # 外环半径


# 控制台编码兜底（GBK 终端下打印中文/符号不崩）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_gray(path):
    im = Image.open(path).convert("RGB")
    return im, np.asarray(im.convert("L")).astype(np.float32)


def integral(g):
    return np.pad(g, ((1, 0), (1, 0))).cumsum(0).cumsum(1)


def box_sum(ii, x0, y0, x1, y1):
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = max(x0 + 1, x1); y1 = max(y0 + 1, y1)
    return ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]


def iconness(g, ii):
    """内盘均值 − 外环均值（外环 = 大框均值去掉小框）"""
    H, W = g.shape
    out = np.zeros((H, W), dtype=np.float32)
    a = box_sum(ii, 0, 0, 2 * R_IN + 1, 2 * R_IN + 1)
    _ = a
    for y in range(R_OUT, H - R_OUT):
        ys0, ys1 = y - R_IN, y + R_IN + 1
        yo0, yo1 = y - R_OUT, y + R_OUT + 1
        for x in range(R_OUT, W - R_OUT):
            xs0, xs1 = x - R_IN, x + R_IN + 1
            xo0, xo1 = x - R_OUT, x + R_OUT + 1
            inner = box_sum(ii, xs0, ys0, xs1, ys1) / ((xs1 - xs0) * (ys1 - ys0))
            outer_all = box_sum(ii, xo0, yo0, xo1, yo1) / ((xo1 - xo0) * (yo1 - yo0))
            out[y, x] = inner - outer_all
    return out


def _score_map(small, cols, rows, dx, dy):
    """固定间距时，所有 (x0,y0) 的打分图：格点响应之和（向量化）"""
    sh, sw = small.shape
    spanx = dx * (cols - 1)
    spany = dy * (rows - 1)
    if spanx >= sw or spany >= sh:
        return None
    acc = np.zeros((sh - spany, sw - spanx), dtype=np.float32)
    for j in range(rows):
        for i in range(cols):
            acc += small[j * dy: j * dy + sh - spany, i * dx: i * dx + sw - spanx]
    return acc


def fit_lattice(resp, cols, rows, x_lo=0, x_hi=0, y_lo=0, y_hi=0):
    """两级搜索格子 (x0, dx, y0, dy)，按「格点上图标响应之和」打分。

    为什么不用「边数」打分：原实现 dx 上限 180px，遇到放大后的地图（一层实测 215px）直接失效。
    为什么不用 Python 三重循环：约 96 万次迭代，实测超过工具 120s 超时。
    这里先 1/8 缩略粗搜（向量化打分图），再 1/4 缩略细化。

    2026-10-29 修复：x_lo/x_hi/y_lo/y_hi 四个边界参数原来被**完全忽略**
    （main 一直认真在传，函数体一次没用）。现在它们是**格点锚点 (x0,y0) 的搜索窗口**：
    打分图上只允许窗口内的锚点参选（掩码 argmax，不是切子图——切子图会把整个格网切没）。
    窗口内无正分时回落全图并提示。窗口全 0（缺省）= 不限制，行为同旧版。
    另外本函数可能返回 (None, None)——调用方必须判空，别直接 round()。
    """
    bounded = (x_hi > x_lo) and (y_hi > y_lo)
    win = (x_lo, x_hi, y_lo, y_hi) if bounded else None

    def _stage(src):
        """在给定响应图上做两级搜索，返回 best2 或 None。"""
        h2, w2 = src.shape
        img = Image.fromarray(np.clip(src, 0, 255).astype(np.uint8))

        def prep(scale):
            return np.asarray(img.resize((max(1, w2 // scale), max(1, h2 // scale)), Image.BOX),
                              dtype=np.float32)

        def search(small, scale, dx_range, dy_range, step, best):
            for dx in dx_range:
                for dy in dy_range:
                    sm = _score_map(small, cols, rows, dx, dy)
                    if sm is None or sm.size == 0:
                        continue
                    if win is not None:
                        # 锚点窗口（真实坐标 → 当前缩放坐标），窗口外一律不参选
                        wx0, wx1 = max(0, win[0] // scale), min(sm.shape[1], max(0, win[1] // scale))
                        wy0, wy1 = max(0, win[2] // scale), min(sm.shape[0], max(0, win[3] // scale))
                        if wx1 <= wx0 or wy1 <= wy0:
                            continue
                        m = np.zeros_like(sm, dtype=bool)
                        m[wy0:wy1, wx0:wx1] = True
                        masked = np.where(m, sm, -1.0)
                        if not (masked > 0).any():
                            continue
                        idx = int(np.argmax(masked))
                    else:
                        idx = int(np.argmax(sm))
                    sc = float(sm.flat[idx])
                    if best is None or sc > best[0]:
                        y0, x0 = np.unravel_index(idx, sm.shape)
                        best = (sc, x0 * scale, y0 * scale, dx * scale, dy * scale)
            return best

        coarse = prep(8)
        # 最小间距 80px（各层实测 126~222px），避免把各行挤在一起骗分数
        best = search(coarse, 8, range(10, 46, 2), range(10, 46, 2), 2, None)
        if best is None:
            return None
        fine = prep(4)
        cdx = max(3, best[3] // 4)
        cdy = max(3, best[4] // 4)
        best2 = None
        for dx in range(max(10, cdx - 4), max(10, cdx - 4) + 9):
            for dy in range(max(10, cdy - 4), max(10, cdy - 4) + 9):
                sm = _score_map(fine, cols, rows, dx, dy)
                if sm is None or sm.size == 0:
                    continue
                if win is not None:
                    wx0, wx1 = max(0, win[0] // 4), min(sm.shape[1], max(0, win[1] // 4))
                    wy0, wy1 = max(0, win[2] // 4), min(sm.shape[0], max(0, win[3] // 4))
                    if wx1 <= wx0 or wy1 <= wy0:
                        continue
                    m = np.zeros_like(sm, dtype=bool)
                    m[wy0:wy1, wx0:wx1] = True
                    masked = np.where(m, sm, -1.0)
                    if not (masked > 0).any():
                        continue
                    idx = int(np.argmax(masked))
                else:
                    idx = int(np.argmax(sm))
                sc = float(sm.flat[idx])
                if best2 is None or sc > best2[0]:
                    y0, x0 = np.unravel_index(idx, sm.shape)
                    best2 = (sc, x0 * 4, y0 * 4, dx * 4, dy * 4)
        return best2 or best

    best2 = _stage(resp)
    if best2 is None and bounded:
        # 窗口内无解（窗口给错/地图越出窗口）→ 回落全图，别把边界参数用成"一格都找不到"
        print(f"  ⚠️ 拟合窗口 x[{x_lo},{x_hi}) y[{y_lo},{y_hi}) 内无解，回落全图拟合")
        best2 = _stage(resp)
    off_x, off_y = (x_lo, y_lo) if bounded else (0, 0)
    if best2 is None:
        return None, None

    _, bx, by, bdx, bdy = best2
    # 窗口语义=锚点约束：搜索在**全图**上进行（坐标已是真实值），无需再加窗口偏移
    gx = [bx + i * bdx for i in range(cols)]
    gy = [by + j * bdy for j in range(rows)]
    return gx, gy


def response_map(path):
    """截图 → 图标响应图（裁 HUD / 抹掉右上角「行动力」徽章）。

    main() 与 board_read.py 共用：两者必须用**同一套**响应图，
    否则「拟合出的格点」和「标签配到的格点」会各说各话。
    """
    _, g = load_gray(path)
    H, W = g.shape
    top = int(HUD_TOP_RATIO * H)
    bot = int(H - HUD_BOT_RATIO * H)
    g2 = g.copy()
    g2[:top, :] = 0
    g2[bot:, :] = 0
    ii = integral(g2)

    resp = iconness(g2, ii)
    resp[:top + R_OUT, :] = 0
    resp[bot - R_OUT:, :] = 0
    # 右上角「行动力」徽章会落在网格点上，屏蔽掉
    resp[: int(0.30 * H), int(0.88 * W):] = 0
    return resp



