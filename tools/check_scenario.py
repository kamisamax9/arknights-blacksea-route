#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_scenario.py — 校验 scenario 的连通关系是否**完全来自构型表**

为什么需要
----------
2026-10-27：有会话盯着截图里一根断掉的线，怀疑 `(1,2)-(2,2)` 不连通，
差点把边表改掉。**图像不能用来否定构型表**——截图会有遮挡、暗线、缩放失真，
而边表是逐条核验过的（`check_configs.py` 保证条数、连通、端点都对）。

但当时没有任何工具能拦住"手工把边删了"这件事。本脚本就是那道闸：

  scenario 的左区边必须和构型表**逐条一致**，少一条或多一条都报错。

用法
----
  python tools/check_scenario.py runs/L2-c7-2340.json --layer 2 --config 7
  python tools/check_scenario.py 场景.json --layer 4 --config L6 --allow-extra 右区

退出码：0 = 一致；1 = 有差异
"""
import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import build_scenario as BS      # noqa: E402
import label_ocr as LO           # noqa: E402


def norm(e):
    a, b = tuple(e[0]), tuple(e[1])
    return (a, b) if a <= b else (b, a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--layer", type=int, required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--left-max", type=int, default=None,
                    help="左区最右列；默认按层查表（右区不在构型表里，不参与比对）")
    ap.add_argument("--allow-extra", nargs="?", const="", default=None,
                    help="容忍场景里**多出**的边（只警告不判错；少了的边仍然 ❌）。"
                         "可附一句理由，如 --allow-extra 右区接缝。缺省：多边也算 ❌")
    a = ap.parse_args()

    try:
        cfg_edges = {norm(e) for e in BS.config_edges(a.layer, a.config)}
    except SystemExit as exc:
        print(f"❌ 取不到构型 {a.config} 的边表：{exc}")
        return 1
    cfg_cells = {p for e in cfg_edges for p in e}
    lm = a.left_max if a.left_max is not None else LO.LEFT_MAX.get(a.layer, 99)
    if a.layer == 5 and str(a.config).upper() == "L8":
        lm = 8

    sc = json.load(open(a.scenario, encoding="utf-8-sig"))   # 容忍 Windows 的 UTF-8 BOM
    sc_edges = {norm(e) for e in sc["edges"]}

    # 只看左区：右区的连通关系没有构型表
    def in_left(e):
        return e[0][0] <= lm and e[1][0] <= lm

    cfg_l = {e for e in cfg_edges if in_left(e)}
    sc_l = {e for e in sc_edges if in_left(e)}

    missing = sorted(cfg_l - sc_l)
    extra = sorted(sc_l - cfg_l)

    print(f"层 {a.layer} 构型 {a.config}：构型表左区 {len(cfg_l)} 条 / 场景左区 {len(sc_l)} 条")
    if missing:
        print(f"  ❌ 场景**少了** {len(missing)} 条边（构型表里有）：")
        for e in missing[:10]:
            print(f"       {e[0]}-{e[1]}")
    if extra:
        # --allow-extra：多出的边降为警告（例如右区接缝、待收录的新边）；缺边仍然一票否决
        mark, hint = ("⚠️", "（--allow-extra 放行"
                      + (f"：{a.allow_extra}" if a.allow_extra else "") + "）") \
            if a.allow_extra is not None else ("❌", "")
        print(f"  {mark} 场景**多了** {len(extra)} 条边（构型表里没有）{hint}")
        for e in extra[:10]:
            print(f"       {e[0]}-{e[1]}")
    if not missing and (not extra or a.allow_extra is not None):
        print("  ✅ 左区连通关系与构型表一致"
              + ("（多出的边已按 --allow-extra 放行——记得把确认的边补进构型表）" if extra else
                 "（没有用图像改动过）"))
        return 0
    print("\n   → 连通关系只能来自构型表。截图有遮挡/暗线/失真，**不能用来否定或补充边**。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
