#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route.py — 一条命令跑完整条链，最后只给一个判词

为什么要有这个
--------------
执行方（另一个会话）可能**不开思考**，不会自己发现「起点读错了」「grants 灌水了」
「路线回头了」。所以凡是需要判断的地方，都不该留给人：

    截图 → 读图 → 起点 → 构型 → 定点覆盖 → 建场景 → 求解 → 全部校验 → ✅/❌

本脚本把上面整条串起来，**任何一项检查不过就整体判 ❌ 并说明原因**，
不需要执行方理解任何细节。

用法
----
  python tools/route.py 截图 --layer 1 --config 3 --ap 6
  python tools/route.py 截图 --layer 2 --config 1 --ap 5 --items "试作外骨骼:2"
  python tools/route.py 截图 --layer 5 --config L8 --ap 7 --grants "3,0:2"
  python tools/route.py 截图 --layer 3 --config L1 --ap 6 --mode 普通分队   # 不传 --root = 无理想源

输出
----
1. 判定表（起点/构型/格点/自检/定价/AP/回头/**输入可信度**）
2. 路线
3. 最后一行：**四态**（2026-11-02 起；不许互相改写）

   | 末行 | exit | 含义 |
   |---|---|---|
   | `✅ 可用` | 0 | 有解，且搜索已穷尽（**在本场景模型内**已证最优） |
   | `⚠️ 有条件可用（N 项风险…）` | 0 | 有解，但**未证明最优**（撞上限/分阶段/多候选）或依赖未验证假设 |
   | `⏳ 未定：…` | 4 | 预算内**没找到解**且搜索未穷尽 ⇒ **不能判定无解**（旧版误判 ❌） |
   | `❌ 不可用：原因` | 3 | 识别失败 / 数据矛盾 / **已证无解**（搜索穷尽仍无可行解） |
4. `--explain`：另附一段「转述文案」（【结论】【下一步】【备选】【再看】【风险】），
   给小模型/执行方**逐字转达**用——文案里不含任何需要自行判断的内容，
   执行方不许改写、不许补充规则。
"""
import argparse
import collections
import contextlib
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import build_scenario as BS       # noqa: E402
import route_solver as RS         # noqa: E402
import apply_config as AC         # noqa: E402
import rules as R                 # noqa: E402
import numpy as np                # noqa: E402
from PIL import Image             # noqa: E402
import label_ocr as LO            # noqa: E402

# grants 的合法来源**按模式取**（R.grant_ok_types()，调用点在 main 里）——
# 这里不再缓存成模块常量：普通分队模式下树根不回复 AP，缓存会把模式算错。

# 分阶段求解：三/四/五层**默认都要进右区**，所以从总预算里固定切一块给右区，
# 剩下的归左区支配（2026-10-28 玩家口径）。数值就是玩家给的 3 / 4 / 5。
RIGHT_RESERVE = {3: 3, 4: 4, 5: 5}


def _replay(solver, path):
    """按路径重放，返回 (终局 AP, 用掉的道具 {名字: 次数})。"""
    free = {it["name"] for it in solver.items if it.get("free")}
    bonus = {it["name"]: int(it.get("bonus", 0)) for it in solver.items}
    ap = solver.ap0
    used = {}
    granted_seen = set()
    for q, name, _g in path:
        gain = 0 if q in granted_seen else solver.grants.get(q, 0)
        granted_seen.add(q)
        ap = ap - (0 if (name in free) else 1) + gain + bonus.get(name, 0)
        if name:
            used[name] = used.get(name, 0) + 1
    return ap, used


def _minus_items(items, used):
    """复制一份库存，扣掉左区已经用掉的次数（同名多件逐个扣）。"""
    out = [dict(it) for it in items]
    for name, n in used.items():
        for it in out:
            if n <= 0:
                break
            if it["name"] == name and it.get("uses", 1) > 0:
                take = min(n, it["uses"])
                it["uses"] -= take
                n -= take
    return [it for it in out if it.get("uses", 1) > 0]


def _can_land(it, t):
    """道具能不能落在类型 t 上。

    **必须走 `rules.land_ok`**：它和求解器用同一份 only/exclude 前缀展开
    （「右区凶戾」这种改名后的类型也要能匹配基础名）。
    2026-10-28 踩过：这里自己写 startswith，没做「右区」展开 →
    进区件判定说融雪能落「右区作战」（exclude 里明明有「作战」），
    求解器却拒绝 → 打印「进区件：融雪 → (8,0) 右区作战」之后右区 0 分。
    """
    return R.land_ok(it, t)


def _landable_right_cells(right_cells, nodes, blocked=(), except_cells=()):
    """把**被 block 的右区格**剔掉——死格不能当落点（也不能出现在进区日志里）。

    2026-10-28 踩过：`_find_entry` 只看「COVER 里有没有右区格」，不看那格是不是
    已经被 `block` 删掉（三层起空地是硬墙、三/四层命运所指也是墙）。
    实测左区终点 (6,0)、右区 (8,0)=空地、(10,0)=右区作战，手里只有轮子（直线 2 格）：
    入口打印「进区件：报废轮子 → 落 (8,0) 空地」，求解器却把空地删了 → 右区 0 分。
    `any` 类件（原理）碰巧会改落到真格上，所以**日志错但结果对**，更容易漏掉。

    `except_cells` = 类型虽在 blocked 里、但**本格**被豁免（带橡果的林间空地，
    2026-10-30 新口径）——求解器那边是 `block_except`，两边必须同一口径。
    """
    blocked = set(blocked or ())
    keep = {tuple(q) for q in (except_cells or ())}
    return {q for q in right_cells
            if nodes.get(RS.key_xy(q), "") not in blocked or tuple(q) in keep}


def _find_entry(items, from_cell, right_cells, nodes, blocked=(), except_cells=()):
    """从左区终点出发，挑一件**真够得着右区某格**的件（最便宜的）。

    只看 cov 不够（轮子=直线 2 格、原理=任意），还要看：
      1. `only/exclude`（走 `rules.land_ok`，含「右区」前缀展开）；
      2. **落点没被 block 删掉**（空地/密道/三/四层命运所指都是硬墙；
         带橡果的空地被 `block_except` 豁免，算可落）。
    落点必须与求解器完全一致：日志里说的那格，求解器必须真能落。
    """
    usable = _landable_right_cells(right_cells, nodes, blocked, except_cells)
    if not usable:
        return None
    xs = sorted({q[0] for q in usable})
    ys = sorted({q[1] for q in usable})
    best = None
    for it in sorted(items, key=lambda z: R.PRICE.get(z["name"], 2)):
        if it.get("uses", 1) <= 0:
            continue
        cov = it.get("cov")
        fn = RS.COVER.get(cov)
        if fn is None:
            continue
        raw = fn(from_cell) if cov in RS.FREE_COV else fn(from_cell, xs, ys)
        for q in raw:
            if q in usable and _can_land(it, nodes.get(RS.key_xy(q), "")):
                best = (it["name"], q)
                break
        if best:
            break
    return best


def _entry_blocked_hint(items, nodes, right_cells):
    """进不去右区时，补一句「哪件为什么用不了」，别让人猜。

    典型：五层右区**固定有一个诡意行商，但它不一定被直接探明**——
    未探明时那格显示成「未知的诡秘」，触须（限行商）就落不了。
    这时不该无声无息地换成别的件，要说明「等探明再算」。
    """
    hints = []
    types = {nodes.get(RS.key_xy(q), "") for q in right_cells}
    has_merchant = "诡意行商" in types or "秘境行商" in types
    has_unrevealed_secret = any(t.endswith("诡秘") for t in types)
    for it in items:
        only = R.expand_prefixes(it.get("only"))
        if not any(o.startswith(("诡意行商", "秘境行商")) for o in only):
            continue
        if has_merchant:
            continue
        if has_unrevealed_secret:
            hints.append(
                f"{it['name']}：右区还没探明「诡意行商」（当场只看到未知的诡秘），"
                f"限行商的件暂时落不了 —— 等探明后再算，别把它算进进区件")
    return hints


def _adj_after_block(edges, nodes, blocked, except_cells=()):
    """**和求解器同一张图**的邻接表：把 block 掉的类型从边里删掉。

    校验（回头步判定）必须用它，不能直接用原始 edges——
    否则「绕经一面墙」会被当成真路（2026-10-28 踩过：三/四层命运所指、
    三层起空地、险路恶敌、险路尽头被删后，一条不得不回头的路线被判成
    「可避免的回头步」→ 整体 ❌，而求解本身是对的）。

    `except_cells` = 被 `block_except` 豁免的格（带橡果的空地）：它们**留在图里**。
    少了这一条，一条经过橡果空地的路线会被判成「绕墙」→ 误报可避免的回头步。
    """
    blocked = set(blocked or ())
    keep = {tuple(q) for q in (except_cells or ())}
    adj = collections.defaultdict(set)
    for u, v in edges:
        tu = nodes.get(RS.key_xy(u))
        tv = nodes.get(RS.key_xy(v))
        if (tu in blocked and tuple(u) not in keep) or (tv in blocked and tuple(v) not in keep):
            continue
        adj[tuple(u)].add(tuple(v))
        adj[tuple(v)].add(tuple(u))
    return adj


def split_right_dots(dots, lm):
    """把报来的「右区空地圆点」分成 `(右区, 左区)` 两组。

    **左区那组绝不能再往下用**（2026-11-02 修复）：`--right-dots` 的坐标会进
    `block_except` + `cell_values`（0 分中继）。而**左区**的「空地」是 -99 硬墙
    （三层起，`values["空地"] = -99` 且 `block`）——一个打错列的坐标就能在左区
    凭空开出一条 0 分通道，路线会从墙里穿过去，且没有任何报错。
    `build_scenario.right_zone()` 一直有这道过滤（打「落在左区 → 忽略」），
    route.py 漏了同一道闸；两个入口必须同口径。
    """
    lm = int(lm)
    keep, bad = [], []
    for q in dots or ():
        (keep if tuple(q)[0] > lm else bad).append(tuple(q))
    return keep, bad


def _should_learn_start(problems, n_pre_solve, explicit_start, warned=False):
    """起点表（`learn_start`）的写入判据（2026-10-28 审计「起点表写得太早」）。

    以前在识别中途就落盘，一次偏掉的识别会把构型起点**锁死**
    （写进去之后 `config_start` 就当它是对的，冲突只打印、不覆盖）。
    三个条件（前两条是「可以写」，第三条是一票否决）：
      ① **整次校验通过**（problems 为空）；或
      ② `--start` 手报、且**识别阶段**没有出错（`n_pre_solve == 0`，即求解前
         一条问题都没有）——求解与校验阶段的问题（AP 超支、无可行解…）
         与「这个构型的起点是几」无关，而玩家手报的起点是硬事实
         （数据以人报的为准）。识别阶段出过错就连手报也不记：
         那样会把起点挂在错误构型名下。
      ③ `warned`（起点判据打出了 ⚠️，例如「候选没有一格同时满足『格内+无标签』」）
         → **一律不记**：那种情况下起点是猜的，只是碰巧落在格内且无标签，
         记进去就成了硬事实（2026-10-28 复查补的否决位）。
    `n_pre_solve` = 求解开始前的 problems 数量。
    """
    if warned:
        return False
    if not problems:
        return True
    return bool(explicit_start) and n_pre_solve == 0


def solve_staged(sc, layer, lm, right_cells, max_time):
    """三/四/五层：**左区、右区各自求优**，右区固定预留 N 点。

    规则（2026-10-28 玩家口径）：
      · 三层留 3 点、四层留 4 点、五层留 5 点给右区，其余归左区；
      · 左区用自己那份预算求最优（图里**没有右区格子**），没花掉的**不结转**；
      · 右区从**左区终点**出发，预算**固定 = 预留值**（不是「预留 + 结转」）；
      · 两边各自带自己的必走点，右区若要终点（五层命运所指）也在右区段收。
    代价：这条是**分解**，不保证等于整体最优（可能漏掉跨区的最优组合），
    换来的是搜索规模从「左+右一起」降到两个小搜索。`--joint` 可切回整体求解。
    """
    reserve = RIGHT_RESERVE[layer]
    right = set(right_cells)
    ap0 = int(sc["ap"])
    # 2026-10-29 修复：整段分阶段求解共用 **--max-time 的总预算**。
    # 旧口径：左区每轮 0.6 × max_time、最多 3 轮，右区再给 0.4 × max_time
    # → 最坏 2.2 × max_time（默认 60s 会跑到 130s+），与「max-time」语义不符。
    # 现在按剩余墙钟分配：每段最多用「剩余预算」，预算耗尽就交出已找到的最好解。
    # 2026-11-02：`time.time()` → `time.monotonic()`（deadline 语义；NTP 跳时钟会让它失真）。
    t0 = time.monotonic()
    deadline = t0 + max(1.0, float(max_time))

    # ---------- 左区（先按完整库存解，看能不能自然把手里的件留到右区） ----------
    nodes_l = {k: v for k, v in sc["nodes"].items() if RS.xy(k)[0] <= lm}
    edges_l = [[list(e[0]), list(e[1])] for e in sc["edges"]
               if e[0][0] <= lm and e[1][0] <= lm]
    base_l = {
        "nodes": nodes_l, "edges": edges_l,
        "must": [q for q in sc.get("must", []) if q[0] <= lm],
        "grants": {k: v for k, v in sc["grants"].items() if RS.xy(k)[0] <= lm},
        "ap": max(0, ap0 - reserve),
        # 次数钳制格（五层右区命运所指）全在右区：左区段不该看到它，
        # 否则会打「clamp_items_at 不在格子里」的噪声告警（格被本段删掉了）。
        "clamp_items_at": [],
    }
    full = sc.get("items", [])
    # 手写的 scenario 可能只写 {"name","uses"}（没有 cov）→ 这里统一补全，
    # 否则 `_find_entry` 拿不到 cov，会误判「没有件够得着右区」。
    try:
        full = [R.normalize_item(it) for it in full]
    except R.BadItem as e:
        raise SystemExit("!! scenario 的库存条目非法：%s" % e)
    blocked = sc.get("block") or []    # 空地/密道/三·四层命运所指：死格不能当落点
    blocked_except = sc.get("block_except") or []   # 带橡果的空地：豁免（可落）
    reserved = {}                      # 为了右区而扣出来的件（名字 -> 次数）
    found = None
    s_l = None
    # 最多三轮：解左区 → 看剩下的件够不够得着右区 → 不够就扣一件重解。
    # 关键：**扣哪一件必须按 + 左区终点 的几何判断**（`_find_entry`），
    # 不能按「最便宜且覆盖类型合格」——轮子便宜但它只走直线 2 格，
    # 左区终点在 (2,2)、右区在 (8,0) 时就够不着，而原理（任意）能。
    for _round in range(3):
        remain = deadline - time.monotonic()
        if _round > 0 and remain <= 2.0:
            print("  ⚠️ --max-time 预算已耗尽，左区重解提前收手（本次结果可能未复核进区件）")
            break
        sc_l = dict(sc, **base_l)
        sc_l["items"] = _minus_items(full, reserved)
        sc_l.pop("final", None)
        s_l = RS.Solver(sc_l)
        # 本轮上限 = min(单轮 0.6×max_time, 剩余预算 − 留给右区的 2s)
        left_budget = min(max(5.0, max_time * 0.6), max(1.0, remain - 2.0))
        score_l, path_l, hit_l = s_l.run(max_time=left_budget,
                                         node_limit=80000000)
        ap_end, used = _replay(s_l, path_l)
        left_end = path_l[-1][0] if path_l else tuple(sc["start"])
        left_items = _minus_items(full, used)
        found = _find_entry(left_items, left_end, set(right), sc["nodes"], blocked,
                            blocked_except)
        if found:
            break
        # 剩下的都够不着：从「还没扣」的件里挑一件**真的够得着**的扣出来
        pick = _find_entry(_minus_items(full, reserved), left_end, set(right),
                           sc["nodes"], blocked, blocked_except)
        if not pick:
            break                       # 一件都够不着 → 右区真的进不去
        reserved[pick[0]] = reserved.get(pick[0], 0) + 1
        print(f"  进区件：{pick[0]} 够得着右区 → 扣出 1 次留给右区，重解左区")
    if found:
        print(f"  进区件：{found[0]} → 落 ({found[1][0]},{found[1][1]}) "
              f"{sc['nodes'].get(RS.key_xy(found[1]), '')}")
    else:
        print("  ⚠️ 没有任何件能从 (左区终点 %s) 够到右区 → 右区进不去" % (left_end,))
        for h in _entry_blocked_hint(_minus_items(full, reserved), sc["nodes"], right):
            print("     " + h)

    # ---------- 右区 ----------
    # 预算**固定** = 预留 3/4/5（玩家口径）。左区没花完的**不结转**——
    # 「剩余的归左区支配」意味着左区可以全花掉，花不掉就是损耗，不是右区的预算。
    nodes_r = {k: v for k, v in sc["nodes"].items() if RS.xy(k)[0] > lm}
    edges_r = [[list(e[0]), list(e[1])] for e in sc["edges"]
               if e[0][0] > lm and e[1][0] > lm]
    nodes_r[RS.key_xy(left_end)] = "起点"     # 左区终点当作右区段的起点（不重复记分）
    sc_r = dict(sc)
    sc_r.update({
        "nodes": nodes_r, "edges": edges_r,
        "must": [q for q in sc.get("must", []) if q[0] > lm],
        "grants": {k: v for k, v in sc["grants"].items() if RS.xy(k)[0] > lm},
        "ap": reserve,                            # 固定，不结转
        "start": [left_end[0], left_end[1]],
        "items": left_items,
        # 次数钳制格只留右区的（右区段才有意义；左区的已被 base_l 清空）
        "clamp_items_at": [q for q in sc.get("clamp_items_at", []) if RS.xy(q)[0] > lm],
    })
    sc_r.pop("final", None)
    if layer == 5 and sc.get("final") and tuple(sc["final"]) in right:
        sc_r["final"] = sc["final"]
    s_r = RS.Solver(sc_r)
    # 右区也只许用「剩余预算」——不兼顾总墙钟的话，右区再给一份 0.4×max_time，
    # 整段最坏 2.2×max_time（2026-10-29 修复）
    right_budget = min(max(3.0, max_time * 0.4), max(1.0, deadline - time.monotonic()))
    score_r, path_r, hit_r = s_r.run(max_time=right_budget, node_limit=80000000)

    # 2026-10-29 修复：右区段的两种失败必须显式带出去，不能被 total 吞掉——
    #   · score_r = -1 是求解器的「无可行解」哨兵（终点/必走约束满足不了，
    #     典型：五层命运所指在右区、又进不去右区）；
    #   · path_r 为空 = 右区一格都没踩（没有任何件能进区，或进了也无处可落）。
    # 旧代码 `score_r if score_r > 0 else 0` 把 -1 当 0 加：左 17 + 右 -1 → total=17，
    # 「total < 0」的无可行解判词永不触发，路线没走到命运所指照样判 ✅。
    right_entered = bool(path_r)
    right_infeasible = score_r < 0

    segments = [
        {"tag": "left", "solver": s_l, "path": path_l, "score": score_l,
         "label": f"预算 {s_l.ap0}（{ap0} − 右区预留 {reserve}）"
                  + (f"，剩 {ap_end} 点没用掉（不结转）" if ap_end > 0 else "，余 AP 0"),
         "hit": hit_l},
        {"tag": "right", "solver": s_r, "path": path_r, "score": score_r,
         "label": f"预算 {s_r.ap0}（固定预留 {reserve}）", "hit": hit_r},
    ]
    cost_l = (s_l.last_cost or {}).get("cost", 0)
    cost_r = (s_r.last_cost or {}).get("cost", 0)
    return {
        "segments": segments,
        "path": list(path_l) + list(path_r),
        # 右区 -1（无可行解）必须让 total 变负 → main 的「无可行解」判词能触发；
        # 只有 score_r >= 0 时才是「左 + 右」的正常合计（0 分右区照旧不加）。
        "total": (score_l + score_r) if score_r >= 0 else score_r,
        "right_entered": right_entered,
        "right_infeasible": right_infeasible,
        "hit": bool(hit_l or hit_r),
        "items_view": sc.get("items", []),
        "cost": {"cost": cost_l + cost_r,
                 "cost_proven": bool((s_l.last_cost or {}).get("cost_proven"))
                 and bool((s_r.last_cost or {}).get("cost_proven")),
                 "tie_phase": bool((s_l.last_cost or {}).get("tie_phase"))
                 or bool((s_r.last_cost or {}).get("tie_phase"))},
    }


def apply_visited(nodes, grants, visited, start, notes):
    """**滚动重算**：把「已经走过/已经触发过」的格子从未来的收益与回复里扣掉。

    2026-10-29 修：以前**没有任何入口**能表达「这一格已经走过了」——
      ① 走完树根后重算，(6,3) 仍是「树根」，求解器会再给 15 分；
      ② 玩家口报的 `--ap` 是**已领过树根 +2 之后**的余额，但 grants 仍挂着 (6,3):2，
         solver 把起点格当「没踩过的 grant 格」再发一次 → 预算虚高 2 点（三层实测）。

    ⛔ **不要改写节点类型**（2026-10-29 我第一次修就踩了这个坑）：
    把 (6,3) 树根改成「起点」会**抹掉紧急作战的配额账目**——树根本身就是那场紧急作战，
    改完工具以为「紧急作战还差 1 个」，把本来 0 分的 (1,3) 抬成 9.0 分并写进路线。
    正确做法：**类型不动**，只
      · 抽掉 grants（+AP 不再发）；
      · 写进 `visited`（求解器把它当已访问：`0 if seen else v/gr` → 0 分、0 回复）；
      · 由调用方把 visited 格从 `must` 里摘掉（已经打过，不该再要求走一遍）。

    就地修改 grants，返回规范化后的 visited 坐标列表。
    """
    out = []
    for part in filter(None, (visited or "").split(";")):
        try:
            out.append(RS.xy(part))
        except Exception:
            notes.append(f"--visited 格式错误：{part!r}（正确写法 \"c,r\"，多段用分号）")
    if not out:
        return out
    if start is not None:
        vset = {RS.key_xy(q) for q in out}
        if RS.key_xy(tuple(start)) not in vset:
            out.append(tuple(start))
            notes.append(f"起点 {tuple(start)} 自动并入 --visited（玩家从那里出发，已走过）")
    for q in out:
        grants.pop(RS.key_xy(q), None)   # 已领过 → 不再发
    return out


class _View(object):
    """分阶段求解后的「联合视图」：只提供下游校验用的字段（不做搜索）。"""

    def __init__(self, res):
        self.segments = res["segments"]
        self.path = res["path"]
        self.vals = self.segments[0]["solver"].vals
        # 道具价表要**合并所有区段**：只取左区段的话，右区段才用到的件（例如融雪进区）
        # 在这里查不到 → 打印会落到默认 2，和 last_cost 加出来的总额对不上
        # （2026-10-28 实测：道具价 14，明细却写成「融雪(2)」）。
        self.cost_of = {n: R.PRICE.get(n, 2) for n in R.available_items()}
        self.nodes, self.grants = {}, {}
        for seg in reversed(self.segments):        # 右区先写、左区覆盖，左区优先
            self.cost_of.update(seg["solver"].cost_of)
            self.nodes.update(seg["solver"].nodes)
            self.grants.update(seg["solver"].grants)
        self.items = res["items_view"]
        self.last_cost = res["cost"]
        # 迷藏（橡果）命中数按区段加总；分阶段视图也要能报告
        self.acorn_hits = sum(getattr(seg["solver"], "acorn_hits", 0)
                              for seg in self.segments)

    def fmt(self, path):
        out = []
        for seg in self.segments:
            name = "左区" if seg["tag"] == "left" else "右区"
            out.append(f"  ── {name}（{seg['label']}）{seg['score']:g} 分 ──")
            out.append(seg["solver"].fmt(seg["path"]))
        return "\n".join(out)

# 道具的 cov / only / exclude / free / bonus **只在 tools/rules.py 定义**。
# 2026-10-28 审计：这里原先手抄了一份 ITEM_COV，把鸣笛写成 adj8（文档是周围 12 格），
# 而且不给免AP件打 free —— 一张表抄三遍，就是「文档对、代码错」的来源。


def resolve_start(start, cfg_start, explicit_start, cand_cells, grid, cells,
                  outside_probe, edges, layer, config, problems, notes,
                  rolling=False):
    """起点校正 + 构型一致性 + 地标交叉校验（原 main 第 2 步，抽出来便于单测）。

    判据链（2026-10-28 三层偏一列那次复盘定下的顺序，**不能换**）：
      ① 构型没声明起点（三/四/五层标题里不写）→ 从候选里挑
         「在该构型格子集合内 + 无标签格」的第一格去校正；
      ② 起点与构型声明不符 → **先判构型选错，再判检测偏了**——
         「起点偏一列」和「构型选错」症状相同、处理相反，顺序反了会伪造一致性；
      ③ 地标（险路恶敌/尽头）是第二把钥匙：起点可能被多个构型共用。
    就地往 problems / notes 追加；返回 (start, warned)。
    `warned=True` 表示起点其实是**猜的**（判据里唯一一条 ⚠️：候选没有一格
    同时满足「在构型格内 + 无标签」）——调用方据此拒绝写入 `起点表.json`。

    `rolling=True`（滚动重算，2026-10-29 新增）：`--start` 报的是**玩家当前所在格**，
    不再是进层起点，因此**不能**拿它跟构型声明的进层起点比对。
    2026-10-29 事故：打完树根后重算，起点是 (6,3)、构型 L1 的进层起点是 (0,2)，
    这条校验判 ❌，只好退回 `--start 0,2` 重算 —— 于是求解器把 (6,3) 树根当**没打过**，
    又发一次 +2 AP、再给 15 分，预算从真实的 8 虚增到「5 + 树根 2 = 7」。
    滚动模式下：只校验「当前格在该构型格子里」，构型起点不一致降级为备注。
    """
    warned = False
    if start is None:
        # 2026-11-02 修复：小人没检出（或图里根本没有小人）时，以前会把 None 一路带下去——
        # 滚动分支和判词「输入可信度」里都有 `tuple(start)`，直接 TypeError（traceback + exit 1），
        # 违反「本文件只出四态判词」的契约。起点是整条路线的前提，缺了就当场判 ❌。
        problems.append("起点未检测到（禁止猜，用 --start 手报）")
        return None, True
    if rolling:
        # 滚动模式：起点=当前格，只要求在格子集合内（构型已由层数+地标定死）。
        # 传了 --start 就以人报的为准；没传则用小人检测的结果，**都不许**套成构型进层起点。
        if cells and tuple(start) not in {tuple(c) for c in cells}:
            problems.append(
                f"起点 {tuple(start)} 不在构型{config} 的格子里"
                f"（滚动重算的起点必须是**当前所在格**；构型已按层数+地标确定）")
        notes.append(f"滚动重算：起点按**当前所在格** {tuple(start)} 处理"
                     f"（不校验进层起点 {cfg_start}"
                     + ("，来自 --start" if explicit_start else "，来自小人检测") + "）")
        return start, warned
    if cfg_start is None and edges and not explicit_start and cand_cells:
        ok = [q for q in cand_cells if q in cells and q not in grid]
        if ok and tuple(start) != ok[0]:
            notes.append(f"起点检测 {tuple(start)} 不在「构型{config} 格内且无标签」集合里，"
                         f"按判据校正为 {ok[0]}（候选 {cand_cells[:3]}）")
            start = ok[0]
        elif not ok:
            notes.append(f"⚠️ 候选 {cand_cells[:3]} 没有一个同时满足"
                         f"「在构型{config} 格内 + 无标签」，起点可能读错了"
                         f"（本次不写入起点表）")
            warned = True
        if len(ok) > 1:
            # 2026-10-28 四层 L1 事故：多候选（含角格 (0,0)）按序取首并学习 → 污染起点表。
            # 多候选 = 猜测：求解照常（玩家可当场纠正），但起点表**禁止写入**。
            notes.append(f"⚠️ {len(ok)} 个候选同时满足「格内+无标签」：{ok[:4]}"
                         "——起点按序取，本次**不写入起点表**（需玩家确认或查起点表）")
            warned = True
    # start 为 None 的情况已在函数开头返回（上面的 `if start is None`），这里只管「有起点但不对」。
    if cfg_start and tuple(start) != cfg_start:
        if explicit_start:
            problems.append(
                f"--start 报的是 {tuple(start)}，但构型{config} 的起点是 {cfg_start}"
                f"（构型表是硬事实；若确定是 {tuple(start)}，那是构型选错了）")
        elif outside_probe:
            problems.append(
                f"起点不符：检测 {tuple(start)}，构型{config} 应为 {cfg_start}；"
                f"且读到的 {sorted(outside_probe)[:4]} 不在构型{config} 的格子里"
                f"（共 {len(outside_probe)} 格）→ **构型选错**，先换构型，别改起点")
        elif tuple(start) in grid:
            # 检测落在有标签的格上（起点不写字）→ 明显偏了，按构型表校正
            notes.append(f"起点检测落在有标签的 {tuple(start)}，按构型 {config} 校正为 {cfg_start}")
            start = cfg_start
        elif cfg_start in cand_cells:
            notes.append(f"起点检测偏到 {tuple(start)}，按构型表校正为 {cfg_start}"
                         f"（候选里有它；构型{config} 的起点是硬事实）")
            start = cfg_start
        else:
            problems.append(
                f"起点不符：检测 {tuple(start)}，构型{config} 应为 {cfg_start}"
                f"（构型选错或起点读错；候选：{cand_cells[:4]}）")

    # 地标交叉校验（玩家 2026-10-28 的定构型法）：
    # 起点可能被多个构型共用，**险路恶敌/险路尽头坐标**是第二把钥匙。
    # 三层/五层只有恶敌、四层只有尽头 → 两者合起来一定唯一。
    obs_landmark = [q for q, t in grid.items() if str(t[0]) in ("险路恶敌", "险路尽头")]
    if edges and obs_landmark:
        ok_lm, why = BS.landmark_conflict(layer, config, obs_landmark)
        if not ok_lm:
            problems.append(f"地标与构型不符：{why}（构建型选错；可用地标反查："
                            f"{BS.configs_by_landmark(layer, obs_landmark[0])}）")
    return start, warned


def _explain_lines(solver, path, total, args, notes, end_txt, budget, paid, alts_ready,
                   plan3="", plan5=""):
    """--explain 的「转述文案」（对齐 SKILL.md 五·输出格式）。

    铁律：这里只做**转述格式化**，不做任何新的规则判断——
    每步得分/余AP 从 solver.fmt 解析（fmt 是 grant/免AP/首访结算的唯一实现，
    在这里重演一遍必然「文档对、代码错」）；备选解用求解器在同一搜索里
    顺带收集的 alts（--explain 时 slack=3、按落点集合去重）。
    """
    steps = []
    for ln in solver.fmt(path).splitlines():
        m = re.match(r"\s*\((\d+),(\d+)\)\s+(\S+)\s+(.*?)\s+得\s*(-?[\d.]+)\s+余AP=(-?\d+)\s*$", ln)
        if not m:
            continue          # 分阶段视图的「── 左区 ──」表头行
        x, y, ntype, tag, gain, ap_left = m.groups()
        steps.append({"q": (int(x), int(y)), "type": ntype, "tag": tag.strip(),
                      "gain": float(gain), "ap": int(ap_left)})
    if not steps:
        return ["（--explain：路线格式解析失败，请直接转述上方「== 路线 ==」原文）"]

    def act(tag):
        if tag == "走":
            return "徒步进入"
        m2 = re.match(r"<([^>(]+)", tag)
        item = m2.group(1) if m2 else tag
        extra = "，免行动力" if "免AP" in tag else ""
        return f"用「{item}」跳跃{extra}"

    s0 = steps[0]
    tops = sorted(range(len(steps)), key=lambda i: -steps[i]["gain"])[:2]
    main_line = "、".join(
        f"({steps[i]['q'][0]},{steps[i]['q'][1]}) {steps[i]['type']} {steps[i]['gain']:g} 分"
        for i in tops)
    out = [f"【结论】第{args.layer}层·构型{args.config}｜{R.mode_label()}／{R.end_mode()}"
           f"｜AP 预算 {budget}、实走 {paid} 步｜预计 {total:g} 分。"
           + (f"（{R.layer2_plan_text(2)}）" if args.layer == 2 else "")
           + (f"（{plan3}）" if plan3 else "")
           + (f"（{plan5}）" if plan5 else "")
           + f"主线先拿 {main_line}。"]

    nxt = (f"→ ({s0['q'][0]},{s0['q'][1]})「{s0['type']}」{act(s0['tag'])}，"
           f"预期得 {s0['gain']:g} 分，走完余 AP {s0['ap']}")
    if s0["type"].startswith("未知的"):
        nxt += "（类型未探明，按期望计）"
    out.append("【下一步】" + nxt + "。")

    alts = getattr(solver, "alts", None) or {}
    if not alts_ready:
        alt_line = "分阶段/多候选求解未收集备选（需要对比就加 --joint 重跑）"
    else:
        cands = [(t, p) for t, p in alts.values()
                 if p and p[0][0] != path[0][0] and tuple(p) != tuple(path)]
        if cands:
            tot_a, path_a = max(cands, key=lambda t: t[0])
            qa, ta = path_a[0][0], solver.nodes.get(path_a[0][0], "?")
            why = ("同为最优解的另一种开局" if tot_a >= total - 0.05
                   else f"低 {total - tot_a:g} 分的次优开局")
            alt_line = f"→ ({qa[0]},{qa[1]})「{ta}」，{why}"
        else:
            alt_line = "没有首步不同的备选（这个开局是唯一最优）"
    out.append("【备选】" + alt_line + "。")

    conds = []
    if s0["type"].startswith("未知的"):
        conds.append(f"({s0['q'][0]},{s0['q'][1]}) 探明类型后重算")
    if s0["tag"] != "走":
        conds.append("此步花道具，库存/件数有变就重算")
    conds.append("AP、库存、掉落与截图不符就重算")
    out.append("【再看】" + "；".join(conds) + "。")

    risks = [n for n in notes if n]
    unknown = sum(1 for s in steps if s["type"].startswith("未知的"))
    if unknown:
        risks.append(f"路线上 {unknown} 格未探明（分值是期望，不是事实）")
    if end_txt:
        risks.append(f"终点模式：最后一步必须落在「{end_txt}」")
    out.append("【风险】" + ("；".join(risks) + "。" if risks else "工具校验全过，无额外风险。"))
    return out


def label_input(confirmed, n_labels):
    """「节点标签」这一项的可信度（2026-11-02 新增 `--labels-confirmed`）。

    OCR 读出来的节点标签默认是**待确认**——它决定每条路线的分数，却没人看过。
    玩家逐格核对过之后，这一项才能升成**事实**，`✅` 的末行也才不用再挂它。

    返回 `(名称, 等级, 依据)`；`n_labels == 0`（图上没读到任何标签）时返回 None（不记这一项）。
    只管节点标签：徽章是像素检测、右区拓扑是形状表、起点是小人检测，各有各的账。
    """
    if not n_labels:
        return None
    if confirmed:
        return ("节点标签", "事实", "玩家口报：本层标注已逐格核对（%d 格）" % n_labels)
    return ("节点标签", "待确认", "OCR %d 格" % n_labels)


def verdict(problems, unknown, warnings, pending=(), backs=()):
    """四态判词的**唯一决策点**（2026-11-02）。返回 `(末行文本, exit code)`；❌ 时文本为 None。

    优先级不能换：**❌ > ⏳ > ⚠️ > ✅**
      · `problems` = 识别失败 / 数据矛盾 / 已证无解 —— 数据坏了，后面都不用谈；
      · `unknown`  = 搜索没穷尽且没找到解 —— 不是「无解」，是「判不了」；
      · `warnings` = 有解但未证明最优 / 依赖未验证假设 —— 条件可用；
      · 都没有   = 场景模型内已证最优；但仍要标出「有 N 项输入待确认」。
    抽成函数是给 selftest 用的：这四种末行是**对外契约**，不能只靠人试。
    """
    if problems:
        return None, 3
    if unknown:
        return "⏳ 未定（搜索没跑完，**不能判定无解**）", 4
    if warnings:
        # ⚠️ 仍 exit 0（不是错，是**条件可用**），但下游必须把风险一起转达。
        return (f"⚠️ 有条件可用（{len(warnings)} 项风险，见上「风 险」栏——不是完整绿灯）", 0)
    _qual = ("（在本场景模型内已证最优；%d 项输入待确认）" % len(pending)) if pending else ""
    return "✅ 可用" + _qual + ("（回头步均为必经）" if backs else ""), 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--layer", type=int, required=True, choices=[1, 2, 3, 4, 5, 6])
    ap.add_argument("--config", required=True)
    ap.add_argument("--ap", type=int, required=True)
    ap.add_argument("--start", default=None)
    ap.add_argument("--start-origin", dest="start_origin", default="player",
                    choices=("player", "detected"),
                    help="--start 的来源：player=玩家口报（事实）；detected=**observe.py 的"
                         "小人检测**（待确认）。observe 透传时必须写 detected，否则判词会把"
                         "一个从图上猜出来的坐标算成「玩家口报的事实」")
    ap.add_argument("--mode", default=None, choices=R.mode_choices(),
                    help="分队模式（SKILL.md 〇·〇）：N16文明开化（内部名「文明开化」）=旧默认"
                         "（通过追猎进入二~六层时大概率出现理想源、+2AP+4藏品，--root 必传；"
                         "推荐搭配全程追猎）；普通分队=不保证有理想源（不传 --root 即按无理想源算；"
                         "打了也不 +2AP、不给 4 藏品，价值=紧急作战）。不传 = N16文明开化")
    ap.add_argument("--end-mode", dest="end_mode", default=None, choices=list(R.END_MODES),
                    help="终点模式（与分队模式**独立**）：全程追猎=旧默认（尽头/恶敌照旧禁踩；"
                         "二层尽头 8、走 1 次）；险路尽头=一/四层最后一步落在险路尽头**或险路小径**"
                         "（同分值）、**二层默认只能落险路尽头**（四结局=不要，或 要且已获取前置藏品时，"
                         "小径与尽头同分、可作可选终点）、三/五/六层必须落在险路恶敌"
                         "（之前不进尽头/恶敌；终点不再 block）。不传 = 全程追猎")
    ap.add_argument("--final", default=None,
                    help="终点坐标手报（仅险路尽头终点模式），如 3,0。图上没读到尽头/恶敌/小径、"
                         "或构型表地标缺失时用；以人报的为准")
    ap.add_argument("--right-zone", dest="right_zone", default="自动",
                    choices=("自动", "有", "无"),
                    help="三/四/五层右区（熄日余径）有无：默认按格点宽度自动判；"
                         "截图/识别拿不准时，先问玩家「本局是否存在熄日余径？」再传 有/无")
    ap.add_argument("--right-dots", dest="right_dots", default="",
                    help='右区**空地小圆点**坐标（手报，如 "10,3;9,4"）。'
                         "四层 1↔4、2↔3 的格集互相包含，没有空地位置**永远多解** → "
                         "只能 ⚠️；报出来形状唯一锁定，边表才算已核验。"
                         "空地是 **0 分中继（可穿过）**：报出来才能把穿过它的形状表边接进图，"
                         "不报就只能丢边、结论偏保守")
    ap.add_argument("--auto-dots", dest="auto_dots", action="store_true",
                    help="自动检测右区空地圆点（**实验性**，未按实拍标定；检不到就退回多解 ⚠️）")
    ap.add_argument("--four-ending", dest="four_ending", default=None,
                    metavar="要|不要",
                    help="**二层必答**（玩家手报/截图识别到二层都要先问）：本局是否要打四结局？"
                         "全程追猎：要=尽头 8 走 1 次；不要=尽头 0、不再必走。"
                         "险路尽头：要→再问 --four-ending-pre；不要→小径=尽头、可作可选终点")
    ap.add_argument("--four-ending-pre", dest="four_ending_pre", default=None,
                    metavar="已获取|未获取",
                    help="二层·险路尽头模式·四结局=要 的必答问题二：是否已获取四结局前置藏品？"
                         "已获取→小径=尽头、可作可选终点；未获取→小径 0、不作终点")
    ap.add_argument("--three-ending", dest="three_ending", default=None,
                    metavar="要|不要",
                    help="**三层必答（仅本层无右区/熄日余径时）**：本局是否打三结局？"
                         "不要→普通逻辑；要→再问 --three-ending-pre。"
                         "有右区 = 四结局线，这道题不适用（传了算错）")
    ap.add_argument("--three-ending-pre", dest="three_ending_pre", default=None,
                    metavar="已获取|未获取",
                    help="三层·三结局=要 的必答问题二：是否已获取三结局前置收藏品？"
                         "未获取→「先行一步」按 15 分必走点（藏着就列候选坐标让玩家去找）")
    ap.add_argument("--second-ending", dest="second_ending", default=None,
                    metavar="要|不要",
                    help="**五层必答（仅左区出现 3 个「命运所指」时）**：本局是否打二结局？"
                         "不要→常规逻辑、终点=右区命运所指；要→右区命运所指必走（尽量排最后，"
                         "进入后加工品次数强制→1），最后一步=左区带徽章的命运所指")
    ap.add_argument("--second-ending-node", dest="second_ending_node", default=None,
                    metavar="c,r",
                    help="五层·二结局=要 时，左区**带徽章**那个「命运所指」的坐标（手报，"
                         "如 3,1）。徽章检测没认出来/认错时用它锁定最后一步")
    ap.add_argument("--root", default=None,
                    help="理想源（树根）坐标，如 3,0。树根**地图上永远没有标签**，只能玩家口报："
                         "N16文明开化模式二~六层**必传**（追猎进入时大概率出现；漏=漏 2AP+15 分）；"
                         "普通分队模式可不传（不传=本层按无理想源算）")
    ap.add_argument("--grants", default="")
    ap.add_argument("--visited", default="",
                    help="**滚动重算**用的「已经走过/已经触发过的格子」，分号分隔，如 \"6,3;4,0;1,2\"。"
                         "语义（2026-10-29 起）：① 这些格子不再按原类型计分（避免把打过的树根/行商"
                         "再算一遍 15/11 分）；② 其中的**起点格**上的 grant（树根 +2 / 羽瞰点 +1）"
                         "**视为已领取**，不再重复发放——玩家口报 --ap 时已经是领过之后的余额。"
                         "不传 = 全部未走（开局口径）")
    ap.add_argument("--items", default="",
                    help='默认按**件数**："标准引擎:1" = 1 件（耐久 3 次）；'
                         '只剩 N 次要写 "标准引擎:剩1次"，或 "标准引擎:2*1"（2 件各剩 1 次）')
    ap.add_argument("--no-must", action="store_true",
                    help="不启用必走点约束（只按价值最优；会先给出路线，但可能漏树根/行商/右区作战；"
                         "二层四结局=要 的「尽头必走」、三层三结局的「先行一步必走」不关）")
    ap.add_argument("--no-learn-start", action="store_true",
                    help="本次不写入起点表（起点是猜的/弱证据时用；observe.py 在候选判据打 ⚠️ 时会带上）")
    ap.add_argument("--badge-none", dest="badge_none", action="store_true",
                    help="玩家口报**本局没有白圈徽章**：徽章检测崩溃时用它把 ❌ 降为 ⚠️（"
                         "橡果 +12 / 泪之聚落 0 分 / 五层二结局入口这三类定价仍未经验证）。"
                         "不确定就别传——默认按「读失败 = 数据不可信」判 ❌")
    ap.add_argument("--labels-confirmed", dest="labels_confirmed", action="store_true",
                    help="玩家**逐格核对过本层图上的节点标签**：判词「输 入」里的「节点标签」"
                         "从「待确认（OCR）」升为「事实」，✅ 的末行不再为这部分挂「N 项输入待确认」。"
                         "只管**节点标签**——徽章像素检测、右区拓扑、猜出来的起点不在其内，"
                         "那几项仍会各自记「待确认」，所以干净的 ✅ 要它们也清掉")
    ap.add_argument("--joint", action="store_true",
                    help="三/四/五层改回「左右区一起求」的整体最优（默认是分阶段求优）")
    ap.add_argument("--max-time", type=float, default=60.0)
    ap.add_argument("--explain", action="store_true",
                    help="判词前附一段「转述文案」（【结论】【下一步】【备选】【再看】【风险】），"
                         "给小模型/流水线**逐字转达**用；会顺带收集 slack=3 内的备选解"
                         "（仅整体求解分支收集；分阶段/二层多候选不产出备选并如实说明）")
    a = ap.parse_args()

    # 两个模式开关都必须在任何建场景/求解之前定下（grants、定价、block、终点约束都按它们算）
    R.set_mode(a.mode)
    R.set_end_mode(a.end_mode)

    # 二层「四结局」问询（2026-10-30 玩家新增）：**先问玩家，再规划**。
    # 校验放在读图之前——不能让工具先算出一版「未问」的结果，也不能让未答参数静默按旧口径跑。
    try:
        R.set_four_ending(a.four_ending)
        R.set_four_ending_pre(a.four_ending_pre)
    except ValueError as exc:
        print("❌ 不可用：")
        print(f"   - {exc}")
        return 2
    _ask_errs = R.layer2_answer_errors(a.layer)
    if _ask_errs:
        print("❌ 不可用：二层的「四结局」问询还没完成——**先问玩家，再规划**。")
        for _e in _ask_errs:
            print(f"   - {_e}")
        print("   · 全程追猎：只问「本局是否要打四结局？」→ --four-ending 要|不要")
        print("   · 险路尽头：先问 Q1；答「要」再问 Q2「是否已获取四结局前置藏品？」"
              "→ --four-ending-pre 已获取|未获取")
        return 2

    # 三层「三结局」问询（2026-10-30 玩家新增）：只在**本层无右区**时问。
    # 这里先挡「用错层」（不需要读图）；右区有无要等格点拟合，见下面的 _ask3。
    try:
        R.set_three_ending(a.three_ending)
        R.set_three_ending_pre(a.three_ending_pre)
    except ValueError as exc:
        print("❌ 不可用：")
        print(f"   - {exc}")
        return 2
    _e3 = R.layer3_scope_errors(a.layer)
    if _e3:
        print("❌ 不可用：")
        for _e in _e3:
            print(f"   - {_e}")
        return 2

    # 五层「二结局」问询（2026-10-31 玩家新增）：只在「五层 + 左区 3 个命运所指」时问。
    # 这里先挡「用错层」；是否真的 3 个命运所指要等建完节点表（见下面的 _ask5）。
    try:
        R.set_second_ending(a.second_ending)
    except ValueError as exc:
        print("❌ 不可用：")
        print(f"   - {exc}")
        return 2
    _e5 = R.layer5_scope_errors(a.layer)
    if _e5:
        print("❌ 不可用：")
        for _e in _e5:
            print(f"   - {_e}")
        return 2

    # ── 截图本身先验：不存在 / 打不开 → 直接 ❌（exit 3）────────────────
    # 2026-10-31 QA：以前路径写错时 RapidOCR 抛 `LoadImageError`、PIL 抛
    # `UnidentifiedImageError`，都是一个 Python traceback（exit 1）——
    # 与本文件开头「任何一项检查不过就整体判 ❌ 并说明原因」的约定不符，
    # 执行方还会误以为是「工具坏了」而转手动规划（正是输出门禁要禁止的行为）。
    # 位置：**放在三个问询闸之后**（问询闸的 exit 2 约定优先，见 〇·〇·一/三/四），
    # 读图之前。
    if not os.path.isfile(a.image):
        print("❌ 不可用：")
        print(f"   - 截图不存在：{a.image}")
        print("   · 要给**本局刚截的图**：先跑 tools/grab.py，或用 --image 指到"
              "刚存下的那张；别指向 runs/shots 里的旧图（旧图会把整层路线算错）。")
        return 3
    try:
        with Image.open(a.image) as _im:
            _im.verify()
    except Exception as _exc:                      # noqa: BLE001  坏图/非图片
        print("❌ 不可用：")
        print(f"   - 截图打不开（不是图片 / 文件损坏）：{a.image}")
        print(f"   · 底层报错：{type(_exc).__name__}: {_exc}")
        print("   · 重新截一张（tools/grab.py），或确认 --image 指向的是 PNG/JPG。")
        return 3

    # 坐标参数先做格式校验：玩家/Agent 口报的 "3,0" 写错时，
    # 2026-10-29 前会在 RS.xy 里抛 ValueError traceback（exit 1），
    # 与「任何一项检查不过就整体判 ❌」的约定不符。
    for _flag, _val in (("--start", a.start), ("--root", a.root), ("--final", a.final),
                        ("--second-ending-node", a.second_ending_node)):
        if not _val:
            continue
        try:
            RS.xy(_val)
        except ValueError as exc:
            print("❌ 不可用：")
            print(f"   - {_flag} 坐标格式错误：{exc}（正确写法如 3,0）")
            return 3

    problems = []          # 任何一条都会让整体判 ❌
    warnings = []          # 不致命、但会改变可信度：最终判「⚠️ 有条件可用」并在判词里列全
    unknown = []           # 搜索没跑完 ⇒ **不能判定**（⏳ 未定，exit 4）。既不是 ❌ 也不是可用
    notes = []

    # ── 1. 读图 ────────────────────────────────────────────────
    rows = LO.layer_dims(a.layer, a.config)[1]
    labels = LO.read_image(a.image)
    _W, _H = Image.open(a.image).size
    # 三/四/五层可能**没有右区**（未获取四结局前置藏品，左区居中）。
    # 按「完整宽度 vs 左区宽度」A/B 选列；没有右区时不再按 11/12/14 列硬套。
    # 六层有两个完全不同的图（2026-10-30）：盲目者所见 5×4 / 源流交汇处 6×5。
    _six = (a.layer == 6 and "源流" in str(a.config or ""))
    cols, gx, ginfo = LO.choose_layer_cols([l[0] for l in labels], a.layer, _W, cfg=a.config)
    if _six:
        cols = LO.layer_dims(6, "源流")[0]
        gx = sorted(LO.choose_lattice([l[0] for l in labels], cols, center=_W / 2.0))
        ginfo = {"has_right": False, "bounds_ok": True}
    has_right = LO.has_right_cols(a.layer, cols)
    _n_rz = len(problems)
    if a.right_zone != "自动" and a.layer not in (3, 4, 5):
        problems.append(f"--right-zone {a.right_zone} 只对三/四/五层有意义")
    elif a.right_zone == "有" and not has_right:
        problems.append("你指定本层有右区，但截图按左区宽度拟合（可能只截了左区）——"
                        "重截完整宽度，或改 --right-zone 无")
    elif a.right_zone == "无" and has_right:
        problems.append("你指定本层无右区，但截图是完整宽度（含右区列）——"
                        "先确认是否已获取四结局前置藏品/截图是否完整")
    _rz_conflict = len(problems) > _n_rz
    # 三层·无右区 → 三结局问询（2026-10-30 玩家新增，强制）。
    # 放在右区判定之后：右区有无决定这道题问不问；右区参数自相矛盾时先别追问，
    # 让玩家先修截图/参数（否则会拿一个「右区冲突」的局面去问不适用的题）。
    if not _rz_conflict:
        _ask3 = R.layer3_answer_errors(a.layer, has_right)
        if _ask3:
            print("❌ 不可用：" + R.layer3_error_headline(_ask3) + "。")
            for _e in _ask3:
                print(f"   - {_e}")
            print("   · 本题只在**本层无右区（熄日余径）**时问："
                  "Q1「本局是否打三结局？」→ 答「要」再问 "
                  "Q2「是否已获取三结局前置收藏品？」")
            print("   · 参数：--three-ending 要|不要；要 再加 --three-ending-pre 已获取|未获取")
            print("   · 三层有右区 = 走四结局线，这道题不适用；"
                  "同一局三层的滚动重算沿用答案")
            return 2
    if not ginfo.get("bounds_ok"):
        # 2026-11-02（玩家第 1 条意见）：格点整体贴到地图边缘 = **坐标基准不可信**。
        # 这不是「提个醒」，而是整张图的 x/y 都可能整体偏一列/一行——旧版只进 notes，
        # 于是「格点错了但求解器恰好找得到一条路」照样判 ✅。现在一律 ❌。
        problems.append("格点拟合越出地图区（截图/列数可能不对）→ **坐标基准不可信**，"
                        "整条路线不能按这张图算。重截一张：让地图区左右/上下都留出边距，"
                        "或核对 --layer/--config 是否选错")
    gy = sorted(LO.choose_lattice([l[1] for l in labels], rows, center=0.49 * _H), reverse=True)
    # 小人锚点自证：最左列只有起点（无标签）时整体会右移一列（2026-10-29 一层）。
    gx, gy, _align_note = LO.align_lattice(a.image, labels, gx, gy, cols, rows)
    if _align_note:
        notes.append(_align_note)
    # 先把「格 → 标签」铺好：起点判据（无标签格）和地标判据（险路恶敌/尽头）都要用它，
    # 2026-10-28 曾把它放在这些判据之后 → UnboundLocalError。
    grid = LO.assign(labels, gx, gy, cols, rows)
    im = Image.open(a.image).convert("RGB")
    # 白圈徽章（2026-10-30）：压在「不期而遇」上要分两种——
    # 骷髅面罩=泪之聚落（0 分、默认不进，可 0 分过路）、蜘蛛=线人（正常分）；
    # 失与得/作战只提示、不改价；**橡果=迷藏**（记坐标，收益 +12 并入所在节点，
    # 带橡果的空地从硬墙里放行 —— 2026-10-30 新口径，见 rules.ACORN_VALUE）。
    # **必须拿底下的节点类型再解释**。
    acorn_cells = []
    fate_badge = []
    try:
        _bd = LO.apply_badges(im, grid, gx, gy, cols, rows, layer=a.layer)
        for _bn in _bd["notes"]:
            print("  徽章：" + _bn)
        acorn_cells = _bd["acorn"]
        fate_badge = _bd.get("fate_badge") or []
        if fate_badge:
            print("  命运所指+徽章：" + "、".join(f"({q[0]},{q[1]})" for q in fate_badge)
                  + "（五层=二结局关卡入口；交给二结局问询判终点）")
        if acorn_cells:
            print("  迷藏（橡果）：" + "、".join(f"({q[0]},{q[1]})" for q in acorn_cells)
                  + f" → 收益 +{R.acorn_value():g} 并入所在节点（带橡果的空地放行）")
    except Exception as _be:
        # 2026-11-02（玩家第 1 条意见）：**徽章读失败 ≠ 没有徽章**。
        # 同一个检测器同时决定三条定价：橡果 +12/格、泪之聚落 0 分、
        # 五层二结局入口（二结局=不要 时要 -99 硬避；=要 时是本层终点）。
        # 旧版只进 notes → 徽章全丢也照样判 ✅。现在默认 ❌；
        # 玩家确报「本局没有白圈徽章」时用 --badge-none 降为 ⚠️（风险仍要展示）。
        _bmsg = (f"徽章检测失败：{_be}（橡果 +12/格、泪之聚落 0 分、五层二结局入口"
                 f" 三类定价全部未经验证）")
        if a.badge_none:
            warnings.append(_bmsg + " —— --badge-none：按「本局没有白圈徽章」继续")
        else:
            problems.append(_bmsg + "。确认本局确实没有白圈徽章就加 --badge-none 重跑；"
                                    "否则先修截图重截（白圈要完整压在节点上）")
    # 起点：**拿候选列表**，再和构型表核对（不要盲取第一个）
    cands = LO.detect_start_candidates(im, gx, list(reversed(gy)), labels)
    cand_cells = [(c, rows - 1 - r) for c, r in cands]
    st = cands[0] if cands else None
    explicit_start = bool(a.start)
    start = RS.xy(a.start) if a.start else (
        (st[0], rows - 1 - st[1]) if st else None)

    # ── 2. 构型与起点一致性（判据链见 resolve_start 的 docstring）──
    try:
        edges = BS.config_edges(a.layer, a.config)
    except SystemExit as exc:
        problems.append(f"找不到构型 {a.config}：{exc}")
        edges = []
    cfg_start = BS.config_start(a.layer, a.config) if edges else None
    cells = {p for e in edges for p in e}
    # 「构型外的格子」要先算出来：**起点偏一列**和**构型选错**必须区分开
    # （前者校正起点，后者换构型——见 resolve_start）。
    outside_probe = [q for q in grid
                     if q[0] <= BS.left_max(a.layer, cols, a.config) and q not in cells]
    start, start_warned = resolve_start(start, cfg_start, explicit_start, cand_cells,
                                        grid, cells, outside_probe, edges,
                                        a.layer, a.config, problems, notes,
                                        rolling=bool(a.visited))
    if start is None:
        # 起点是整条路线的前提。以前这里会继续用 `[0, 0]` 兜底把场景建出来：
        # 既可能算出一条**从假起点出发**的路线，又会在判词「输入可信度」里 `tuple(None)` 崩掉
        # （2026-11-02 实测：二层构型1 的 (0,0) 恰好是合法格 → TypeError + exit 1）。
        # 现在直接判 ❌，不再拿占位坐标往下跑。
        print("❌ 不可用：")
        for _p in problems:
            print("   - " + _p)
        print("   · 起点只能由**玩家口报**或小人检测给出：用 --start c,r；"
              "滚动重算报**当前所在格**（截图只是佐证，冲突以人报为准）")
        return 3

    # 起点是**硬事实**（玩家 2026-10-28：所有构型的起点都是固定的），
    # 但**写入 `起点表.json` 推迟到整次校验之后**（见 main 末尾）——
    # 2026-10-28 审计：以前写在这里（地标校验、构型外格子检查之前），
    # 一次偏掉的识别会把构型起点锁死（写进去后冲突只打印、不覆盖）。

    # ── 3. 定点覆盖 + 自检 ─────────────────────────────────────
    forced = {}
    # 六层也有定点节点（(0,2)/(4,3) 紧急作战、(3,3) 行商…），2026-10-28 前没接
    if a.layer != 5:
        ends, fixed = AC.config_fixed(a.layer, a.config)
        for q in ends:
            forced[RS.key_xy(q)] = "险路尽头"
        for q, t in fixed.items():
            forced[RS.key_xy(q)] = t

    cells = {p for e in edges for p in e}
    # 左区最右列：**只认 build_scenario.left_max() 这一个来源**
    # （五层 L8 是 13 列、缝在 x=9，左区只到 8；以前这里和 right_zone 各算一遍，对不上）
    lm = BS.left_max(a.layer, cols, a.config)
    forced_t = {RS.xy(k) for k in forced}      # 几何检查用元组键
    outside = [q for q in (set(grid) | forced_t) if q[0] <= lm and q not in cells]
    if outside:
        problems.append(f"读到构型外的格子 {sorted(outside)}（格点或行列错位）")

    # ── 4. 建场景 ──────────────────────────────────────────────
    # 三/四/五层的**右区不在「左区构型」文件里**，必须单独拼上。
    # 这段逻辑现在在 build_scenario.right_zone()，**两个入口共用一份**
    # （2026-10-28 前 route.py 有、build_scenario 没有，同一张图给出不同格子集合）。
    # 2026-10-30：先判本层**有没有右区**——未获取四结局前置藏品时，左区居中、没有右区；
    # 此时不预留 AP、右区必走点/收益全部失效，绝不能按「右区没进去」判 ❌。
    extra_cells, rz_notes = [], []
    _rz_meta = {}
    _right_dots = []
    if a.layer in (3, 4, 5):
        if has_right:
            try:
                _right_dots, _dot_notes = BS.resolve_right_dots(
                    im, grid, gx, gy, lm, manual=a.right_dots, auto=a.auto_dots)
            except ValueError as exc:
                problems.append(str(exc))
                _right_dots, _dot_notes = [], []
            _right_dots, _bad_dots = split_right_dots(_right_dots, lm)
            if _bad_dots:
                problems.append(
                    "报来的空地圆点落在**左区**：" + "、".join(f"({q[0]},{q[1]})" for q in _bad_dots)
                    + "——左区「空地」是 -99 的硬墙（不是 0 分中继）。"
                      "把它当右区空地会凭空开一条通道（路线能从墙里穿过去）。"
                      "核对 --right-dots 的列号再重跑")
            for _n in _dot_notes:
                print("  " + _n)
            _probe = None
            if _right_dots:
                try:
                    _probe = LO.make_right_edge_probe(im, gx, gy)
                except Exception as _pe:               # noqa: BLE001  探针是加分项
                    print(f"  ⚠️ 右区连线探针不可用：{_pe}")
            extra_cells, rz_notes = BS.right_zone(a.layer, grid, cells, edges, lm,
                                                  meta=_rz_meta, dots=_right_dots,
                                                  probe=_probe)
            for n in rz_notes:
                print("  " + n)
            # 右区格数对不上固定构成（三层 6 / 四层 7 / 五层 9）→ 数据本身不可信。
            if any(n.startswith("⚠️ 右区格数") for n in rz_notes):
                problems.append("右区格数与固定构成不符（三层 6 / 四层 7 / 五层 9）——"
                                "先核对截图（多半漏了暗图标），别直接用这份读数求解")
            # 2026-11-02（玩家第 2 条意见）：**拓扑可信度单独进判词**。
            # 边表查不到形状（或形状多解）时只能用四邻接假设——求解器仍会给你一条
            # 「合法于假设图」的路线，但那不等于合法于真实地图。降级为 ⚠️，不判 ✅。
            if _rz_meta.get("topology") in ("ambiguous", "none"):
                _sh = _rz_meta.get("shape") or ""
                warnings.append(
                    "右区（熄日余径）边表**未验证**：按 4 邻接临时假设"
                    + (f"（形状多解：{_sh}）" if _sh else "（没有形状对得上，可能漏检）")
                    + "——右区若有暗线不连通或斜向连接，这条路线就错了。"
                      "核验办法：按 12-三层全图 / 16-四层全图 人工对一遍，"
                      "或把右区空地小圆点的坐标报出来")
            if _rz_meta.get("dropped"):
                # 形状表对上了（table），但有些边的端点落在**未探知的空地**上：
                # 那一格没坐标就进不了图，边只能缺着。不是「墙」，是「不知道在哪」。
                warnings.append(
                    "右区（熄日余径）最多有 %d 条形状表边**没接进图**——端点落在未探知的空地上"
                    "（没报 --right-dots，空地位置只能由形状反推）→ 右区可能少一条通路，"
                    "这条路线偏保守；把空地坐标用 --right-dots c,r 报出来即可补全"
                    % len(_rz_meta["dropped"]))
            if not extra_cells:
                problems.append("截图是完整宽度、本层应有右区，但右区一格都没读到——"
                                "可能切边/OCR 漏检；先重截或核对，别用这份读数求解")
        else:
            print("  右区：无（本层无熄日余径；左区居中）→ 不预留 AP、右区必走点/收益失效")

    nodes = {}
    for q in sorted(cells):
        t = forced.get(RS.key_xy(q)) or (grid[q][0] if q in grid else "空地")
        # 六层「源流交汇处」：无标签格就是**林间空地**（可通行、0 分，见 block_types 例外）
        # 右区改名（三/四/五层统一）：右区作战 16、右区紧急 21、右区诡秘 1.3/8 ——
        # 不改名的话求解器按左区同类定价（三层右区普通作战会被当成 0 分）
        if q in extra_cells:
            t = R.right_zone_type(t)
        nodes[RS.key_xy(q)] = t
    if start:
        k = RS.key_xy(start)
        if nodes.get(k, "空地") == "空地":
            nodes[k] = "起点"

    # ── 五层·二结局问询（2026-10-31 玩家新增）──────────────────────────
    # 判据：**层数=5 且左区读到 3 个「命运所指」** = 拿了二结局相关藏品。
    # 这时先问玩家「本局是否打二结局？」，没答就停在提问（exit 2），不许先算一版。
    _se_left, _se_right = BS.layer5_fate_cells(nodes, lm) if a.layer == 5 else ([], [])
    _se_marker = bool(a.layer == 5 and len(_se_left) >= R.SECOND_FATE_MARKER_COUNT)
    _ask5 = R.layer5_answer_errors(a.layer, _se_marker)
    if _ask5:
        print("❌ 不可用：五层的「二结局」问询还没完成——**先问玩家，再规划**。")
        for _e in _ask5:
            print(f"   - {_e}")
        print("   · 判据：五层左区读到 3 个「命运所指」= 拿了二结局相关藏品")
        print("   · 参数：--second-ending 要|不要；"
              "要 时可用 --second-ending-node c,r 锁定带徽章那个")
        return 2
    if _se_marker:
        print("  二结局：左区读到 3 个「命运所指」"
              + "、".join(f"({q[0]},{q[1]})" for q in _se_left)
              + f"（右区 {len(_se_right)} 个）→ 本层是二结局强化局")

    # 树根：地图上不写标签（显示为未知的凶戾/紧急作战），只能玩家报坐标
    if a.root:
        rq = RS.xy(a.root)
        k = RS.key_xy(rq)
        if k not in nodes:
            problems.append(f"理想源 {k} 不在该构型的格子里")
        else:
            nodes[k] = "树根"
    elif R.is_normal_squad():
        # 普通分队模式：不报 = 没有理想源（玩家 2026-10-29 定）。
        # 文明开化模式下走不到这个分支——那时漏报会由下面的 ⚠️ 提醒。
        print("  模式：普通分队 —— 未报 --root，本层按**无理想源**求解")

    # 六层源流（2026-10-30 玩家口径）：**block 所有不带橡果徽章的林间空地**。
    # 与盲目者所见同口径；带橡果的空地（迷藏）可进、按 12 分（地基 0 + 橡果 12）。
    # 做法：把墙格从边表/节点里删掉（求解器图里就不存在），报错/路线都按真实可达算；
    # 玩家没给加工品、徒步到不了 block 点 → 没有路线就如实 ❌，不硬凑。
    if _six:
        _wall = BS.source_wall_cells(nodes, acorn_cells)
        if _wall:
            _wall_t = {RS.xy(k) for k in _wall}
            edges = [[a, b] for a, b in edges
                     if RS.xy(a) not in _wall_t and RS.xy(b) not in _wall_t]
            for _k in _wall:
                nodes.pop(_k, None)
            cells = {p for e in edges for p in e}
            print("  源流：block 无橡果的林间空地 " +
                  "、".join(f"({q[0]},{q[1]})" for q in sorted(_wall_t)))

    # 定点覆盖校验：**只核对「格子是否在边表内」是不够的**——
    # 一层 (0,0) 在边表里，但它必须从「未知的诡秘」覆盖成「险路尽头」（伪装尽头）。
    # 构型表声明的定点格，即使没出现在边表里也**确实存在**（尽头常常只画一条暗线）
    for k, t in forced.items():
        if k not in nodes:
            nodes[k] = t
            cells.add(RS.xy(k))
    # 显示名 → 实定：把「地图上写的」和「实际是什么」并排列出来，
    # 免得每次都有人问「为什么凶戾显示成诡秘」。地图上的伪装是**规则**，不是矛盾。
    aliases = []
    for k, t in nodes.items():
        d = grid.get(RS.xy(k))
        shown = d[0] if d else "∅(无标签)"
        # 只列「地图上有字、但和实定不同」的格——∅→空地/起点 是常识，列出来是噪音
        if shown != t and not shown.startswith("∅"):
            aliases.append((k, shown, t))
    if aliases:
        print("  显示 → 实定（地图上的名字是伪装，不是矛盾）：")
        for k, shown, t in sorted(aliases):
            why = []
            if t == "树根":
                why.append("玩家口报理想源")
            if t == "险路尽头":
                why.append("构型表定点")
            if t == "诡意行商":
                why.append("构型表定点")
            print(f"    ({k})  地图「{shown}」 → {t}" + (f"（{'、'.join(why)}）" if why else ""))

    if forced:
        bad = [(k, t, nodes.get(k)) for k, t in forced.items() if nodes.get(k) != t]
        if bad:
            problems.append("定点覆盖失败：" + "、".join(
                f"({k}) 应为{t} 实际{v}" for k, t, v in bad))
        print("  定点覆盖：" + "、".join(f"{k}={t}" for k, t in sorted(forced.items())))

    grants = {}
    # 树根（理想源）的 +2 AP **按模式**：文明开化 +2；普通分队 0（不再回复）。
    # 补多少必须走 rules.grant_ap()——直接写死 2 会在普通分队模式灌水。
    # 树根在 nodes 里却拿不到 grant → 说明「玩家报了理想源、预算却按基础值算」
    # 的机械拦截点（仅文明开化模式）。
    for k, t in nodes.items():
        g = R.grant_ap(t)
        if g > 0 and k not in grants:
            grants[k] = g

    # 羽瞰点必定 +1 AP —— 从节点类型自动推出，不需要玩家口报
    for k, t in nodes.items():
        if t == "羽瞰点":
            grants[k] = 1
    if a.root and not R.is_normal_squad() and RS.key_xy(rq) in nodes:
        grants[RS.key_xy(rq)] = 2   # 树根 +2 AP（普通分队模式不给，见 grant_ap）
        # 坐标越界时上面已记 problems；这里别再给幽灵格加 grant，
        # 否则会多报一条误导性的「grants 非法：(9,9)=?」（2026-10-29 修）
    for part in filter(None, a.grants.split(";")):
        # 2026-10-29 修复：`--grants "3,0"`（漏冒号/漏数字）原来直接 ValueError traceback，
        # 现在给可读的格式说明并按「无效输入」整体判 ❌
        try:
            k, v = part.split(":")
            grants[RS.key_xy(k)] = int(v)   # 归一化：容忍 "3, 0" 这类带空格的写法
        except ValueError:
            problems.append(
                f"--grants 格式错误：{part!r}（正确写法 \"c,r:AP\"，多段用分号，"
                f"如 --grants \"3,0:2;1,3:1\"）")
    bad_grants = [(k, nodes.get(k, "?")) for k in grants
                  if nodes.get(k, "?") not in R.grant_ok_types()]
    if bad_grants:
        problems.append("grants 非法（选项收益不能当确定收益）：" +
                        ", ".join(f"({k})={t}" for k, t in bad_grants))
    items = []
    try:
        # cfg 传进去：六层两张图次数规则不同（源流不夹、盲目者所见每件 1 次）
        items, item_notes = R.parse_items_spec(a.items, a.layer, a.config)
    except R.UnknownItem as e:
        problems.append(f"未知道具「{e}」——可用：{'、'.join(R.available_items())}")
        item_notes = []
    except ValueError as e:
        # 2026-10-29 修复：`--items "标准引擎:abc"` / `"2*"` 以前直接 traceback
        problems.append(
            f"--items 无法解析（{e}）——写法：标准引擎:2（2 件）/ 标准引擎:剩1次 / 标准引擎:2*1")
        item_notes = []
    for line in item_notes:
        print("  道具：" + line)

    # 定价 = 通用兜底 + 本层口径。**两层都要 merge**：
    # 2026-10-28 审计：以前只用 BS.PRICES[layer]，缺的节点类型在求解器里静默取 0 分
    # （三层实测「曲折密道」被当成 0 分中继走了过去，而它必须是 -99 的禁区）。
    values = dict(RS.DEFAULT_VALUES)
    values.update(R.layer_values(a.layer))
    if _six:
        # 六层源流：无标签格是林间空地（可通行、0 分），不是 -99 的墙
        values["空地"] = 0
    # 别名必须在推期望**之前**套：`_infer_left_quota` 会用逐格期望覆盖
    # 「未知的凶戾」，反过来套就把期望换回 `凶戾` 的层均值（2026-10-28 复查）。
    BS.apply_unknown_aliases(values)

    # ── 诡秘：按距离带 + 数量约束逐格推候选，不用统一期望 ──────
    # 依据 `06-距离表`：某类节点在某层只可能出现在「距起点 N–M 步」的格子上。
    # 再扣掉「本层已点亮过」的类型（每层每种通常只有 1 个），剩下的取均值。
    # ── 诡秘 / 凶戾 的期望值：距离带 + 数量约束 ─────────────────
    # 逻辑在 build_scenario.infer_unknown()，**两个入口共用一份**
    # （以前只有 route.py 有，build_scenario 直接按「诡秘 = 5」铺，路线因此不同）。
    secret_notes, quota_notes = BS.infer_unknown(
        a.layer, nodes, values, edges, start, lm, a.root)
    if secret_notes:
        print(f"  推断：诡秘按距离带逐格推候选（{len(secret_notes)} 格）")
        for n in secret_notes[:6]:
            print("    " + n)
        if len(secret_notes) > 6:
            print(f"    ……其余 {len(secret_notes) - 6} 格同理")
    for n in quota_notes:          # 左区一句 + 右区一句都打（右区不许盖掉左区）
        print("  推断：" + n)

    # ── 五层·二结局：定价 / 必走点 / 终点 / 加工品次数钳制（2026-10-31）──
    # 放在 infer_unknown **之后**：逐格期望会把「诡秘」改名成 `诡秘@x,y`，
    # 早算的话 `layer5_fate_cells` 看到的类型还是旧的（命运所指不受影响，但口径统一）。
    se = BS.second_ending_setup(
        a.layer, nodes, lm,
        fate_badge=fate_badge,
        answer=R.second_ending(),
        node_xy=RS.xy(a.second_ending_node) if a.second_ending_node else None)
    _se_prefix = "二结局：" if se.get("marker") else "五层终点："
    for n in se["notes"]:
        print("  " + _se_prefix + n)
    if se.get("error"):
        print("❌ 不可用：")
        print("   - " + se["error"])
        return 3

    sc = {
        "name": f"{a.layer}层 构型{a.config} / 起点{start} / AP{a.ap}",
        "mode": R.mode(),          # 分队模式随场景落盘：route_solver 直读文件时按它恢复
        "end_mode": R.end_mode(),  # 终点模式（全程追猎/险路尽头），与分队模式独立
        "has_right": bool(has_right),   # 三/四/五层：本层是否有右区（熄日余径）
        # 二层四结局问询（2026-10-30）：落盘，滚动重算/直读 scenario 时不再重复问
        **({"four_ending": R.four_ending()} if a.layer == 2 else {}),
        **({"four_ending_pre": R.four_ending_pre()}
           if a.layer == 2 and R.four_ending_pre() is not None else {}),
        # 三层三结局问询（2026-10-30）：同上，只在三层且答案存在时落盘
        **({"three_ending": R.three_ending()}
           if a.layer == 3 and R.three_ending() is not None else {}),
        **({"three_ending_pre": R.three_ending_pre()}
           if a.layer == 3 and R.three_ending_pre() is not None else {}),
        # 五层二结局问询（2026-10-31）：同上，只在五层落盘
        **({"second_ending": R.second_ending()}
           if a.layer == 5 and R.second_ending() is not None else {}),
        **({"second_ending_node": list(se["badged"])}
           if a.layer == 5 and se.get("badged") else {}),
        # 进入后加工品次数强制→1 的格（五层右区命运所指）——「尽量排最后」靠它自动实现
        **({"clamp_items_at": [[q[0], q[1]] for q in se["clamp"]]}
           if a.layer == 5 and se["clamp"] else {}),
        # 迷藏（橡果徽章）：收益已并入所在节点（cell_values + block_except，见下方 sc 补写）
        **({"acorn": [[q[0], q[1]] for q in acorn_cells]} if acorn_cells else {}),
        "edges": [[list(u), list(v)] for u, v in edges],
        "nodes": nodes, "start": list(start) if start else [0, 0], "ap": a.ap,
        "grants": grants, "items": items, "values": values,
    }
    # ── 滚动重算（--visited）：见 apply_visited 的 docstring ──
    visited_set = set()
    if a.visited:
        _v = apply_visited(nodes, grants, a.visited, start, notes)
        if _v:
            visited_set = {q for q in _v}
            sc["visited"] = [[q[0], q[1]] for q in _v]
    if start:
        sc["start"] = [start[0], start[1]]
    # 别名已在推期望**之前**套用（`BS.apply_unknown_aliases`，2026-10-28 复查）：
    # 放在这里会把 `_infer_left_quota` 算出的「未知的凶戾」逐格期望冲掉。
    # 终点模式（2026-10-29）：险路尽头模式下按**类型**收终点（solver 的 final_type：
    # 碰到任何一个该类节点即结束，「最后一步之前不进该类节点」自动成立）；
    # 全程追猎保持旧口径（五层 final = 命运所指坐标）。
    end_types = R.ending_types(a.layer) if R.is_end_jintou() else ()
    # 2026-11-02 修复：`end_type` / `end_txt` 以前在下面的「二结局接管」分支**之后**才赋值，
    # 而那个分支的 print 已经引用了 end_txt → 五层 + 3 个命运所指 + 险路尽头模式 + 已答
    # `--second-ending` 时抛 UnboundLocalError（traceback + exit 1），违反「一律 ✅/❌」的契约。
    end_type = end_types[0] if end_types else None      # 主类型（打印/兜底用）
    end_txt = "/".join(end_types)                       # 判词里列全候选
    # ── 五层·二结局：本层终点由二结局问询决定（**优先于终点模式**；玩家 2026-10-31）──
    # 五层左区出现 3 个命运所指时，「险路尽头」模式要求的「最后一步 = 险路恶敌」不再适用：
    #   二结局=要   → 最后一步 = 左区带徽章的「命运所指」（进入二结局关卡）；
    #   二结局=不要 → 最后一步 = 右区「命运所指」（常规逻辑）。
    _se_override = bool(a.layer == 5 and se.get("final") is not None
                        and R.second_ending() is not None)
    if _se_override and end_types:
        print("  二结局：本层终点改由二结局决定"
              f"（五层 3 个命运所指优先于终点模式「{end_txt}」）")
        end_types = ()
        # 终点已经交给 se["final"]（左区带徽章命运所指）：别让后续判词/--explain 的
        # 风险段再要求「最后一步 = 险路恶敌」——那会把正确解报成违规。
        end_type, end_txt = None, ""
    _se_force_joint = bool(_se_override and se["final"][0] <= lm)
    if end_types:
        sc["final_type"] = list(end_types)
        # 二层例外：小径=8 时也作为**硬终点**（进入即结束），否则「小径 8 + 尽头 8」会重复计分。
        _hard_end = R.ending_hard_types(a.layer)
        if "险路小径" in _hard_end:
            sc["hard_final_types"] = list(_hard_end)
        # 终点落格兜底：三/五层的恶敌是**构型表声明的地标硬事实**，OCR 漏检也要落格
        # （一/二/四层尽头已由定点覆盖强制落格；六层恶敌无构型表，靠 OCR 或 --final 手报）
        if "险路恶敌" in end_types and a.layer in (3, 5):
            for q in sorted(BS.config_landmarks(a.layer, a.config)):
                k = RS.key_xy(q)
                old = nodes.get(k)
                if old != "险路恶敌":
                    nodes[k] = "险路恶敌"
                    print(f"  终点兜底：构型表声明的险路恶敌 ({q[0]},{q[1]})"
                          + (f" 由「{old}」覆盖" if old else "（构型外格子，按地标落格）"))
        if a.final:
            fk = RS.key_xy(RS.xy(a.final))
            if fk not in nodes:
                problems.append(f"--final {fk} 不在该构型的格子里")
            else:
                old = nodes.get(fk)
                # 人报的终点坐标：类型已在候选里就保留（如一层小径），否则按主类型覆盖
                nodes[fk] = old if old in end_types else end_type
                sc["final_type"] = list(end_types)
        # 迷藏（2026-10-30 玩家口径）：带橡果的尽头/小径「可作为中继放行」。
        # 表达成**软终点**（停这里算收尾、继续走也不违规）；但玩家建议直接留给最后一步，
        # 所以这里只放宽、不强制。二层 8 分的尽头/小径不在此列（会重复计分）。
        _soft_end = BS.acorn_soft_final_types(nodes, values, acorn_cells, end_types)
        if _soft_end:
            sc["final_soft_types"] = list(_soft_end)
            if "hard_final_types" in sc:
                sc["hard_final_types"] = [t for t in sc["hard_final_types"]
                                          if t not in _soft_end]
            print("  迷藏（橡果）：带橡果的「" + "/".join(_soft_end) +
                  "」按**软终点**处理（可当中继，12 分）——建议留给最后一步")
    elif a.final:
        problems.append("--final 只在「险路尽头」终点模式下有意义（本次是全程追猎，已忽略）")
    elif a.layer == 5 and se.get("final") is not None:
        # 五层终点永远是「命运所指」：
        #   二结局=要   → 左区带徽章那个（se 已算好）；
        #   不要/常规   → 右区那个（玩家 2026-10-31 口径：常规逻辑终点在右区命运所指）。
        sc["final"] = [se["final"][0], se["final"][1]]
        print(f"  终点：({se['final'][0]},{se['final'][1]})"
              f"「{nodes.get(RS.key_xy(se['final']), '?')}」"
              + ("（二结局=要：左区带徽章命运所指）"
                 if R.second_ending() else "（二结局=不要/常规：右区命运所指）"))
    elif a.layer == 5:
        _l5 = [RS.xy(k) for k, t in nodes.items() if t == "命运所指"]
        if not _l5:
            problems.append("五层没有任何「命运所指」可作终点——核对截图（OCR 漏检？）")
        else:
            problems.append("五层读到命运所指但没能定出终点（左区 %d 个）——"
                            "核对徽章/重答 --second-ending" % len(_l5))

    # ── 三层·三结局：先行一步 = 15 分必走点（2026-10-30 玩家新增）──
    # 只在**本层无右区**时存在（有右区 = 四结局线，这道题不适用）。
    # 放在终点兜底之后：避免把刚被覆盖成「险路恶敌」的格子还当成先行一步候选。
    # 已探明 → derive_must 会把它锁进 must；藏着 → 列候选坐标提示玩家去找，
    # 求解器只能靠 15 分的期望值引导（坐标约束表达不了「N 选 1」）。
    xianxing_cands = []
    if a.layer == 3 and not has_right and R.three_ending_need_xianxing():
        _revealed = [RS.xy(k) for k, t in nodes.items() if t == R.THREE_XIANXING_NODE]
        if _revealed:
            print("  三结局：先行一步已探明 "
                  + "、".join(f"({q[0]},{q[1]})" for q in _revealed)
                  + " → 按 15 分必走点处理（进 must）")
        else:
            xianxing_cands = BS.xianxing_candidates(
                a.layer, nodes, edges, start, lm, visited=sorted(visited_set))
            if xianxing_cands:
                print("  ⚠️ 三结局：先行一步还没探明，可能藏在这些「未知的诡秘」里（距离带 2–4）：")
                print("     " + "、".join(f"({q[0]},{q[1]}) 距{d}步"
                                         for q, d in xianxing_cands))
                if len(xianxing_cands) == 1:
                    print("     → 三层左区必出 1 个先行一步，只剩这一格 → 它就是先行一步"
                          "（仍建议进场确认）")
                print(f"     → 请在这 {len(xianxing_cands)} 个坐标里找「先行一步」；"
                      "进去就拿到三结局前置收藏品（找到后按新截图重算）")
                notes.append("待找先行一步：候选 "
                             + "、".join(f"({q[0]},{q[1]})" for q, _d in xianxing_cands)
                             + "（三结局前置收藏品在这些「未知的诡秘」里）")
            else:
                print("  ⚠️ 三结局：先行一步还没探明，且没有距离 2–4 步的未知诡秘 → "
                      "本层找不到它（核对截图漏检/已走格）")
                notes.append("先行一步还没探明，且本层没有距离 2–4 步的未知诡秘"
                             "——三结局前置收藏品本层拿不到，先核对截图")

    # 必走点：树根 / 已探明的秘境行商·紧急作战 / 右区作战类 —— 锁死坐标再求解
    must = [] if a.no_must else BS.derive_must(a.layer, nodes, extra_cells)
    if (a.no_must and a.layer == 3 and not has_right
            and R.three_ending_need_xianxing()):
        # 与「二层四结局=要 的尽头必走」同口径：--no-must 不关三结局的先行一步必走
        _xq = [RS.xy(k) for k, t in nodes.items() if t == R.THREE_XIANXING_NODE]
        _add = [q for q in _xq if q not in must]
        if _add:
            must = list(must) + _add
            print("  --no-must：三结局的「先行一步必走」不关，仍锁进 must")
    if a.no_must and a.layer in (3, 4, 5) and extra_cells:
        # 2026-11-02（玩家第 6 条意见，裁决 = 保留配额约束）：
        # 右区作战的**期望**是「m 个名额分 21·need_e + 16·need_p」摊出来的。
        # 只有把被定价的那几格**全走一遍**，这个摊法才等于真实配额总值；
        # 关掉必走就会变成「只挑有价格的格子踩」——子集访问时逐格期望被高估
        # （四层 4 未知 3 名额：模型 17.7/格 vs 真实边际 13.25/格）。
        # 所以与「二层四结局尽头」「三层先行一步」同口径：--no-must 不关这一条。
        _rq = [RS.xy(k) for k, t in nodes.items()
               if RS.xy(k)[0] > lm and t in ("右区作战", "右区紧急作战", "右区凶戾")]
        _addq = sorted(q for q in _rq if q not in must)
        if _addq:
            must = list(must) + _addq
            print("  --no-must：右区作战**配额约束不关**（否则未知凶戾期望会被高估），"
                  "仍锁进 must：" + "、".join(f"({q[0]},{q[1]})" for q in _addq))
    # 五层·二结局=要：右区「命运所指」是**必走点**（硬约束；进入后加工品次数强制→1，
    # 所以它在最优解里会自然被排到最后几步——求解器状态带 items，钳制会真的砍后续跳跃）。
    if a.layer == 5 and R.second_ending() is True and se.get("must"):
        _add5 = [q for q in se["must"] if q not in must]
        if _add5:
            must = list(must) + _add5
            print("  二结局：右区命运所指 "
                  + "、".join(f"({q[0]},{q[1]})" for q in _add5)
                  + " 锁进 must（尽量排最后：进入后加工品次数强制→1）")
    if visited_set:
        # 已经打过的格子（如刚打掉的树根）不该再出现在必走里——否则必然判「必走点走不到」。
        dropped = [q for q in must if q in visited_set]
        if dropped:
            must = [q for q in must if q not in visited_set]
            print("  必走点：已走格 " + "、".join(f"({q[0]},{q[1]})" for q in dropped)
                  + " 从必走里摘掉（本轮已经打过）")
    sc["must"] = [[q[0], q[1]] for q in must]
    # 不可达类型：三层起空地/密道是硬墙，三/四层的命运所指也是墙（五层它是终点，不删）。
    sc["block"] = BS.block_types(a.layer, a.config)
    if end_types and "险路小径" in sc["block"]:
        # 2026-10-30 玩家新规：一/四层的险路小径是终点候选，**不许 block**
        # （二层小径不是终点、但同样永不 block：它是 0 分中继）
        sc["block"] = [t for t in sc["block"] if t != "险路小径"]
        print("  终点模式：「险路小径」已从 block 移除（本层最后一步候选之一）")
    # ── 迷藏（橡果）：收益并进所在节点 + 特定类型从硬墙里放行（2026-10-30 新口径）──
    # 排在这里：节点类型已被「树根 / 终点兜底 / 已走格→起点」改完，收益也过了
    # infer_unknown 的逐格定价 —— 早一步算会拿到旧类型或层均值。
    # 放行规则：空地 / 曲折密道（配对成功才放） / 险路尽头 放行；
    # **险路恶敌（全程追猎）与三·四层命运所指不放行**（玩家逐条口径）。
    _acorn_pairs, _acorn_pair_notes = BS.acorn_portal_pairs(nodes, acorn_cells)
    _acorn_vals = BS.acorn_cell_values(nodes, values, acorn_cells, _acorn_pairs)
    _acorn_except = BS.acorn_block_except(nodes, sc["block"], acorn_cells, _acorn_pairs)
    for _an in _acorn_pair_notes:
        print("  " + _an)
    if _acorn_pairs:
        sc["portals"] = [[[a[0], a[1]], [b[0], b[1]]] for a, b in _acorn_pairs]
    if _acorn_vals:
        print(f"  迷藏（橡果）：{len(_acorn_vals)} 格收益已并入节点（每格 +{R.acorn_value():g} 分）："
              + "、".join(f"({RS.xy(k)[0]},{RS.xy(k)[1]})={v:g}"
                          for k, v in sorted(_acorn_vals.items())))
    # 逐格价值覆盖 = 二结局命运所指（7 分 / -99 硬避）+ 迷藏橡果（+12）。
    # 两者不会落在同一格（徽章互斥），但合并写法保证后加的不会被覆盖掉。
    _cell_vals = dict(_acorn_vals or {})
    _cell_vals.update(se.get("cell_values") or {})
    # 右区空地（2026-11-02 玩家澄清：**0 分中继、可穿过**）：
    # 与 build_scenario 同一口径——block 豁免 + 逐格价 0（层表的「空地」是左区硬墙价）。
    if _right_dots:
        _ex = {tuple(x) for x in (_acorn_except or [])} | {tuple(q) for q in _right_dots}
        _acorn_except = [list(x) for x in sorted(_ex)]
        for _q in _right_dots:
            _cell_vals[RS.key_xy(_q)] = 0.0
        print("  右区空地：" + "、".join(f"({q[0]},{q[1]})" for q in _right_dots)
              + " 按 **0 分中继**接入（可穿过，block_except 豁免）")
    if _cell_vals:
        sc["cell_values"] = _cell_vals
    if se.get("cell_values"):
        print("  二结局：命运所指逐格定价 "
              + "、".join(f"({RS.xy(k)[0]},{RS.xy(k)[1]})={v:g}"
                          for k, v in sorted(se["cell_values"].items())))
    if _acorn_except:
        sc["block_except"] = _acorn_except
        print("  迷藏（橡果）：以下格原本按硬墙 block，带橡果后**放行**："
              + "、".join(f"({q[0]},{q[1]})" for q in _acorn_except))
    _acorn_waste = BS.acorn_wasted_cells(nodes, sc["block"], acorn_cells, _acorn_pairs)
    if _acorn_waste:
        notes.append("橡果落在禁踩格 " +
                     "、".join(f"({q[0]},{q[1]})={t}" for q, t in _acorn_waste) +
                     f"：这两类不放行（玩家口径），+{R.acorn_value():g} 分也不改变"
                     " → 这一层迷藏吃不到（要拿就得先确认能不能破例）")
    if must:
        print("  必走节点：" + "，".join(
            f"({x},{y})={nodes.get(RS.key_xy((x, y)), '?')}" for x, y in must))

    # ── 4.9 场景自检：把「求解器拒收输入」变成 ❌，不许是 traceback ──────
    # `route_solver.Solver.__init__` 对坏输入一律 `raise SystemExit(...)`（→ exit 1 +
    # stderr），与「任何检查不过就整体判 ❌ 并说明原因」的契约不符（2026-11-02 实测：
    # 起点落在被 block 删掉的格上时，整条命令只吐一行 `!! 起点 … 被 block 列表删掉了`）。
    # 这里在**求解之前**构造一次 Solver（只跑 __init__，不做搜索），顺便吞掉它的
    # `!! …按不可达处理` 噪音（真求解时还会再打一遍，不重复）。
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            RS.Solver(sc)
    except SystemExit as exc:
        print("❌ 不可用：")
        print("   - 场景自检失败（求解器拒收这份输入）：" + str(exc))
        print("   · 常见原因：起点/必走点落在 block（空地·密道·命运所指·恶敌）里，"
              "或 --items 写错了道具名/cov。核对截图与参数后重跑")
        return 3
    except Exception as exc:                       # noqa: BLE001  构造期任何异常都算数据问题
        print("❌ 不可用：")
        print(f"   - 场景自检异常：{type(exc).__name__}: {exc}")
        return 3

    # 识别/建场景阶段到此为止：此后 problems 里的都是求解与校验类问题。
    # 起点表写入判据（`_should_learn_start`）用它区分「识别出错」和「后续出错」。
    n_pre_solve = len(problems)

    # ── 5. 求解 ────────────────────────────────────────────────
    # 二层「险路尽头只走一次」：尽头是四结局道具，第 2 个没有战略价值。
    # 价格层表达不了这条规则（对着 8 分，求解器当然想多走），所以**逐个候选各解一次**：
    # 选中的那个按 8 分，其余按 0 分，取最优。这是机械替换，不靠人记。
    # （仅全程追猎；险路尽头模式由 solver 的 final_type 收终点，不需要这套替换。）
    end_cells = [k for k, v in nodes.items() if v == "险路尽头"]
    picked_end = None
    segments = None
    alts_ready = False   # --explain 的备选解是否已随求解收集（仅整体求解分支收集）
    # 2026-11-02（玩家第 3 条意见）：**把「求解口径」写明**，别让「解得出一条路线」
    # 被读成「这是最优解」。四种口径：
    #   STAGED_BEST   分阶段（右区固定预留）——结构性**不是**全局最优；
    #   CANDIDATE_BEST 二层多候选择优——撞到预算上限，未证明；
    #   JOINT_BEST    整体求解，但撞到时间/节点上限（结果未必最优）；
    #   JOINT_PROVEN  整体求解且搜索已穷尽 = 全局最优（在场景模型内）。
    solution_status = "JOINT_BEST"
    if end_types:
        n_end = sum(1 for t in nodes.values() if t in end_types)
        _hard_txt = "/".join(R.ending_hard_types(a.layer)) or "无"
        _soft_txt = ""
        if "险路小径" in end_types:
            _soft_txt = ("、「险路小径」进入即结束（硬终点，避免与尽头重复计 8）"
                         if "险路小径" in R.ending_hard_types(a.layer)
                         else "、「险路小径」可停可续（软终点）")
        print(f"  终点模式（{R.end_mode()}）：本层最后一步必须落在「{end_txt}」"
              f"（图上 {n_end} 个；「{_hard_txt}」碰到即结束{_soft_txt}，多个候选自动择优"
              + ("；玩家手报 " + str(RS.key_xy(RS.xy(a.final))) if a.final and not problems else "") + "）")
        if a.layer in RIGHT_RESERVE:
            print("  终点模式：三层起自动**整体求解**——终点约束是全路线性质，分阶段无法表达"
                  "（耗时明显增加，必要时调大 --max-time）")
    if _se_force_joint:
        print("  二结局：终点在左区（带徽章命运所指）→ **自动整体求解**"
              "（分阶段表达不了「先进右区、再回左区收尾」，必要时调大 --max-time）")
    if (a.layer in RIGHT_RESERVE and not a.joint and not end_type and has_right
            and not _se_force_joint):
        # 三/四/五层：**分阶段**——右区先按层固定预留，左区用剩下的
        try:
            res = solve_staged(sc, a.layer, lm, extra_cells, a.max_time)
        except SystemExit as exc:
            # 子场景（左/右分区）也可能被求解器拒收：同样走 ❌，不吐 traceback
            print("❌ 不可用：")
            print("   - 分阶段求解拒收分区子场景：" + str(exc))
            return 3
        segments = res["segments"]
        solver = _View(res)
        path, total, hit = solver.path, res["total"], res["hit"]
        cost_info = res.get("cost") or {}
        solution_status = "STAGED_BEST"
        warnings.append(
            "求解口径 = STAGED_BEST（分阶段：右区固定预留、左区余量不结转）——"
            "**不是全局最优**。跨区组合（左区少拿一点换更便宜的进区件/更好的终点）"
            "看不到；要最优加 --joint 重跑")
        print(f"  分阶段：右区预留 {RIGHT_RESERVE[a.layer]} 点（--joint 可改为整体求解）")
        # 2026-10-29 修复：右区进不去原来只打 ⚠️（route.py:271），不升级 ❌——
        # 现实中常被右区必走点撞出来，但 --no-must 或右区未探明（没有必走点）时
        # 会静默判 ✅。现在两条都进 problems：整体判 ❌ 并说明原因。
        if res.get("right_infeasible"):
            problems.append(
                "右区（熄日余径）无可行解：终点/必走约束落在右区但 AP 内到不了"
                "（补进区件，或核对右区必走点/命运所指坐标；--joint 可整体重估）")
        elif extra_cells and not res.get("right_entered"):
            problems.append(
                "右区（熄日余径）没进去：没有任何件能从左区终点够到右区格，"
                "固定预留的 %d 点行动力被浪费（补进区件，或 --joint 重估）"
                % RIGHT_RESERVE[a.layer])
    elif (a.layer == 2 and end_cells and not end_type
          and R.four_ending() is True):
        best = None
        # 每个候选各解一次。2026-11-02（玩家第 5 条意见）：
        # 旧写法 `per = max(5.0, max_time/n)` 有 **5 秒下限** —— `--max-time 3` + 2 个候选
        # 实际跑 10 秒，违反 `--max-time` 的**总预算**语义。现在改成共享 deadline：
        # 每个候选只拿「剩余墙钟 ÷ 剩余候选数」，用光就停（并如实标未解满）。
        _t0 = time.monotonic()
        _deadline = _t0 + max(1.0, float(a.max_time))
        _broke = False
        for _i, chosen in enumerate(end_cells):
            _remain = _deadline - time.monotonic()
            if _remain <= 0 and best is not None:
                _broke = True
                print(f"  ⚠️ --max-time {a.max_time:g}s 已耗尽：余下 {len(end_cells) - _i} 个尽头候选"
                      f"没解 → 本次是**未证明**的最优（加 --max-time 重跑才能比全）")
                break
            per = max(0.5, (_remain if _remain > 0 else 0.5) / max(1, len(end_cells) - _i))
            sc_try = json.loads(json.dumps(sc))
            for k in end_cells:
                sc_try["nodes"][k] = "险路尽头" if k == chosen else "空地"
            sc_try["must"] = [list(q) for q in must] + [list(RS.xy(chosen))]
            s_try = RS.Solver(sc_try)
            t_try, p_try, h_try = s_try.run(max_time=per, node_limit=80000000)
            if best is None or t_try > best[0]:
                best = (t_try, p_try, h_try, s_try, chosen)
        total, path, hit, solver, picked_end = best
        hit = bool(hit or _broke)
        # `CANDIDATE_BEST` 以前只写在 _status_txt 里、**从没被赋过值**（死分支）：
        # 二层多候选一旦解不完，实际报的是 JOINT_BEST，读者看不出「还有些候选根本没解」。
        if _broke:
            solution_status = "CANDIDATE_BEST"
        else:
            solution_status = "JOINT_BEST" if hit else "JOINT_PROVEN"
        if picked_end and RS.key_xy(RS.xy(picked_end)) not in {RS.key_xy(q) for q in must}:
            must.append(RS.xy(picked_end))   # 终点也是本层必走点（校验/输出都要认）
        print(f"  二层尽头只走一次：{len(end_cells)} 个候选里选 {picked_end}"
              f"（四结局=要 → 该尽头为必走；其余按 0 分处理）")
    elif a.layer == 2 and not end_type and R.four_ending() is True and not end_cells:
        problems.append("四结局=要：图上没有识别到险路尽头（OCR 漏检/构型不对？"
                        "先核对坐标；不要跳过四结局道具直接规划）")
        solver = RS.Solver(sc)
        total, path, hit = solver.run(max_time=a.max_time, node_limit=80000000)
    else:
        solver = RS.Solver(sc)
        # --explain：同一搜索顺带收集备选解（slack=3 = 低 3 分以内的解也入池，按落点集合去重）。
        # 不开 --explain 时参数不传，搜索行为与旧版完全一致。
        total, path, hit = solver.run(
            max_time=a.max_time, node_limit=80000000,
            **({"slack": 3.0, "max_alts": 8} if a.explain else {}))
        # hit=False 是「搜索已穷尽」（route_solver 的 cut_reason=None，上界可采纳）
        # → 在场景模型内就是**已证最优**；撞到时间/节点上限才降为 JOINT_BEST。
        solution_status = "JOINT_BEST" if hit else "JOINT_PROVEN"
        alts_ready = bool(a.explain)
    if segments is None:
        path = RS.resolve_items(path, list(solver.items))
    # 「本次没解出路线」的判据（2026-11-02）：total<0 是求解器的无解哨兵。
    # 一旦成立，下游那些**针对路线**的校验（必走点覆盖/回头步/右区必进/AP 结余）
    # 全都失去意义——空路线必然「漏必走点」，会把 ⏳ 未定硬拽成 ❌ 不可用。
    no_solution = total < 0
    # 三结局：先行一步藏着时，路线至少要走一个候选格才有机会探明它。
    # 不升级成 ❌（玩家可能本来就打算下一步再去找，或候选是 OCR 漏检），
    # 但必须**明说**——否则「15 分必走点」看起来像已经安排好了。
    if xianxing_cands and not no_solution:
        _cand_qs = {q for q, _d in xianxing_cands}
        if not any(q in _cand_qs for q, _it, _cv in path):
            notes.append("这条路线一格「先行一步」候选都没经过 → 拿不到三结局前置收藏品"
                         "（换路线/补跳跃件，或先在这些候选格里探明）")
    # 2026-10-29 修复：整体求解（--joint / 终点模式）也要检查「右区必进」——
    # 旧代码只在分阶段分支里查 right_entered，--joint --no-must 时一步不进右区照样 ✅
    # （三层实景实测：分阶段 ❌ 19.8 vs --joint ✅ 35.0）。
    if (segments is None and a.layer in RIGHT_RESERVE and extra_cells
            and not no_solution):
        if not any(q[0] > lm for q, _it, _cv in path):
            problems.append(
                "右区（熄日余径）没进去：整体求解同样要求三/四/五层进右区，"
                "这条路线一步没踩右区（补进区件，或确认右区是否真的不可达）")
    # 同分比价的状态（求解器两阶段：先定最大毛分，再在追平前提下压道具价）
    if segments is None:
        cost_info = getattr(solver, "last_cost", None) or {}
    # 2026-11-02 判词语义改造（玩家审阅第 1 条）：
    # **「预算内没找到解」≠「无解」，也 ≠「不可用」**。旧写法把「撞上限」和「无可行解」
    # 一起塞进 problems → 一律 `❌ 不可用`（exit 3）；于是一次 --max-time 给少了，
    # 工具就告诉玩家「这条路不存在」，而实际只是没搜完。现在按「搜索是否穷尽」分三档：
    #   有解 + 未穷尽 → ⚠️（FEASIBLE：当前最好，未证最优）
    #   无解 + 未穷尽 → ⏳ 未定（UNKNOWN：不能判定无解，加预算重跑）
    #   无解 + 已穷尽 → ❌（INFEASIBLE：已证无解）
    no_solution = total < 0
    if hit and not no_solution:
        warnings.append("结果**未证明最优**（求解口径 = %s）：这份是**当前最好**，"
                        "不是已证最优——要证明就加大 --max-time 重跑" % solution_status)

    # ── 6. 校验 ────────────────────────────────────────────────
    # 求解器用 total < 0 表示「无可行解」：设了 final/final_type 却走不到，或没有正收益路线
    if total < 0:
        if end_types:
            _why_none = (f"终点模式要求最后一步落在「{end_txt}」，但 AP 内走不到"
                         f"（或图上没有该类节点——确认恶敌/尽头/小径坐标，或用 --final c,r 手报）")
        elif a.layer == 2 and R.four_ending() is True:
            _why_none = ("四结局=要（尽头必走），但 AP 内走不到任何险路尽头"
                         "（核对构型/镜头坐标，或补跳跃件）")
        else:
            _why_none = "设了终点(final)但 AP 内走不到，或没有任何正收益路线"
        if hit:
            solution_status = "UNKNOWN"
            unknown.append(f"预算内没找到可行解，但搜索**没穷尽**（撞时间/节点上限）——"
                           f"不能据此判定无解：{_why_none}")
        else:
            solution_status = "INFEASIBLE"
            problems.append(f"无可行解（**已证**：搜索已穷尽）：{_why_none}")

    # grant 只在**第一次**踩到那格时结算（与求解器一致）；按每次经过累加会把预算算高
    granted_cells = {q for q, _, _ in path if q in solver.grants}
    granted = sum(solver.grants.get(q, 0) for q in granted_cells)
    # 免AP件那一步**不花行动力**，遥控器落地还 +N AP —— 预算必须按这两条算，
    # 否则「10 步 / 预算 9」会被误判成超支（2026-10-28 实测）。
    free_names = {it["name"] for it in solver.items if it.get("free")}
    bonus_of = {it["name"]: int(it.get("bonus", 0)) for it in solver.items}
    paid = sum(1 for _q, it, _g in path if not (it and it in free_names))
    item_bonus = sum(bonus_of.get(it, 0) for _q, it, _g in path if it)
    budget = a.ap + granted + item_bonus
    spent_txt = f"{paid}（免费 {len(path) - paid} 步）" if len(path) != paid else f"{paid}"
    # 步数超过预算 = 数据坏了；少于预算 = 图到头了（穷举解，停就是真的没得走）
    if paid > budget:
        problems.append(f"AP 超支：预算 {budget}（{a.ap}+{granted}"
                        + (f"+道具{item_bonus}" if item_bonus else "")
                        + f"），实走 {paid} 步（共 {len(path)} 步，含免AP）")
    elif paid < budget and not no_solution:
        if end_types and path and solver.nodes.get(path[-1][0]) in end_types:
            # 终点模式：提前落到终点就收尾，剩下的 AP 不是「无处可去」
            notes.append(f"终点已到（最后一步是「{solver.nodes.get(path[-1][0])}」），"
                         f"剩 {budget - paid} 点行动力未用")
        else:
            notes.append(f"剩 {budget - paid} 点无处可去（穷举解已停：死路或无正分节点）")

    # 回头步：判断是否「必要」——机械判据，不靠人看
    #   对第 i 步重踩的格 q，取它的前后两步 a=path[i-1]、b=path[i+1]：
    #   如果 a 与 b 之间有一条不经过 q 的 2 步路径（直接相邻，或共用一个第三格），
    #   那这次经过 q 就是可避免的；否则 q 是 a→b 的必经中转。
    #
    # ⚠️ 邻接必须用**删格后的图**（和求解器同一张）。以前这里直接拿原始 edges：
    # 三/四层命运所指、三层起空地、险路恶敌、险路尽头被 block 掉之后，
    # 校验仍把「绕经那面墙」当成一条真路 → 一条**不得不回头**的路线被判成
    # 「可避免的回头步」→ 整体 ❌（求解本身是对的，错在最后这道校验）。
    adj = _adj_after_block(edges, nodes, sc.get("block"), sc.get("block_except"))

    def avoidable(a, b, q):
        if b in adj[a]:
            return True
        for c in adj[a]:
            if c != q and b in adj[c]:
                return True
        return False

    # 兜底检查：路线里险路尽头的**不同格子**数不能超过 1（二层规则）
    # 2026-10-30：四结局=不要 时尽头价值 0、不再必走，允许当 0 分中继（不再报这条）。
    ends_hit = {q for q, _it, _cv in path if solver.nodes.get(q) == "险路尽头"}
    if (a.layer == 2 and len(ends_hit) > 1
            and R.layer2_jintou_value() > 0):
        problems.append(f"踩了 {len(ends_hit)} 个险路尽头 {sorted(ends_hit)}"
                        "——二层只走一次，其余没有战略价值")

    # 终点模式（险路尽头）机械校验：最后一步必须是**候选之一**，且之前不进硬终点
    # （险路小径是软终点：可停可续，所以「之前进过小径」不算违规）。
    # 求解器「碰到该类节点即收解停止」在结构上已保证，这里独立核一遍（别只信求解器自报）。
    if end_types and path:
        last_q = path[-1][0]
        picked_end = last_q
        last_t = solver.nodes.get(last_q)
        if last_t not in end_types:
            problems.append(f"终点模式校验失败：最后一步落在 ({last_q[0]},{last_q[1]})"
                            f"「{last_t or '?'}」，不是「{end_txt}」之一")
        hard_t = tuple(t for t in (sc.get("hard_final_types") or R.ending_hard_types(a.layer))
                       if t not in set(sc.get("final_soft_types") or ()))
        early = sorted({q for q, _it, _cv in path[:-1] if solver.nodes.get(q) in hard_t})
        if early:
            problems.append(f"终点模式校验失败：最后一步之前进入了「{'/'.join(hard_t)}」{early}"
                            "——碰到就该结束，这条路线不该存在")

    # 必走点覆盖：求解器把它当硬约束，这里再独立核一遍（别只信求解器自报）
    must_hit = {q for q, _it, _cv in path}
    must_miss = [q for q in must if q not in must_hit]
    if must and must_miss and not no_solution:
        problems.append(
            "**必走点走不到**：" + "、".join(f"({q[0]},{q[1]})={nodes.get(RS.key_xy(q), '?')}"
                                        for q in must_miss)
            + "。已按「无约束」重解（这条路线漏了必走点）——要么补跳跃件/加 AP，"
              "要么确认这些点是否真的必走（可用 scenario 的 must 字段手工收窄）")

    seen = {tuple(start)} if start else set()
    backs = []
    for i, (q, _it, _cv) in enumerate(path):
        if q in seen and 0 < i < len(path) - 1:
            a_, b_ = path[i - 1][0], path[i + 1][0]
            backs.append((i + 1, q, avoidable(a_, b_, q)))
        seen.add(q)
    waste = [x for x in backs if x[2]]
    if waste and not no_solution:
        problems.append("存在**可避免的**回头步：" +
                        "、".join(f"第{s}步({q[0]},{q[1]})" for s, q, _ in waste))

    # ── 6.6 输入可信度（2026-11-02 玩家审阅第 3 条）──────────────
    # 四类词汇，只标**会改变结论**的输入，不逐条标注全文（标注疲劳之后没人看）：
    #   事实   = 玩家口报 / 参数显式给出
    #   推断   = 由定稿表或机械规则推出（构型表、格点宽度、距离带、配额）
    #   待确认 = 图像识别结果（OCR 标签、小人起点、像素徽章）
    #   期望   = 经济模型估值（价表）
    # 铁律：`已证最优`（JOINT_PROVEN）只在**本场景模型内**成立。输入里只要有「待确认」项，
    # 这句证明就**不覆盖**读错的可能性——所以输入状态必须与判词同场打印，一起转达。
    inputs = []
    _start_player = bool(explicit_start) and getattr(a, "start_origin", "player") == "player"
    # 滚动重算时起点=玩家当前所在格，与构型表的进层起点本来就不同，不能算「未背书」
    _start_weak = (not _start_player) and (not a.visited) and (
        start_warned or (start is not None and cfg_start is not None
                         and tuple(start) != tuple(cfg_start)))
    if _start_player:
        inputs.append(("起点", "事实", "玩家口报"))
    elif _start_weak:
        inputs.append(("起点", "待确认", "小人检测 + 多候选按序取"))
    else:
        inputs.append(("起点", "推断", "小人检测 + 构型表背书"))
    inputs.append(("层数", "事实", "--layer"))
    inputs.append(("AP", "事实", "玩家/观测读数"))
    if a.items:
        inputs.append(("库存", "事实", "--items"))
    else:
        # 没传 --items 时求解器按**空库存**算（不能跳），这是**假设**不是事实。
        # 以前一律记「事实」，会让读判词的人以为「本局确实没有加工品」。
        inputs.append(("库存", "推断", "未传 --items：按无道具算"))
    inputs.append(("构型", "推断", "起点 + 地标查表"))
    _lab = label_input(bool(getattr(a, "labels_confirmed", False)), len(grid))
    if _lab:
        inputs.append(_lab)
    if forced:
        inputs.append(("定点节点", "事实", "构型表"))
    if a.layer in (3, 4, 5):
        _rz_player = a.right_zone != "自动"
        inputs.append(("右区有无", "事实" if _rz_player else "推断",
                       "玩家" if _rz_player else "格点宽度"))
        if has_right and _rz_meta.get("topology") in ("ambiguous", "none"):
            inputs.append(("右区拓扑", "待确认", "4 邻接假设"))
        if has_right and _rz_meta.get("dropped"):
            # 空地位置是从形状反推的（没报 --right-dots）：不是事实，也不是 OCR。
            inputs.append(("右区空地", "推断",
                           "%d 条边的端点靠形状反推（未报 --right-dots）"
                           % len(_rz_meta["dropped"])))
    if a.root:
        inputs.append(("理想源", "事实", "玩家口报"))
    if a.badge_none:
        inputs.append(("徽章", "事实", "玩家口报「本局无」"))
    elif acorn_cells or fate_badge:
        inputs.append(("徽章", "待确认",
                       "像素检测 %d 处" % (len(acorn_cells) + len(fate_badge))))
    if a.final:
        inputs.append(("终点", "事实", "--final 手报"))
    inputs.append(("价表", "期望", "rules.LAYER_PRICES"))

    _lv = ("事实", "推断", "待确认", "期望")
    _cnt = {l: sum(1 for _n, _x, _d in inputs if _x == l) for l in _lv}
    _pend = [f"{_n}（{_d}）" for _n, _x, _d in inputs if _x == "待确认"]
    if _start_weak:
        warnings.append("输入可信度：起点是**小人检测 + 多候选按序取**（构型表没背书）——"
                        "整条路线的分数都以这个假设为前提；玩家口报 --start 即可消除")

    # ── 7. 判词 ────────────────────────────────────────────────
    # 求解口径的**人话解释**（玩家第 3 条意见：分阶段 ≠ 全局最优，必须写明）
    _status_txt = {
        "STAGED_BEST": "分阶段（右区固定预留、左区余量不结转）——**不是全局最优**",
        "CANDIDATE_BEST": "二层多候选择优——余下候选没解满，未证明",
        "JOINT_BEST": "整体求解，但撞到时间/节点上限——**未证明最优**",
        "JOINT_PROVEN": "整体求解且搜索已穷尽 = 全局最优（在本场景模型内）",
        "INFEASIBLE": "搜索已穷尽仍无可行解 = **已证无解**",
        "UNKNOWN": "搜索没穷尽就停了 = **不能判定**（加 --max-time 重跑）",
    }.get(solution_status, solution_status)
    # 同一条口径的**数学状态**（审阅第 2 条：搜索状态 ≠ 数学上能否证明最优）
    _state_en = {
        "JOINT_PROVEN": "OPTIMAL", "JOINT_BEST": "FEASIBLE",
        "STAGED_BEST": "FEASIBLE", "CANDIDATE_BEST": "FEASIBLE",
        "INFEASIBLE": "INFEASIBLE", "UNKNOWN": "UNKNOWN",
    }.get(solution_status, "?")
    print("== 判定 ==")
    print(f"  模　　式：{R.mode_label()}"
          + ("（理想源不保证出现；未报 --root 即按无，打了也不 +2AP）"
             if R.is_normal_squad() else "（通过追猎进入二~六层时大概率出现理想源；--root 必传，推荐搭配全程追猎）"))
    print(f"  求解口径：{solution_status} = {_state_en}（{_status_txt}）")
    # 输入可信度紧跟在求解口径之后：**证明的边界**和**结论的强度**必须同场出现，
    # 否则「已证最优」会被读成「真实地图上也已证最优」。
    print("  输　　入：" + "｜".join(f"{l} {_cnt[l]}" for l in _lv if _cnt[l]))
    if _pend:
        print("  　　　　：" + "、".join(_pend)
              + " —— 这些是**识别/估计**，不是事实；「已证最优」只在本场景模型内成立")
    print(f"  终点模式：{R.end_mode()}"
          + (f"（本层最后一步 = {end_txt}"
             + (f" → {picked_end}" if picked_end is not None else "") + "）"
             if end_types else "（尽头/恶敌照旧禁踩，层在行动力耗尽处自然结束）"))
    if a.layer == 2:
        print(f"  四结局　：{R.layer2_plan_text(2)}")
    if a.layer == 3 and not has_right:
        print(f"  三结局　：{R.layer3_plan_text(3)}")
    if a.layer == 5 and se.get("marker"):
        print(f"  二结局　：{R.layer5_plan_text(5)}")
    if acorn_cells:
        _hits = getattr(solver, "acorn_hits", None)
        print(f"  迷　藏　：{len(acorn_cells)} 个橡果节点（每个 +{R.acorn_value():g} 分，"
              f"已并入所在节点收益）"
              + (f"，本条路线经过 {_hits} 个" if _hits is not None else ""))
    print(f"  起　　点：{start}")
    print(f"  构　　型：{a.config}（边 {len(edges)} 条，格 {len(cells)}）")
    if a.layer in (3, 4, 5):
        print(f"  右　　区：{'有（熄日余径）' if has_right else '无（本层无熄日余径；不预留 AP）'}")
    print(f"  格点校验：{'✅' if not outside else '❌'}")
    print(f"  定点覆盖：{len(forced)} 处" + ("（详见上方）" if forced else ""))
    if must:
        print(f"  必走覆盖：{len(must) - len(must_miss)}/{len(must)}"
              + ("  ✅" if not must_miss else "  ❌ " + "、".join(
                  f"({q[0]},{q[1]})" for q in must_miss)))
    print(f"  grants ：{grants if grants else '无'}")
    # 2026-10-29 修：明细必须用**本段自己的 nodes/grants**（分阶段时 _View.nodes 是左右区
    # 合并的，格名会串）；同时起点格（玩家报 --visited 时已被登记成「起点」）也要列出来——
    # 旧实现只看「路线经过的格」，起点格的 grant 就被静默吞掉，
    # 于是「5 + 树根2 + 羽瞰点1 = 7」被打印成「5 + 羽瞰点1 = 7」，数字和明细对不上。
    try:
        st_key = RS.key_xy(RS.xy(sc["start"])) if sc.get("start") else None
    except Exception:
        st_key = None
    hit_keys = {RS.key_xy(q) for q, _it, _cv in path}
    parts = [f"基础 {a.ap}"]
    for k, v in sorted(grants.items()):
        if k in hit_keys or k == st_key:
            parts.append(f"{nodes.get(k, '?')} {v}")
    if item_bonus:
        parts.append(f"道具 +{item_bonus}")
    print(f"  AP 预算：{' + '.join(parts)} = {budget}，实走 {paid} 步")
    if R.is_normal_squad():
        # 普通分队：理想源不保证有、不回复 AP → 「未报」不是错误，不打印 ⚠️
        pass
    elif a.visited:
        # 滚动重算：树根可能**已经打掉**（玩家当前就站在它上面），
        # 此时 grants 里没有它是正确的，不能报「未计入理想源」。
        pass
    elif ("树根" not in {nodes.get(k) for k in grants}
          and a.layer in (2, 3, 4, 5, 6)):
        # 2026-10-29 修复：原来只提示 3/4/5——二层漏报少 15 分、六层漏报少 2 步
        # （追猎 4→6 / 险路尽头 5→7），都没有警告。现在二~六层（N16文明开化）统一提示。
        print("  　　　　：⚠️ 未计入理想源。**本层若有理想源，必须传 --root c,r**"
              "（它的 +2 不会自己出现；二层漏了少 15 分、六层漏了少 2 步。"
              "N16文明开化下理想源是「追猎进入时大概率出现」，不是必定——真没有就照常求解，"
              "这条警告只提醒别漏报）")
    for n in notes:
        print(f"  　　　　：{n}")
    # 2026-11-02（玩家第 1 条意见）：风险与「备注」分开列——风险会**降级最终判词**。
    for w in warnings:
        print(f"  风　　险：{w}")
    print()
    print("== 路线 ==")
    print(solver.fmt(path))
    print(f"总计价值 {total:.1f}，走了 {len(path)} 步" + (f"（其中免AP {len(path) - paid} 步）"
                                                    if len(path) != paid else ""))
    # 同分更省：把「烧了多少钱」和「最省是否已证明」写进判词。
    # 这条是 2026-10-28 玩家明确要求的：毛分相同时必须优先省道具，且不能悄悄给一条贵的。
    # 0 < cost < 哨兵：无解时求解器把 cost 留在 1<<30（旧代码会打印「道具价 1073741824」）
    if 0 < cost_info.get("cost", 0) < (1 << 29):
        used = [(q, it) for q, it, _g in path if it]
        detail = "、".join(f"{it}({solver.cost_of.get(it, 2)})" for _q, it in used)
        if cost_info.get("cost_proven"):
            print(f"道具价 {cost_info['cost']}（{detail}）｜同分更省的已搜完，**这就是最省**")
        elif cost_info.get("tie_phase"):
            print(f"道具价 {cost_info['cost']}（{detail}）｜⚠️ 同分更省的搜索被预算截断，"
                  "**可能还有更省的同分解**（要证明就加大 --max-time）")
        else:
            print(f"道具价 {cost_info['cost']}（{detail}）｜未做同分比价"
                  "（第一阶段用完了预算；加大 --max-time 才会比价）")
    if backs:
        print("↺ 回头步（已机械判定）：")
        for s, q, av in backs:
            print(f"   第{s}步 ({q[0]},{q[1]})   {'❌ 可避免（有绕开的路）' if av else '✅ 必要（唯一中转）'}")
    print()

    # ── 7.5 转述文案（--explain）────────────────────────────────
    # 放在判词行**之前**：保持「最后一行 = ✅/❌」的输出契约不变。
    # 只在 ✅（problems 为空）且有路线时产出；❌ 时执行方转述 problems 原文即可。
    if a.explain and not problems and path:
        print("== 转述文案（--explain 生成；执行方逐字转达，禁止改写或补充规则）==")
        _plan3 = (R.layer3_plan_text(3) if a.layer == 3 and not has_right else "")
        _plan5 = (R.layer5_plan_text(5) if a.layer == 5 and se.get("marker") else "")
        # 口径说明已随 warnings 进【风险】（STAGED_BEST 一定有那条），这里不再重复。
        _exp_notes = list(notes) + list(warnings)
        # 输入可信度也必须进【风险】：否则 ✅ 的末行写着「N 项输入待确认」，
        # 而转述文案却说「工具校验全过，无额外风险」——两边自相矛盾（2026-11-02）。
        if _pend:
            _exp_notes.append("输入状态：%d 项待确认（%s）——「已证最优」只在本场景模型内成立，"
                              "不覆盖读错的风险" % (len(_pend), "、".join(_pend)))
        for ln in _explain_lines(solver, path, total, a, _exp_notes, end_txt,
                                 budget, paid, alts_ready, _plan3, _plan5):
            print(ln)
        print()

    # 起点表写入（**推迟到这里**，2026-10-28 审计）：条件 = 起点落在该构型格内
    # 且是无标签格（与旧判据一致），且 `_should_learn_start` 放行。
    # 写入失败/被跳过都不影响本局求解，只是下次同一构型还得靠图像定位。
    if a.no_learn_start:
        print("  （--no-learn-start：本次不写入起点表）")
    elif a.visited:
        # 滚动重算：`start` 是**玩家当前所在格**，不是进层起点。
        # 三/四/五层构型若没声明起点，会被这里当成新起点写进 起点表.json，污染后续识别。
        print("  （滚动重算：起点是当前所在格，不写入起点表）")
    elif start and edges and BS.config_start(a.layer, a.config) is None:
        cells_all = {p for e in edges for p in e}
        if tuple(start) in cells_all and tuple(start) not in grid:
            if _should_learn_start(problems, n_pre_solve, explicit_start, start_warned):
                BS.learn_start(a.layer, a.config, start)
            else:
                why = ("起点是猜的（判据打了 ⚠️）" if start_warned
                       else "本次校验未全过")
                print(f"  （起点满足「格内+无标签」，但{why} → 不写入起点表，"
                      "下次通过后再记）")

    line, code = verdict(problems, unknown, warnings, _pend, backs)
    if code == 3:
        print("❌ 不可用：")
        for p in problems:
            print("   - " + p)
        return 3
    if code == 4:
        # 2026-11-02 第 4 态：预算内没解 ≠ 无解。这一态**禁止**被说成「没有路线」，
        # 也禁止被说成「可用」——正确转述是「没搜完，判不了」。
        print(line + "：")
        for u in unknown:
            print("   - " + u)
        print("   · 加 --max-time（或 --joint / 放宽约束）重跑才能判；"
              "在此之前不许当成「❌ 无解」，也不许自己补一条路线")
        return 4
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
