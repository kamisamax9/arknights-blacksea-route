#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_lattice.py — 锁住「整列错位自愈」的行为（2026-10-29 一层事故）。

事故：一层最左列只有起点（无文字标签）→ choose_lattice 把 5 列套在 4 列标签上，
整张图右移一列；标签→图标残差仍为 0，所以「格点校验」照样 ✅，但列号全错。
修复：用「必定站在节点上」的小人像素当锚点，偏差 > 0.85 格就整格平移回来。
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np                      # noqa: E402
from PIL import Image                   # noqa: E402
import label_ocr as L                   # noqa: E402


def _has_scipy():
    try:
        return importlib.util.find_spec("scipy") is not None
    except Exception:
        return False


HAS_SCIPY = _has_scipy()

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg)


def warm_image(sx, sy, w=1920, h=864, size=10):
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[sy - size // 2: sy + size // 2, sx - size // 2: sx + size // 2] = (255, 240, 90)
    return Image.fromarray(a)


LABELS = [
    (754, 478, "作战", 1.0, "作战"),
    (960, 478, "未知的诡秘", 1.0, "未知的诡秘"),
    (1168, 478, "未知的诡秘", 1.0, "未知的诡秘"),
    (754, 685, "未知的诡秘", 1.0, "未知的诡秘"),
    (960, 685, "未知的凶戾", 1.0, "未知的凶戾"),
]
GX = [754.0, 960.0, 1168.0, 1374.0, 1582.0]
GY = [686.0, 478.0, 270.0]


def main():
    print(f"解释器：{sys.executable}（Python {sys.version.split()[0]}）")
    print(f"scipy：{'✅' if HAS_SCIPY else '❌（滚动阶段 on_labeled 判定不可用，10/11 将跳过）'}")
    print("== 1. 最左列无标签：小人锚点触发整格平移 ==")
    im = warm_image(500, 400)
    gx, gy, note = L.align_lattice(im, LABELS, GX, GY, 5, 3)
    check(abs(gx[0] - 547) <= 2, f"左移后首列 ≈547（实际 {gx[0]:.0f}）")
    check(gy == GY, "行不动（小人 y 偏差 <0.85 格）")
    check("x-207" in note, f"note 说明平移量（实际 {note!r}）")
    grid = L.assign(LABELS, gx, gy, 5, 3)
    check(grid.get((1, 1), ("",))[0] == "作战", "标签仍落在 (1,1) 作战")
    check((0, 1) not in grid, "(0,1) 无标签 = 起点格")

    print("== 2. 正常小人偏移（0.3~0.6 格）不触发平移 ==")
    im2 = warm_image(690, 400)
    gx2, gy2, note2 = L.align_lattice(im2, LABELS, GX, GY, 5, 3)
    check(gx2 == GX and gy2 == GY, "格点不变")
    check(note2 == "", "无对齐提示")

    print("== 3. 小人检测失败时不瞎移 ==")
    im3 = Image.new("RGB", (1920, 864), (0, 0, 0))
    gx3, gy3, note3 = L.align_lattice(im3, LABELS, GX, GY, 5, 3)
    check(gx3 == GX and gy3 == GY and note3 == "", "无小人 → 原样返回")

    # ── 2026-10-29b：三层 19:02「平移 x-121」事故的两组回归 ──
    # 事故链：起点列无标签 → choose_lattice 把 11 列套在 10 列标签上（代价 18 vs 22，
    # 差距纯属标签抖动噪声）→ 整图偏一列 → 小人锚点自愈平移 -121 才救回来。
    print("== 4. 事故几何·choose_lattice 平局判决：标签列 476..1566 + center=960 → 选居中解 ==")
    XS3 = [476, 478, 598, 598, 598, 598, 718, 718, 718, 720, 840, 840, 840,
           960, 960, 960, 961, 961, 1080, 1080, 1080, 1323, 1323, 1323,
           1444, 1444, 1444, 1444, 1566, 1566]
    gx4 = L.choose_lattice(XS3, 11, center=960.0)
    check(abs(gx4[0] - 356) <= 1, f"首列 ≈356，不再整体偏一列（实际 {gx4[0]:.0f}）")
    gx4_old = L.choose_lattice(XS3, 11)
    check(abs(gx4_old[0] - 476) <= 1, "不传 center 保持旧行为（首个最小代价胜出）")

    print("== 5. 无歧义标签（每列都有字）→ center 不改变结果 ==")
    XS_FULL = [476 + 121 * i for i in range(11)]
    gx5 = L.choose_lattice(XS_FULL, 11, center=960.0)
    check(abs(gx5[0] - 476) <= 1, "仍选 476 起头的格点（平局判决只作用于噪声级平局）")

    print("== 6. 一层事故几何：4 列标签套 5 列 → 选向左补齐的居中解 ==")
    XS_L1 = [754, 960, 1166, 1372]
    gx6 = L.choose_lattice(XS_L1, 5, center=960.0)
    check(abs(gx6[0] - 548) <= 2, f"首列 ≈548（实际 {gx6[0]:.0f}）")

    print("== 7. 大块暖色（图标/装饰）不再拉动锚点 → 不平移 ==")
    a7 = np.zeros((864, 1920, 3), dtype=np.uint8)
    a7[395:405, 595:605] = (255, 240, 90)      # 正常小人（偏 0.74 格，无标签在下方）
    a7[655:681, 487:513] = (255, 240, 90)      # 26x26=676px 大块，偏离最左列 1.23 格
    gx7, gy7, note7 = L.align_lattice(Image.fromarray(a7), LABELS, GX, GY, 5, 3)
    check(gx7 == GX and gy7 == GY, "格点不变（旧代码会被大块拖去平移 -207）")
    check(note7 == "", f"无提示（实际 {note7!r}）")

    print("== 8. 两处合格小人证据 → 证据歧义，拒绝平移并报告 ==")
    a8 = np.zeros((864, 1920, 3), dtype=np.uint8)
    a8[396:404, 496:504] = (255, 240, 90)      # 偏 1.23 格（若只有它会触发平移）
    a8[396:404, 1340:1348] = (255, 240, 90)    # 另一处同等合格的证据（最右列附近）
    gx8, gy8, note8 = L.align_lattice(Image.fromarray(a8), LABELS, GX, GY, 5, 3)
    check(gx8 == GX and gy8 == GY, "格点不变")
    check("不平移" in note8 and "2 处" in note8, f"给出歧义提示（实际 {note8!r}）")

    print("== 9. 平移后 note 自报残差（执行方可直接核信） ==")
    im9 = warm_image(500, 400)
    gx9, gy9, note9 = L.align_lattice(im9, LABELS, GX, GY, 5, 3)
    check("平移后小人距格心" in note9 and "x-207" in note9, f"note 含残差（实际 {note9!r}）")

    # ── 10/11：起点小人「站在带标签节点上」══════════════════════════════
    # 2026-10-29 真实事故：玩家在 (2,4) 秘境行商，被识别成 (3,3)。
    # 根因：「起点格没有标签」这条判据只在**进层时**成立；玩家站在行商/作战上时，
    # 正确答案自身被罚 +10（几何分 0.037 输给几何分 5.409 的空地邻居）。
    # 判别信号：所有暖色块的正下方都有标签（keep 被剔空）→ 玩家正站在带标签节点上。
    #
    # 2026-10-30：该判定用 `scipy.ndimage` 做连通域。缺 scipy 时 `_sprite_evidence`
    # 会打 ⚠️ 并关闭判定 —— 那两项**按跳过**，不算「代码被改回去」。
    G10 = [754.0, 960.0, 1168.0, 1374.0, 1582.0]
    GY10_TD = [270.0, 478.0, 686.0]          # 自上而下（board_read 的传法）

    if not HAS_SCIPY:
        print("== 10/11. 跳过：缺 scipy → 「站在带标签节点上」判定不可用 ==")
        print("   装 scipy，或先跑 `python tools/pyenv.py` 换一个依赖齐全的解释器；"
              "这两项与求解器无关，不算失败。")
    else:
        print("== 10. 小人站在带标签节点上 → 不得被罚到旁边的空地 ==")
        a10 = np.zeros((864, 1920, 3), dtype=np.uint8)
        a10[458:498, 1148:1188] = (255, 240, 90)          # 节点图标 + 小人融合成一大块
        lab10 = [(1168, 560, "秘境行商", 1.0, "秘境行商")]   # 标签正下方
        cands10, onlab10 = L.detect_start_candidates_ex(
            Image.fromarray(a10), G10, GY10_TD, lab10)
        check(onlab10 is True, f"识别为「站在带标签节点上」（实际 {onlab10}）")
        check(cands10 and cands10[0] == (2, 1),
              f"第一名是 (2,1)（实际 {cands10[0] if cands10 else None}）")

        print("== 11. 干净小人（不站在标签下）→ 行为不变 ==")
        a11 = np.zeros((864, 1920, 3), dtype=np.uint8)
        a11[473:483, 955:965] = (255, 240, 90)            # 10x10 干净小人，落在 (1,1)
        cands11, onlab11 = L.detect_start_candidates_ex(
            Image.fromarray(a11), G10, GY10_TD, lab10)
        check(onlab11 is False, f"不判定为「站在带标签节点上」（实际 {onlab11}）")
        check(cands11 and cands11[0] == (1, 1),
              f"第一名是 (1,1)（实际 {cands11[0] if cands11 else None}）")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
