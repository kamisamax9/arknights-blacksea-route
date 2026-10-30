#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edge_check.py — 判「两格之间有没有连线」（带幅度采样，防采样行偏移）

为什么要有这个工具
------------------
2026-10-26 四层右区：我用单行 y=315 采样判 (9,3)-(10,3)，得出「没有边」——
**实际连线在 y=300**（我拿图标亮心当行坐标，偏了 15px）。
结果多截 2 张放大图重判，判错了一轮。

根因：连线是几像素宽的细线，**单像素行采样对 10–20px 的定位误差零容忍**。
做法：沿两点连线取中段，**在 ±band 的垂直带内取「最大值」**，再和 ±background 处的
背景亮度做对比度。只要线在带内，无论偏几像素都能抓到。

用法（**离线建表工具**，不在每局主链路上；2026-10-30 移到 tools/offline/）
----
  python tools/offline/edge_check.py 截图.png --x0 88 --dx 128 --y0 672 --dy 124 --rows 5 \
        --pairs "9,3-10,3;10,3-11,3;9,2-9,1"
  python tools/offline/edge_check.py 截图.png --cells 88,672,128,124,5 --pairs ... --band 8

输出：每对一条 `有边 / 无边` + 对比度数值；对比度 <3 判无边，3–6 判存疑（需人眼）。
"""
import argparse
import sys

import numpy as np
from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def line_stats(g, a, b, band, bg, trim):
    x1, y1 = a
    x2, y2 = b
    L = max(1e-6, ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    px, py = -uy, ux                    # 垂直单位向量
    H, W = g.shape
    vals = []
    for off in range(-band, band + 1):
        tot, cnt = 0.0, 0
        for i in range(101):
            t = i / 100.0
            if t < trim or t > 1 - trim:
                continue
            x = x1 + (x2 - x1) * t + px * off
            y = y1 + (y2 - y1) * t + py * off
            xi, yi = int(round(x)), int(round(y))
            if 0 <= yi < H and 0 <= xi < W:
                tot += g[yi, xi]
                cnt += 1
        if cnt:
            vals.append(tot / cnt)
    on = max(vals) if vals else 0.0
    flanks = []
    for off in (bg, -bg):
        tot, cnt = 0.0, 0
        for i in range(101):
            t = i / 100.0
            if t < trim or t > 1 - trim:
                continue
            x = x1 + (x2 - x1) * t + px * off
            y = y1 + (y2 - y1) * t + py * off
            xi, yi = int(round(x)), int(round(y))
            if 0 <= yi < H and 0 <= xi < W:
                tot += g[yi, xi]
                cnt += 1
        if cnt:
            flanks.append(tot / cnt)
    base = sum(flanks) / len(flanks) if flanks else 0.0
    return on - base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--x0", type=float, required=True, help="基准列的像素 x")
    ap.add_argument("--col0", type=int, default=0, help="x0 对应的绝对列号（右区传 9）")
    ap.add_argument("--dx", type=float, required=True)
    ap.add_argument("--y0", type=float, required=True, help="第 0 行（最下排）像素 y")
    ap.add_argument("--dy", type=float, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--pairs", required=True, help='"c,r-c,r;c,r-c,r"')
    ap.add_argument("--band", type=int, default=8, help="垂直采样带 ±px")
    ap.add_argument("--bg", type=int, default=26, help="背景参考的偏离距离")
    ap.add_argument("--trim", type=float, default=0.25, help="两端各截掉的比例（避开图标盘）")
    a = ap.parse_args()

    g = np.asarray(Image.open(a.image).convert("L")).astype(np.float32)

    def px(c, r):
        # r=0 是最下排（= y0），往上减小
        return (a.x0 + a.dx * (c - a.col0), a.y0 - a.dy * r)

    print(f"{'边':14s} {'对比度':>7s}  判定")
    weak = 0
    for seg in a.pairs.split(";"):
        seg = seg.strip()
        if not seg:
            continue
        left, right = seg.split("-")
        c1, r1 = [int(v) for v in left.split(",")]
        c2, r2 = [int(v) for v in right.split(",")]
        d = line_stats(g, px(c1, r1), px(c2, r2), a.band, a.bg, a.trim)
        verdict = "有边" if d >= 6 else ("存疑" if d >= 3 else "无边")
        if verdict == "存疑":
            weak += 1
        print(f"({c1},{r1})-({c2},{r2}) {d:7.1f}  {verdict}")
    if weak:
        print(f"!! {weak} 条存疑：放大目视复核，别直接写进边表。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
