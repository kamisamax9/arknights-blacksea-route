#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_config.py — 用构型表覆盖 scenario 里的「定点节点」和「险路尽头」

为什么必须做成工具
------------------
「判出构型 → 用该构型的尽头坐标覆盖 OCR 结果」这一步，我手工做过三次、漏过两次：
- 四层 L6：(3,1) 是伪装成「未知的诡秘」的尽头，我当普通诡秘铺进去，路线第一步就走进了禁区
- 一层 构型3：(4,2) 同理，路线最后一步白走

**漏一次就是整条路线作废，所以不能再靠记性。**

用法
----
  python apply_config.py scenario.json --layer 1 --config 3
  python apply_config.py scenario.json --layer 4 --config L6 --dry-run

做的事
------
1. 把该构型的**险路尽头**全部改成「险路尽头」，价值按层/终点模式/**二层四结局问询**定
   （一/四层 -99；二层 四结局=要 → 8、不要 → 0；险路尽头模式下按 `ending_final_value`）
2. 把该构型的**固定节点**（目前只有一层的诡意行商）改成对应类型
3. 打印改动清单；--dry-run 只打印不写回
"""
import argparse
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "..", "references")
sys.path.insert(0, HERE)
import rules as R                   # noqa: E402  终点模式口径的唯一真源

# 一层的坐标 → 详见 17-一层构型 的索引表（起点/尽头/固定节点）
# 2026-10-29 修复：构型 1 原来写 "fixed": {}，与 17-一层构型.md 末尾的
# 「诡意行商固定位置」表（构型1 → (3,1)）和 20-一层特殊规则.md 矛盾——
# 后果是 route.py / label_ocr.py --config 1 都不覆盖 (3,1)，
# 那格在地图上显示「未知的诡秘」→ 构型 1 按诡秘(2分)/空地(0分)算，少 4 分 + 12 源石锭。
L1 = {
    "1": {"start": (0, 1), "ends": [(4, 0), (4, 2)],
          "fixed": {(3, 1): "诡意行商"}},
    "2": {"start": (1, 1), "ends": [(4, 1)], "fixed": {(3, 1): "诡意行商"}},
    "3": {"start": (2, 1), "ends": [(0, 0), (4, 2)],
          "fixed": {(1, 2): "诡意行商", (4, 1): "诡意行商"}},
}

# 四层：11-四层左区构型 末尾的尽头表
L4_ENDS = {
    "L1": [(3, 4), (6, 0), (7, 3)],
    "L2": [(0, 4), (4, 4), (5, 1)],
    "L3": [(0, 1), (6, 2), (7, 3)],
    "L4": [(2, 4), (7, 1), (7, 4)],
    "L5": [(1, 3), (4, 4), (7, 3)],
    "L6": [(0, 4), (3, 1), (7, 2)],
    "L7": [(0, 4), (1, 1), (7, 4)],
    "L8": [(1, 1), (4, 4), (7, 0)],
    "L9": [(0, 0), (6, 0), (7, 3)],
    "L10": [(1, 4), (2, 1), (5, 0)],
}


# 六层定点节点（15-六层构型，玩家口径「必然如此」）
L6_FIXED = {
    (0, 2): "紧急作战", (4, 3): "紧急作战",
    (3, 3): "诡意行商", (0, 3): "不期而遇", (1, 3): "得偿所愿",
}
# 三结局六层「源流交汇处」（6×5，2026-10-30 玩家口径；与上面的盲目者所见不同图）
L6_SOURCE_FIXED = {
    (0, 2): "起点",
    (1, 2): "命运所指", (3, 0): "作战", (3, 2): "险路恶敌",
    (3, 4): "不期而遇", (5, 2): "诡意行商",
}


def config_fixed(layer, cfg):
    """返回 (险路尽头列表, 定点类型 dict)。

    各层情况不同，**不要一刀切**：

    | 层 | 有尽头吗 | 尽头怎么处理 |
    |---|---|---|
    | 一层 | 有（2 个，只点亮 1 个） | 类型改成险路尽头，价值 -99（禁踩；终点模式改由 main 重定价） |
    | 二层 | 有（2 个，都可进） | 类型改成险路尽头，**价值 8**（四结局道具，不是禁踩！） |
    | 三层 | **没有**（只有险路恶敌，直接探明） | 不需要覆盖 |
    | 四层 | 有（3 个，含伪装） | 类型改成险路尽头，价值 -99（禁踩） |
    | 五层 | 没有（只有命运所指） | 不需要覆盖 |
    """
    if layer == 1:
        info = L1.get(str(cfg)) or {}
        return info.get("ends", []), info.get("fixed", {})
    if layer == 2:
        # 二层标题里就写着「起点 …，尽头 a,b / c,d」，直接解析，不用另立表
        p = os.path.join(REF, "09-二层构型.md")
        if not os.path.exists(p):
            return [], {}
        txt = open(p, encoding="utf-8").read()
        seg = next((s for s in re.split(r"(?m)^## ", txt)
                    if s.startswith("构型" + str(cfg) + "（")), None)
        if seg is None:
            return [], {}
        ends = []
        m = re.search(r"尽头\s*([0-9,、/\s]+)", seg.splitlines()[0])
        if m:
            ends = [(int(a), int(b)) for a, b in re.findall(r"(\d+),(\d+)", m.group(1))]
        return ends, {}
    if layer == 3:
        return [], {}          # 三层没有险路尽头，只有直接探明的险路恶敌
    if layer == 4:
        return L4_ENDS.get(str(cfg).upper(), []), {}
    if layer == 6:
        # 六层有两张图（2026-10-30）：
        #   源流交汇处（三结局，6×5）= 起点/命运所指/作战/恶敌/不期/行商 六个定点
        #   盲目者所见（四结局，5×4）= 两个紧急作战里有一个是树根，由玩家 --root 覆盖
        if "源流" in str(cfg or ""):
            return [], dict(L6_SOURCE_FIXED)
        return [], dict(L6_FIXED)
    return [], {}


def config_cells(layer, cfg):
    """从 references 里解析出该构型的格子集合，用于「读图结果是否越界」的校验。

    2026-10-29 修复：非 1/3/4 层原来**回落到 11-四层左区构型**——层 2/5 调用等于
    拿错表的格子集合当合法域（目前只有 label_ocr 自检在用，是潜在雷）。
    现在每层查自己的文件；六层查「## 边表」小节。
    """
    layer = int(layer)
    table = {
        1: ("17-一层构型.md", "构型" + str(cfg) + "（"),
        2: ("09-二层构型.md", "构型" + str(cfg) + "（"),
        3: ("10-三层左区构型.md", str(cfg) + "（"),
        4: ("11-四层左区构型.md", str(cfg) + "（"),
        5: ("13-五层左区构型.md", str(cfg) + "（"),
        6: ("15-六层构型.md", "边表（源流" if "源流" in str(cfg or "") else "边表"),
    }
    entry = table.get(layer)
    if entry is None:
        return set()
    path, hdr = entry
    p = os.path.join(REF, path)
    if not os.path.exists(p):
        return set()
    txt = open(p, encoding="utf-8").read()
    parts = re.split(r"(?m)^## ", txt)
    # 六层默认「边表」要避开「边表（源流交汇处…）」
    if layer == 6 and "源流" not in str(cfg or ""):
        seg = next((s for s in parts if s.startswith("边表") and "源流" not in s.splitlines()[0]), None)
    else:
        seg = next((s for s in parts if s.startswith(hdr)), None)
    if seg is None:
        return set()
    cells = set()
    for line in seg.splitlines():
        for a, b in re.findall(r"\((\d+),(\d+)\)", line):
            cells.add((int(a), int(b)))
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--layer", type=int, required=True, choices=[1, 2, 3, 4, 6])
    ap.add_argument("--config", required=True)
    ap.add_argument("--end-mode", dest="end_mode", default=None, choices=list(R.END_MODES),
                    help="终点模式；缺省读 scenario 里的 end_mode 键，再缺省 = 全程追猎")
    ap.add_argument("--four-ending", dest="four_ending", default=None,
                    metavar="要|不要",
                    help="二层四结局问询；缺省读 scenario 里的 four_ending 键")
    ap.add_argument("--four-ending-pre", dest="four_ending_pre", default=None,
                    metavar="已获取|未获取",
                    help="二层·险路尽头·四结局=要 的前置藏品问询；缺省读 scenario 键")
    ap.add_argument("--three-ending", dest="three_ending", default=None,
                    metavar="要|不要",
                    help="三层·仅无右区时：本局是否打三结局？缺省读 scenario 里的 three_ending 键")
    ap.add_argument("--three-ending-pre", dest="three_ending_pre", default=None,
                    metavar="已获取|未获取",
                    help="三层·三结局=要 的前置收藏品问询；缺省读 scenario 键")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    sc = json.load(open(a.scenario, encoding="utf-8-sig"))   # 容忍 Windows 的 UTF-8 BOM
    # 2026-10-29 修复：本工具原来不认识「险路尽头模式」——会把本层终点重新改成
    # -99（或二层 8）并留给后续 block 掉，终点约束因此永远满足不了。
    # 现在按模式定尽头价值：终点模式下 = rules.ending_final_value（0 / 二层 8），
    # 且把「险路尽头」从 block 里摘掉（它是必进的最后一步，不能删）。
    R.set_end_mode(a.end_mode if a.end_mode is not None else sc.get("end_mode"))
    # 二层四结局问询（2026-10-30）：CLI 显式值优先，否则读 scenario 落盘值；
    # 旧 scenario 没有这两个键 → 按旧口径兜底（不强迫历史文件）。
    try:
        R.set_four_ending(a.four_ending if a.four_ending is not None else sc.get("four_ending"))
        R.set_four_ending_pre(a.four_ending_pre if a.four_ending_pre is not None
                              else sc.get("four_ending_pre"))
    except ValueError as exc:
        print(f"!! {exc}")
        return 2
    # 三层三结局问询（2026-10-30）：CLI 显式值优先，否则读 scenario 落盘值；
    # 旧 scenario 没有这两个键 → 按旧口径兜底（先行一步 2 分、不必走）。
    try:
        R.set_three_ending(a.three_ending if a.three_ending is not None
                           else sc.get("three_ending"))
        R.set_three_ending_pre(a.three_ending_pre if a.three_ending_pre is not None
                               else sc.get("three_ending_pre"))
    except ValueError as exc:
        print(f"!! {exc}")
        return 2
    # 三结局问询只在「三层 + 本层无右区」时成立：用错层/有右区都直接报错，
    # 别把答案静默写进 scenario（route.py / build_scenario 同样是硬闸）。
    _e3 = R.layer3_scope_errors(a.layer)
    if _e3:
        print("!! " + "；".join(_e3))
        return 2
    if (a.layer == 3 and sc.get("has_right") is True
            and (a.three_ending is not None or a.three_ending_pre is not None
                 or sc.get("three_ending") is not None
                 or sc.get("three_ending_pre") is not None)):
        print("!! 本层有右区（熄日余径）→ 三结局问询不适用；--three-ending 不该传")
        return 2
    # 明确标了「无右区」的三层场景：与 route.py / build_scenario 同一道闸。
    # 旧 scenario 没有 has_right 键时**不逼问**（按旧口径兜底），避免历史文件跑不动。
    if a.layer == 3 and sc.get("has_right") is False:
        _ask3 = R.layer3_answer_errors(3, False)
        if _ask3:
            print("!! 三层（无右区）的三结局问询还没完成——先问玩家：\n   - "
                  + "\n   - ".join(_ask3))
            return 2
    nodes = sc["nodes"]
    vals = sc.setdefault("values", {})
    changes = []

    # 统一走 config_fixed：以前这里又抄了一遍分支，结果二层进不来、六层没实现
    ends, fixed = config_fixed(a.layer, a.config)
    if a.layer == 1:
        info = L1.get(str(a.config))
        if not info:
            print(f"!! 一层的构型 {a.config} 没有记录（只支持 1/2/3）")
            return 2
        if tuple(sc["start"]) != info["start"]:
            print(f"!! 起点不符：scenario 是 {sc['start']}，构型{a.config} 应为 {info['start']}")

    for q in ends:
        k = f"{q[0]},{q[1]}"
        if k in nodes:
            old = nodes[k]
            nodes[k] = "险路尽头"
            changes.append((k, old, "险路尽头", "构型表的尽头"))
    # 尽头价值按层 × 模式定：
    #   全程追猎：二层是四结局道具、走 1 次值 8；其余层是负收益的墙（-99）。
    #   险路尽头模式：本层终点（一/四层 = 尽头**及小径**、**二层 = 只有尽头**、三/五/六层 = 恶敌）是
    #   **必进的最后一步** → rules.ending_final_value（0；二层尽头仍 8），且不再允许 block 把它删掉。
    #   2026-10-28 前这里无条件写 -99，二层场景被改成禁踩（与 SKILL 硬常数冲突）；
    #   2026-10-29 前不认识终点模式，会把终点重新改成 -99/8。
    #   2026-10-30：终点类型改成**元组**（一/四层 = 尽头 + 小径），所以这里批量铺 values；
    #   **二层**默认小径 0、不当终点；但四结局=不要，或 四结局=要且已获取前置藏品 时，
    #   小径 = 尽头（8）、并作为可选终点写进 final_type（rules.ending_types(2)）。
    end_ts = R.ending_types(a.layer) if R.is_end_jintou() else ()
    if end_ts:
        for t, v in R.ending_value_map(a.layer).items():
            vals[t] = v
    else:
        vals["险路尽头"] = R.layer2_jintou_value() if a.layer == 2 else -99
    if a.layer == 2:
        vals["险路小径"] = (R.ending_final_value(2, "险路小径")
                            if R.is_end_jintou() else 0.0)
    # 三层·三结局（2026-10-30）：只在**无右区局**有意义（scenario 的 has_right）。
    # 三结局=要 且 前置收藏品=未获取 → 先行一步 = 15 分；已探明就补进 must。
    # 还藏在未知诡秘里的候选坐标由 route.py / build_scenario.py 算（它们有 BFS/距离带）。
    if a.layer == 3 and sc.get("has_right") is not True:
        if R.three_ending_need_xianxing():
            vals[R.THREE_XIANXING_NODE] = R.THREE_XIANXING_VALUE
            xq = [k for k, t in nodes.items() if t == R.THREE_XIANXING_NODE]
            if xq:
                must = sc.setdefault("must", [])
                have = {tuple(m) for m in must}
                for k in xq:
                    q = tuple(int(z) for z in k.split(","))
                    if q not in have:
                        must.append([q[0], q[1]])
                        print(f"  ({k})  先行一步 已探明 → 补进 must（三结局 · 15 分必走点）")
            else:
                print("  三结局：先行一步还没探明 → 用 route.py / build_scenario.py 列候选坐标"
                      "（或 pool_candidates.py --layer 3 --xianxing）")
        else:
            # 答「不要」/「已获取」/未问 → 回到普通口径（清掉可能残留的 15）
            vals[R.THREE_XIANXING_NODE] = 2
    for q, t in fixed.items():
        k = f"{q[0]},{q[1]}"
        if k in nodes:
            old = nodes[k]
            nodes[k] = t
            changes.append((k, old, t, "构型的固定节点"))

    if end_ts:
        blk = sc.get("block") or []
        drop = [t for t in end_ts if t in blk]
        if drop:
            sc["block"] = [t for t in blk if t not in end_ts]
            print(f"  终点模式：{'、'.join(drop)} 已从 block 移除（本层终点，不能删）")

    # 终点约束也要落进 scenario：只改 values/block 的话，route_solver 直读文件时
    # 仍不知道「最后一步必须是尽头/小径/恶敌」（2026-10-29 补齐：手工管线跑终点模式）。
    sc["end_mode"] = R.end_mode()
    if a.layer == 2 and R.four_ending() is not None:
        sc["four_ending"] = R.four_ending()
        if R.four_ending_pre() is not None:
            sc["four_ending_pre"] = R.four_ending_pre()
    # 三层三结局（2026-10-30）：答案落盘，滚动重算/直读 scenario 时不再重复问
    if a.layer == 3 and sc.get("has_right") is not True and R.three_ending() is not None:
        sc["three_ending"] = R.three_ending()
        if R.three_ending_pre() is not None:
            sc["three_ending_pre"] = R.three_ending_pre()
    if R.is_end_jintou():
        sc["final_type"] = list(end_ts)
        # 二层「小径=8」时小径是硬终点：进入即结束，避免与尽头各计 8。
        _hard_end = R.ending_hard_types(a.layer)
        if "险路小径" in _hard_end:
            sc["hard_final_types"] = list(_hard_end)
        else:
            sc.pop("hard_final_types", None)
        print(f"  终点模式：final_type = {sc['final_type']}（求解器按类型收终点；"
              + ("小径属硬终点：进入即结束，避免与尽头重复计 8）"
                 if "险路小径" in _hard_end else "小径属软终点：可停可续）"))
    else:
        sc.pop("final_type", None)

    if not changes:
        print("没有需要改的格子（可能 scenario 已经覆盖过）")
    for k, old, new, why in changes:
        print(f"  ({k})  {old} → {new}    [{why}]")
    if end_ts:
        print("终点价值：" + "、".join(f"{t}={vals[t]:g}" for t in end_ts)
              + "（终点模式：必进最后一步）"
              + f"；共改 {len(changes)} 格")
        if a.layer == 2:
            print(f"  二层四结局：{R.layer2_plan_text(2)}")
    elif a.layer == 2:
        print(f"二层四结局：{R.layer2_plan_text(2)}")
        print(f"  险路尽头 = {vals['险路尽头']:g}"
              + ("（四结局道具，必走 1 次）" if vals["险路尽头"] > 0 else "（0 分、不再必走）")
              + f"；险路小径 = {vals['险路小径']:g}；共改 {len(changes)} 格")
    else:
        print(f"险路尽头价值 = {vals['险路尽头']}（禁踩）"
              + f"；共改 {len(changes)} 格")
    if a.layer == 3 and sc.get("has_right") is not True:
        print(f"  三层三结局：{R.layer3_plan_text(3)}"
              f"；先行一步 = {vals.get(R.THREE_XIANXING_NODE, 2):g}")

    if not a.dry_run:
        json.dump(sc, open(a.scenario, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"已写回 {a.scenario}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
