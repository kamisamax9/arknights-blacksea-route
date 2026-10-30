#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pixel_probe.py — 像素探针：只读 OCR **永远读不到**的那一层

为什么只剩这点东西
------------------
2026-10-27 起，节点**类型**一律读节点下方的中文标签（`label_ocr.py` + RapidOCR）。
色度/形态判类型已退役：四层深青「未知的诡秘」漏检 1/3，诡秘系图标一对多
（诡秘 / 不期而遇 / 得偿所愿 / 安全的角落），「凶戾」与「紧急作战」还是同一图标族。
旧的 `icon_classify.py` 类型分类器、训练标签 `icon_labels.json`、训练图 `refshots/`
于 2026-10-30 一并删除（见 CHANGELOG）。

本文件保留的唯一一件事：**白圈徽章**——叠在节点右上角的小白圈，文字里没有。
  ・压在「不期而遇」上：**两种**（2026-10-30 三结局新增，靠徽章内部字形分）——
      · **窄字形**（宽/高 < 0.85，骷髅面罩）= **泪之聚落**（三结局特殊事件，0 分、默认不进，可过路）
      · **宽字形**（蜘蛛/放射状）= **线人**（二/三/四层 4/10/10 分；五、六层没有线人）
  ・压在「失与得」上     = 三结局特殊失与得（**收益不变**，仍按普通失与得）
  ・压在「作战」上       = 特殊作战「**无效验尸**」（**收益同普通作战**，不改价）
  ・压在「林间空地」上   = **流窜居民**（居民据点的产物，供 `resident_infer.py` 反推据点）
同一个徽章在不同节点上是两回事，所以 `detect_badges()` / `detect_residents()` 只给候选格
+ 字形，**必须由调用方拿底下的节点类型过滤**（`label_ocr.apply_badges()` 就是这么用的）。

其余像素用途在别的文件里，别在这里重复实现：
  ・起点 / 整列格点自愈 → `label_ocr.py`（小人暖色像素，唯一「必定站在节点上」的证据）
  ・图标响应图          → `read_scene.response_map`（内盘 − 外环）

用法（手动核对徽章时）
----
  python tools/label_ocr.py 截图 --cols 5 --rows 4 --layer 2 --badges
  python tools/label_ocr.py 截图 --cols 7 --rows 5 --layer 3 --badges   # 三/四/五层会自动判有无右区
"""
import os

import numpy as np
from PIL import Image

# 与地图缩放相关的常数：一律按格距 sp 推导，换分辨率只需 set_scale 一次。
_BLOB = (26, 50)   # 白块徽章尺寸下限/上限 = 0.20~0.39 * sp
_BADGE = (8, 60)   # 徽章相对格心的偏移窗口 = 0.06~0.47 * sp


def set_scale(sp):
    """按格距 sp 重设徽章窗口。换模拟器分辨率后只需调用一次。

    2026-10-30：白块下限 0.20→**0.17**（流窜居民的乌鸦徽章只有 24–25px，
    0.20*124=24.8 会把 24px 的那枚漏掉）；见 `_white_blobs` 的方正度也会收紧到 8。
    """
    global _BLOB, _BADGE
    _BLOB = (int(round(0.17 * sp)), int(round(0.39 * sp)))
    _BADGE = (int(round(0.06 * sp)), int(round(0.47 * sp)))
    return dict(sp=sp, BLOB=_BLOB, BADGE=_BADGE)


def _stat_at(im, cx, cy, rad):
    """以 (cx,cy) 为心、半径 rad 的圆盘色度统计（供白圈徽章这类小图形用）。"""
    a = np.asarray(im.crop((int(cx - rad), int(cy - rad), int(cx + rad), int(cy + rad))),
                   dtype=np.float32)
    if a.shape[0] < 2 * rad or a.shape[1] < 2 * rad:
        return None
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    m = ((xx - rad) ** 2 + (yy - rad) ** 2) <= rad ** 2
    R = float(a[:, :, 0][m].mean()); G = float(a[:, :, 1][m].mean()); B = float(a[:, :, 2][m].mean())
    s = R + G + B + 1e-6; V = max(R, G, B)
    return dict(R=R, G=G, B=B, V=V, sat=(V - min(R, G, B)) / (V + 1e-6),
                rg=(R - G) / s, bg=(B - G) / s, gr=(G - R) / s)


def is_resident(f):
    """灰白小徽章的色度判据（必须采在徽章真实位置上——采格子中心一律漏检）。"""
    if not f:
        return False
    return (f["sat"] < 0.12 and 55 <= f["V"] <= 160 and abs(f["rg"]) < 0.06
            and abs(f["bg"]) < 0.08 and f["gr"] >= -0.04 and f["R"] < 170)


def _white_components(m):
    """白色掩码 → 连通域 [(xs, ys), …]。

    2026-10-30 修：旧实现用 20px 网格聚类（8 邻域），**贴着亮图标的徽章会和图标亮块
    合并**成一个 >800px 的大块被整块丢掉——四层 (3,3) 作战的狼头徽章就是这么漏的
    （实拍：狼头徽章与紫色作战图标的亮块相距约 35px，但落在相邻网格里 → 合并后超 maxn）。
    改用**像素级**连通域（scipy.ndimage.label）；没有 scipy 时退回旧网格法（会漏这种相邻的）。
    """
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return []
    try:
        from scipy import ndimage
        lab, n = ndimage.label(m)
        out = []
        for i in range(1, n + 1):
            yy, xx = np.nonzero(lab == i)
            if len(xx):
                out.append((xx, yy))
        return out
    except Exception:
        cell = 20
        grid = {}
        for i, (x, y) in enumerate(zip(xs, ys)):
            grid.setdefault((x // cell, y // cell), []).append(i)
        seen, comps = set(), []
        for k in list(grid):
            if k in seen:
                continue
            stack = [k]; seen.add(k); comp = []
            while stack:
                c = stack.pop(); comp += grid[c]
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nb = (c[0] + dx, c[1] + dy)
                        if nb in grid and nb not in seen:
                            seen.add(nb); stack.append(nb)
            comps.append((xs[np.array(comp)], ys[np.array(comp)]))
        return comps


def _white_blobs(im, minw=None, maxw=None, minn=140, maxn=800):
    """找「白圈徽章」型的白色连通块。

    自适应：**不假设徽章相对格子的固定偏移**，先找白块，再匹配到最近的格子。
    地图缩放或平移时照样工作，而写死 (+34,-30) 会失效。
    """
    minw = _BLOB[0] if minw is None else minw
    maxw = _BLOB[1] if maxw is None else maxw
    out = []
    for cxs, cys in _white_components(_white_mask(im)):
        if not (minn <= len(cxs) <= maxn):
            continue
        w = int(cxs.max() - cxs.min())
        h = int(cys.max() - cys.min())
        if minw <= w <= maxw and (minw - 4) <= h <= maxw and abs(w - h) <= 8:
            out.append((int(cxs.mean()), int(cys.mean()), w, h))
    return out


def _white_mask(im):
    """近白像素掩码（HUD/行动力区已抹掉）——徽章检测与居民检测共用。"""
    a = np.asarray(im.convert("RGB")).astype(np.float32)
    H, W, _ = a.shape
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    m = (R > 150) & (G > 150) & (B > 150) & (np.abs(R - G) < 40) & (np.abs(G - B) < 40)
    m[: int(0.13 * H), :] = False
    m[int(0.85 * H):, :] = False
    # 左侧 UI 条：按宽度比例（1920→80px）。**不能写死 80**——小裁图/局部 fixture
    # （如 examples/badge-merged-gain.png 120px 宽）会被整条抹掉，徽章直接消失。
    m[:, : int(round(0.042 * W))] = False
    m[: int(0.30 * H), int(0.85 * W):] = False       # 行动力徽章区：节点被灰化，易误判
    return m


def _blob_list(im, minn=140, maxn=2000, maxwh=70):
    """所有「可能含徽章」的白块，**不要求方正**（贴图标被合并的大块也算）。

    返回 [{'cx','cy','w','h','npx'}, …]。字形匹配用（`detect_badges`）；
    `_white_blobs` 仍是「独立成块、尺寸方正」的严格版（居民检测用）。
    """
    out = []
    for cxs, cys in _white_components(_white_mask(im)):
        n = len(cxs)
        if not (minn <= n <= maxn):
            continue
        w = int(cxs.max() - cxs.min())
        h = int(cys.max() - cys.min())
        if w > maxwh or h > maxwh:
            continue
        out.append({"cx": int(cxs.mean()), "cy": int(cys.mean()),
                    "w": w, "h": h, "npx": n})
    return out


# 徽章内部字形的宽/高（**辅助校验**，主判据是下面的 NCC 模板）：
#   泪之聚落（骷髅面罩） 14–16 宽 × 20–22 高 → 0.68–0.73
#   线人 / 羊角 / 狼头 / 橡果 / 乌鸦都落在 0.9–1.05 → 宽/高**分不开**它们
GLYPH_RATIO = 0.85


def badge_shape(im, bx, by, rad=None):
    """看白色徽章圈**内部**的深色字形，返回 {'shape','ratio','w','h','dark'}。

    shape：`narrow`（骷髅面罩/泪之聚落）= 宽/高 < GLYPH_RATIO；`wide`（蜘蛛/线人）反之；
    深色像素太少（<40）返回 `unknown`——让调用方按「不期而遇」原值算并提示人工确认。
    `rad` 默认按当前 `set_scale` 的 sp 取 0.10×sp（124px 格距 → 12px）。
    """
    if rad is None:
        rad = max(8, int(round(0.10 * (_BLOB[0] / 0.17))))
    rad = int(rad)
    if rad < 6:
        return {"shape": "unknown", "ratio": None, "w": 0, "h": 0, "dark": 0}
    a = np.asarray(im.crop((int(bx - rad), int(by - rad),
                            int(bx + rad), int(by + rad))), dtype=np.int16)
    if a.shape[0] < 2 * rad or a.shape[1] < 2 * rad:
        return {"shape": "unknown", "ratio": None, "w": 0, "h": 0, "dark": 0}
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    inside = ((xx - rad) ** 2 + (yy - rad) ** 2) <= (rad - 1) ** 2
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    # 字形是黑/深灰；白圈本体是亮的，不能算进去
    dark = (R < 100) & (G < 100) & (B < 100) & inside
    ys, xs = np.nonzero(dark)
    if len(xs) < 40:
        return {"shape": "unknown", "ratio": None, "w": 0, "h": 0, "dark": int(len(xs))}
    w = int(xs.max() - xs.min() + 1)
    h = int(ys.max() - ys.min() + 1)
    ratio = w / float(max(1, h))
    return {"shape": ("narrow" if ratio < GLYPH_RATIO else "wide"),
            "ratio": round(ratio, 2), "w": w, "h": h, "dark": int(len(xs))}


# ── 徽章字形模板（2026-10-30 实拍标本，24×24 灰度 NCC）──────────────────
# 为什么不用「宽/高」硬判：实拍已有 6 种白圈徽章（骷髅/蜘蛛/羊角/狼头/橡果/乌鸦），
# 宽/高只能分出「窄骷髅」和「其余」，后四种都落在 0.9–1.05 → 互相误判。
# 为什么不用二值 IoU：徽章贴着亮图标/红背景时白圈被眩光切碎，二值掩码对不齐
# （五层 (3,2) 失与得的羊角只到 0.37）→ 改**灰度归一化互相关（NCC）+ 小范围平移搜索**：
# 实拍同类 0.84–1.00、异类 0.1–0.4，且对 1–6px 的中心偏移不敏感。
GLYPH_NAMES = ("tears", "informant", "ram", "wolf", "acorn", "raven")
GLYPH_LABELS = {
    "tears": "泪之聚落（骷髅面罩）",
    "informant": "线人（蜘蛛）",
    "ram": "三结局失与得（羊角骷髅）",
    "wolf": "无效验尸（狼头）",
    "acorn": "迷藏（橡果）",
    "raven": "流窜居民（乌鸦）",
}
GLYPH_NCC = 0.55          # 低于它 → unknown（宁可不判，也不要把 0 分格当成 10 分格）
_GLYPH_N = 24
_TEMPLATES = None


def _glyph_vec(im, x, y, rad, n=_GLYPH_N):
    """徽章 2*rad 方框 → n×n 灰度、零均值单位方差向量（模板/现场同一口径）。"""
    crop = im.crop((int(x - rad), int(y - rad), int(x + rad), int(y + rad)))
    a = np.asarray(crop.convert("L").resize((n, n), Image.BILINEAR), dtype=np.float32)
    return (a - a.mean()) / (a.std() + 1e-6)


def _templates():
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = {}
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")
        for name in GLYPH_NAMES:
            p = os.path.join(base, "glyph-%s.png" % name)
            if not os.path.isfile(p):
                continue
            im = Image.open(p).convert("RGB")
            _TEMPLATES[name] = _glyph_vec(im, im.width / 2.0, im.height / 2.0,
                                          im.width / 2.0)
    return _TEMPLATES


def _glyph_at(im, x, y, rad):
    """中心固定：{模板名: NCC}。"""
    tpl = _templates()
    v = _glyph_vec(im, x, y, rad)
    return {name: float((v * t).mean()) for name, t in tpl.items()}


def _best_of(scores):
    name = max(scores, key=scores.get)
    return scores[name], name


def badge_glyph(im, bx, by, rad, search=6):
    """字形模板匹配：返回 {'glyph','score','dx','dy','rad'}。

    在 (bx,by) ±search 像素、rad±1 内找最佳 NCC——徽章可能贴着图标、或白块中心被眩光带偏。
    glyph ∈ tears/informant/ram/wolf/acorn/raven/unknown；实拍同类 ≥0.84。
    """
    tpl = _templates()
    if not tpl:
        return {"glyph": "unknown", "score": 0.0, "dx": 0, "dy": 0, "rad": rad}
    best = (-9.0, "unknown", 0, 0, int(rad))
    rng = range(-int(search), int(search) + 1)
    for r in (int(rad) - 1, int(rad), int(rad) + 1):
        if r < 6:
            continue
        for dx in rng:
            for dy in rng:
                s, name = _best_of(_glyph_at(im, bx + dx, by + dy, r))
                if s > best[0]:
                    best = (s, name, dx, dy, r)
    score = round(float(best[0]), 3)
    return {"glyph": (best[1] if score >= GLYPH_NCC else "unknown"),
            "score": score, "dx": best[2], "dy": best[3], "rad": best[4]}


def _glyph_scan(im, cx, cy, radius, rad, step=2):
    """在大块（徽章与图标合并）里扫一遍，找字形中心。返回 (score,name,x,y,rad)。

    先按 step 粗扫，再在粗解 ±2px 内细扫（步长 1）——徽章中心常落在奇数像素上，
    只粗扫会差 1–2px，NCC 从 0.96 掉到 0.62（五层失与得实测）。
    """
    best = (-9.0, "unknown", int(cx), int(cy), int(rad))
    for sx in range(int(cx - radius), int(cx + radius) + 1, int(step)):
        for sy in range(int(cy - radius), int(cy + radius) + 1, int(step)):
            s, name = _best_of(_glyph_at(im, sx, sy, rad))
            if s > best[0]:
                best = (s, name, sx, sy, rad)
    # 细扫：围绕粗解 ±2px
    bx, by = best[2], best[3]
    fine = best
    for sx in range(bx - 2, bx + 3):
        for sy in range(by - 2, by + 3):
            s, name = _best_of(_glyph_at(im, sx, sy, rad))
            if s > fine[0]:
                fine = (s, name, sx, sy, rad)
    return fine


def _cell_of_blob(blob, gx, gy, cols, rows, right_from):
    """白块中心 → 格子坐标（**grid 坐标**：y=0 是最下一行，与路线工具一致）。"""
    bx, by = blob[0], blob[1]
    best = None
    for c in range(min(cols, right_from)):
        for r in range(rows):
            cx, cy = gx[c], gy[rows - 1 - r]
            dx, dy = bx - cx, by - cy
            if _BADGE[0] <= dx <= _BADGE[1] and -_BADGE[1] <= dy <= -_BADGE[0]:
                d = abs(dx - 34) + abs(dy + 30)        # 以旧偏移为参考排序
                if best is None or d < best[0]:
                    best = (d, (c, rows - 1 - r))
    return best[1] if best else None


def detect_badges(im, gx, gy, cols, rows, right_from=None):
    """找白圈徽章 → [{'cell','blob','shape','glyph'}, …]，**只给证据，不解释含义**。

    含义必须由调用方按「徽章底下的节点类型」翻译（`label_ocr.apply_badges`）：
    不期而遇 + tears/informant = 泪之聚落/线人；失与得 + ram、作战 + wolf = 三结局特殊节点（不改价）；
    任意节点 + acorn = 迷藏（所在节点收益 +12，建场景时并入）；空地 + raven = 流窜居民。

    2026-10-30 两条实拍教训（五层）：
      · 徽章贴着亮图标（失与得的青色光晕）会被**像素级合并**成大块 → 用白块当候选、在块内
        用字形 NCC **扫一遍**找真正的徽章中心（`_glyph_scan`）；
      · 红/彩色背景上的乌鸦徽章过不了旧的 `is_resident` 色度闸 → `detect_badges` **不再用色度闸**，
        判定只认「有字形（dark≥40）+ 字形 NCC≥0.55」；色度闸只留给旧的 `detect_residents`。
    """
    if right_from is None:
        right_from = {11: 8, 12: 9, 13: 10, 14: 11}.get(cols, cols)
    sp = ((float(gx[-1]) - float(gx[0])) / max(1, len(gx) - 1)) if len(gx) > 1 else 124.0
    rad = max(8, int(round(0.10 * sp)))
    best_by_cell = {}          # 同一格可能被多个白块命中（合并块 + 碎片）→ 取分数最高的

    def _near_badge_window(cx, cy):
        """粗筛：这个白块离某个格子的「右上徽章窗口」够近吗（含 ±8px 容差）。"""
        for c in range(min(cols, right_from)):
            for r in range(rows):
                dx = cx - gx[c]
                dy = cy - gy[rows - 1 - r]
                if (_BADGE[0] - 8 <= dx <= _BADGE[1] + 8
                        and -_BADGE[1] - 8 <= dy <= -_BADGE[0] + 8):
                    return True
        return False

    def _take(cx, cy, glyph, blob):
        cell = _cell_of_blob((cx, cy, 0, 0), gx, gy, cols, rows, right_from)
        if cell is None or glyph.get("glyph") == "unknown":
            return
        shape = badge_shape(im, cx, cy, rad=int(glyph.get("rad") or rad))
        if (shape.get("dark") or 0) < 40:
            return
        prev = best_by_cell.get(cell)
        if prev is None or (glyph.get("score") or 0) > (prev["glyph"].get("score") or 0):
            best_by_cell[cell] = {"cell": cell,
                                  "blob": (int(cx), int(cy), blob["w"], blob["h"]),
                                  "shape": shape, "glyph": glyph}

    for blob in _blob_list(im):
        # 常规：块本身就像徽章（方正、尺寸在徽章区间）→ 原地小范围找字形
        lo, hi = _BLOB
        square = abs(blob["w"] - blob["h"]) <= 8
        if square and lo <= blob["w"] <= hi and (lo - 4) <= blob["h"] <= hi:
            g = badge_glyph(im, blob["cx"], blob["cy"], rad, search=6)
            _take(blob["cx"] + g["dx"], blob["cy"] + g["dy"], g, blob)
            continue
        # 合并块 / 偏移块：在块内扫字形中心（步长 2；先粗筛离徽章窗口太远的块，省时间）
        if not _near_badge_window(blob["cx"], blob["cy"]):
            continue
        radius = max(6, max(blob["w"], blob["h"]) // 2)
        s, name, sx, sy, r = _glyph_scan(im, blob["cx"], blob["cy"], radius, rad, step=2)
        if s < GLYPH_NCC:
            continue
        g = {"glyph": name, "score": round(float(s), 3), "dx": 0, "dy": 0, "rad": r}
        _take(sx, sy, g, blob)
    return [best_by_cell[q] for q in sorted(best_by_cell)]


def detect_residents(im, gx, gy, cols, rows, right_from=None, empty_cells=None):
    """找白圈徽章并映射到格子，返回候选格 [(c, r), …]（**不含**类型判断）。

    `gy` 与路线工具一致：自下而上（gy[0] = 最下一行）。
    自适应做法：先找白块（_white_blobs），再把白块匹配到「右上方偏移窗口」内的格子。
    2026-10-26 实测（四层图）：采格子中心时旧判据 3 只全漏；改成这版 **3/3 命中、0 误报**。
    右区（x 大）不会出现流窜居民，用 right_from 直接排除。

    `empty_cells`：给了就只保留这些格（= 底下是林间空地 → 流窜居民）。
    **不要用它来判类型**：压在「不期而遇」上的同一枚徽章是**线人**，
    调用方应拿 grid 里的节点类型分别处理（label_ocr.py --badges）。
    """
    if right_from is None:
        right_from = {11: 8, 12: 9, 13: 10, 14: 11}.get(cols, cols)
    out = []
    for blob in _white_blobs(im):
        # 白块之后再过一道色度：金色/彩色图标（秘境行商等）也是亮块，但饱和度不低。
        if not is_resident(_stat_at(im, blob[0], blob[1], 14)):
            continue
        cell = _cell_of_blob(blob, gx, gy, cols, rows, right_from)
        if cell is not None and cell not in out:
            out.append(cell)
    if empty_cells is not None:
        out = [h for h in out if h in empty_cells]
    return out
