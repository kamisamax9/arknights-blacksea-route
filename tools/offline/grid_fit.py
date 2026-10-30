#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grid_fit.py — 实战截图 → 格子标定 + 节点吸附 + 残差校验（禁止肉眼估格子）

为什么要有这个工具
------------------
2026-10-26 四层那局，我靠「看放大图估格子间距」先后算成 113px 和 128px，
右区又用错行坐标采样（y=315 vs 实际 300），多读了三次同一张图才定下来。
**格子间距是像素级事实，不该由人眼估。**

标定算法直接复用 `read_scene.py` 的 `fit_lattice`（全局搜索：让「格点上图标响应之和」最大）。
为什么不自研：图标亮心相对格子中心有 10–35px 漂移（白色徽章、装饰最明显），
「两两差值众数」「枚举 dx 数内点」都会被这些漂移带偏——实测错成 125.4px（真值 128）。
fit_lattice 是把整张响应图按给定 cols/rows 做全局最优，对单点漂移免疫。

本工具的职责是**校验 + 吸附**：
  1. 打印标定出的格点坐标 / dx / dy；
  2. 把每个候选图标吸附到最近格点，打印**残差**；
  3. 残差 > tol 的点单独列出（装饰/文字/徽章）；
  4. 给出格子集合与逐行/空列统计，可直接和构型表求集合差。

用法
----
  python tools/offline/grid_fit.py 截图.png --cols 12 --rows 5
  python tools/offline/grid_fit.py 截图.png --cols 12 --rows 5 --out annotated.png
  python tools/offline/grid_fit.py 截图.png --cols 12 --rows 5 --tol 12

铁律（写进 SKILL.md）
--------------------
**格子坐标一律取本工具输出，不许从放大图里估**；估出来的间距差 10% 就足以串格。
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from read_scene import fit_lattice  # 复用同一套标定，别让两套算法互相打架

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

R_IN, R_OUT = 18, 46
HUD_TOP, HUD_BOT = 0.13, 0.85


def box_mean(ii, r):
    k = 2 * r + 1
    H, W = ii.shape[0] - 1, ii.shape[1] - 1
    out = np.zeros((H, W), dtype=np.float32)
    a = ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]
    out[r:H - r, r:W - r] = (a / float(k * k)).astype(np.float32)
    return out


def iconness(g):
    ii = np.pad(g, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    return box_mean(ii, R_IN) - box_mean(ii, R_OUT)


def load_configs(md_path):
    """从构型表 md 里解析出每个构型的格子集合（(x,y) 的集合）。"""
    import re
    txt = open(md_path, encoding="utf-8").read()
    out = {}
    for part in re.split(r"(?m)^##\s+", txt)[1:]:
        head = part.split("\n", 1)[0]
        m = re.match(r"([A-Za-z]+\d+)", head)
        if m:
            cname = m.group(1)
        else:
            m2 = re.match(r"构型\s*(\d+)", head)     # 一/二层写作「## 构型1（起点 0,1）」或「## 构型1（起点 (1,1)）」
            if not m2:
                continue
            cname = "C" + m2.group(1)
        pairs = re.findall(r"\((\d+)\s*,\s*(\d+)\)\s*[\-—–]\s*\((\d+)\s*,\s*(\d+)\)", part)
        if not pairs:
            continue
        cs = set()
        for x1, y1, x2, y2 in pairs:
            cs.add((int(x1), int(y1)))
            cs.add((int(x2), int(y2)))
        out[cname] = cs
    return out


def match_configs(peaks, gx, gy, rows, tol, ref_path, want):
    """用构型表做硬约束：哪个构型的格子集合最能解释当前的图标点位。

    这是对「凭肉眼估间距 / 凭印象套构型」的替代：
    标定由 fit_lattice 给，**构型由集合匹配定**，两者互相校验。
    同时枚举 col/row 偏移（0,±1），防止 fit_lattice 相位差一格导致整体串位。
    """
    cfgs = load_configs(ref_path)
    if not cfgs:
        print("!! 没能从 %s 解析出构型表" % ref_path)
        return
    best = []
    for name, cs in cfgs.items():
        for oc in (-1, 0, 1):
            for orr in (-1, 0, 1):
                shifted = {(x + oc, y + orr) for x, y in cs}
                inside, outside = [], 0
                for v, x, y in peaks:
                    c = int(min(range(len(gx)), key=lambda k: abs(x - gx[k])))
                    j = int(min(range(len(gy)), key=lambda k: abs(y - gy[k])))
                    r = rows - 1 - j
                    if max(abs(x - gx[c]), abs(y - gy[j])) > tol:
                        outside += 1
                        continue
                    if (c, r) in shifted:
                        inside.append((c, r))
                    else:
                        outside += 1
                got = len(set(inside))
                cover = got / float(len(shifted))
                score = got - 1.0 * outside + 4.0 * cover
                best.append((score, name, got, cover, oc, orr, shifted))
    best.sort(key=lambda t: -t[0])
    print("-- 构型表集合匹配（硬约束）--")
    for sb, name, got, cover, oc, orr, shifted in best[:4]:
        print(f"   {name:5s} 命中 {got:2d} 个 / 共 {len(shifted):2d} 格  "
              f"覆盖率 {cover*100:4.0f}%  偏移({oc:+d},{orr:+d})")
    sb, name, got, cover, oc, orr, shifted = best[0]
    hit = set()
    for v, x, y in peaks:
        c = int(min(range(len(gx)), key=lambda k: abs(x - gx[k])))
        j = int(min(range(len(gy)), key=lambda k: abs(y - gy[k])))
        r = rows - 1 - j
        if max(abs(x - gx[c]), abs(y - gy[j])) <= tol and (c, r) in shifted:
            hit.add((c, r))
    print(f"→ 最佳构型 {name}（覆盖率 {cover*100:.0f}%），偏移({oc:+d},{orr:+d})")
    print("   该构型里**没看到图标**的格子（空地/暗图标，需人眼补）:",
          " ".join(f"({c},{r})" for c, r in sorted(shifted - hit)) or "无")
    if want:
        print(f"   你指定的是 {want}；若与上面不一致，先核对起点坐标。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--cols", type=int, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--tol", type=float, default=12.0)
    ap.add_argument("--min-resp", type=float, default=9.0)
    ap.add_argument("--min-dist", type=int, default=55)
    ap.add_argument("--out", default="")
    ap.add_argument("--ref", default="", help="构型表 md，用它的格子集合做硬约束")
    ap.add_argument("--config", default="", help="你预期的构型名，仅用于对照提示")
    a = ap.parse_args()

    im = Image.open(a.image).convert("RGB")
    W, H = im.size
    g = np.asarray(im.convert("L")).astype(np.float32)
    top, bot = int(HUD_TOP * H), int(HUD_BOT * H)

    g2 = g.copy()
    g2[:top, :] = 0
    g2[bot:, :] = 0
    g2[:, :80] = 0                             # 左侧缩放按钮
    g2[: int(0.30 * H), int(0.85 * W):] = 0    # 右上行动力徽章

    resp = iconness(g2)
    resp[:top + R_OUT, :] = 0
    resp[bot - R_OUT:, :] = 0
    resp[:, :80] = 0
    resp[: int(0.30 * H), int(0.85 * W):] = 0

    gx, gy = fit_lattice(resp, a.cols, a.rows)
    if gx is None:
        print("标定失败：找不到合适的格子。")
        return 1
    # 直接用 fit_lattice 的结果。曾试过「±16px 内重新最大化格点响应和」做亚像素细化，
    # 实测反而把四层的 128 拉成 126.5、三层拉成 126.5（真值 120）——
    # 因为 60 个格点的响应和存在大量局部最优。**不要加细化，就信全局最优这一版。**
    dx = (gx[-1] - gx[0]) / max(1, a.cols - 1)
    dy = (gy[0] - gy[-1]) / max(1, a.rows - 1)
    print(f"图像 {W}x{H}  网格 {a.cols}x{a.rows}")
    print(f"格点 x: [{', '.join(str(round(v)) for v in gx)}]  -> dx={dx:.2f}")
    print(f"格点 y: [{', '.join(str(round(v)) for v in gy)}]  -> dy={abs(dy):.2f}（自上而下）")
    # 合理性闸门：十层实测的格距都在 95–135（1600x900/14 列 = 120、1920x864/14 列 = 120、
    # 1600x900/5 列 = 220 属一层例外，单独放宽）。**超出范围一律判失败**——
    # 2026-10-26 五层 1600x900 那张，画幅装不下 14 列，fit_lattice 退化成 dx=76
    # 却照样输出一份「看起来很合理」的构型匹配，差点被骗。
    # 闸门：黑流树海的格子是**正方形**，所以 dx 与 dy 必须接近。
    # 用「正方形」判据而不是绝对区间——绝对区间随层数变化：
    # 一层 5x3 的 dx 是 220，六层 5x4 只有 121，写死任何区间都会误杀。
    # 2026-10-26 五层 1600x900 那张：dx=76 / dy=124（比值 0.61）→ 当场拦下。
    _r = dx / abs(dy) if dy else 0
    if not (0.85 <= _r <= 1.18) or not (30 <= dx <= 400):
        print(f"!! 标定失败：dx={dx:.1f} dy={abs(dy):.1f}（比值 {_r:.2f}）不像正方形格。")
        print("!! 通常是**画幅装不下整张图**（地图被裁/被放大）。请缩小地图重拍，别用这次的结果。")
        return 2

    flat = resp.copy()
    peaks = []
    for _ in range(400):
        idx = int(np.argmax(flat))
        y, x = np.unravel_index(idx, flat.shape)
        v = float(flat[y, x])
        if v < a.min_resp:
            break
        peaks.append((v, int(x), int(y)))
        flat[max(0, y - a.min_dist):y + a.min_dist + 1,
             max(0, x - a.min_dist):x + a.min_dist + 1] = 0.0

    cells, bad = {}, []
    for v, x, y in peaks:
        c = int(np.argmin([abs(x - q) for q in gx]))
        j = int(np.argmin([abs(y - q) for q in gy]))
        rx, ry = abs(x - gx[c]), abs(y - gy[j])
        if max(rx, ry) > a.tol:
            bad.append((v, x, y, rx, ry))
            continue
        key = (c, a.rows - 1 - j)
        if key not in cells or v > cells[key][0]:
            cells[key] = (v, rx, ry)

    print(f"峰值 {len(peaks)} 个 -> 吸附 {len(cells)} 个，超差 {len(bad)} 个（tol={a.tol:.0f}px）")
    if bad:
        print("-- 超差点（装饰/文字/徽章，或标定偏了）--")
        for v, x, y, rx, ry in sorted(bad, key=lambda t: -t[0])[:8]:
            print(f"   resp={v:5.1f} px=({x},{y}) 残差=({rx:.0f},{ry:.0f})")

    print("-- 吸附后的格子（(col,row) resp 残差）--")
    for (c, r), (v, rx, ry) in sorted(cells.items(), key=lambda t: (-t[0][1], t[0][0])):
        print(f"   ({c},{r})  resp={v:5.1f}  resid=({rx:.0f},{ry:.0f})")
    rows, cols = {}, {}
    for (c, r) in cells:
        rows.setdefault(r, []).append(c)
        cols.setdefault(c, []).append(r)
    print("-- 逐行格子数（与构型表逐行核对）--")
    for r in sorted(rows, reverse=True):
        print(f"   y={r}: {len(rows[r]):2d} 格 x={sorted(rows[r])}")
    print("   空列:", [c for c in range(a.cols) if c not in cols])
    print("格子集合:", " ".join(f"({c},{r})" for c, r in sorted(cells, key=lambda t: (-t[1], t[0]))))

    if a.ref:
        match_configs(peaks, gx, gy, a.rows, a.tol, a.ref, a.config)

    if a.out:
        d = ImageDraw.Draw(im, "RGBA")
        try:
            F = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 15)
        except Exception:
            F = ImageFont.load_default()
        for x in gx:
            for y in gy:
                d.ellipse([x - 3, y - 3, x + 3, y + 3], outline=(255, 255, 0, 200))
        for (c, r), (v, rx, ry) in cells.items():
            x, y = gx[c], gy[a.rows - 1 - r]
            d.ellipse([x - 24, y - 24, x + 24, y + 24], outline=(0, 255, 120, 255), width=3)
            d.text((x + 22, y - 30), f"{c},{r}", fill=(0, 255, 120, 255), font=F)
        im.save(a.out)
        print("已保存:", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
