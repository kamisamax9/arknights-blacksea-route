#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edge_builder.py — 从构型截图重建「节点 + 边」表（含图标强制节点）

教训来源：2026-10-24 三层/四层返工。
**必须把「格子里有没有节点」和「两格之间有没有路」分成两件事判**，
否则空格的背景辉光会被读成路，造出幽灵边。

用法（**离线建表工具**，不在每局主链路上；2026-10-30 移到 tools/offline/）
----
  python tools/offline/edge_builder.py <图片目录> --layer 3            # 三层：红=险路恶敌
  python tools/offline/edge_builder.py <图片目录> --layer 4            # 四层：绿=险路尽头(3个)
  python tools/offline/edge_builder.py <图片目录> --layer 5 --anchor 8 # 五层 L8 用锚定网格

输出：每张图的 边集合 + 格点亮度 + 图标位置，JSON 到 stdout（或 --out 文件）。

判定规则（全部经玩家核对）
------------------------
1. **节点存在性**：格内最大亮度 ≥ 100 → 有节点。
   （普通节点 ~140；险路恶敌/尽头这类暗盘图标 ~110–160，**不能用 130**；空格通常 < 100）
2. **图标强制节点**：三/五/六层先找红色险路恶敌，四层先找绿色险路尽头（3 个）。
   它们是暗盘＋辉光，容易被判成空格 → **必须强制标为有节点**。
3. **边**：两端都有节点 且 连线亮度 > 0.8。
4. **禁止反推**：连线很亮 ≠ 两端有节点（玩家否决过这条）。

层参数
------
  layer 3: 7 列 × 5 行，图标=红（险路恶敌，1 个）
  layer 4: 8 列 × 5 行，图标=绿（险路尽头，3 个）
  layer 5: 10 列 × 5 行，图标=红（险路恶敌，1 个）；L8 只有 9 列，需 --anchor
  layer 2: 5 列 × 4 行，无图标
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

THR_ROAD = 65       # 道路亮度阈值（像素级）
FRAC_EDGE = 0.8     # 连线亮度分数阈值
NODE_MAX = 100      # 节点存在性：格内最大亮度
PAD, HALF = 18, 15  # 测量时的收缩/半宽

LAYER_CFG = {
    2: dict(nc=5, nr=4, icon=None, icon_n=0, dxr=(125, 175), dyr=(120, 160)),
    3: dict(nc=7, nr=5, icon="red", icon_n=1, dxr=(125, 150), dyr=(125, 150)),
    4: dict(nc=8, nr=5, icon="green", icon_n=3, dxr=(120, 152), dyr=(120, 152)),
    5: dict(nc=10, nr=5, icon="red", icon_n=1, dxr=(118, 140), dyr=(118, 140)),
}

# 五层 L8 的锚定格（由起点 (6,3) 反推，只有 9 列）
L8_COLS = [103, 234, 372, 509, 647, 784, 922, 1059, 1197]
L8_ROWS = [79, 219, 356, 491, 628]


# 控制台编码兜底（GBK 终端下打印中文/符号不崩）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def frac_h(a, x0, x1, y):
    H, W = a.shape
    lo, hi = sorted((int(x0), int(x1)))
    x0, x1 = max(0, lo + PAD), min(W, hi - PAD)
    y0, y1 = max(0, int(y) - HALF), min(H, int(y) + HALF + 1)
    if x1 - x0 < 6:
        return 0.0
    return float((a[y0:y1, x0:x1].max(axis=0) > THR_ROAD).mean())


def frac_v(a, y0, y1, x):
    H, W = a.shape
    lo, hi = sorted((int(y0), int(y1)))
    y0, y1 = max(0, lo + PAD), min(H, hi - PAD)
    x0, x1 = max(0, int(x) - HALF), min(W, int(x) + HALF + 1)
    if y1 - y0 < 6:
        return 0.0
    return float((a[y0:y1, x0:x1].max(axis=1) > THR_ROAD).mean())


def cell_max(a, x, y, r=14):
    H, W = a.shape
    return int(a[max(0, y - r):min(H, y + r + 1), max(0, x - r):min(W, x + r + 1)].max())


def fit_grid(a, nc, nr, dxr, dyr):
    """以「亮度 > 0.8 的边数」为目标拟合网格（比峰值定位稳）"""
    H, W = a.shape
    best = None
    for x0 in range(40, 135, 5):
        for dx in range(dxr[0], dxr[1], 3):
            xs = [x0 + i * dx for i in range(nc)]
            if xs[-1] > W - 4:
                continue
            for y0 in range(40, 135, 5):
                for dy in range(dyr[0], dyr[1], 3):
                    ys = [y0 + j * dy for j in range(nr)]
                    if ys[-1] > H - 4:
                        continue
                    s = 0
                    for j in range(nr):
                        for i in range(nc - 1):
                            if frac_h(a, xs[i], xs[i + 1], ys[j]) > FRAC_EDGE:
                                s += 1
                    for j in range(nr - 1):
                        for i in range(nc):
                            if frac_v(a, ys[j], ys[j + 1], xs[i]) > FRAC_EDGE:
                                s += 1
                    if best is None or s > best[0]:
                        best = (s, xs, ys)
    return best[1], best[2]


def icon_mask(rgb, color):
    R = rgb[:, :, 0].astype(int)
    G = rgb[:, :, 1].astype(int)
    B = rgb[:, :, 2].astype(int)
    if color == "red":     # 险路恶敌：红/粉
        return (R > 110) & (R - G > 45) & (R - B > 45)
    if color == "green":   # 险路尽头：黄绿
        return (G > 120) & (G - B > 60) & (G > R - 10)
    return np.zeros(R.shape, dtype=bool)


def blobs(mask, min_px=120):
    H, W = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    for y in range(H):
        for x in range(W):
            if mask[y, x] and not seen[y, x]:
                st = [(y, x)]
                seen[y, x] = True
                cnt = sy = sx = 0
                while st:
                    cy, cx = st.pop()
                    cnt += 1
                    sy += cy
                    sx += cx
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            st.append((ny, nx))
                if cnt >= min_px:
                    out.append((cnt, sx / cnt, sy / cnt))
    out.sort(reverse=True)
    return out


def _norm(e):
    return tuple(sorted((tuple(e[0]), tuple(e[1]))))


def load_exceptions(path, layer, config):
    if not path or not os.path.exists(path):
        return set(), set()
    d = json.load(open(path, encoding="utf-8"))
    wl = {_norm(x["edge"]) for x in d.get("whitelist", [])
          if x.get("layer") == layer and x.get("config") == config}
    bl = {_norm(x["edge"]) for x in d.get("blacklist", [])
          if x.get("layer") == layer and x.get("config") == config}
    return wl, bl


def build(image_path, layer, anchor=False, config=None, exc_path=""):
    cfg = LAYER_CFG[layer]
    nc, nr = cfg["nc"], cfg["nr"]
    rgb = np.asarray(Image.open(image_path).convert("RGB"))
    a = np.asarray(Image.open(image_path).convert("L"), dtype=np.uint8)
    if anchor and layer == 5:
        gx, gy = L8_COLS, L8_ROWS
        nc = len(gx)
    else:
        gx, gy = fit_grid(a, nc, nr, cfg["dxr"], cfg["dyr"])

    forced = set()
    icons = []
    if cfg["icon"]:
        for area, cx, cy in blobs(icon_mask(rgb, cfg["icon"])):
            i = int(np.argmin([abs(cx - x) for x in gx]))
            j = int(np.argmin([abs(cy - y) for y in gy]))
            cell = (i, nr - 1 - j)
            icons.append((area, cell))
            forced.add(cell)
            if len(icons) >= cfg["icon_n"]:
                break

    has = {}
    for j in range(nr):
        for i in range(nc):
            c = (i, nr - 1 - j)
            has[c] = (cell_max(a, int(gx[i]), int(gy[j])) >= NODE_MAX) or (c in forced)

    edges = set()
    for j in range(nr):
        for i in range(nc - 1):
            if has[(i, nr - 1 - j)] and has[(i + 1, nr - 1 - j)]:
                if frac_h(a, gx[i], gx[i + 1], gy[j]) > FRAC_EDGE:
                    edges.add(tuple(sorted([(i, nr - 1 - j), (i + 1, nr - 1 - j)])))
    for j in range(nr - 1):
        for i in range(nc):
            if has[(i, nr - 1 - j)] and has[(i, nr - 2 - j)]:
                if frac_v(a, gy[j], gy[j + 1], gx[i]) > FRAC_EDGE:
                    edges.add(tuple(sorted([(i, nr - 1 - j), (i, nr - 2 - j)])))

    wl, bl = load_exceptions(exc_path, layer, config)
    edges = {e for e in edges if e not in bl}
    edges |= wl

    return {
        "image": os.path.basename(image_path),
        "config": config,
        "exceptions": {"whitelist": [list(e) for e in sorted(wl)], "blacklist": [list(e) for e in sorted(bl)]},
        "grid_x": [round(float(v), 1) for v in gx],
        "grid_y": [round(float(v), 1) for v in gy],
        "icons": [{"area": ar, "cell": list(c)} for ar, c in icons],
        "nodes": sorted([list(c) for c, v in has.items() if v]),
        "edges": [list(e) for e in sorted(edges)],
        "n_edges": len(edges),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--layer", type=int, required=True, choices=[2, 3, 4, 5])
    ap.add_argument("--anchor", type=int, default=0, help="五层 L8 传 8")
    ap.add_argument("--out", default="")
    ap.add_argument("--prefix", default="L")
    ap.add_argument("--exceptions", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "exceptions.json"))
    args = ap.parse_args()

    files = sorted(
        [f for f in os.listdir(args.dir) if f.lower().endswith(".png") and not f.startswith("_")],
        key=lambda s: (len(s), s))
    res = []
    for f in files:
        k = None
        stem = os.path.splitext(f)[0]
        digits = "".join(ch for ch in stem if ch.isdigit())
        if digits:
            k = int(digits)
        res.append(build(os.path.join(args.dir, f), args.layer, anchor=(k == args.anchor), config=k, exc_path=args.exceptions))
    payload = json.dumps(res, ensure_ascii=False, indent=1)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(payload)
        print(f"written {args.out}  ({len(res)} configs)")
    else:
        print(payload)


if __name__ == "__main__":
    main()

