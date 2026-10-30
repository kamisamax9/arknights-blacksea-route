#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_read.py — 截图 → 认层 → 建格点 → 节点表（**不依赖手输层数**）

为什么要有这个文件
------------------
`grab.py --scene` 以前把 cols/rows 交给调用方：插件不带 `layer` 时默认 11×5，
于是一层（5×3）被当成三层读。真实节距 ~216px 被锁成 108px（半节距混叠），
5 列变 11 列、3 行变 5 行，只认出 8 个节点，还把「至少应有 11」当成「不是地图」。
（2026-10-29 实况；同一个坑见 references/19-已知坑.md「认层只能靠标题/HUD」）

现在的做法，按 references 里已经定死的判据：
  1. **层号从标题 OCR 认**（唯一权威）；认不出就**报错**，不再瞎猜一个 11×5；
  2. 格点由**节点标签 OCR** 建（label_ocr.choose_lattice + align_lattice），
     不用响应图的无约束拟合——拟合会被出发点那个白色小人吸走，整格偏半列；
  3. 起点用小人检测（小人站在起点节点**左侧**），归一到 (c, r)；
  4. 一/二层补一次「弱响应格点」：林间空地的图标极暗（实测 resp 8~14），
     OCR 读不到、15 的阈值也过不去，不补就漏格（本图一层漏了 (1,2)/(2,2)）。

坐标约定：返回的 (c, r) 是**（列, 行）**，r=0 是**最下面**一行（与路线工具一致）。
"""
import argparse
import os
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import label_ocr as LO          # noqa: E402
import read_scene as RS         # noqa: E402

# 层名关键词 → 层号（画面顶部中间那行字）
LAYER_TITLE = {
    "玻利瓦尔": 1,
    "甜美的伤口": 2,
    "血色空脉": 3,
    "受害者腐殖": 4,
    "卡德霍之颅": 5,
    "盲目者所见": 6,       # 四结局六层（5×4）
    "源流交汇处": 6,       # 三结局六层（6×5，构型名=源流）
}
LAYER_DIMS = LO.LAYER_DIMS      # {1:(5,3), 2:(5,4), 3:(11,5), 4:(12,5), 5:(14,5), 6:(5,4)}

# 弱响应格点：只在一/二层开（这两层林间空地是合法中继，图标又极暗）。
# 实测空地 resp 8.2 / 13.2，空格子 -13.9 ~ 0.6，阈值 6 能把两者分开。
WEAK_THR = 6.0
WEAK_LAYERS = (1, 2)


def _raw_ocr(path):
    res, _ = LO.engine()(path)
    out = []
    for item in (res or []):
        txt = item[1] if len(item) > 1 else ""
        if txt:
            out.append(str(txt).strip())
    return out


def _match_layer(text):
    """标题原文 → (layer, 匹配分)。

    用**滑动窗口的模糊匹配**而不是 `key in txt`：标题是美术字，OCR 掉/错一两个字
    很常见（`卡德霍之颅`→`卡德霍之频`）。宁可匹配到再让格点自检兜底，
    也不要因为一个字对不上就把整张图判成「认不出」。
    """
    import difflib
    txt = "".join(str(text).split())
    if not txt:
        return None, 0.0
    best, score = None, 0.0
    for key, val in LAYER_TITLE.items():
        n = len(key)
        for i in range(0, max(1, len(txt) - n + 1)):
            for w in (n, n + 1):           # n=原样命中(1.0)，n+1=多认一个字也算
                seg = txt[i:i + w]
                if len(seg) < n:
                    continue
                r = difflib.SequenceMatcher(None, key, seg).ratio()
                if r > score:
                    best, score = val, r
    return (best if score >= 0.66 else None), score


def _clusters(vals, tol=40.0):
    """一维坐标聚类（列/行去重），返回各簇中心。"""
    vs = sorted(vals)
    groups = []
    for v in vs:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def read_layer(im):
    """从标题区认层。返回 (layer|None, 标题原文, 匹配分)。"""
    w, h = im.size
    crop = im.crop((int(w * 0.30), 0, int(w * 0.72), int(h * 0.10)))
    fd, tmp = tempfile.mkstemp(suffix="_title_ocr.png")
    os.close(fd)
    try:
        crop.save(tmp)
        texts = _raw_ocr(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    txt = "".join(texts)
    layer, score = _match_layer(txt)
    return layer, txt, score


def read_board(path, layer=None):
    """截图 → {ok, layer, title, cols, rows, gx, gy, cells, start, note}

    认不出层 / 标签太少时 ok=False，并给出可读的 error —— 调用方据此报错，
    **不允许**悄悄退回 11×5（那正是这次要修的 bug）。
    """
    im = Image.open(path).convert("RGB")
    title = ""
    score = 1.0
    note = ""
    if layer is None:
        layer, title, score = read_layer(im)
        if layer is None:
            return {"ok": False, "layer": None, "title": title,
                    "error": f"认不出层（标题 OCR 读到「{title or '空'}」）"}
        if score < 0.92:
            note = f"标题 OCR 模糊匹配 {score:.2f}，已按第 {layer} 层读"
    # 六层两个变体：源流交汇处（三结局）= 6×5，盲目者所见（四结局）= 5×4
    if layer == 6 and not title:
        _t, title, _s = read_layer(im)      # 手传 --layer 6 时也读标题分变体
    _six = "源流" if (layer == 6 and "源流" in (title or "")) else None
    cols, rows = LO.layer_dims(layer, _six)

    labels = LO.read_image(path)
    resp = RS.response_map(path)
    pairs = LO.pair_nodes(labels, resp)
    paired = [p for p in pairs if p[3]]
    if len(paired) < 2:
        return {"ok": False, "layer": layer, "title": title,
                "error": f"节点标签太少（配对成功 {len(paired)} 个），读不出格点"}

    # 2026-10-30：三/四/五层可能**没有右区**（未获取四结局前置藏品，左区居中）。
    # 先按「完整宽度 vs 左区宽度」选列，避免完整宽度把格点顶出地图区。
    if _six:
        cols = LO.layer_dims(6, "源流")[0]
        gx = sorted(LO.choose_lattice([p[0] for p in paired], cols, center=im.size[0] / 2.0))
        ginfo = {"has_right": False}
        note = (note + "；" if note else "") + "六层变体：源流交汇处（6×5，无右区）"
    else:
        cols, gx, ginfo = LO.choose_layer_cols([p[0] for p in paired], layer, im.size[0])
    rows = LO.layer_dims(layer, _six)[1]
    if layer in (3, 4, 5) and ginfo.get("cols"):
        note = (note + "；" if note else "") + (
            f"格点按 {cols} 列（{'完整宽度' if ginfo.get('has_right') else '仅左区/无右区'}）")
    # gy 用**下→上**的顺序（r=0 是最下面一行），与路线工具的坐标一致
    gy = sorted(LO.choose_lattice([p[1] for p in paired], rows, center=0.49 * im.size[1]), reverse=True)
    try:
        gx, gy, align_note = LO.align_lattice(im, labels, gx, gy, cols, rows)
        if align_note:
            note = note + "；" + align_note if note else align_note
    except Exception:
        pass

    # 层/网格自检：① 拟合出的节距要和标签实测的列距/行距对得上；
    # ② 标签到最近格点的残差不能太大。任一条不过 ⇒ 报错让玩家复核，
    # 绝不把「一层读成三层」那种错表端上去（2026-10-29 的坑）。
    dxm = float(np.median(np.diff(sorted(gx)))) if len(gx) > 1 else 0.0
    dym = float(np.median(np.diff(sorted(gy)))) if len(gy) > 1 else 0.0
    cxs = _clusters([p[0] for p in paired])
    cys = _clusters([p[1] for p in paired])
    gapx = [b - a for a, b in zip(cxs, cxs[1:])]
    gapy = [b - a for a, b in zip(cys, cys[1:])]
    bad = ""
    # ① 格点必须落在地图区内：多一行/一列就会顶进 HUD 或底栏
    Hg, Wg = resp.shape
    top_lim = int(RS.HUD_TOP_RATIO * Hg)
    bot_lim = int(Hg - RS.HUD_BOT_RATIO * Hg)
    if min(gy) < top_lim or max(gy) > bot_lim or min(gx) < 0.04 * Wg or max(gx) > 0.96 * Wg:
        bad = f"格点越出地图区域（y {min(gy):.0f}~{max(gy):.0f}）——行列数多了/少了"
    # ② 拟合节距要和标签实测的列距/行距对得上
    # 2026-10-30：用**中位数**而不是最小值——同一列里个别标签的 x 抖动/换行
    # 会切出一个 50px 的小簇，拿 min(gap) 当实测节距会误判整张图（三层全图/左区居中都踩过）。
    if not bad and len(cxs) >= 3 and gapx and dxm:
        emp = float(np.median(gapx))
        if abs(dxm - emp) / emp > 0.25:
            bad = f"列距对不上：标签实测 {emp:.0f}px，按第 {layer} 层是 {dxm:.0f}px"
    if not bad and len(cys) >= 3 and gapy and dym:
        emp = float(np.median(gapy))
        if abs(dym - emp) / emp > 0.25:
            bad = f"行距对不上：标签实测 {emp:.0f}px，按第 {layer} 层是 {dym:.0f}px"
    # ③ 标签到最近格点的残差
    if not bad:
        rx = [min(abs(p[0] - g) for g in gx) for p in paired]
        ry = [min(abs(p[1] - g) for g in gy) for p in paired]
        bad_x = float(np.median(rx)) / dxm if dxm else 0.0
        bad_y = float(np.median(ry)) / dym if dym else 0.0
        if len(paired) >= 3 and (bad_x > 0.30 or bad_y > 0.30):
            bad = f"格点与节点标签对不上（残差 x {bad_x:.2f} / y {bad_y:.2f} 格）"
    if bad:
        return {"ok": False, "layer": layer, "title": title, "cols": cols, "rows": rows,
                "error": f"{bad}——层可能认错了，请核对标题/手传 --layer"}

    grid = LO.assign(labels, gx, gy, cols, rows)
    # 白圈徽章（2026-10-30）：不期而遇+字形 = 泪之聚落/线人；失与得/作战只提示、不改价；
    # 橡果=迷藏（记坐标，建场景时 +12 并入所在节点）；空地+乌鸦=流窜居民。
    # 节点表里会直接写改写后的类型。
    badge_notes, acorn_cells = [], []
    try:
        _bd = LO.apply_badges(im, grid, gx, gy, cols, rows, layer=layer,
                          empty_label=("林间空地" if _six else "未探明节点（小圆点）"))
        badge_notes = _bd["notes"]
        acorn_cells = _bd["acorn"]
    except Exception as exc:
        badge_notes = [f"!! 徽章检测失败：{exc}（不期而遇按普通值算）"]
    # 右区是否存在：看**格点选择**（完整宽度）而不是看节点有没有被 OCR 到。
    # 若完整宽度但右区一格都没有，提示可能漏检；若左区宽度，则本层就是无右区。
    has_right = LO.has_right_cols(layer, cols)
    right_cells = [q for q in grid if q[0] > LO.LEFT_MAX.get(layer, 99)]
    if has_right and not right_cells:
        note = (note + "；" if note else "") + "⚠️ 完整宽度截图但右区没有读到节点（OCR 漏检/裁切？）"

    # 起点：小人检测。候选里优先取**没有标签**的那格（进层时起点=空地，不写字）
    #
    # ⛔ 2026-10-29 修：这条「必须无标签」只在**进层阶段**成立。滚动阶段玩家会站在
    # 带标签的节点上（实测：站在 (2,4) 秘境行商）。那时他脚下的格子**本来就有标签**，
    # 按旧逻辑被跳过 → 落到旁边的无标签空地 (3,3)，与真实位置差一整格。
    # `detect_start_candidates_ex` 的 on_labeled_node 就是这种情况的判别信号。
    start = None
    try:
        cands, on_labeled = LO.detect_start_candidates_ex(im, gx, list(reversed(gy)), labels)
    except Exception:
        cands, on_labeled = [], False
    if on_labeled and cands:
        # 玩家正站在带标签节点上 → 候选第一名就是它，**不要再要求「不在 grid 里」**
        c, r = cands[0]
        start = (c, rows - 1 - r)
    else:
        for c, r in cands:
            cell = (c, rows - 1 - r)
            if cell not in grid:
                start = cell
                break
    if start is None and cands:
        c, r = cands[0]
        start = (c, rows - 1 - r)

    cells = dict(grid)
    if start is not None:
        cells.setdefault(start, ("起点", 1.0, ""))

    # 弱响应格点（仅一/二层）：空格子 resp ≈ 0 或负，空地 8~14
    weak = []
    if layer in WEAK_LAYERS:
        H, W = resp.shape
        for c in range(cols):
            for r in range(rows):
                q = (c, r)
                if q in cells:
                    continue
                # 取格点周围 ±12px 的最大响应：空地图标小，格点常差 10px 就掉到阈值下
                x, y = int(gx[c]), int(gy[r])
                x0, x1 = max(0, x - 12), min(W, x + 13)
                y0, y1 = max(0, y - 12), min(H, y + 13)
                v = float(resp[y0:y1, x0:x1].max()) if y1 > y0 and x1 > x0 else 0.0
                if v > WEAK_THR:
                    cells[q] = ("空地?", 0.0, "")
                    weak.append((q, v))

    return {
        "ok": True, "layer": layer, "title": title,
        "cols": cols, "rows": rows, "has_right": has_right,
        "gx": [float(x) for x in gx], "gy": [float(y) for y in gy],
        "cells": {q: t[0] for q, t in cells.items()},
        "start": start, "weak": weak, "note": note,
        "badges": badge_notes, "acorn": acorn_cells,
        "error": "",
    }


def format_board(res, thr=15.0):
    """节点表 → 人/插件都能读懂的多行文本（保留「识别到 N 个节点」这句）。"""
    lines = []
    if not res.get("ok"):
        lines.append(f"!! {res.get('error', '读取失败')}")
        return "\n".join(lines)
    lines.append(f"层：{res['layer']}（标题「{res['title']}」）")
    if res.get("note"):
        lines.append(f"  {res['note']}")
    if res.get("start") is not None:
        lines.append(f"起点：{res['start']}（小人检测）")
    lines.append(f"格点 {res['cols']}×{res['rows']}  x: {[round(v) for v in res['gx']]}")
    lines.append(f"                     y: {[round(v) for v in res['gy']]}")
    if res.get("layer") in (3, 4, 5):
        lines.append("右区：有（熄日余径，已获取四结局前置藏品）" if res.get("has_right")
                     else "右区：无（本层无熄日余径；左区居中，不预留 AP）")
    if res.get("badges"):
        lines.append("徽章：")
        for b in res["badges"]:
            lines.append("  · " + b)
    if res.get("acorn"):
        lines.append("迷藏（橡果）：" + "、".join(f"({c},{r})" for c, r in res["acorn"])
                     + " → 所在节点收益 +12（建场景时并入；带橡果的空地放行）")
    lines.append(f"识别到 {len(res['cells'])} 个节点")

    rows = res["rows"]
    for (c, r), txt in sorted(res["cells"].items(), key=lambda kv: (-kv[0][1], kv[0][0])):
        x = res["gx"][c]
        y = res["gy"][r]
        tag = "  [弱]" if (c, r) in [q for q, _ in res.get("weak", [])] else ""
        lines.append(f"  ({c},{r}) {txt:<8s} px=({round(x)},{round(y)}){tag}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="截图 → 认层 + 节点表（自动认层）")
    ap.add_argument("image")
    ap.add_argument("--layer", type=int, default=None, help="手传覆盖（正常不用传）")
    a = ap.parse_args()

    try:
        res = read_board(a.image, a.layer)
    except ImportError as e:
        raise SystemExit(f"❌ 缺 OCR 依赖（{e}）——按 label_ocr 的提示装 rapidocr_onnxruntime")
    except Exception as e:
        raise SystemExit(f"❌ 读图失败：{e}")
    print(format_board(res))
    if not res.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
