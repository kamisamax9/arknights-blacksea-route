#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_solver.py — 求解器行为自检：专门验证「曾经修过的 bug」没有被改回去

为什么需要
----------
2026-10-27：有会话对求解器做性能实验，撤回了 4 个改动。它靠「跑一遍回归看分数」判断，
但**分数不变不代表修复还在**——有些修复只在特定输入下才显形：

- 道具等价类如果丢掉「价格」维度，报废假肢(2)和标准引擎(4)会被当成同一件，
  结果出现「同一件用 5 次」——在小场景里分数可能碰巧不变
- 免行动力道具如果被当成普通道具，AP 收支会虚高 1 点
- 授予格如果被重复计数，羽瞰点能反复刷 +1 AP

这些都是**局部逻辑**，用分数回归很容易漏。所以这里直接断言行为。

用法
----
  python tools/selftest_solver.py          # 全部跑一遍
  python tools/selftest_solver.py -v       # 显示每项细节

退出码：0 = 全过；1 = 有失败
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import route_solver as RS      # noqa: E402


def base_sc(**kw):
    sc = {
        "name": "selftest",
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
        "nodes": {"0,0": "起点", "1,0": "诡秘", "2,0": "作战"},
        "start": [0, 0], "ap": 3, "grants": {}, "items": [],
        "values": {"起点": 0, "诡秘": 5, "作战": 3, "空地": 0},
    }
    sc.update(kw)
    return sc


def t_final_must_be_last():
    """设了 final 就必须**停在它上面**（不是「非终点不记分」）。"""
    sc = {
        "name": "final-last",
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
        "nodes": {"0,0": "起点", "1,0": "作战", "2,0": "命运所指"},
        "start": [0, 0], "ap": 3, "grants": {}, "items": [],
        "values": {"起点": 0, "作战": 3, "命运所指": 0, "空地": 0},
        "final": [2, 0],
    }
    s = RS.Solver(sc)
    total, path, hit = s.run()
    last = path[-1][0] if path else None
    return last == (2, 0), f"路线最后一格是 {last}，应为 (2,0)"


def t_auto_grant_not_farmable():
    """节点是羽瞰点但 grants 没配 → 求解器自动补 +1，且**不能反复刷**。"""
    sc = {
        "name": "auto-grant",
        "edges": [[[0, 0], [1, 0]]],
        "nodes": {"0,0": "起点", "1,0": "羽瞰点"},
        "start": [0, 0], "ap": 2, "grants": {}, "items": [],
        "values": {"起点": 0, "羽瞰点": 5, "空地": 0},
    }
    s = RS.Solver(sc)
    total, path, hit = s.run()
    got = s.grants.get((1, 0))
    return got == 1 and len(path) <= 3, \
        f"自动补 AP={got}（应 1），路线 {len(path)} 步（上限 3，超过=在刷 AP）"


def t_free_item_no_ap():
    """免行动力道具（喷气背包）那一步不扣 AP：1 点 AP 也要够用。"""
    sc = {
        "name": "free-item",
        "edges": [],                       # 没有可走的路，只能靠道具跳
        "nodes": {"0,0": "起点", "2,0": "作战"},
        "start": [0, 0], "ap": 1, "grants": {}, "items": [
            {"name": "一次性喷气背包", "cov": "any", "uses": 1, "free": True}],
        "values": {"起点": 0, "作战": 3, "空地": 0},
    }
    s = RS.Solver(sc)
    total, path, hit = s.run()
    free_used = any(it == "一次性喷气背包" for _, it, _ in path)
    return free_used and total >= 3, f"免AP道具是否用上={free_used}，总分={total}（AP=1，免AP应够）"


def t_grant_once():
    """授予格只能算一次：反复经过不能反复 +AP。"""
    sc = {
        "name": "grant-once",
        # 只有一条来回的边；羽瞰点有分值，求解器会想反复踩它
        "edges": [[[0, 0], [1, 0]]],
        "nodes": {"0,0": "起点", "1,0": "羽瞰点"},
        "start": [0, 0], "ap": 2, "grants": {"1,0": 1}, "items": [],
        "values": {"起点": 0, "羽瞰点": 5, "空地": 0},
    }
    s = RS.Solver(sc)
    total, path, hit = s.run()
    # 若能反复刷 +1 AP，步数会失控（>3）；正常应 ≤ 2+1
    return len(path) <= 3, f"羽瞰点(+1AP) 让路线走了 {len(path)} 步（AP=2，上限应 3，超过=在刷AP）"


def t_free_after_ap_exhausted():
    """花光 AP 后仍可用免AP道具再拿一格（位置相依的假肢）。"""
    sc = {
        "name": "free-after-walk",
        "edges": [[[0, 0], [1, 1]]],
        "nodes": {"0,0": "起点", "1,1": "作战", "2,2": "诡秘"},
        "start": [0, 0], "ap": 1, "grants": {},
        "items": [{"name": "报废假肢", "cov": "adj8", "uses": 1, "free": True}],
        "values": {"起点": 0, "作战": 5, "诡秘": 10, "空地": 0},
    }
    s = RS.Solver(sc)
    total, path, _h = s.run()
    return total == 15, f"总分 {total}（应 15 = 走(1,1)拿5 + 假肢跳(2,2)拿10）"


def t_empty_items_no_crash():
    """没有任何道具时不能崩（_xs/_ys、expanded 一度被缩进到循环里）。"""
    sc = base_sc()
    s = RS.Solver(sc)
    total, _p, _h = s.run()
    return total == 8, f"总分 {total}（应 8，且不能抛异常）"


def t_negative_value_guard():
    """负价值节点不能作为终点被凑数。"""
    sc = base_sc(values={"起点": 0, "诡秘": -5, "作战": -99, "空地": 0})
    s = RS.Solver(sc)
    total, path, hit = s.run()
    return total == 0 or all(s.vals.get(s.nodes[q], 0) >= 0 for q, _, _ in path), \
        f"负价值节点进了路线：{[q for q, _, _ in path]}"


# ---------------------------------------------------------------------------
# 2026-10-28 审计（入口流水线）：上面这些用例手写着 cov / free，
# 所以「入口生成的 item 缺字段」这类 bug 一直测不出来。以下四项专测流水线。
# ---------------------------------------------------------------------------

def t_portal_string_coords():
    """portals 用文档格式 `[["1,0","3,0"]]` 时必须真的传送。

    审计 bug 1：`tuple("1,0")` = `('1', ',', '0')`，portal 字典永远匹配不上，
    传送静默失效（而文档、注释、示例写的都是字符串格式）。
    """
    sc = {
        "name": "portal-string",
        "edges": [[[0, 0], [1, 0]]],
        "nodes": {"0,0": "起点", "1,0": "曲折密道", "3,0": "诡秘"},
        "portals": [["1,0", "3,0"]],
        "start": [0, 0], "ap": 2, "grants": {}, "items": [],
        "values": {"起点": 0, "曲折密道": -99, "诡秘": 10, "空地": 0},
    }
    s = RS.Solver(sc)
    _t, path, _h = s.run()
    landed = [q for q, _, _ in path]
    return (3, 0) in landed and (1, 0) not in landed, \
        f"落点 {landed}（应出现 (3,0) 且不出现 (1,0)=传送两端）"


def t_item_adj12_vs_adj8():
    """鸣笛 = 周围 12 格（曼哈顿距离 ≤2），标准引擎 = 相邻 8 格。

    审计 bug 2：route.py 的 ITEM_COV 把鸣笛写成 adj8，曼哈顿 2 格的目标跳不到。
    这里用**名字**造道具（不写 cov），直接测 rules.py → solver 的链路。
    """
    cells = {(0, 0): "起点", (2, 0): "作战", (2, 1): "作战"}
    sc = {
        "name": "cov-adj12",
        "edges": [],                     # 没有路，只能靠道具
        "nodes": {f"{x},{y}": t for (x, y), t in cells.items()},
        "start": [0, 0], "ap": 1, "grants": {},
        "items": [{"name": "鸣笛", "uses": 1}],
        "values": {"起点": 0, "作战": 7, "空地": 0},
    }
    s = RS.Solver(sc)
    _t, path, _h = s.run()
    far = any(q == (2, 0) for q, _it, _g in path)
    # 同样位置换标准引擎（相邻 8 格）应当够不到 (2,0)/(2,1)
    sc2 = dict(sc, items=[{"name": "标准引擎", "uses": 1}], ap=1)
    _t2, path2, _h2 = RS.Solver(sc2).run()
    near = any(q[0] >= 2 for q, _i, _g in path2)
    return far and not near, f"鸣笛到 (2,x)={far}（应 True），标准引擎到 (2,x)={near}（应 False）"


def t_pipeline_item_fields():
    """入口造出来的 item 必须**直接可解**：带 cov，且免AP件带 free。

    审计 bug 3/4：route.py / build_scenario.py 曾只写 {"name","uses"}，
    结果缺 cov → 求解器 KeyError；缺 free → 真跑时白扣 1 点行动力。
    """
    import rules as R
    ent = R.make_item("一次性喷气背包", 1)
    ok_fields = ent.get("cov") == "any" and ent.get("free") is True
    sc = {
        "name": "pipeline-free",
        "edges": [], "nodes": {"0,0": "起点", "2,0": "作战"},
        "start": [0, 0], "ap": 0, "grants": {}, "items": [ent],
        "values": {"起点": 0, "作战": 5, "空地": 0},
    }
    s = RS.Solver(sc)                     # 缺 cov 会在这里 SystemExit
    total, path, _h = s.run()
    used = any(it == "一次性喷气背包" for _q, it, _g in path)
    return ok_fields and used and total == 5, \
        f"字段 {ent}；AP=0 时免AP件用上={used}，总分={total}（应 5）"


def t_two_free_items_at_zero_ap():
    """AP=0 且身上有两件免AP件 → 两格都能拿到（上界必须按**次数**放宽）。

    这是 bug 3 的连带问题：原上界只加「布尔 1」，两件免AP件时界不够，最优解被剪。
    """
    sc = {
        "name": "two-free",
        "edges": [],
        "nodes": {"0,0": "起点", "1,0": "作战", "4,4": "诡秘"},
        "start": [0, 0], "ap": 0, "grants": {},
        "items": [{"name": "一次性喷气背包", "uses": 1},
                  {"name": "重弹簧", "uses": 1}],
        "values": {"起点": 0, "作战": 5, "诡秘": 9, "空地": 0},
    }
    total, path, _h = RS.Solver(sc).run()
    return total == 14 and len(path) == 2, \
        f"总分 {total}（应 14 = 5+9），步数 {len(path)}（应 2，AP=0 全靠免AP件）"


def t_no_walk_without_ap():
    """没有行动力就**不能徒步**（只有免AP件能动）。

    放宽上界时如果把「徒步」也放进放宽额度里，AP 会被走到负数。
    """
    sc = {
        "name": "no-walk-ap0",
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
        "nodes": {"0,0": "起点", "1,0": "诡秘", "2,0": "诡秘"},
        "start": [0, 0], "ap": 0, "grants": {}, "items": [],
        "values": {"起点": 0, "诡秘": 5, "空地": 0},
    }
    total, path, _h = RS.Solver(sc).run()
    return total == 0 and len(path) == 0, f"总分 {total}、步数 {len(path)}（AP=0 时都应为 0）"


def t_unknown_item_errors():
    """认不出来的道具要**报错**，不能静默当 0 分/2 价混过去。"""
    sc = base_sc(items=[{"name": "杜林人的小推车", "uses": 1}])
    try:
        RS.Solver(sc)
    except SystemExit as exc:
        return "认不出来" in str(exc), f"报错信息：{exc}"
    except Exception as exc:                       # noqa: BLE001
        return False, f"抛了非 SystemExit：{exc!r}"
    return False, "居然没报错"


def t_tie_cheaper_items_kept():
    """**同分但更省道具**的路径必须被保留（2026-10-28 玩家明确要求）。

    构造：两条都得 10 分
      · 徒步链 (0,0)→(1,0)→(2,0)：0 元，但中间态的「上界 == 最优」会被剪枝挡住
      · 原理链 (0,0)→(5,5) 一步拿 10：12 元，先被发现、先占住 best
    旧行为（`_ub <= best` 就剪）只会拿到 12 元那条；
    新行为（两阶段：先定最大分，再在追平前提下压价）应给出 **0 元**那条。
    """
    sc = {
        "name": "tie-cheaper",
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
        "nodes": {"0,0": "起点", "1,0": "诡秘", "2,0": "诡秘", "5,5": "诡秘"},
        "start": [0, 0], "ap": 2, "grants": {},
        "items": [{"name": "结构性原理", "uses": 1}],
        "values": {"起点": 0, "诡秘": 5, "空地": 0},
    }
    s = RS.Solver(sc)
    total, path, _h = s.run()
    cost = s.last_cost["cost"]
    names = [it for _q, it, _g in path if it]
    ok = total == 10 and cost == 0 and not names
    return ok, (f"总分 {total}（应 10），道具价 {cost}（应 0），用到 {names}"
                f"，已证明最省={s.last_cost['cost_proven']}")


def t_cost_reported_matches_path():
    """`last_cost` 报的道具价必须与路径里实际用掉的道具一致（账目自洽）。"""
    sc = {
        "name": "cost-accounting",
        "edges": [],
        "nodes": {"0,0": "起点", "2,0": "诡秘"},
        "start": [0, 0], "ap": 1, "grants": {},
        "items": [{"name": "报废轮子", "uses": 1},
                  {"name": "标准引擎", "uses": 1}],
        "values": {"起点": 0, "诡秘": 16, "空地": 0},
    }
    s = RS.Solver(sc)
    total, path, _h = s.run()
    from_path = sum(s.cost_of.get(it, 2) for _q, it, _g in path if it)
    return total == 16 and from_path == s.last_cost["cost"], \
        f"路径代价 {from_path} vs last_cost {s.last_cost['cost']}，总分 {total}"


# ---------------------------------------------------------------------------
# 2026-10-28 第二轮：右区定价 / 六层入口
# ---------------------------------------------------------------------------

def t_right_zone_pricing():
    """右区必须**改名并按右区口径定价**（右区作战 16、右区紧急 21、右区诡秘 1.3）。

    旧行为：三/四层右区不改名 → 右区普通作战按左区 0 分算（等于白打 16 分）；
    五层只改了凶戾/诡秘，右区作战仍是左区 0 分。
    """
    import rules as R
    l3, l5 = R.LAYER_PRICES[3], R.LAYER_PRICES[5]
    ok = (R.right_zone_type("作战") == "右区作战"
          and R.right_zone_type("未知的诡秘") == "右区诡秘"
          and l3.get("右区作战") == 16 and l3.get("右区紧急作战") == 21
          # 2026-10-28 玩家口径：右区诡秘三/四/五层统一 8
          and l3.get("右区诡秘") == 8 and l5.get("右区诡秘") == 8
          # 2026-10-28 玩家口径：三/四层右区的不期/得偿/安全与左区同价 9/8/3
          and l3.get("右区不期而遇") == 9 and l3.get("右区得偿所愿") == 8
          and l3.get("右区安全的角落") == 3
          # 三/四层命运所指是墙；五层是最后一步
          and l3.get("命运所指") == -99 and R.LAYER_PRICES[4].get("命运所指") == -99
          and l5.get("命运所指") == 0)
    total = sum(l3[k] for k in ("右区紧急作战", "右区作战", "右区不期而遇",
                                "右区得偿所愿", "右区安全的角落"))
    return ok, (f"右区作战 {l3.get('右区作战')} / 紧急 {l3.get('右区紧急作战')} / "
                f"不期 {l3.get('右区不期而遇')} 得偿 {l3.get('右区得偿所愿')} "
                f"安全 {l3.get('右区安全的角落')}（满扫应 57，实 {total}）/ "
                f"诡秘 3-4 {l3.get('右区诡秘')} 5层 {l5.get('右区诡秘')}（应都 8）")


def t_layer6_config():
    """六层：单构型 15 边 / 16 格、起点 (2,0)、5 个定点、道具强制 1 次。"""
    import build_scenario as BS
    import apply_config as AC
    import rules as R
    edges = BS.config_edges(6, "唯一")
    cells = {p for e in edges for p in e}
    start = BS.config_start(6, "唯一")
    ends, fixed = AC.config_fixed(6, "唯一")
    # 2026-10-29 玩家裁定：六层诡意行商 = 12（必去）；旧值 4 是漂移
    ok = (len(edges) == 15 and len(cells) == 16 and start == (2, 0)
          and len(fixed) == 5 and R.clamp_uses(6, 3) == 1 and R.clamp_uses(3, 3) == 3
          and R.LAYER_PRICES[6].get("诡意行商") == 12)
    return ok, (f"边 {len(edges)}（应 15）格 {len(cells)}（应 16）起点 {start}（应 (2,0)）"
                f"定点 {len(fixed)}（应 5）六层引擎次数 {R.clamp_uses(6, 3)}（应 1）"
                f"六层行商价 {R.LAYER_PRICES[6].get('诡意行商')}（应 12）")


# ---------------------------------------------------------------------------
# 必走节点（2026-10-28 玩家口径）
# ---------------------------------------------------------------------------

def t_must_is_hard_constraint():
    """必走点是**硬约束**：分数更高的路线若漏点，也不算解。

    构造：走 A 链拿 12 分（漏掉必走点 (5,5)）；走 B 链拿 5 分但踩到 (5,5)。
    """
    sc = {
        "name": "must-hard",
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
        "nodes": {"0,0": "起点", "1,0": "诡秘", "2,0": "诡秘", "5,5": "树根"},
        "start": [0, 0], "ap": 2, "grants": {"5,5": 2}, "items": [],
        "must": [[5, 5]], "items2": None,
        "values": {"起点": 0, "诡秘": 6, "树根": 5, "空地": 0},
    }
    sc.pop("items2")
    sc["items"] = [{"name": "结构性原理", "uses": 1}]
    s = RS.Solver(sc)
    tot, path, _h = s.run(max_time=10)
    hit_must = (5, 5) in {q for q, _i, _g in path}
    return hit_must and not s.must_dropped, \
        f"总分 {tot}（5 分那条才对），踩到必走点={hit_must}，回落={s.must_dropped}"


def t_must_infeasible_falls_back():
    """必走点走不到 → 回落成无约束解，**但必须报告**（不许假装满足规则）。"""
    sc = {
        "name": "must-infeasible",
        "edges": [[[0, 0], [1, 0]]],
        "nodes": {"0,0": "起点", "1,0": "诡秘", "9,9": "树根"},
        "start": [0, 0], "ap": 1, "grants": {}, "items": [],
        "must": [[9, 9]],
        "values": {"起点": 0, "诡秘": 6, "树根": 5, "空地": 0},
    }
    s = RS.Solver(sc)
    tot, path, _h = s.run(max_time=10)
    return (tot == 6 and s.must_dropped and s.must_uncovered == ((9, 9),)), \
        (f"总分 {tot}（应 6，回落解）回落={s.must_dropped} 未覆盖={s.must_uncovered}")


def t_must_bad_coord_errors():
    """must 里写了不在图上的坐标 → 报错（否则会变成「永远无解」的假象）。"""
    sc = {
        "name": "must-bad", "edges": [], "nodes": {"0,0": "起点"},
        "start": [0, 0], "ap": 1, "grants": {}, "items": [], "must": [[7, 7]],
        "values": {"起点": 0, "空地": 0},
    }
    try:
        RS.Solver(sc)
    except SystemExit as exc:
        return "不在该构型格子里" in str(exc), f"报错：{str(exc).splitlines()[0]}"
    return False, "居然没报错"


def t_derive_must_rules():
    """必走点的推断规则：树根任何层；行商/紧急只在二~五层；右区只认作战类。"""
    import build_scenario as BS
    nodes = {
        "0,0": "树根",            # 任何层
        "1,1": "秘境行商",        # 二~五层
        "2,2": "紧急作战",        # 二~五层
        "3,3": "未知的凶戾",      # 左区：不算
        "8,8": "右区凶戾",        # 右区：算
        "9,8": "右区诡秘",        # 右区诡秘：不算
        "10,2": "命运所指",       # 不算
        "8,2": "右区安全的角落",  # 不算
    }
    right = {(8, 8), (9, 8), (10, 2), (8, 2)}
    l3 = set(BS.derive_must(3, nodes, right))
    l6 = set(BS.derive_must(6, nodes, right))
    want3 = {(0, 0), (1, 1), (2, 2), (8, 8)}
    want6 = {(0, 0), (8, 8)}     # 六层只强制树根（与右区作战类）

    # 五层右区**固定 1 个诡意行商** → 必走（03：五层行商「全进」）
    n5 = {"0,0": "树根", "11,2": "诡意行商", "12,2": "右区凶戾"}
    must5 = set(BS.derive_must(5, n5, {(11, 2), (12, 2)}))
    ok5 = must5 == {(0, 0), (11, 2), (12, 2)}
    return l3 == want3 and l6 == want6 and ok5, \
        (f"三层 {sorted(l3)}；六层 {sorted(l6)}；五层 {sorted(must5)}"
         f"（应含 (11,2) 诡意行商）")


def t_l8_boundary_consistent():
    """五层 L8（13 列）：**左右区边界只能有一个来源**。

    `route.py` 收 lm=8，`right_zone()` 若还用 LEFT_MAX[5]=9，
    x=9 那列会「进分阶段的右区求解、却不在 extra_cells 里」——
    必走推断看不见它，两个入口给出不同格子集合（2026-10-28 报告的第 4 条）。
    """
    import build_scenario as BS
    grid = {(8, 2): ("未知的凶戾",), (9, 2): ("未知的诡秘",), (10, 2): ("未知的诡秘",)}
    cells, edges = set(), []
    lm = BS.left_max(5, 13)
    extra, _notes = BS.right_zone(5, grid, cells, edges, lm)
    ok_l8 = (lm == 8) and set(extra) == {(9, 2), (10, 2)}
    # 常驻 14 列版：lm = 9，右区从 x=10 起
    cells2, edges2 = set(), []
    lm2 = BS.left_max(5, 14)
    extra2, _n2 = BS.right_zone(5, grid, cells2, edges2, lm2)
    ok_14 = (lm2 == 9) and set(extra2) == {(10, 2)}
    # 2026-10-30：无右区（左区居中）时的列数也要能判对边界
    ok_left = (BS.left_max(5, 10) == 9 and BS.left_max(5, 10, "L8") == 8
               and BS.left_max(5, 9, "L8") == 8)
    return ok_l8 and ok_14 and ok_left, (
        f"L8: lm={lm} 右区 {sorted(extra)}（应 {{(9,2),(10,2)}}）；"
        f"14列: lm={lm2} 右区 {sorted(extra2)}（应 {{(10,2)}}）；"
        f"无右区 lm(10列)={BS.left_max(5, 10)} lm(L8/9列)={BS.left_max(5, 9, 'L8')}")


def t_right_zone_absent_geometry():
    """2026-10-30：三/四/五层**无右区**时，按左区宽度选列、has_right=False。

    锁定：
      ① 完整宽度（11/12/14）→ has_right=True；
      ② 左区居中（7/8/10）→ has_right=False；
      ③ 五层 L8 左区居中（9 列）→ has_right=False；
      ④ `left_max` 对「无右区 10 列」与「L8 9 列」都能给对边界。
    """
    import label_ocr as LO
    import build_scenario as BS

    def span(n, width=1920.0, spacing=120.0):
        start = width / 2.0 - (n - 1) * spacing / 2.0
        return [start + i * spacing for i in range(n)]

    full3, left3 = span(11), span(7)
    left4 = span(8)
    left5, full5 = span(10), span(14)
    left5_l8 = span(9)
    c3, gx3, i3 = LO.choose_layer_cols(full3, 3, 1920.0)
    cl3, _, il3 = LO.choose_layer_cols(left3, 3, 1920.0)
    cl4, _, il4 = LO.choose_layer_cols(left4, 4, 1920.0)
    cl5, _, il5 = LO.choose_layer_cols(left5, 5, 1920.0)
    cf5, _, if5 = LO.choose_layer_cols(full5, 5, 1920.0)
    cl8, _, il8 = LO.choose_layer_cols(left5_l8, 5, 1920.0, cfg="L8")
    ok = (c3 == 11 and LO.has_right_cols(3, c3)
          and cl3 == 7 and not LO.has_right_cols(3, cl3)
          and cl4 == 8 and not LO.has_right_cols(4, cl4)
          and cl5 == 10 and not LO.has_right_cols(5, cl5)
          and cf5 == 14 and LO.has_right_cols(5, cf5)
          and cl8 == 9 and not LO.has_right_cols(5, cl8))
    return ok, (f"full3={c3}/{i3.get('has_right')} left3={cl3} left4={cl4} "
                f"left5={cl5} full5={cf5} left5L8={cl8}; "
                f"left_max 10列={BS.left_max(5, 10)} L8/9列={BS.left_max(5, 9, 'L8')}")


def t_tentacle_enters_l5_merchant():
    """五层右区固定 1 个诡意行商 → **已探明时**触须能当五层进区件。

    触须的 only 就是「诡意行商 / 秘境行商」，`rules.land_ok` 必须让它通过，
    否则手上只有触须时会被判「进不去右区」。
    """
    import rules as R
    import route as RT
    tent = R.normalize_item({"name": "坎诺特的触须", "uses": 1})
    ok_type = R.land_ok(tent, "诡意行商") and not R.land_ok(tent, "右区凶戾")
    nodes = {"11,2": "诡意行商", "12,2": "右区凶戾"}
    found = RT._find_entry([tent], (6, 2), {(11, 2), (12, 2)}, nodes)
    ok_geo = found is not None and found[1] == (11, 2)      # 只能落行商，不能落凶戾
    return ok_type and ok_geo, f"类型判断={ok_type}；几何落点={found}（应 (11,2)）"


def t_tentacle_needs_revealed_merchant():
    """**未探明行商时，触须不能当进区件**（2026-10-28 玩家口径）。

    五层右区固定有一个诡意行商，但它不一定被直接探明：
    没探明时那里显示成「未知的诡秘」，限行商的件语义上落不了，必须换别的件。
    """
    import rules as R
    import route as RT
    tent = R.normalize_item({"name": "坎诺特的触须", "uses": 1})
    princ = R.normalize_item({"name": "结构性原理", "uses": 1})
    right = {(11, 2), (12, 2)}
    unrevealed = {"11,2": "右区诡秘", "12,2": "右区诡秘"}
    revealed = {"11,2": "诡意行商", "12,2": "右区诡秘"}

    ok_unrevealed = (RT._find_entry([tent], (6, 2), right, unrevealed) is None)
    # 有通用件时应该换成原理，而不是错误地报「进不去」
    swap = RT._find_entry([tent, princ], (6, 2), right, unrevealed)
    ok_swap = swap is not None and swap[0] == "结构性原理"
    # 探明之后同一件就得能用
    ok_revealed = RT._find_entry([tent], (6, 2), right, revealed) == ("坎诺特的触须", (11, 2))
    # 提示文案要出现（否则玩家会问「我的触须怎么不用」）
    hints = RT._entry_blocked_hint([tent], unrevealed, right)
    ok_hint = bool(hints) and "未探明" in hints[0] or "还没探明" in (hints[0] if hints else "")
    return ok_unrevealed and ok_swap and ok_revealed and ok_hint, \
        (f"未探明: 挑={ok_unrevealed} 换原理={ok_swap} 探明后={ok_revealed} 提示={hints[:1]}")


def t_entry_skips_blocked_cells():
    """进区件不能把**已被 block 删掉的格**当落点（2026-10-28 报告）。

    几何：左区终点 (6,0)，右区 (8,0)=空地（三层起是硬墙）、(10,0)=右区作战；
    手里只有报废轮子（直线 2 格）。
    旧逻辑：`_find_entry` 只看「COVER 里有没有右区格」→ 打印「进区件：报废轮子 → 落 (8,0) 空地」，
    求解器把空地删了 → 轮子够不到 (10,0) → 右区 0 分、必走点回落。
    `any` 类件（原理）会碰巧改落到真格上，所以这个 bug 只在**直线件**上显形。
    """
    import rules as R
    import route as RT
    wheel = R.normalize_item({"name": "报废轮子", "uses": 1})
    princ = R.normalize_item({"name": "结构性原理", "uses": 1})
    nodes = {"8,0": "空地", "10,0": "右区作战"}
    right = {(8, 0), (10, 0)}
    blocked = ["空地", "曲折密道"]
    # 只有轮子：只能到 (8,0)=空地（死格），而 (10,0) 是直线 4 格之外 → 应该判「进不去」
    bad = RT._find_entry([wheel], (6, 0), right, nodes, blocked)
    # 有原理：必须改落到 (10,0) 那块真格，绝不能仍报 (8,0)
    good = RT._find_entry([wheel, princ], (6, 0), right, nodes, blocked)
    ok = (bad is None) and (good is not None and good[1] == (10, 0))
    return ok, (f"只有轮子={bad}（应 None）；轮子+原理={good}（应落 (10,0)）")


def t_items_piece_semantics():
    """`--items` 的裸数字是**件数**，不是次数（玩家 2026-10-28 纠错）。

    「1标准引擎」= 1 件 = 耐久 3 次。只有写明「次」才按次数算；
    **四结局六层**每件强制 1 次——**写「次」也要过 clamp**（2026-10-28 审计：
    `剩N次`/`N次` 分支是唯一没走 clamp_uses 的，`标准引擎:剩3次` 在六层仍是 3 次）。
    **三结局六层「源流交汇处」（cfg=源流）不夹次数**（2026-10-31 玩家裁定）——
    以前 `clamp_uses` 只看 layer==6，把源流也压成 1 次，白砍了 2/3 的跳跃预算。
    """
    import rules as R
    cases = [
        ("标准引擎", 3, 3), ("标准引擎:1", 3, 3), ("标准引擎:1个", 3, 3),
        ("标准引擎:2", 3, 6), ("标准引擎:剩1次", 3, 1),
        ("标准引擎:2*1", 3, 2), ("标准引擎:2", 6, 2),
        ("鸣笛:1", 5, 1), ("一次性喷气背包:2", 5, 2),
        # 六层「次」分支也要 clamp（2026-10-28 审计图 1）
        ("标准引擎:剩3次", 6, 1), ("标准引擎:剩1次", 6, 1), ("标准引擎:2次", 6, 1),
        ("标准引擎:剩3次", 3, 3),          # 非六层不 clamp
        ("标准引擎:1", None, 3),           # layer 缺省 = 不按层夹（以前 int(None) 直接崩）
        ("标准引擎:剩0次", 6, 0),          # 六层只夹**正**次数：用完了就是 0，不抬成 1
    ]
    bad = []
    for spec, layer, want in cases:
        items, _notes = R.parse_items_spec(spec, layer)
        got = items[0]["uses"] if items else None
        if got != want:
            bad.append(f"{spec}@L{layer} → {got}（应 {want}）")
    # 六层两张图次数规则不同（2026-10-31 玩家裁定）：源流 = 默认耐久，不夹
    var_cases = [
        ("标准引擎", "源流", 3), ("标准引擎:2", "源流", 6),
        ("标准引擎:剩1次", "源流", 1), ("标准引擎:2*1", "源流", 2),
        ("气垫底座:1", "源流", 2), ("标准引擎:剩0次", "源流", 0),
        ("标准引擎:2", "唯一", 2),        # 四结局「盲目者所见」仍每件 1 次
    ]
    for spec, cfg, want in var_cases:
        items, _notes = R.parse_items_spec(spec, 6, cfg)
        got = items[0]["uses"] if items else None
        if got != want:
            bad.append(f"{spec}@L6/{cfg} → {got}（应 {want}）")
    if R.clamp_uses(6, 3, "源流") != 3 or R.clamp_uses(6, 3, "唯一") != 1:
        bad.append(f"clamp_uses 源流={R.clamp_uses(6, 3, '源流')}（应 3）/ "
                   f"唯一={R.clamp_uses(6, 3, '唯一')}（应 1）")
    return not bad, ("全部正确" if not bad else "错：" + "；".join(bad))


def t_landmark_disambiguates_config():
    """起点重复时，用**险路恶敌 / 险路尽头坐标**定构型（玩家 2026-10-28 的判定法）。

    三层 L1 与 L2 的起点都是 (0,2)，单看起点定不出来；
    加上恶敌坐标就唯一：`(0,2)+(5,2)→L1`、`(0,2)+(6,2)→L2`。
    四层没有恶敌、只有尽头（每构型 3 个、开局只亮 1 个）；五层只有恶敌。

    2026-10-28 审计追加：起点表是**逐步补满**的，没学过起点的构型
    **不能因为「表里没有」被丢掉**——否则 L1 已记住 (0,2)、L2 还没有时，
    查 (0,2)+L2 的恶敌会得到空列表。用固定表（只有 L1 学过）钉住这条。
    """
    import build_scenario as BS
    bad = []
    got = BS.configs_by_evidence(3, [(0, 2)], [])
    if not ("L1" in got and "L2" in got and "L4" not in got):
        bad.append(f"只给起点 (0,2)：L1/L2 必须在、起点不同的 L4 不在，实得 {got}")
    if BS.configs_by_evidence(3, [(0, 2)], [(5, 2)]) != ["L1"]:
        bad.append("起点(0,2)+恶敌(5,2) 应为 L1")
    if BS.configs_by_evidence(3, [(0, 2)], [(6, 2)]) != ["L2"]:
        bad.append("起点(0,2)+恶敌(6,2) 应为 L2")
    if BS.configs_by_evidence(3, [(1, 3)], [(5, 2)]) != ["L4"]:
        bad.append("起点(1,3)+恶敌(5,2) 应为 L4")
    # 未学过起点的构型不许被丢（固定表，不依赖起点表.json 当前内容）
    real = BS._load_start_table
    BS._load_start_table = lambda: {"3": {"L1": [0, 2]}}   # 只有 L1 学过起点
    try:
        got = BS.configs_by_evidence(3, [(0, 2)], [(6, 2)])
        if got != ["L2"]:
            bad.append(f"L2 未学起点时，(0,2)+恶敌(6,2) 应仍得 L2，实得 {got}")
        got = BS.configs_by_evidence(3, [(0, 2)], [(5, 2)])
        if got != ["L1", "L4"]:
            bad.append(f"未学起点时 (0,2)+恶敌(5,2) 应得 L1+L4（L4 未学不排除），实得 {got}")
    finally:
        BS._load_start_table = real
    if BS.configs_by_landmark(4, (3, 4)) != ["L1"]:
        bad.append(f"四层尽头(3,4) 应是 L1，实得 {BS.configs_by_landmark(4, (3, 4))}")
    if BS.landmark_conflict(3, "L1", [(6, 2)])[0]:
        bad.append("三层 L1 + 恶敌(6,2) 应报冲突")
    if not BS.landmark_conflict(3, "L2", [(6, 2)])[0]:
        bad.append("三层 L2 + 恶敌(6,2) 应通过")
    if BS.landmark_conflict(4, "L2", [(3, 4)])[0]:
        bad.append("四层 L2 + 尽头(3,4) 应报冲突")
    return not bad, ("起点+地标合起来能唯一确定构型" if not bad else "；".join(bad))


def t_start_table():
    """所有构型的起点都是固定的（玩家 2026-10-28），已知值必须查得到。

    三/四/五层的构型标题里**没写起点**，所以单独维护 `references/起点表.json`。
    这里锁住已知的几条，并验证「起点不唯一」这个事实被正确处理：
    三层 L1/L2 都是 (0,2) → 构型必须靠格子集合定，不能靠起点反查。
    """
    import build_scenario as BS
    want = {(1, "1"): (0, 1), (2, "6"): (2, 3),
            (3, "L1"): (0, 2), (3, "L2"): (0, 2), (3, "L4"): (1, 3),
            (5, "L8"): (6, 3)}
    bad = []
    for (layer, cfg), exp in want.items():
        got = BS.config_start(layer, cfg)
        if got != exp:
            bad.append(f"{layer}层 {cfg} → {got}（应 {exp}）")
    # 未知构型必须返回 None（宁可不猜）
    if BS.config_start(4, "L3") is not None:
        bad.append("四层 L3 未记录却返回了值")
    # 冲突写入不许覆盖
    if BS.learn_start(3, "L4", (9, 9), quiet=True) is not False:
        bad.append("learn_start 覆盖了已有记录")
    return not bad, ("起点表完整且冲突保护有效" if not bad else "；".join(bad))


def t_start_learn_gating():
    """起点表只在「整次校验通过」或「--start 手报 + 识别阶段无错」时写入。

    2026-10-28 审计（「起点表写得太早」）：以前 `learn_start` 在地标校验、
    构型外格子检查**之前**就落盘，触发条件只有「在构型格内、无标签」；
    三/四/五层没写起点时入口会拿候选里第一个无标签格去校正，这一格也被记成硬事实
    ——写进去之后 `config_start` 就当它是对的（冲突只打印、不覆盖），
    一次偏掉的识别会把构型起点锁死。
    """
    import route as RT
    ok_full = RT._should_learn_start([], 0, False) is True            # 整次通过 → 记
    ok_manual = RT._should_learn_start(["AP 超支"], 0, True) is True  # 手报+识别无错 → 记
    ok_auto = RT._should_learn_start(["AP 超支"], 0, False) is False  # 自动识别+没全过 → 不记
    ok_recog = RT._should_learn_start(["构型选错", "AP 超支"], 1, True) is False
    # 识别阶段出错 → 连手报也不记（起点会被挂在错误构型名下）
    # 起点判据打了 ⚠️（= 起点是猜的）→ 一票否决，连「整次通过」也不记（2026-10-28 复查）
    ok_warn = RT._should_learn_start([], 0, True, True) is False
    # observe.py 那条路也要把「猜的起点」传下去：它自己打的 ⚠️ 会带 --no-learn-start，
    # 否则 --start 在 route.py 眼里等于「手报」，照样写表
    import inspect
    import observe as OB
    src = inspect.getsource(OB.main)
    ok_observe = ("start_warned" in src and "--no-learn-start" in src)
    ok = ok_full and ok_manual and ok_auto and ok_recog and ok_warn and ok_observe
    return ok, (f"全过={ok_full} 手报={ok_manual} 自动={ok_auto} "
                f"识别出错={ok_recog} 起点是猜的={ok_warn} observe传旗={ok_observe}")


def t_alias_before_inference():
    """`未知的凶戾` 的别名必须在 `infer_unknown` **之前**套，否则期望被冲掉。

    2026-10-28 复查：`_infer_left_quota` 把「未知的凶戾」改成逐格期望
    （9 × 剩余名额 / 凶戾格数），而两个入口原先都在推断**之后**再套
    `未知的凶戾 = 凶戾` 的别名 —— 判词打印「每个期望 4.5 分」、实际按 1.0 分算。
    这里同时钉两件事：
      ① 正确顺序（先别名、后推断）下，逐格期望能活下来；
      ② 两个入口的 main 里，别名调用必须出现在 infer_unknown 调用之前。
    """
    import inspect
    import build_scenario as BS
    import route as RT
    bad = []

    values = {"凶戾": 1.0, "紧急作战": 9}
    BS.apply_unknown_aliases(values)
    if values.get("未知的凶戾") != 1.0:
        bad.append(f"别名没套上：{values}")
    nodes = {"0,1": "未知的凶戾", "1,1": "未知的凶戾"}
    BS.infer_unknown(3, nodes, values, [], (0, 0), 6, root=(3, 0))
    want = 4.5                      # 三层、理想源占 1 → 剩 1 个名额分给 2 格
    if values.get("未知的凶戾") != want:
        bad.append(f"先别名后推断：未知的凶戾={values.get('未知的凶戾')}（应 {want}）")

    for mod, fn in ((BS, BS.main), (RT, RT.main)):
        src = inspect.getsource(fn)
        # route.py 走的是 BS.apply_unknown_aliases / BS.infer_unknown
        a = src.find("apply_unknown_aliases")
        b = src.find("infer_unknown")
        if a < 0 or b < 0 or a > b:
            bad.append(f"{mod.__name__}.main 的调用顺序不对"
                       f"（apply_unknown_aliases@{a} infer_unknown@{b}）")
    return not bad, ("别名在推断之前，逐格期望不被覆盖" if not bad else "；".join(bad))


def t_start_not_on_labeled_cell():
    """起点不能落在**有标签的格子**上（起点本身是空地、不写字）。

    2026-10-28 三层那次：小人偏了一列，检测出 (1,2)，而那格读到「曲折密道」。
    判据链：① 离小人像素最近 → ② 格内无标签 → ③ 在该构型格子集合里。
    这里用仓库内的真实截图 `scene-L3-real.png` 跑候选排序（三层 L4，真值起点 (1,3)），
    断言：top 候选 = (1,3)，且**候选里的 (0,3)（曲折密道）不得排第一**。
    """
    import label_ocr as LO
    from PIL import Image
    img = os.path.join(HERE, "examples", "scene-L3-real.png")
    if not os.path.exists(img):
        return True, "跳过（没有 scene-L3-real.png）"
    try:
        labels = LO.read_image(img)
    except (ModuleNotFoundError, SystemExit) as exc:
        # OCR 引擎是可选依赖（工具链设计「失败可降级」）；缺它只影响这条视觉用例，
        # 与求解器行为无关，不该把整个自检判成「修复被改回去」。
        # 2026-10-30 修复：`label_ocr.engine()` 缺依赖时抛的是 **SystemExit**（不是 ImportError），
        # 旧 guard 只接 ModuleNotFoundError → 整个 selftest 进程被 SystemExit 终止
        # （连带后面所有用例都不跑，本机 2026-10-30 复现）。
        name = getattr(exc, "name", None) or "rapidocr_onnxruntime"
        return True, ("跳过（**本解释器**缺 %s；与求解器无关。"
                      "换解释器：`python tools/pyenv.py`）" % name)
    cols, rows = 11, 5
    gx = sorted(LO.choose_lattice([l[0] for l in labels], cols))
    gy = sorted(LO.choose_lattice([l[1] for l in labels], rows), reverse=True)
    grid = LO.assign(labels, gx, gy, cols, rows)
    cands = LO.detect_start_candidates(Image.open(img).convert("RGB"),
                                       gx, list(reversed(gy)), labels)
    cells = [(c, rows - 1 - r) for c, r in cands]
    top = cells[0] if cells else None
    ok = top == (1, 3) and (1, 2) not in cells[:1]
    labeled_top = top in grid if top else False
    return ok and not labeled_top, \
        (f"top={top}（应 (1,3)）；该格有标签={labeled_top}；候选前3={cells[:3]}")


def t_right_quota_inference():
    """右区作战配额**固定且独立于左区**，未点亮的右区凶戾按剩余配额推。

    玩家 2026-10-28：三层 1 紧急 + 1 普通、四层 1 紧急 + 2 普通、五层 2 紧急 + 1 普通；
    左区已探明 2 个紧急也**不影响**右区那个紧急。
    所以「右区凶戾」不是盲踩 18 分：已探明一个，剩下全变确定。

    2026-10-28 审计追加两条（右区期望不看「看见了几格」）：
    · 名额只够 min(未知数, 剩余名额) 个格用——四层 4 个未知全给 17.7 = 70.8，
      而真实上限只有 53；改后 3 个按 17.7 配、多出的 1 个记 0。
    · 紧急已见、普通剩 2 个名额、图上却有 3 个未知：不能 3 个全改成「右区作战」
      （48 > 32），只有 2 个改名，多出的记 0 且**不进必走点**。
    """
    import build_scenario as BS
    import rules as R

    def infer(layer, nodes):
        v = dict(R.LAYER_PRICES[layer])
        n = dict(nodes)
        notes = BS.infer_unknown(layer, n, v, [], (0, 0), 6)[1]
        return n, v, notes

    bad = []
    # 三层：普通已见 1 → 另一个必是紧急 21
    n, _v, _qn = infer(3, {"5,2": "右区作战", "6,2": "右区凶戾"})
    if n["6,2"] != "右区紧急作战":
        bad.append(f"三层普通已满时另一个是 {n['6,2']}（应 右区紧急作战）")
    # 三层：紧急已见 1 → 另一个必是普通 16
    n, _v, _qn = infer(3, {"5,2": "右区紧急作战", "6,2": "右区凶戾"})
    if n["6,2"] != "右区作战":
        bad.append(f"三层紧急已满时另一个是 {n['6,2']}（应 右区作战）")
    # 三层：都未点亮 → 18.5（1 紧急 + 1 普通）
    n, v, _qn = infer(3, {"5,2": "右区凶戾", "6,2": "右区凶戾"})
    if v["右区凶戾"] != 18.5:
        bad.append(f"三层未知凶戾期望 {v['右区凶戾']}（应 18.5）")
    # 五层：2 紧急 + 1 普通 → 19.3
    n, v, _qn = infer(5, {"9,2": "右区凶戾", "9,3": "右区凶戾", "9,4": "右区凶戾"})
    if v["右区凶戾"] != 19.3:
        bad.append(f"五层未知凶戾期望 {v['右区凶戾']}（应 19.3）")
    # 独立性：左区已探明 2 个紧急作战，右区凶戾仍按 18.5，不被左区配额吃掉
    n, v, _qn = infer(3, {"0,1": "紧急作战", "1,1": "紧急作战",
                          "5,2": "右区凶戾", "6,2": "右区凶戾"})
    if v["右区凶戾"] != 18.5:
        bad.append(f"左区配额满时右区凶戾被压成 {v['右区凶戾']}（应仍 18.5）")
    # 审计情形 ②：四层配额 1紧急+2普通，图上 4 个未知 → 3 个按 17.7 配 + 1 个记 0
    n, v, qn = infer(4, {"8,1": "右区凶戾", "9,1": "右区凶戾",
                         "9,2": "右区凶戾", "9,3": "右区凶戾"})
    extras = [k for k, t in n.items() if str(t).startswith("右区凶戾@")]
    kept = [k for k, t in n.items() if t == "右区凶戾"]
    ok = (len(extras) == 1 and len(kept) == 3 and v["右区凶戾"] == 17.7
          and extras and v[n[extras[0]]] == 0)
    if not ok:
        bad.append(f"四层 4 未知：应 3 个@17.7 + 1 个@0，实得 kept={kept} "
                   f"extras={extras} 期望={v['右区凶戾']}")
    # 审计情形 ③：紧急已见、普通剩 2 个名额、图上 3 个未知 → 2 个改名右区作战 + 1 个记 0
    n, v, qn = infer(4, {"8,1": "右区紧急作战", "9,1": "右区凶戾",
                         "9,2": "右区凶戾", "9,3": "右区凶戾"})
    renamed = [k for k, t in n.items() if t == "右区作战"]
    extras = [k for k, t in n.items() if str(t).startswith("右区凶戾@")]
    ok = (len(renamed) == 2 and len(extras) == 1 and v["右区作战"] == 16
          and extras and v[n[extras[0]]] == 0)
    if not ok:
        bad.append(f"四层紧急已见+3 未知：应 2 个右区作战 + 1 个@0，"
                   f"实得 renamed={renamed} extras={extras}")
    # 多出来的格不进必走点（derive_must 只认精确类型名）
    must = BS.derive_must(4, n, {(8, 1), (9, 1), (9, 2), (9, 3)})
    extra_cells = {tuple(int(x) for x in k.split(",")) for k in extras}
    if extra_cells & set(must):
        bad.append(f"多余格 {sorted(extra_cells)} 不该进必走点，实得 must={must}")
    # 左右区说明都保留（审计图 2：右区不许盖掉左区那句）
    n, v, qn = infer(3, {"3,1": "未知的凶戾", "5,2": "右区凶戾", "6,2": "右区凶戾"})
    if not any(str(x).startswith("左区：") for x in qn):
        bad.append(f"左右区都有未知凶戾时缺左区说明：{qn}")
    if not any(str(x).startswith("右区：") for x in qn):
        bad.append(f"左右区都有未知凶戾时缺右区说明：{qn}")
    # 两个配额都满（slots=0）：单独一条话术，别落进「0/N 定为右区普通作战 16」（2026-10-28 复查）
    n, v, qn = infer(3, {"8,1": "右区紧急作战", "8,2": "右区作战", "9,1": "右区凶戾"})
    if n["9,1"] != "右区凶戾@9,1" or v.get(n["9,1"]) != 0:
        bad.append(f"名额满时多余格应改名记 0，实得 {n['9,1']} / {v.get(n['9,1'])}")
    if any("0/" in str(x) for x in qn):
        bad.append(f"名额满时话术不对（出现了 0/N）：{qn}")
    if not any("全部记 0" in str(x) for x in qn):
        bad.append(f"名额满时应有一条「全部记 0」说明：{qn}")
    return not bad, ("右区配额推断、名额封顶与独立性均正确" if not bad else "；".join(bad))


def t_cover_semantics():
    """可达域（COVER）的**格数与语义**必须逐类正确——「内」= 1~N，不是恰好 N。

    2026-10-28 踩过：试作外骨骼原文「直线 3 格内」，求解器建成「恰好 3 格」，
    于是 (2,3)→(4,3)（直线 2 格）被判不可达，整条路线错。
    这里用「10×10 网格上从 (5,5) 展开」的精确格数把六类钉住：
      adj8=8、adj12=12（曼哈顿 ≤2）、line2=**8**（4 方向 × 距离 1~2）、
      line3=**12**（4 方向 × 距离 1~3）、row_col=18（同行 9 + 同列 9，去重自身）、
      any=99（100 格减去自身），且**都不含自身**。
    """
    import route_solver as RS
    p = (5, 5)
    xs, ys = list(range(10)), list(range(10))
    want = {"adj8": 8, "adj12": 12, "line2": 8, "line3": 12, "row_col": 18, "any": 99}
    bad = []
    for name, fn in RS.COVER.items():
        got = set(fn(p) if name in RS.FREE_COV else fn(p, xs, ys))
        if len(got) != want[name]:
            bad.append(f"{name} 格数 {len(got)} ≠ {want[name]}")
        if p in got:
            bad.append(f"{name} 含自身")
    return not bad, ("六类可达域格数正确且都不含自身" if not bad else "；".join(bad))


def t_bands_match_distance_table():
    """`BANDS` 必须与 `06-距离表` 逐类一致（分层核对）。

    候选少一类，均值就会被抬高：2026-10-28 三层漏了
    先行一步(2–4) / 诡意行商(3–8) / 应急助力(4+)，(2,2) 的期望从 6.0 变成 10.0。
    真实截图实测（三层 血色空脉，起点 (1,3)）：(2,2)=6.0、(1,1)=4.3、(4,4)=4.3。
    """
    import build_scenario as BS
    want = {
        3: {"狭路相逢": (2, 4), "误入奇境": (5, 6), "诡意行商": (3, 8), "秘境行商": (3, 6),
            "安全的角落": (3, 6), "不期而遇": (1, 99), "得偿所愿": (4, 99), "失与得": (3, 6),
            "先行一步": (2, 4), "应急助力": (4, 99)},
        4: {"诡意行商": (2, 99), "先行一步": (2, 4), "应急助力": (2, 3), "狭路相逢": (2, 99),
            "秘境行商": (2, 6), "误入奇境": (5, 8), "得偿所愿": (4, 99), "不期而遇": (1, 99),
            "失与得": (2, 4), "安全的角落": (2, 99)},
        5: {"诡意行商": (1, 99), "应急助力": (3, 5), "狭路相逢": (2, 4), "秘境行商": (2, 99),
            "误入奇境": (3, 8), "得偿所愿": (1, 99), "不期而遇": (1, 99), "失与得": (2, 99),
            "安全的角落": (2, 99)},
    }
    problems = []
    for layer in (3, 4, 5):
        got = BS.BANDS[layer]
        for k, v in want[layer].items():
            if k not in got:
                problems.append(f"L{layer} 缺 {k}")
            elif got[k] != v:
                problems.append(f"L{layer} {k} 区间 {got[k]} ≠ {v}")
        for k in got:
            if k not in want[layer]:
                problems.append(f"L{layer} 多出 {k}")
    return not problems, ("三层候选与 06 表一致" if not problems else "；".join(problems))


def t_all_items_priced():
    """16 件加工品**全部**要有价格（`rules.PRICE` 覆盖满），且与文档口径一致。

    落在默认 2 的件会被当成报废假肢同价——融雪就这样错了一整天（文档写 8，
    代码没写 price → `cost_of` 取默认 2）。这里把整张表钉住。
    """
    import rules as R
    want = {
        "报废轮子": 2, "报废假肢": 2, "标准引擎": 4, "重弹簧": 1, "气垫底座": 4,
        "试作外骨骼": 4, "小八界": 1, "一次性喷气背包": 4,
        "老妈妈的融雪": 8, "坎诺特的触须": 4, "紧急逃生座椅": 4,
        "结构性原理": 16, "简易遥控器": 16, "力反馈方向盘": 12, "鸣笛": 2,
        "坎诺特特快": 16,
    }
    missing = [n for n in R.available_items() if n not in R.PRICE]
    wrong = [(n, R.PRICE.get(n), w) for n, w in want.items() if R.PRICE.get(n) != w]
    return (not missing) and (not wrong) and len(R.available_items()) == 16, \
        (f"缺价 {missing or '无'}；与期望不符 {wrong or '无'}；共 {len(R.available_items())} 件")


def t_view_cost_of_merges():
    """分阶段视图的道具价表要覆盖**所有**道具，不能只取左区段。

    只取左区段时，右区段才用到的件（如融雪进区）查不到价 → 打印落到默认 2，
    和 `last_cost` 加出来的总额对不上（2026-10-28 实测：道具价 14，明细写「融雪(2)」）。
    """
    import route as RT
    import rules as R
    sc = {
        "name": "view-cost", "edges": [], "nodes": {"0,0": "起点", "2,0": "诡秘"},
        "start": [0, 0], "ap": 1, "grants": {},
        "items": [{"name": "老妈妈的融雪", "uses": 1}],
        "values": {"起点": 0, "诡秘": 9, "空地": 0},
    }
    res = RT.solve_staged(sc, 3, 6, [], 10)
    view = RT._View(res)
    bad = [n for n in R.available_items() if view.cost_of.get(n) != R.PRICE.get(n, 2)]
    return (not bad) and view.cost_of.get("老妈妈的融雪") == 8, \
        (f"融雪价 {view.cost_of.get('老妈妈的融雪')}（应 8）；缺价 {bad or '无'}")


def t_backtrack_uses_blocked_graph():
    """回头步校验必须用**删格后的图**，否则「绕经一面墙」会被当成真路。

    构造：a(0,0) - q(1,0) - b(2,0) 是直线；另有一条 a - c(1,1) - b 的绕路，
    但 c 是空地（三层起被 block）。真实图里 q 是 a→b 的唯一中转，
    所以 q 上的回头是「必要」；用原始边表则会误判成「可避免」。
    """
    import route as RT
    edges = [[[0, 0], [1, 0]], [[1, 0], [2, 0]],
             [[0, 0], [1, 1]], [[1, 1], [2, 0]]]
    nodes = {"0,0": "起点", "1,0": "诡秘", "2,0": "诡秘", "1,1": "空地"}

    def avoidable(adj, a, b, q):
        if b in adj[a]:
            return True
        for c in adj[a]:
            if c != q and b in adj[c]:
                return True
        return False

    raw = RT._adj_after_block(edges, nodes, ())              # 不删格：绕路存在
    cut = RT._adj_after_block(edges, nodes, ["空地"])        # 删空地：绕路消失
    a, b, q = (0, 0), (2, 0), (1, 0)
    ok = avoidable(raw, a, b, q) and not avoidable(cut, a, b, q)
    return ok, (f"原始边表判可避免={avoidable(raw, a, b, q)}（应 True）；"
                f"删空地后={avoidable(cut, a, b, q)}（应 False）")


def t_block_types_per_layer():
    """block 清单按层：险路恶敌全删；尽头除二层全删；三/四再删命运所指。"""
    import build_scenario as BS
    b1, b2, b3 = BS.block_types(1), BS.block_types(2), BS.block_types(3)
    b4, b5 = BS.block_types(4), BS.block_types(5)
    ok = (
        # 险路恶敌：所有层都删（负收益强制战斗）
        all("险路恶敌" in b for b in (b1, b2, b3, b4, b5))
        # 险路尽头：除二层都删（五层即使没有这种格，写进去也是无害的 no-op）；
        # 二层是四结局道具 +8，必须可进
        and "险路尽头" in b1 and "险路尽头" in b4
        and "险路尽头" not in b2
        # 空地：一/二层可走，三层起是墙
        and "空地" not in b1 and "空地" not in b2 and "空地" in b3
        # 命运所指：三/四是墙，五层是终点
        and "命运所指" in b3 and "命运所指" in b4 and "命运所指" not in b5
    )
    return ok, f"L1={b1} L2={b2} L3={b3} L5={b5}"


def t_entry_exclude_right_prefix():
    """`exclude` 也要做「右区」前缀展开——否则入口和求解器互相矛盾。

    2026-10-28 踩过：老妈妈的融雪 exclude 里有「作战」，
    但 `route.py::_can_land` 没展开成「右区作战」→ 判定「能进右区」并打印
    「进区件：融雪 → (8,0) 右区作战」，求解器却因为 exclude 命中而拒绝 → 右区 0 分。
    现在两边都走 `rules.expand_prefixes / land_ok`。
    """
    import rules as R
    import route as RT
    items = [R.normalize_item({"name": "老妈妈的融雪", "uses": 1})]
    nodes = {"8,0": "右区作战", "9,0": "右区安全的角落"}
    right = {(8, 0), (9, 0)}
    found = RT._find_entry(items, (2, 2), right, nodes)
    # 只能落「右区安全的角落」，不能是「右区作战」
    ok_land = (not R.land_ok(items[0], "右区作战")) and R.land_ok(items[0], "右区安全的角落")
    ok_entry = found is not None and found[1] == (9, 0)
    return ok_land and ok_entry, (f"only/exclude 展开={R.expand_prefixes(['作战'])}；"
                                  f"落右区作战={R.land_ok(items[0], '右区作战')}（应 False）；"
                                  f"进区件={found}")


def t_staged_entry_geometric():
    """分阶段的进区件必须**按几何**挑：便宜但够不着的件不能顶替。

    几何取自 2026-10-28 的实测：左区终点在 (2,2)、右区在 (8,0)。
    轮子（直线 2 格）便宜但够不着；只有原理（任意）能进。
    """
    import route as RT
    sc = {
        "name": "staged-entry",
        "edges": [[[2, 2], [2, 3]], [[2, 2], [4, 2]]],
        "nodes": {"2,2": "起点", "2,3": "诡秘", "4,2": "诡秘",
                  "8,0": "右区作战", "9,0": "右区紧急作战"},
        "start": [2, 2], "ap": 6, "grants": {},
        "items": [{"name": "报废轮子", "uses": 1},
                  {"name": "结构性原理", "uses": 1}],
        "values": {"起点": 0, "诡秘": 9, "右区作战": 16, "右区紧急作战": 21,
                   "空地": -99, "曲折密道": -99},
        "block": ["空地", "曲折密道"],
    }
    res = RT.solve_staged(sc, 3, 6, [(8, 0), (9, 0)], 20)
    rpath = next(seg["path"] for seg in res["segments"] if seg["tag"] == "right")
    # 右区段第一步必须是「原理跳进右区」，而不是用便宜但够不着的轮子
    first = rpath[0] if rpath else (None, None, None)
    ok = bool(rpath) and first[1] == "结构性原理" and first[0] in {(8, 0), (9, 0)}
    return ok, (f"分 {res['total']:g}（左 {res['segments'][0]['score']:g} / "
                f"右 {res['segments'][1]['score']:g}），右区首步 {first}（应 原理 → (8,0)/(9,0)）")


def t_must_zero_value_enforced():
    """**0 价值的必走点也必须生效**（2026-10-29 修复）。

    以前 pcells 只收「正值格 + grant 格」，0 分必走点没有 bit →
    must_mask 盖不住它 → 求解器绕开它照样收解（约束静默失效，打印却说
    「仍要求走到」）；而 must_uncovered 又按 must 全集算 → route.py 独立复核
    误报「必走点点不到 → ❌」（即使本来走得到）。

    构造：0 分「险路小径」(1,1) 挂在 10 分诡秘 (1,0) 旁，AP=2：
      · 修复后：唯一满足 must 的解是 (1,0)→(1,1)，总分 10；
      · 修坏时：求解器直接拿 (1,0)→(2,0) 的 20 分，必走点被绕开。
    """
    sc = {
        "name": "must-zero-value",
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]], [[1, 0], [1, 1]]],
        "nodes": {"0,0": "起点", "1,0": "诡秘", "2,0": "诡秘", "1,1": "险路小径"},
        "start": [0, 0], "ap": 2, "grants": {}, "items": [],
        "must": [[1, 1]],
        "values": {"起点": 0, "诡秘": 10, "险路小径": 0, "空地": 0},
    }
    s = RS.Solver(sc)
    tot, path, _h = s.run(max_time=10)
    landed = [q for q, _i, _g in path]
    ok = ((1, 1) in landed and not s.must_dropped and s.must_uncovered == ())
    return ok, (f"路线 {landed} 总分 {tot}（0 分必走点 (1,1) 必须被踩，"
                f"20 分的绕开解不许收）；回落={s.must_dropped} 未覆盖={s.must_uncovered}")


def t_staged_right_failure_surfaces():
    """分阶段：右区「没进去 / 无可行解」必须显式上报，不能被 total 吞掉（2026-10-29 修复）。

    旧代码 `total = score_l + (score_r if score_r > 0 else 0)` 把右区 -1 当 0：
    五层命运所指在右区又进不去时 total 仍为正 → 「total < 0」判词永不触发，
    路线没走到命运所指照样判 ✅；⚠️ 也不升级 ❌（--no-must 或右区未探明时漏掉）。
    """
    import route as RT
    # ① 右区有格、没有任何件能进（无道具）→ right_entered=False（入口据此判 ❌）
    sc = {
        "name": "staged-right-fail",
        "edges": [[[0, 0], [1, 0]]],
        "nodes": {"0,0": "起点", "1,0": "诡秘", "8,0": "右区作战"},
        "start": [0, 0], "ap": 6, "grants": {}, "items": [],
        "values": {"起点": 0, "诡秘": 9, "右区作战": 16, "空地": 0},
    }
    res = RT.solve_staged(sc, 3, 6, [(8, 0)], 10)
    ok_enter = (not res["right_entered"]) and res["total"] >= 0
    # ② 命运所指在右区（五层 final）且进不去 → 无可行解：total 必须为负
    sc2 = dict(sc, final=[8, 0], nodes=dict(sc["nodes"], **{"8,0": "命运所指"}))
    res2 = RT.solve_staged(sc2, 5, 6, [(8, 0)], 10)
    ok_inf = res2["right_infeasible"] and res2["total"] < 0
    return ok_enter and ok_inf, (
        f"没进去：entered={res['right_entered']} total={res['total']:g}（应 ≥0 且 entered=False）；"
        f"无可行解：infeasible={res2['right_infeasible']} total={res2['total']:g}（应 infeasible 且 <0）")


def t_landmark_subset_not_intersection():
    """地标冲突判据必须是**子集**（observed ⊆ want），不是交集（2026-10-29 修复）。

    旧实现 `observed & want` 非空即通过：观察 [(1,2),(0,0)] 混进一个错坐标也放行；
    三层 L1/L4 恶敌共用 (5,2)，交集判据会放行错构型。observe.detect_config 一直是子集。
    """
    import build_scenario as BS
    ok1 = BS.landmark_conflict(3, "L1", [(5, 2)])[0]          # 全部在声明集合里 → 通过
    ok2 = not BS.landmark_conflict(3, "L1", [(5, 2), (6, 2)])[0]  # 混入 (6,2) → 必须冲突
    # 交集写法下 L1∩[(5,2),(6,2)] 非空会误判通过——这条钉死子集语义
    hit_single = [c for c in BS.configs_by_evidence(3, [], [(5, 2)])]
    hit_double = [c for c in BS.configs_by_evidence(3, [], [(5, 2), (6, 2)])]
    ok3 = ("L1" in hit_single) and ("L1" not in hit_double)   # L1 没声明 (6,2)，两个地标时必须出局
    return ok1 and ok2 and ok3, (f"单地标过={ok1} 混入错坐标拦={ok2} "
                                 f"L1∈单地标={hit_single} L1∉双地标={hit_double}")


def t_base_type_and_band_coverage():
    """派生名必须能还原成基名；infer/expect_by_distance 的带宽要覆盖 1/2/6 层。"""
    import rules as R
    ok1 = (R.base_type("诡秘@2,3") == "诡秘"
           and R.base_type("右区凶戾@9,1") == "凶戾"
           and R.base_type("右区诡秘") == "诡秘"
           and R.base_type("普通作战") == "作战"
           and R.base_type("未知的凶戾") == "凶戾"
           and R.base_type("险路尽头") == "险路尽头")
    import infer as IF
    import expect_by_distance as EB
    ok2 = all(l in IF.BAND for l in (1, 2, 3, 4, 5)) and IF.URGENT_TOTAL.get(6) == 2
    ok3 = all(l in EB.DIST for l in (1, 2, 3, 4, 5))
    ok4 = EB.candidates(1, 3) and EB.candidates(2, 4) and EB.candidates(6, 3) is None
    return ok1 and ok2 and ok3 and ok4, (
        f"base_type={ok1}；infer BAND 1/2 齐={ok2}；expect DIST 1/2 齐={ok3}；"
        f"一层/二层出候选、六层显式 None={ok4}")


def t_right_zone_count_checked():
    """右区节点数固定（三层 6 / 四层 7 / 五层 9），格数对不上必须打 ⚠️（2026-10-29 修复）。"""
    import build_scenario as BS
    g5 = {(7, 2): ("右区作战",), (7, 3): ("右区凶戾",), (8, 2): ("右区诡秘",),
          (8, 3): ("右区不期而遇",), (9, 2): ("命运所指",)}
    c, e = set(), []
    cells5, notes5 = BS.right_zone(3, g5, c, e, lm=6)
    warn5 = any(n.startswith("⚠️ 右区格数") for n in notes5)
    g6 = dict(g5)
    g6[(9, 3)] = ("右区得偿所愿",)
    c2, e2 = set(), []
    _cells6, notes6 = BS.right_zone(3, g6, c2, e2, lm=6)
    warn6 = any(n.startswith("⚠️ 右区格数") for n in notes6)
    return (len(cells5) == 5 and warn5 and not warn6), (
        f"5 格时告警={warn5}；6 格（固定构成）无告警={not warn6}")


def t_right_zone_shape_table():
    """2026-11-02（玩家第 2 条意见）：右区边表**按 references 形状表**连，不再一律四邻接。

    锁四条：
      ① 三层构型3（8 格 2 空地）的观测子集 → `table`，且**不含** `(9,Y)-(9,Y-1)`——
         文档明写「（9,Y）与（9,Y-1）不连（玩家确认）」，四邻接一定会造出这条假边；
      ② 四层只有 7 个有标签格时形状多解（四种形状的格集互相包含）→ 必须 `ambiguous`，
         并回退成 ⚠️ 说明（不许静默当四邻接已验证）；
      ③ 五层 3×3 九格 = 四邻全连通（文档口径），12 条边；
      ④ 右区空地 = **0 分中继（可穿过）**（玩家 2026-11-02 澄清）：报出圆点后形状唯一锁定，
         穿过空地的边**保留**，空地格要能进图（并登记进 `empty_cells` 供上层豁免 block）。
    """
    import build_scenario as BS

    # ① 三层构型3，空地 = (8,3)/(8,4)
    g3 = {(8, 2): ("右区诡秘",), (9, 2): ("命运所指",), (9, 3): ("右区战斗",),
          (9, 4): ("右区诡秘",), (10, 3): ("右区凶戾",), (10, 4): ("右区作战",)}
    c3, e3, m3 = set(), [], {}
    extra3, notes3 = BS.right_zone(3, g3, c3, e3, lm=6, meta=m3)
    es3 = {tuple(sorted(x)) for x in e3}
    no_false = ((9, 3), (9, 4)) not in es3 and m3.get("topology") == "table"
    # 四邻接会加这条（对照：证明测的是「表」不是「邻接」）
    four_adj_would = abs(9 - 9) + abs(4 - 3) == 1

    # ② 四层 7 个有标签格 → 多解
    g4 = {(9, 4): ("右区作战",), (10, 4): ("右区作战",), (9, 3): ("右区诡秘",),
          (10, 3): ("命运所指",), (11, 3): ("右区诡秘",), (10, 2): ("右区诡秘",),
          (11, 2): ("右区稳妥",)}
    c4, e4, m4 = set(), [], {}
    BS.right_zone(4, g4, c4, e4, lm=8, meta=m4)
    amb_warn = (m4.get("topology") == "ambiguous"
                and any("右区边表：**未验证**" in n for n in BS.right_zone(
                    4, g4, set(), [], lm=8)[1]))

    # ③ 五层 3×3
    g5 = {(x, y): ("右区作战",) for x in (11, 12, 13) for y in (0, 1, 2)}
    c5, e5, m5 = set(), [], {}
    BS.right_zone(5, g5, c5, e5, lm=9, meta=m5)
    ok5 = m5.get("topology") == "table" and len(e5) == 12

    # ④ 2026-11-02：**报出空地圆点后，四层也能唯一锁定**
    #    没有 dots 时四层恒多解（1↔4、2↔3 格集互相包含）；给了 dots 走精确匹配。
    sh4 = {s["name"]: s for s in BS._parse_right_shapes(4)}["构型1"]
    base4 = {(x, 2 + dy) for (x, dy) in sh4["cells"]}       # 构型1 在 Y=2 的 8 格
    dots4 = [(9, 2)]                                        # 报出空地
    g4b = {q: ("右区作战",) for q in sorted(base4 - set(dots4))}
    c4b, e4b, m4b = set(), [], {}
    _x4, _n4 = BS.right_zone(4, g4b, c4b, e4b, lm=8, meta=m4b, dots=dots4)
    ok_dots = (m4b.get("topology") == "table"
               and len(e4b) == len(sh4["edges"])               # 穿过空地的边**保留**
               and m4b.get("empty_cells") == [(9, 2)]          # 空地格已登记
               and (9, 2) in {tuple(q) for q in _x4}           # 并进右区格集合
               and m4b.get("dropped") == [])
    # 反例：圆点报错位置（和任何形状都对不上）→ 不许硬锁，退回多解/回退
    bad = [(0, 0)]
    c4c, e4c, m4c = set(), [], {}
    BS.right_zone(4, g4b, c4c, e4c, lm=8, meta=m4c, dots=bad)
    ok_bad = m4c.get("topology") in ("ambiguous", "none")

    # ⑤ 2026-11-02：**格集相同、边集不同**（四层构型2 vs 构型3）→ 像素连线探针定案。
    #    实拍：有线对比 8–21、无线 −2.5–0.2，故探针阈值 4.5；这里用假探针测**选择逻辑**。
    sh2 = {s["name"]: s for s in BS._parse_right_shapes(4)}["构型2"]
    sh3 = {s["name"]: s for s in BS._parse_right_shapes(4)}["构型3"]
    sh2e = {tuple(sorted(((x1, 2 + a), (x2, 2 + b)))) for (x1, a), (x2, b) in sh2["edges"]}
    cells33 = {(x, y) for x in (9, 10, 11) for y in (2, 3, 4)}
    dots33 = [(10, 3), (11, 3)]
    g33 = {q: ("右区作战",) for q in sorted(cells33 - set(dots33))}
    c33a, e33a, m33a = set(), [], {}
    BS.right_zone(4, g33, c33a, e33a, lm=8, meta=m33a, dots=dots33)     # 无探针 → 多解
    c33b, e33b, m33b = set(), [], {}

    def _fake_probe(edges):
        return {tuple(sorted(e)): (12.0 if tuple(sorted(e)) in sh2e else 0.0) for e in edges}

    BS.right_zone(4, g33, c33b, e33b, lm=8, meta=m33b, dots=dots33, probe=_fake_probe)
    ok_probe = (m33a.get("topology") != "table"          # 没探针时不许猜
                and m33b.get("topology") == "table"      # 探针能定案
                and "构型2" in (m33b.get("shape") or "")
                and {tuple(sorted(e)) for e in e33b} == sh2e)

    return (no_false and four_adj_would and amb_warn and ok5 and ok_dots and ok_bad and ok_probe), (
        f"三层 table={m3.get('topology')} 假边(9,3)-(9,4)={((9,3),(9,4)) in es3}；"
        f"四层={m4.get('topology')} 有告警={amb_warn}；"
        f"五层边数={len(e5)}（应 12）；报点后 table={m4b.get('topology')}"
        f" 边 {len(e4b)}（应 {len(sh4['edges'])}，空地可穿过）={ok_dots}；"
        f"报错点不硬锁={ok_bad}；探针定案={m33b.get('shape')}（无探针时 {m33a.get('topology')}）={ok_probe}")


def t_default_values_match_rules():
    """route_solver.DEFAULT_VALUES 必须与 rules 对齐（2026-10-29 修复 #18 漂移）。

    2026-10-31 QA：旧断言只钉了 7 个键，`狭路相逢`（4 vs 10）和 `居民据点`（5 vs 10）
    就这样漏了过去——手写/漏写 values 的 scenario 会静默少算 5–6 分。
    现在改成**全表属性检查**：任何「在所有出现层上都是同一个值」的键，
    DEFAULT_VALUES 里的兜底值必须等于那个值（层间本来就会变的键不参与比较）。
    """
    import rules as R
    dv = RS.DEFAULT_VALUES
    seen = {}
    for _layer, prices in R.LAYER_PRICES.items():
        for k, v in prices.items():
            seen.setdefault(k, set()).add(v)
    drift = []
    for k, vals in seen.items():
        if len(vals) != 1 or k not in dv:
            continue
        want = next(iter(vals))
        if abs(dv[k] - want) > 1e-9:
            drift.append(f"{k} 默认 {dv[k]:g} ≠ 各层恒定 {want:g}")
    ok = not drift
    return ok, ("DEFAULT_VALUES 与 LAYER_PRICES 恒定值一致" if ok
                else "；".join(drift))


def t_run_time_limit_really_stops():
    """run(max_time) 必须**真的**停：旧写法只砍 1/1024 个节点，0.05s 限时跑了 11 秒（2026-11-02 修）。"""
    import json as _json
    import time as _t
    path = os.path.join(HERE, "examples", "L3-run3.json")
    if not os.path.exists(path):
        return False, "缺 examples/L3-run3.json"
    sc = dict(_json.load(open(path, encoding="utf-8")))
    items = [it for it in sc.get("items", [])
             if not str(it.get("name", "")).startswith("结构性原理")]
    sc["items"] = items + [{"name": "结构性原理(扫描1)", "cov": "any", "uses": 1}]
    s = RS.Solver(sc, ())
    t0 = _t.monotonic()
    total, _p, hit = s.run(max_time=0.05)
    dt = _t.monotonic() - t0
    ok = hit and s.cut_reason == "timeout" and dt < 1.5
    return ok, ("上限 0.05s → 实际 %.2fs，hit=%s，reason=%s，score=%g（旧版约 11s）"
                % (dt, hit, s.cut_reason, total))


def t_run_complete_has_no_cut_reason():
    """跑完的搜索必须 hit=False 且 cut_reason=None——不能被「撞上限」污染。

    旧版时间检查在 1024 的倍数上 `st["cut"]=True` 后只 return 当次调用，
    搜索其实跑到了底，却对外报 hit=True（solve_scan 因此把"跑完"当"截断"）。
    """
    import json as _json
    sc = _json.load(open(os.path.join(HERE, "examples", "mini-smoke.json"),
                         encoding="utf-8"))
    s = RS.Solver(sc, ())
    total, _p, hit = s.run(max_time=0)
    ok = (not hit) and s.cut_reason is None
    return ok, "score=%g hit=%s reason=%s" % (total, hit, s.cut_reason)


def _scan_cli(args, timeout=120):
    """跑一次 solve_scan.py，返回 (exit code, 合并后的输出)。"""
    import subprocess
    cmd = [sys.executable, os.path.join(HERE, "solve_scan.py")] + list(args)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout


def t_solve_scan_cov_precedence():
    """`--cov` 必须压过原型件；不传才沿用原型（2026-11-02 P2）。"""
    import solve_scan as SS
    base = {"items": [{"name": "老妈妈的融雪", "uses": 1, "cov": "line2"}]}
    d = SS.prep(base, [], 2, "老妈妈的融雪", None)["items"][-1]
    c = SS.prep(base, [], 2, "老妈妈的融雪", "adj8")["items"][-1]
    u = SS.prep({"items": []}, [], 1, "不存在的道具", None)["items"][-1]
    has_ex = "作战" in (c.get("exclude") or [])
    ok = (d["cov"] == "line2" and c["cov"] == "adj8" and has_ex and u["cov"] == "any")
    return ok, ("不传→%s；显式 adj8→%s（exclude 保留=%s）；未知件→%s"
                % (d["cov"], c["cov"], has_ex, u["cov"]))


def t_solve_scan_truncated_is_not_converged():
    """截断档不许报「不再增值」。用 --node-limit 做**确定性**截断（2026-11-02 P1）。"""
    rc, out = _scan_cli([os.path.join(HERE, "examples", "L3-regression-73.json"),
                         "--node-limit", "800", "--budget", "30",
                         "--max", "2", "--alts", "0"])
    ok = (rc == 0 and "未证最优" in out and "NODE_LIMIT" in out
          and "不再增值" not in out)
    return ok, ("rc=%d；未证最优=%s / NODE_LIMIT=%s / 误报不再增值=%s"
                % (rc, "未证最优" in out, "NODE_LIMIT" in out, "不再增值" in out))


def t_solve_scan_bound_stop_is_proof():
    """摸到可采纳上界才是证明：mini-smoke 的 k=0 必须直接报「已证/达界」。"""
    rc, out = _scan_cli([os.path.join(HERE, "examples", "mini-smoke.json"),
                         "--budget", "5", "--max", "0", "--alts", "0"])
    hit_bound = "OPTIMAL(达界)" in out
    said = "已证：总分 = 可采纳上界" in out
    return (rc == 0 and hit_bound and said), \
        "rc=%d；达界=%s；已证文案=%s" % (rc, hit_bound, said)


def t_solve_scan_prep_keeps_land():
    """solve_scan 的扫描件必须保留 only/exclude/free/cov（2026-10-29 修复 #6 后半）。"""
    import solve_scan as SS
    base = {"items": [{"name": "老妈妈的融雪", "uses": 1}]}
    sc = SS.prep(base, [], 2, "老妈妈的融雪", "any")
    ent = sc["items"][-1]
    ok = (ent["cov"] == "any" and "树根" in (ent.get("exclude") or [])
          and "作战" in (ent.get("exclude") or []))
    return ok, f"扫描件字段：{ {k: ent.get(k) for k in ('cov', 'exclude')} }（exclude 必须含 树根/作战）"


def t_observe_layer6_and_config_cells():
    """observe 六层自动返回唯一构型；apply_config.config_cells 每层查自己的表。"""
    import observe as OB
    import apply_config as AC
    name, st = OB.detect_config(6, [], {})
    ok1 = name == "唯一" and st == (2, 0)
    c2 = AC.config_cells(2, "7")
    c5 = AC.config_cells(5, "L8")
    c6 = AC.config_cells(6, "唯一")
    c4 = AC.config_cells(4, "L1")
    ok2 = bool(c2) and bool(c5) and bool(c6) and (c2 != c4)
    return ok1 and ok2, (f"六层 detect={name}/{st}；cells L2c7={len(c2)} L8={len(c5)} "
                         f"L6={len(c6)} L4L1={len(c4)}（都应非空且 L2c7≠L4L1）")


def t_apply_config_end_mode():
    """apply_config 必须认识「险路尽头模式」：终点值 0（非 -99）、block 摘除（2026-10-29 修复）。"""
    import json as _json
    import subprocess as _sp
    import tempfile
    sc = {"name": "t-endmode", "nodes": {"0,1": "起点", "3,1": "未知的诡秘",
                                         "4,0": "未知的诡秘", "4,2": "未知的诡秘"},
          "start": [0, 1], "block": ["险路尽头"], "values": {}}
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        _json.dump(sc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        r = _sp.run([sys.executable, os.path.join(HERE, "apply_config.py"), path,
                     "--layer", "1", "--config", "1", "--end-mode", "险路尽头"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = _json.load(open(path, encoding="utf-8"))
        ok = (r.returncode == 0 and out["values"].get("险路尽头") == 0
              and "险路尽头" not in (out.get("block") or [])
              and out.get("final_type") == ["险路尽头", "险路小径"]
              and out.get("end_mode") == "险路尽头")
        # 对照：全程追猎下仍是 -99、block 保留
        sc2 = dict(sc)
        _json.dump(sc2, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        r2 = _sp.run([sys.executable, os.path.join(HERE, "apply_config.py"), path,
                      "--layer", "1", "--config", "1"], capture_output=True, text=True,
                     encoding="utf-8", errors="replace")
        out2 = _json.load(open(path, encoding="utf-8"))
        ok2 = (r2.returncode == 0 and out2["values"].get("险路尽头") == -99
               and "final_type" not in out2)
        return ok and ok2, (f"终点模式 值={out['values'].get('险路尽头')} block={out.get('block')}；"
                            f"全程追猎 值={out2['values'].get('险路尽头')} "
                            f"final_type={out2.get('final_type')}")
    finally:
        os.remove(path)


def t_check_scenario_allow_extra():
    """check_scenario 的 --allow-extra：多边降级为警告放行，缺边仍一票否决。"""
    import json as _json
    import subprocess as _sp
    import tempfile
    import build_scenario as BS
    cfg = {tuple(sorted((tuple(e[0]), tuple(e[1])))) for e in BS.config_edges(3, "L8")}
    cells = {p for e in cfg for p in e}
    add = None
    for u in sorted(c for c in cells if c[0] <= 6):
        for v in sorted(c for c in cells if c[0] <= 6):
            e = (u, v) if u <= v else (v, u)
            if u != v and e not in cfg:
                add = e
                break
        if add:
            break
    sc = {"edges": [[list(a), list(b)] for a, b in cfg] + [[list(add[0]), list(add[1])]]}
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        _json.dump(sc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        base_cmd = [sys.executable, os.path.join(HERE, "check_scenario.py"), path,
                    "--layer", "3", "--config", "L8"]
        r1 = _sp.run(base_cmd, capture_output=True, text=True,
                     encoding="utf-8", errors="replace")
        r2 = _sp.run(base_cmd + ["--allow-extra", "临时新边"], capture_output=True, text=True,
                     encoding="utf-8", errors="replace")
        return r1.returncode == 1 and r2.returncode == 0, (
            f"多边无旗 exit={r1.returncode}（应 1）；--allow-extra exit={r2.returncode}（应 0）")
    finally:
        os.remove(path)


def t_examples_conform_configs():
    """7 份 L3 示例（幽灵边清理后）必须与构型表逐条一致——别再把错边当模板复制。"""
    import build_scenario as BS
    import check_scenario as CS
    files = [("L3-L4-R3.json", "L4")] + [(f, "L8") for f in (
        "L3-L8-R2.json", "L3-L8-nocombat.json", "L3-L8-nofight0.json",
        "L3-L8-nofight3.json", "L3-L8-noport-e2.json", "L3-L8-noport.json")]
    bad = []
    for fn, cfg in files:
        sc = _json_load(os.path.join(HERE, "examples", fn))
        cfg_e = {CS.norm(e) for e in BS.config_edges(3, cfg)}
        sc_e = {CS.norm(e) for e in sc["edges"]}
        left = {e for e in cfg_e if e[0][0] <= 6 and e[1][0] <= 6}
        sc_l = {e for e in sc_e if e[0][0] <= 6 and e[1][0] <= 6}
        if left - sc_l or sc_l - left:
            bad.append(f"{fn}(多{len(sc_l - left)}/少{len(left - sc_l)})")
    return not bad, ("7 份示例左区边全部与构型表一致" if not bad else "；".join(bad))


def t_layer12_hazard_inference():
    """一层/二层普通分队的凶戾期望：无理想源 = 3+6/k，报了树根 = 普通作战 3。

    2026-10-29 修复：
      · 一层此前恒按 3.0（只实现了「有理想源」那一支）；
      · 二层普通分队分支因 `infer_unknown` 的 `layer not in BANDS` 早退，整段是死代码。
    """
    import rules as R
    import build_scenario as BS

    def haz(layer, root, mode):
        R.set_mode(mode)
        nodes = {"0,0": "起点", "1,0": "未知的凶戾", "2,0": "未知的凶戾"}
        vals = {"凶戾": 3.0, "未知的凶戾": 3.0}
        notes = BS.infer_unknown(layer, nodes, vals, [], (0, 0), 4, root)[1]
        return nodes, vals, notes

    try:
        _n1, v1, q1 = haz(1, None, None)
        ok1 = v1.get("未知的凶戾") == 6.0 and any("一层" in x for x in q1)
        n1r, v1r, _ = haz(1, (0, 0), None)
        ok1r = v1r.get("普通作战") == 3 and n1r["1,0"] == "普通作战"
        _n2, v2, q2 = haz(2, None, "普通分队")
        ok2 = v2.get("未知的凶戾") == 6.0 and any("二层" in x for x in q2)
        n2r, v2r, _ = haz(2, (0, 0), "普通分队")
        ok2r = v2r.get("普通作战") == 3 and n2r["1,0"] == "普通作战"
        _n2c, v2c, _ = haz(2, None, None)   # 文明开化二层：唯一紧急=理想源，维持 3.0
        ok2c = v2c.get("未知的凶戾") == 3.0
    finally:
        R.set_mode(None)
    ok = all((ok1, ok1r, ok2, ok2r, ok2c))
    return ok, (f"一层 无根={v1.get('未知的凶戾')}/有根={v1r.get('普通作战')}；"
                f"二层普通 无根={v2.get('未知的凶戾')}/有根={v2r.get('普通作战')}；"
                f"二层文明={v2c.get('未知的凶戾')}")


def t_apply_config_end_enemy():
    """三/五/六层终点模式：apply_config 也要把「险路恶敌」改成 0 并从 block 摘除。

    2026-10-29 修复：以前只处理「险路尽头」，三/五/六层的终点类型是恶敌，
    手工管线跑终点模式时它仍是 -6 且留在 block 里，final_type 永远满足不了。
    """
    import json as _json
    import subprocess as _sp
    import tempfile
    sc = {"name": "t-end-enemy", "nodes": {"0,0": "起点", "5,2": "险路恶敌"},
          "start": [0, 0], "block": ["险路恶敌", "空地"],
          "values": {"险路恶敌": -6, "险路尽头": -99}}
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        _json.dump(sc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        r = _sp.run([sys.executable, os.path.join(HERE, "apply_config.py"), path,
                     "--layer", "3", "--config", "L4", "--end-mode", "险路尽头"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = _json.load(open(path, encoding="utf-8"))
        blk = out.get("block") or []
        ok = (r.returncode == 0 and out["values"].get("险路恶敌") == 0
              and "险路恶敌" not in blk and "空地" in blk
              and out.get("final_type") == ["险路恶敌"])
        return ok, (f"恶敌值={out['values'].get('险路恶敌')} block={blk} "
                    f"final_type={out.get('final_type')}（应 0 / 只摘恶敌 / [险路恶敌]）")
    finally:
        os.remove(path)


def t_ending_types_and_mode_alias():
    """2026-10-30 玩家新规：一/四层「险路小径」与尽头同待遇 + 分队模式改名 N16文明开化。

    锁五条（**二层是小径的例外**，玩家 2026-10-30 修正）：
      ① `rules.LAYER_ENDING` 一/四层是**元组**（尽头 + 小径）；**二层只有尽头**；三/五/六层只有恶敌；
      ② 同分值：一/四层 尽头=小径=0；**二层 尽头=8、小径=0**（小径拿不到四结局藏品）；
      ③ **险路小径永不进 block**（任何层、两种终点模式）；
      ④ 模式别名：`--mode N16文明开化` 归一成内部短名「文明开化」，判词显示名带 N16；
      ⑤ `apply_config` 跑**二层**终点模式：`final_type` 只有尽头、小径价值被写回 0。
    """
    import json as _json
    import subprocess as _sp
    import tempfile
    import rules as R
    import build_scenario as BS

    # 本用例锁「未回答四结局」时的旧口径兜底；显式清掉别的用例可能留下的状态。
    R.set_four_ending(None)
    R.set_four_ending_pre(None)

    ok1 = (R.ending_types(1) == ("险路尽头", "险路小径")
           and R.ending_types(2) == ("险路尽头",)
           and R.ending_types(4) == ("险路尽头", "险路小径")
           and R.ending_types(3) == ("险路恶敌",)
           and R.ending_types(5) == ("险路恶敌",)
           and R.ending_types(6) == ("险路恶敌",))
    ok2 = (R.ending_final_value(1, "险路小径") == 0
           and R.ending_final_value(4, "险路尽头") == 0
           and R.ending_final_value(2, "险路尽头") == 8
           and R.ending_final_value(2, "险路小径") == 0
           and R.ending_final_value(3, "险路恶敌") == 0)
    blocked = []
    try:
        for em in R.END_MODES:
            R.set_end_mode(em)
            for l in (1, 2, 3, 4, 5, 6):
                if "险路小径" in BS.block_types(l):
                    blocked.append(f"{em}/L{l}")
    finally:
        R.set_end_mode(None)
    ok3 = not blocked
    try:
        R.set_mode("N16文明开化")
        ok4 = (R.mode() == "文明开化" and R.mode_label() == "N16文明开化模式"
               and R.mode_label("普通分队") == "普通分队模式")
        R.set_mode("N16文明开化模式")
        ok4 = ok4 and R.mode() == "文明开化"
    finally:
        R.set_mode(None)
    # ⑤ apply_config 二层终点模式：final_type 只有尽头、小径归 0
    sc = {"name": "t-l2-small-not-final",
          "nodes": {"2,0": "起点", "4,2": "险路尽头", "3,1": "未知的诡秘"},
          "start": [2, 0], "block": ["险路尽头"], "values": {"险路小径": 8}}
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    ok5, l2_note = False, "未跑到"
    try:
        _json.dump(sc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        r = _sp.run([sys.executable, os.path.join(HERE, "apply_config.py"), path,
                     "--layer", "2", "--config", "4", "--end-mode", "险路尽头"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = _json.load(open(path, encoding="utf-8"))
        ok5 = (r.returncode == 0
               and out.get("final_type") == ["险路尽头"]
               and out["values"].get("险路小径") == 0
               and out["values"].get("险路尽头") == 8
               and "险路尽头" not in (out.get("block") or []))
        l2_note = (f"rc={r.returncode} final_type={out.get('final_type')} "
                   f"小径={out['values'].get('险路小径')} 尽头={out['values'].get('险路尽头')}")
    finally:
        os.remove(path)
    R.set_four_ending(None)
    R.set_four_ending_pre(None)
    return all((ok1, ok2, ok3, ok4, ok5)), (
        f"终点类型 L1={R.ending_types(1)} L2={R.ending_types(2)} L3={R.ending_types(3)}；"
        f"二层分值 尽头={R.ending_final_value(2, '险路尽头')}/小径={R.ending_final_value(2, '险路小径')}；"
        f"小径被 block 的组合={blocked or '无'}；模式归一={ok4}；apply_config 二层 {l2_note}")


def t_four_ending_layer2():
    """2026-10-30 新增：二层「四结局」问询驱动定价 / 终点类型 / CLI 闸。

    锁五条：
      ① 全程追猎：要 → 尽头 8；不要 → 尽头 0、不再必走。
      ② 险路尽头：要+未获取 → 终点只有尽头、小径 0；
         要+已获取 / 不要 → 小径 = 尽头 = 8，追加为可选终点。
      ③ 问询没问全时 `layer2_answer_errors` 必须报错（CLI 用它挡住求解）。
      ④ 非二层传四结局参数 → 报错（防止上一层回答串到下一层）。
      ⑤ `apply_config` 二层 + 不要 + 险路尽头：final_type 追加小径、双 8。
    """
    import json as _json
    import subprocess as _sp
    import tempfile
    import rules as R

    def reset():
        R.set_end_mode(None)
        R.set_four_ending(None)
        R.set_four_ending_pre(None)

    reset()
    ok = True
    notes = []
    try:
        # ① 全程追猎
        R.set_end_mode("全程追猎")
        R.set_four_ending("要")
        ok &= R.layer_values(2)["险路尽头"] == 8
        R.set_four_ending("不要")
        ok &= R.layer_values(2)["险路尽头"] == 0
        # ② 险路尽头
        R.set_end_mode("险路尽头")
        R.set_four_ending("要")
        R.set_four_ending_pre("未获取")
        ok &= R.ending_types(2) == ("险路尽头",)
        ok &= "险路小径" not in R.ending_hard_types(2)
        ok &= R.ending_final_value(2, "险路小径") == 0
        R.set_four_ending_pre("已获取")
        ok &= R.ending_types(2) == ("险路尽头", "险路小径")
        ok &= "险路小径" in R.ending_hard_types(2)
        ok &= R.ending_value_map(2) == {"险路尽头": 8.0, "险路小径": 8.0}
        R.set_four_ending("不要")
        R.set_four_ending_pre(None)
        ok &= R.ending_types(2) == ("险路尽头", "险路小径")
        ok &= "险路小径" in R.ending_hard_types(2)
        # ③ 问询完整性
        R.set_four_ending(None)
        ok &= bool(R.layer2_answer_errors(2))
        R.set_end_mode("险路尽头")
        R.set_four_ending("要")
        ok &= bool(R.layer2_answer_errors(2))
        R.set_four_ending_pre("未获取")
        ok &= not R.layer2_answer_errors(2)
        R.set_four_ending("不要")
        R.set_four_ending_pre("已获取")
        ok &= bool(R.layer2_answer_errors(2))
        R.set_end_mode("全程追猎")
        R.set_four_ending("要")
        R.set_four_ending_pre(None)
        ok &= not R.layer2_answer_errors(2)
        # ④ 非二层
        ok &= bool(R.layer2_answer_errors(3))
        # CLI 闸：二层未答 → rc=2，且提示先问玩家（在读到图之前就返回）
        r = _sp.run([sys.executable, os.path.join(HERE, "route.py"), "unused.png",
                     "--layer", "2", "--config", "4", "--ap", "5"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok &= (r.returncode == 2 and "先问玩家" in (r.stdout + r.stderr))
        # ⑤ apply_config 二层 + 不要 + 险路尽头
        sc = {"name": "t-l2-four-no",
              "end_mode": "险路尽头", "four_ending": False,
              "nodes": {"2,0": "起点", "4,2": "险路尽头", "3,1": "险路小径"},
              "start": [2, 0], "block": ["险路尽头"], "values": {}}
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            _json.dump(sc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
            r2 = _sp.run([sys.executable, os.path.join(HERE, "apply_config.py"), path,
                          "--layer", "2", "--config", "4", "--end-mode", "险路尽头",
                          "--four-ending", "不要"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
            out = _json.load(open(path, encoding="utf-8"))
            ok5 = (r2.returncode == 0
                   and out.get("final_type") == ["险路尽头", "险路小径"]
                   and out.get("hard_final_types") == ["险路尽头", "险路小径"]
                   and out["values"].get("险路尽头") == 8
                   and out["values"].get("险路小径") == 8)
            ok &= ok5
            notes.append(f"apply_config rc={r2.returncode} final={out.get('final_type')} "
                         f"hard={out.get('hard_final_types')} "
                         f"值={out['values'].get('险路尽头')}/{out['values'].get('险路小径')}")
        finally:
            os.remove(path)
    finally:
        reset()
    return ok, "；".join(notes) or "定价 / 终点类型 / 问询校验全过"


def t_three_ending_layer3():
    """2026-10-30 玩家新增：三层·无右区 → 三结局问询驱动「先行一步」定价/必走/候选。

    锁六条：
      ① 无右区 + 未答 → `layer3_answer_errors(3, False)` 报 Q1；答「要」缺 Q2 → 报 Q2。
      ② 有右区 → 不问；传了三结局参数算错（这道题只属于无右区局）。
      ③ 非三层传参数 → 报错（防串层；CLI 在读图之前就挡）。
      ④ 要 + 未获取 → `layer_values(3)["先行一步"]=15`，`derive_must` 把它锁进必走；
         不要 / 已获取 → 仍 2 分、不进 must。
      ⑤ `xianxing_candidates` 只列距起点 2–4 的未知诡秘；已探明 → 空；visited 排除。
      ⑥ CLI 早校验：非三层传 --three-ending → route.py exit 2（不需要读图/OCR）。
    """
    import subprocess as _sp
    import rules as R
    import build_scenario as BS

    def reset():
        R.set_end_mode(None)
        R.set_three_ending(None)
        R.set_three_ending_pre(None)

    reset()
    ok = True
    notes = []
    try:
        # ① 问询完整性（三层无右区）
        ok &= bool(R.layer3_answer_errors(3, False))
        R.set_three_ending("要")
        ok &= bool(R.layer3_answer_errors(3, False))
        R.set_three_ending_pre("未获取")
        ok &= not R.layer3_answer_errors(3, False)
        # ② 有右区：答了算错、没答不问
        ok &= bool(R.layer3_answer_errors(3, True))
        R.set_three_ending(None)
        R.set_three_ending_pre(None)
        ok &= not R.layer3_answer_errors(3, True)
        # ③ 非三层传参数 → 报错
        R.set_three_ending("要")
        ok &= bool(R.layer3_scope_errors(2)) and bool(R.layer3_scope_errors(4))
        ok &= not R.layer3_scope_errors(3)
        # ④ 定价 + 必走
        R.set_three_ending_pre("未获取")
        ok &= R.three_ending_need_xianxing()
        ok &= R.layer_values(3).get("先行一步") == 15
        nodes = {"0,0": "起点", "2,0": "先行一步", "3,0": "未知的诡秘"}
        ok &= (2, 0) in BS.derive_must(3, nodes)
        R.set_three_ending_pre("已获取")
        ok &= not R.three_ending_need_xianxing()
        ok &= R.layer_values(3).get("先行一步", 2) == 2
        ok &= (2, 0) not in BS.derive_must(3, nodes)
        R.set_three_ending("不要")
        R.set_three_ending_pre(None)
        ok &= R.layer_values(3).get("先行一步", 2) == 2
        ok &= (2, 0) not in BS.derive_must(3, nodes)
        # ⑤ 候选坐标（三层先行一步距离带 2–4）
        R.set_three_ending("要")
        R.set_three_ending_pre("未获取")
        edges = [[[0, 0], [1, 0]], [[1, 0], [2, 0]], [[2, 0], [3, 0]],
                 [[3, 0], [4, 0]], [[4, 0], [5, 0]]]
        cand_nodes = {"0,0": "起点", "1,0": "未知的诡秘", "2,0": "未知的诡秘",
                      "3,0": "诡秘@3,0", "4,0": "未知的诡秘", "5,0": "未知的诡秘"}
        cand = BS.xianxing_candidates(3, cand_nodes, edges, (0, 0))
        ok &= [q for q, _d in cand] == [(2, 0), (3, 0), (4, 0)]
        ok &= BS.xianxing_candidates(3, cand_nodes, edges, (0, 0),
                                     visited=[(2, 0)]) == [((3, 0), 3), ((4, 0), 4)]
        revealed = dict(cand_nodes)
        revealed["2,0"] = "先行一步"
        ok &= BS.xianxing_candidates(3, revealed, edges, (0, 0)) == []
        # ⑥ CLI 早校验（不需要读图）
        r = _sp.run([sys.executable, os.path.join(HERE, "route.py"), "unused.png",
                     "--layer", "4", "--config", "L1", "--ap", "6",
                     "--three-ending", "要"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok &= (r.returncode == 2 and "只在三层" in (r.stdout + r.stderr))
        notes.append(f"候选={[(q, d) for q, d in cand]}；"
                     f"CLI rc={r.returncode}（应 2）")
    finally:
        reset()
    return ok, "；".join(notes) or "三结局问询 / 定价 / 必走 / 候选全过"


def t_l6_source_variant():
    """三结局六层「源流交汇处」：6×5、起点 (0,2)、17 边/14 格、两种终点模式（2026-10-30）。

    锁五条：
      ① `layer_dims(6,"源流")=(6,5)`，盲者仍是 (5,4)；
      ② `config_edges(6,"源流")` = 17 条边 / 14 格（与玩家 xlsx 一致），起点 (0,2)；
      ③ `config_fixed(6,"源流")` 六个定点（命运所指/作战/恶敌/不期/行商/起点）；
      ④ 全程追猎：险路恶敌 blocked，路线不会踩 (3,2)，最后一步≠恶敌；
      ⑤ 险路尽头：恶敌按 0 分终点，最后一步 = (3,2)。
    """
    import label_ocr as LO
    import rules as R
    import build_scenario as BS
    import apply_config as AC

    ok = (LO.layer_dims(6, "源流") == (6, 5)
          and LO.layer_dims(6, "唯一") == (5, 4))
    edges = BS.config_edges(6, "源流")
    cells = {p for e in edges for p in e}
    ok = ok and len(edges) == 17 and len(cells) == 14
    ok = ok and BS.config_start(6, "源流") == (0, 2)
    _ends, fixed = AC.config_fixed(6, "源流")
    ok = ok and (fixed.get((1, 2)) == "命运所指" and fixed.get((3, 0)) == "作战"
                 and fixed.get((3, 2)) == "险路恶敌" and fixed.get((3, 4)) == "不期而遇"
                 and fixed.get((5, 2)) == "诡意行商")
    # 2026-10-31 玩家裁定：源流**不夹**加工品次数（只有四结局「盲目者所见」每件 1 次）
    _src_items, _src_notes = R.parse_items_spec("标准引擎:2;气垫底座:1", 6, "源流")
    ok = ok and [(i["name"], i["uses"]) for i in _src_items] == [
        ("标准引擎", 6), ("气垫底座", 2)]
    ok = ok and any("不夹次数" in n for n in _src_notes)
    _blind_items, _ = R.parse_items_spec("标准引擎:2;气垫底座:1", 6, "唯一")
    ok = ok and [i["uses"] for i in _blind_items] == [2, 1]

    # 2026-10-30 玩家口径：源流的无标签格是**林间空地**（可通行、0 分），
    # 不能按六层「空地=墙」删掉；盲目者所见仍 block 空地
    ok = ok and ("空地" not in BS.block_types(6, "源流"))
    ok = ok and ("空地" in BS.block_types(6, "唯一"))
    # 不带橡果的空地 = 墙；带橡果的 (4,3) 保留
    _walls = BS.source_wall_cells(
        {"2,3": "空地", "3,1": "空地", "4,1": "空地", "4,3": "空地", "2,2": "安全的角落"},
        [(4, 3)])
    ok = ok and _walls == {"2,3", "3,1", "4,1"}

    # 2026-10-30 玩家口径：源流的林间空地**除带橡果徽章外全部 block**（与盲目者同口径）。
    # 这里模拟 route/build 的处理：把 (2,3)/(3,1)/(4,1) 从图里删掉，(4,3)（迷藏）保留。
    wall = {(2, 3), (3, 1), (4, 1)}
    edges = [e for e in edges if tuple(e[0]) not in wall and tuple(e[1]) not in wall]
    nodes = {"0,2": "起点", "1,2": "命运所指", "2,2": "安全的角落", "3,2": "险路恶敌",
             "4,2": "得偿所愿", "5,2": "诡意行商", "3,4": "不期而遇", "3,3": "不期而遇",
             "2,1": "作战", "3,0": "作战", "4,3": "空地"}
    values = {"起点": 0, "命运所指": 0, "安全的角落": 0, "得偿所愿": 2,
              "诡意行商": 12, "不期而遇": 2, "作战": 3, "空地": 0,
              "险路恶敌": -6}
    try:
        R.set_end_mode("全程追猎")
        sc = base_sc(edges=[[list(a), list(b)] for a, b in edges], nodes=nodes,
                     start=[0, 2], ap=5, values=dict(values),
                     block=BS.block_types(6, "源流"), end_mode="全程追猎",
                     acorn=[[4, 3]])
        s = RS.Solver(sc)
        _t, path, _h = s.run(max_time=5)
        full = {q for q, _n, _g in path}
        ok = ok and (3, 2) not in full and path and path[-1][0] != (3, 2)
        R.set_end_mode("险路尽头")
        v2 = dict(values)
        v2["险路恶敌"] = 0          # 终点模式：恶敌是终点，按 0 分
        sc2 = base_sc(edges=[[list(a), list(b)] for a, b in edges], nodes=nodes,
                      start=[0, 2], ap=5, values=v2, block=[],
                      end_mode="险路尽头", final_type=["险路恶敌"],
                      hard_final_types=["险路恶敌"])
        s2 = RS.Solver(sc2)
        _t2, path2, _h2 = s2.run(max_time=5)
        ok = ok and bool(path2) and path2[-1][0] == (3, 2)
    finally:
        R.set_end_mode(None)
    return ok, (f"边 {len(edges)}/格 {len(cells)}/起点 {BS.config_start(6, '源流')}；"
                f"源流道具 {[(i['name'], i['uses']) for i in _src_items]}（应默认耐久）；"
                f"追猎末格 {path[-1][0] if path else None}（应非 (3,2)）；"
                f"尽头末格 {path2[-1][0] if path2 else None}（应 (3,2)）")


def t_badge_glyph():
    """徽章字形模板（NCC）：6 个 24×24 标本自匹配必须 ≥0.95 且认对名字。

    2026-10-30 用真实截图裁的标本：骷髅/蜘蛛/羊角/狼头/橡果/乌鸦。
    `泪之聚落` 定价 0、三层线人 = 10。
    """
    import pixel_probe as PP
    from PIL import Image
    base = os.path.join(HERE, "examples")
    names = ("tears", "informant", "ram", "wolf", "acorn", "raven")
    files = {n: os.path.join(base, "glyph-%s.png" % n) for n in names}
    if not all(os.path.exists(p) for p in files.values()):
        return True, "跳过（缺 glyph-*.png 标本）"
    bad, tails = [], []
    for n in names:
        im = Image.open(files[n]).convert("RGB")
        g = PP.badge_glyph(im, im.width / 2.0, im.height / 2.0,
                           im.width / 2.0, search=0)
        tails.append(f"{n}:{g['glyph']}({g['score']})")
        if g["glyph"] != n or g["score"] < 0.95:
            bad.append(n)
    import rules as R
    ok = (not bad
          and R.layer_values(3).get("泪之聚落") == 0
          and R.layer_values(3).get("不期而遇线人") == 10)
    return ok, " ".join(tails) + f"；泪之聚落={R.layer_values(3).get('泪之聚落')}"


def t_badge_merged():
    """贴图标被合并的徽章也要认出来（2026-10-30 五层实拍）。

    标本 `badge-merged-gain.png` 是五层 (3,2) 失与得的 120×120 原图裁片：
    羊角徽章与失与得的青色光晕在像素级**连成一块**（44×30、544 白像素），
    旧版按「方正白块」直接丢掉；现在在块内扫字形 → ram 0.96。
    """
    import pixel_probe as PP
    from PIL import Image
    f = os.path.join(HERE, "examples", "badge-merged-gain.png")
    if not os.path.exists(f):
        return True, "跳过（缺 badge-merged-gain.png 标本）"
    im = Image.open(f).convert("RGB")
    PP.set_scale(121)
    got = PP.detect_badges(im, [40], [79], 1, 1)   # 单格：图标 (40,79)
    ok = bool(got) and got[0]["cell"] == (0, 0)
    if ok:
        ok = (got[0]["glyph"]["glyph"] == "ram" and got[0]["glyph"]["score"] >= 0.80)
    return ok, ([(it["cell"], it["glyph"]["glyph"], it["glyph"]["score"]) for it in got]
                or "未检出（合并块没有扫出羊角）")


def t_acorn_value():
    """迷藏（橡果）：**收益 +12 并进所在节点**，带橡果的空地不再是墙（2026-10-30 新口径）。

    三组断言（旧口径「不改价、同分优先」的 ACORN_EPS 已删除，这里锁新行为）：
      ① `cell_values` 生效：诡秘(5) 带橡果 → 17，AP=1 时选它（不是选另一格 5 分）；
      ② 只写 `acorn` 不写 `cell_values` 时求解器也按 rules.acorn_value() 兜底 +12；
      ③ **带橡果的空地**：类型在 block 里，靠 `block_except` 放行、地基按 0 起算
         → 12 分（不是 -99+12=-87），且能走进去（右边那格诡秘 5 靠它当通路）。
    """
    # ① cell_values 优先
    sc = base_sc(
        edges=[[[0, 0], [1, 0]], [[0, 0], [0, 1]]],
        nodes={"0,0": "起点", "1,0": "诡秘", "0,1": "诡秘"},
        start=[0, 0], ap=1, values={"起点": 0, "诡秘": 5},
        acorn=[[0, 1]], cell_values={"0,1": 17},
    )
    s = RS.Solver(sc)
    tot, path, _hit = s.run(max_time=5)
    ok1 = (bool(path) and path[-1][0] == (0, 1) and abs(tot - 17) < 1e-9
           and getattr(s, "acorn_hits", 0) == 1)

    # ② 旧场景（只有 acorn 列表）也要按 12 兜底
    sc2 = dict(sc)
    sc2.pop("cell_values", None)
    s2 = RS.Solver(sc2)
    tot2, path2, _hit2 = s2.run(max_time=5)
    ok2 = (bool(path2) and path2[-1][0] == (0, 1) and abs(tot2 - 17) < 1e-9)

    # ③ 空地 + 橡果：block 类型 + 本格豁免 + 地基 0
    sc3 = base_sc(
        edges=[[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
        nodes={"0,0": "起点", "1,0": "空地", "2,0": "诡秘"},
        start=[0, 0], ap=2, values={"起点": 0, "空地": -99, "诡秘": 5},
        block=["空地"], acorn=[[1, 0]],
        cell_values={"1,0": 12}, block_except=[[1, 0]],
    )
    s3 = RS.Solver(sc3)
    tot3, path3, _hit3 = s3.run(max_time=5)
    ok3 = (bool(path3) and [q for q, _n, _g in path3] == [(1, 0), (2, 0)]
           and abs(tot3 - 17) < 1e-9)

    # ④ 建场景侧的三个 helper 必须同口径（这两处最容易各写一份数）
    import build_scenario as BS
    _nodes = {"0,0": "起点", "1,0": "空地", "2,0": "诡秘", "3,0": "作战"}
    _vals = {"起点": 0, "空地": -99, "诡秘": 5, "作战": 0}
    _cv = BS.acorn_cell_values(_nodes, _vals, [(1, 0), (3, 0)])
    _exc = BS.acorn_block_except(_nodes, ["空地", "曲折密道"], [(1, 0), (3, 0)])
    _waste = BS.acorn_wasted_cells({"0,0": "起点", "1,0": "曲折密道"},
                                   ["曲折密道"], [(1, 0)])
    ok4 = (_cv == {"1,0": 12.0, "3,0": 12.0}       # 空地 -99 按 0 起算 → 12
           and _exc == [[1, 0]]                     # 只放行空地
           and _waste == [((1, 0), "曲折密道")])    # 密道上的橡果要报「吃不到」

    return ok1 and ok2 and ok3 and ok4, (
        f"①cell_values：末格={path[-1][0] if path else None} 总分={tot:g}；"
        f"②acorn 兜底：总分={tot2:g}；"
        f"③空地+橡果：路径={[q for q, _n, _g in path3] if path3 else None} 总分={tot3:g}；"
        f"④helpers：cell_values={_cv} block_except={_exc} wasted={_waste}")


def t_acorn_pass_policy():
    """橡果的**放行 / 照旧禁踩**策略（玩家 2026-10-30 逐条口径）。

    · 放行：空地 / 曲折密道（配对成功）/ 险路尽头
    · 不放：险路恶敌（全程追猎）/ 三·四层命运所指
    直接锁 build_scenario 的三个 helper —— 这三条口径最容易在别处被抄错。
    """
    import build_scenario as BS
    nodes = {"0,0": "起点", "1,0": "空地", "2,0": "险路尽头", "3,0": "险路恶敌",
             "4,0": "曲折密道", "5,0": "曲折密道", "6,0": "命运所指",
             "7,0": "曲折密道"}
    block = ["空地", "险路尽头", "险路恶敌", "曲折密道", "命运所指"]
    acorn = [(1, 0), (2, 0), (3, 0), (4, 0), (6, 0), (7, 0)]
    vals = {"起点": 0, "空地": 0, "险路尽头": 0, "险路恶敌": -6,
            "曲折密道": -99, "命运所指": -99}
    pairs, notes = BS.acorn_portal_pairs(nodes, acorn)
    # (4,0) 与 (7,0) 都是密道、都带橡果 → 本层 3 个密道格 → 配对不确定 → 不放行
    ok1 = (pairs == [] and notes and "暂不放行" in notes[0])
    # 砍掉第三个密道格 → 配对成功（4,0 ↔ 5,0）
    nodes2 = {k: v for k, v in nodes.items() if k != "7,0"}
    pairs2, notes2 = BS.acorn_portal_pairs(nodes2, acorn)
    ok2 = pairs2 == [((4, 0), (5, 0))] and notes2 and "放行" in notes2[0]
    exc = BS.acorn_block_except(nodes2, block, acorn, pairs2)
    ok3 = exc == [[1, 0], [2, 0], [4, 0]]        # 空地 / 尽头 / 密道 放行
    waste = BS.acorn_wasted_cells(nodes2, block, acorn, pairs2)
    ok4 = waste == [((3, 0), "险路恶敌"), ((6, 0), "命运所指")]
    cv = BS.acorn_cell_values(nodes2, vals, acorn, pairs2)
    ok5 = (cv.get("1,0") == 12.0 and cv.get("2,0") == 12.0
           and cv.get("4,0") == 12.0 and cv.get("5,0") == 12.0)   # 密道两端同价
    return ok1 and ok2 and ok3 and ok4 and ok5, (
        f"配对失败={ok1}；配对成功={ok2}；放行={exc}；吃不到={waste}；两端同价={cv}")


def t_acorn_portal():
    """带橡果的曲折密道：放行 + 12 分 + **强制传送** + 两端只结算一次。

    图：0,0 起点 —(边)— 1,0 密道A …(传送)… 3,0 密道B —(边)— 4,0 诡秘
    两端各 12 分（结算格是落点）。AP=2 时应走 1,0 → 落 3,0（得 12）→ 4,0（得 5）= 17；
    AP=3 时**不能**再从 3,0 刷一次 12（两端 bit 一起置位），总分仍是 17。
    """
    sc = base_sc(
        edges=[[[0, 0], [1, 0]], [[3, 0], [4, 0]]],
        nodes={"0,0": "起点", "1,0": "曲折密道", "3,0": "曲折密道", "4,0": "诡秘"},
        start=[0, 0], ap=2, values={"起点": 0, "曲折密道": -99, "诡秘": 5},
        portals=[[[1, 0], [3, 0]]],
        cell_values={"1,0": 12, "3,0": 12}, acorn=[[1, 0]],
    )
    s = RS.Solver(sc)
    tot, path, _hit = s.run(max_time=5)
    cells = [q for q, _n, _g in path]
    ok1 = (cells == [(3, 0), (4, 0)] and abs(tot - 17) < 1e-9
           and getattr(s, "acorn_hits", 0) == 1)
    sc3 = dict(sc, ap=3)
    s3 = RS.Solver(sc3)
    tot3, _p3, _h3 = s3.run(max_time=5)
    ok2 = abs(tot3 - 17) < 1e-9          # 不许把两端各算一次（那会变 29）
    # 手写场景只写 acorn（不写 cell_values）时，对端也要按 12 兜底
    sc4 = dict(sc)
    sc4.pop("cell_values")
    s4 = RS.Solver(sc4)
    tot4, _p4, _h4 = s4.run(max_time=5)
    ok3 = abs(tot4 - 17) < 1e-9
    fmt = s.fmt(path)
    ok4 = "强制传送" in fmt               # 打印必须告诉玩家「踩的是哪一格」
    return ok1 and ok2 and ok3 and ok4, (
        f"AP2：{cells} 总分={tot:g}；AP3 再刷：总分={tot3:g}；"
        f"仅 acorn 兜底：总分={tot4:g}；fmt 含强制传送={ok4}")


def t_acorn_soft_final():
    """带橡果的险路尽头（险路尽头模式）→ **软终点**：可当中继继续走。

    0,0 起点 → 1,0 险路尽头（0 分，带橡果 → 12）→ 2,0 诡秘(5) → 3,0 险路尽头(0)，AP=3。
    · 软终点（`final_soft_types`）→ 1,0 当中继，边走边收，末格仍是终点类型：**17、3 步**；
    · 仍按硬终点（不传 `final_soft_types`）→ 进 1,0 就结束：**12、1 步**。
    玩家口径「可作中继、但建议留给最后一步」就是这两种行为的差别。
    """
    base = dict(
        edges=[[[0, 0], [1, 0]], [[1, 0], [2, 0]], [[2, 0], [3, 0]]],
        nodes={"0,0": "起点", "1,0": "险路尽头", "2,0": "诡秘", "3,0": "险路尽头"},
        start=[0, 0], ap=3,
        values={"起点": 0, "险路尽头": 0, "诡秘": 5},
        end_mode="险路尽头", final_type=["险路尽头"],
        cell_values={"1,0": 12}, acorn=[[1, 0]],
    )
    s = RS.Solver(base_sc(final_soft_types=["险路尽头"], **base))
    tot, path, _hit = s.run(max_time=5)
    last_t = s.nodes.get(path[-1][0]) if path else None
    ok1 = abs(tot - 17) < 1e-9 and len(path) == 3 and last_t == "险路尽头"
    s2 = RS.Solver(base_sc(**base))
    tot2, path2, _hit2 = s2.run(max_time=5)
    ok2 = abs(tot2 - 12) < 1e-9 and len(path2) == 1
    return ok1 and ok2, (f"软终点：总分={tot:g} 步数={len(path)} 末格={path[-1][0] if path else None}；"
                         f"硬终点：总分={tot2:g} 步数={len(path2)}")


def t_layer2_small_path_hard_endpoint():
    """二层「小径=8」必须是**硬终点**：进入即结束，不能小径 8 + 尽头 8 重复计。

    构造：起点→小径(8)→诡秘(5)→尽头(8)，AP=3。
      · 若小径按软终点（旧逻辑），最优会走到尽头，得 21（8+5+8）→ 重复计分；
      · 硬终点（hard_final_types 含小径）应停在 1,0，得 8。
    """
    sc = base_sc(
        edges=[[[0, 0], [1, 0]], [[1, 0], [2, 0]], [[2, 0], [3, 0]]],
        nodes={"0,0": "起点", "1,0": "险路小径", "2,0": "诡秘", "3,0": "险路尽头"},
        start=[0, 0], ap=3,
        values={"起点": 0, "险路小径": 8, "诡秘": 5, "险路尽头": 8},
        end_mode="险路尽头",
        final_type=["险路尽头", "险路小径"],
        hard_final_types=["险路尽头", "险路小径"],
    )
    s = RS.Solver(sc)
    tot, path, _hit = s.run(max_time=5)
    ok = (abs(tot - 8) < 1e-9 and path and path[-1][0] == (1, 0)
          and len(path) == 1)
    return ok, f"总分={tot} 末格={path[-1][0] if path else None} 步数={len(path)}"


def t_small_path_soft_final():
    """险路小径是**软终点**：可停可续（2026-10-30 玩家新规；一/四层，及二层两种四结局分支）。

    两条断言（差值都在 5 分以上，不会因浮点/顺序误判）：
      ① **可续**：起点→小径→诡秘(必走)→尽头，必须走满 4 步、末格是尽头、必走点命中。
         若小径被当成硬终点，路线会在小径提前收尾 → 必走点走不到。
         分值用**合成口径**（小径/尽头都给 8，只测机制，不代表任何一层的真实定价）：
         走满 = 8+5+8 = 21，在小径提前收尾只有 8，两种解差 13 分，不会被同分平局搅乱。
      ② **可停**：尽头不可达（没有边）时，停在小径 = 合法收尾（不是「无可行解」）。
    """
    sc = base_sc(
        edges=[[[0, 0], [1, 0]], [[1, 0], [2, 0]], [[2, 0], [3, 0]]],
        nodes={"0,0": "起点", "1,0": "险路小径", "2,0": "诡秘", "3,0": "险路尽头"},
        start=[0, 0], ap=4,
        values={"起点": 0, "险路小径": 8, "诡秘": 5, "险路尽头": 8},
        must=[[2, 0]],
        end_mode="险路尽头", final_type=["险路尽头", "险路小径"],
    )
    s = RS.Solver(sc)
    tot, path, _hit = s.run(max_time=5)
    thru_ok = (abs(tot - 21) < 1e-9 and path and path[-1][0] == (3, 0)
               and not s.must_dropped)

    sc2 = base_sc(
        edges=[[[0, 0], [1, 0]]],
        nodes={"0,0": "起点", "1,0": "险路小径", "2,0": "险路尽头"},
        start=[0, 0], ap=2,
        values={"起点": 0, "险路小径": 8, "险路尽头": 8},
        end_mode="险路尽头", final_type=["险路尽头", "险路小径"],
    )
    tot2, path2, _hit2 = RS.Solver(sc2).run(max_time=5)
    stop_ok = (path2 and path2[-1][0] == (1, 0) and abs(tot2 - 8) < 1e-9)
    return thru_ok and stop_ok, (
        f"可续：总分 {tot:g}（应 21 = 8+5+8）、末格 {path[-1][0] if path else None}（应 (3, 0)）、"
        f"必走回落={s.must_dropped}（应 False）；"
        f"可停：末格 {path2[-1][0] if path2 else None}（应 (1, 0)）、总分 {tot2:g}（应 8）")


def t_input_errors_are_clean():
    """坐标/道具写错：route.py 判 ❌（exit 3），不能 traceback；数量错误给中文说明。"""
    import subprocess as _sp
    import rules as R
    img = os.path.join(HERE, "examples", "scene-L3-real.png")
    r = _sp.run([sys.executable, os.path.join(HERE, "route.py"), img,
                 "--layer", "3", "--config", "L4", "--ap", "6", "--root", "abc"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok1 = (r.returncode == 3 and "坐标格式错误" in r.stdout
           and "Traceback" not in (r.stderr or ""))
    try:
        R.parse_items_spec("标准引擎:abc")
        ok2 = False
    except ValueError as exc:
        ok2 = "不是数字" in str(exc)
    return ok1 and ok2, f"route.py rc={r.returncode}（应 3）；items 中文说明={ok2}"


def t_grant_once_and_float_fmt():
    """展示/校验层：grant 只算首次；单格分值用 %g 打印，不许把 4.8 截成 4。"""
    import route as RT
    s = type("S", (), {})()
    s.ap0, s.items, s.grants = 2, [], {(1, 0): 1}
    ap, _used = RT._replay(s, [((1, 0), None, 0), ((1, 0), None, 0)])
    ok1 = ap == 1          # 2 −1 +1（只给一次） −1 +0
    sc = base_sc(ap=1, edges=[[[0, 0], [1, 0]]],
                 nodes={"0,0": "起点", "1,0": "诡秘"},
                 values={"起点": 0, "诡秘": 4.8})
    sv = RS.Solver(sc)
    _tot, path, _hit = sv.run(max_time=5)
    txt = sv.fmt(path)
    ok2 = "4.8" in txt
    return ok1 and ok2, f"重踩授予后 AP={ap}（应 1）；fmt 含 4.8={'4.8' in txt}"


def t_visited_cell_not_refunded():
    """**滚动重算**（2026-10-29）：`visited` 里的格子不再计分、也不再发 grant。

    事故重现（三层，真实对局）：玩家用一次性喷气背包跳进 (6,3) 打掉树根，
    拿到 +2 AP（行动力 6→8）和 4 件藏品。重算时**正确**口径是
    `--start 6,3 --ap 8 --visited "0,2;6,3"`；但当时的入口拒绝 --start 6,3
    （拿当前位置跟构型进层起点 (0,2) 比对），只好退回 `--start 0,2`——
    于是求解器把 (6,3) 树根当成**没打过**：再给 15 分、再发 +2 AP，
    判词写成「5 + 树根 2 = 7」，而玩家真实只有 8 点。

    这里锁**求解器这一侧**的口径：visited 格的分与 grant 都不再结算。
    """
    # visited 版：起点就是打过的树根，起始 AP 已含 +2（这里给 2 = "领完之后剩的"）
    sc = base_sc(
        nodes={"0,0": "起点", "1,0": "树根", "2,0": "不期而遇"},
        start=[1, 0], ap=2, grants={"1,0": 2},
        values={"起点": 0, "树根": 15, "不期而遇": 2},
        visited=[[1, 0]],
    )
    _tot, path, _hit = RS.Solver(sc).run(max_time=5)
    rescored = any(q == (1, 0) for q, _i, _g in path)   # 反过来重踩会再拿 15 分
    tot_visited = _tot

    # 对照：同一张图、不登记 visited → 树根照常计 15 分并照常发 +2（不能把正常回复砍掉）
    sc2 = base_sc(
        nodes={"0,0": "起点", "1,0": "树根", "2,0": "不期而遇"},
        start=[1, 0], ap=2, grants={"1,0": 2},
        values={"起点": 0, "树根": 15, "不期而遇": 2},
    )
    tot_fresh, _path2, _hit2 = RS.Solver(sc2).run(max_time=5)

    # 求解器口径（route_solver 第 537-538 行 `0 if seen else v/gr`）：
    #   已 seen 的格 → 0 分、0 grant；所以 visited 版最多拿到 (2,0) 的 2 分，
    #   未登记版回踩树根还能拿 15 分 → 2 + 15 = 17。
    ok = (not rescored) and abs(tot_visited - 2) < 1e-9 and abs(tot_fresh - 17) < 1e-9
    return ok, (f"visited 版：树根格再计分={rescored}（应 False）、总分 {tot_visited:g}（应 2）；"
                f"未登记对照总分 {tot_fresh:g}（应 17 = 2 + 树根15）")


def t_rolling_start_not_rejected():
    """入口层：滚动重算时 `--start`（当前所在格）**不许**被判「构型选错」。

    判据：
      ① rolling=True + --start 与进层起点不同 → 无 problem、留一条备注；
      ② rolling=False + 同样输入 → 必须报 problem（原硬校验不许被削弱）；
      ③ rolling=True 但起点不在该构型格子里 → 仍要报 problem（这条闸门必须保留）。
    """
    import route as RT

    def run(start, rolling):
        problems, notes = [], []
        cells = {(0, 2), (0, 1), (1, 2), (6, 3)}
        got, _warned = RT.resolve_start(
            start, (0, 2), True, [], {}, cells, [],
            [[[0, 2], [0, 1]]], 3, "L1", problems, notes, rolling=rolling)
        return got, problems, notes

    _s1, p1, n1 = run((6, 3), True)
    _s2, p2, _n2 = run((6, 3), False)
    _s3, p3, _n3 = run((9, 9), True)      # 不在格子集合里

    ok = (not p1) and bool(n1) and bool(p2) and bool(p3)
    return ok, (f"rolling 当前位置 (6,3)：problem {len(p1)} 条（应 0）、备注 {len(n1)} 条（应>=1）；"
                f"非 rolling：problem {len(p2)} 条（应>=1）；"
                f"rolling 但越界 (9,9)：problem {len(p3)} 条（应>=1）")


def t_apply_visited_rewrites():
    """入口层：`apply_visited` 的**正确**口径（2026-10-29 第二次修）。

    ⛔ 不许改写节点类型：树根本身就是那场紧急作战，改成「起点」会把配额账目抹掉
       （实测把 0 分的 (1,3) 抬成 9.0 并写进路线）。
    只做三件事：抽 grant、登记 visited、自动并入起点。
    """
    import route as RT
    nodes = {"6,3": "树根", "5,4": "诡秘", "0,2": "起点"}
    grants = {"6,3": 2, "5,0": 1}
    notes = []
    # 只报 (6,3)：起点 (0,2) 必须被**自动并入**（玩家一定从那里出发过）
    out = RT.apply_visited(nodes, grants, "6,3", (0, 2), notes)
    ok = (nodes.get("6,3") == "树根"        # 类型必须保留（配额账目靠它）
          and "6,3" not in grants           # grant 必须抽掉
          and grants.get("5,0") == 1        # 没走过的羽瞰点必须留着
          and nodes.get("5,4") == "诡秘"     # 没走过的节点不许改
          and (6, 3) in out and (0, 2) in out)   # 起点自动并入
    return ok, f"nodes={nodes} grants={grants} visited={out}"


def t_second_ending_layer5():
    """五层二结局（2026-10-31）：单点定价 / 终点 / 必走点 / 钳制点 一条链的口径。

    锁死的是「再改回去」最容易犯的三处：
      ① 无徽章命运所指 = 得偿所愿−1（五层 7 分），不是 8 也不是 0；
      ② 要 → final=左区带徽章那个、右区命运所指进 must；不要 → final=右区那个、带徽章那个 -99；
      ③ 未答（`second_ending=None`）时 `layer5_answer_errors(5, True)` 必须报错（exit 2）。
    """
    import rules as Rm
    import build_scenario as BS
    Rm.set_second_ending(None)
    nodes = {"2,1": "命运所指", "3,1": "命运所指", "5,3": "命运所指",
             "13,3": "命运所指", "4,0": "诡秘"}
    left, right = BS.layer5_fate_cells(nodes, 9)
    ok_cells = left == [(2, 1), (3, 1), (5, 3)] and right == [(13, 3)]
    val = Rm.second_fate_random_value(5)
    errs = Rm.layer5_answer_errors(5, True)
    Rm.set_second_ending("要")
    se_yes = BS.second_ending_setup(5, nodes, 9, fate_badge=[(3, 1)], answer=True)
    Rm.set_second_ending("不要")
    se_no = BS.second_ending_setup(5, nodes, 9, fate_badge=[(3, 1)], answer=False)
    Rm.set_second_ending(None)
    ok = (ok_cells and val == 7.0 and bool(errs)
          and se_yes["final"] == (3, 1) and se_yes["must"] == [(13, 3)]
          and se_yes["clamp"] == [(13, 3)]
          and se_yes["cell_values"].get("2,1") == 7.0
          and se_yes["cell_values"].get("3,1") == 0.0
          and se_no["final"] == (13, 3) and se_no["must"] == []
          and se_no["cell_values"].get("3,1") == -99.0
          and se_no["cell_values"].get("2,1") == 7.0)
    return ok, (f"left={left} right={right} 无徽章值={val} 未答报错={errs} "
                f"要:final={se_yes['final']} must={se_yes['must']} "
                f"不要:final={se_no['final']} cell_values={se_no['cell_values']}")


def t_clamp_items_after_fate():
    """五层二结局：进右区「命运所指」后，所有加工品剩余次数强制→1（之后的跳跃也被砍）。

    这条是「尽量排最后」的实现基础：钳制不是分值惩罚，是真把 items 压到 1。
    """
    sc = {
        "name": "clamp",
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]], [[2, 0], [3, 0]]],
        "nodes": {"0,0": "起点", "1,0": "命运所指", "2,0": "诡秘", "3,0": "诡秘"},
        "start": [0, 0], "ap": 3, "grants": {},
        "items": [{"name": "结构性原理", "uses": 3}],
        "values": {"起点": 0, "命运所指": 0, "诡秘": 5},
        "clamp_items_at": [[1, 0]],
    }
    s = RS.Solver(sc)
    walk = [m for m in s.moves((0, 0), 0, 3, (3,), ()) if m[1] == (1, 0)]
    jump_over = [m for m in s.moves((0, 0), 0, 3, (3,), ()) if m[1] == (2, 0)]
    ok1 = bool(walk) and walk[0][5] == (1,)
    ok2 = bool(jump_over) and jump_over[0][5] == (2,)
    return (ok1 and ok2,
            f"走进钳制格 items={walk[0][5] if walk else None}（应 (1,)）；"
            f"跳过它 items={jump_over[0][5] if jump_over else None}（应 (2,)）")


def t_tie_phase_proves_cost():
    """阶段 B 必须**真的跑**，并把「最省」标记为已证明。

    2026-11-02 实际踩过：重构 `run()` 时把 `proven = not b["cut"]` 弄丢，
    分数 / 步数 / 道具价全都对，只有报告文案从「这就是最省」退化成
    「可能还有更省的同分解」——`regress.py` 的四项断言抓不到这种漂移。

    顺带钉住「B 不能一进去就被剪掉」：曾经试过让 B 复用 A 的 memo 做支配剪枝，
    结果根状态早就在 A 的 memo 里 → B 第一步就 return，什么都没搜却报「已证明」。
    """
    sc = {
        "name": "tie-proven",
        "edges": [],                     # 只能靠道具跳，徒步无路
        "nodes": {"0,0": "起点", "2,0": "诡秘"},
        "start": [0, 0], "ap": 1, "grants": {},
        "items": [{"name": "报废轮子", "uses": 1},      # 直线 2 格，价 2
                  {"name": "标准引擎", "uses": 1}],     # 相邻 8 格，够不到 (2,0)
        "values": {"起点": 0, "诡秘": 10},
    }
    s = RS.Solver(sc)
    total, path, _hit = s.run()
    lc = s.last_cost
    b_expanded = s.phase_stats[1][1]["expanded"] if len(s.phase_stats) > 1 else 0
    ok = (total == 10 and lc["cost"] == 2 and lc["tie_phase"]
          and lc["cost_proven"] and b_expanded > 1)
    return ok, (f"分 {total}（应 10），价 {lc['cost']}（应 2），tie_phase={lc['tie_phase']}，"
                f"cost_proven={lc['cost_proven']}，B 展开 {b_expanded}（应 >1）")


def t_verdict_four_states():
    """四态判词的优先级必须钉死：**❌ > ⏳ > ⚠️ > ✅**（2026-11-02）。

    起因（玩家审阅第 1 条）：旧版把「撞时间/节点上限」和「无可行解」都塞进 problems，
    于是一次 `--max-time` 给少了，工具就报 `❌ 不可用`——**用搜索进度冒充数学结论**。
    现在四态各管一段，这里锁住三件事：
      1. `problems` 存在就是 ❌（exit 3），哪怕同时有 `unknown`；
      2. 只有 `unknown` 是 ⏳（exit 4）——既不许降成 ❌，也不许升成 ✅；
      3. 都没有时 ✅ 必须把「N 项输入待确认」写进**末行**（不能用一次 ✅ 把 OCR 风险盖掉）。
    """
    import route

    def call(**kw):
        base = {"problems": [], "unknown": [], "warnings": []}
        base.update(kw)
        return route.verdict(**base)

    cases = []
    line, code = call(problems=["格点拟合越出地图区"], unknown=["没搜完"])
    cases.append(("problems 压过 unknown", line is None and code == 3, f"{line} / {code}"))
    line, code = call(unknown=["预算内没找到可行解"])
    cases.append(("没搜完 = ⏳ exit4", bool(line) and line.startswith("⏳") and code == 4,
                  f"{line} / {code}"))
    line, code = call(warnings=["撞到节点上限"])
    cases.append(("未证明最优 = ⚠️ exit0",
                  bool(line) and line.startswith("⚠️") and code == 0, f"{line} / {code}"))
    line, code = call(pending=["起点（小人检测）"])
    cases.append(("已证最优 = ✅ 且末行带待确认",
                  bool(line) and line.startswith("✅ 可用") and "待确认" in line and code == 0,
                  f"{line} / {code}"))
    bad = [f"{n}（{d}）" for n, ok, d in cases if not ok]
    return (not bad), ("；".join(f"{n}✅" for n, ok, _d in cases if ok)
                       + ("；❌ " + "、".join(bad) if bad else ""))


def t_labels_confirmed():
    """`--labels-confirmed` 只许把**节点标签**升成事实（2026-11-02）。

    玩家核过标签之后，OCR 那一项不该再挂「待确认」；但这条开关**不许顺手**把
    徽章像素、右区拓扑、猜出来的起点也洗白——那三项各有各的检测，混淆它们
    等于自己给 ✅ 放水。这里锁三件事：
      · 没核过 → 待确认，且依据里写明是 OCR；
      · 核过   → 事实；
      · 图上没标签 → 根本不记这一项（None）。
    """
    import route

    a = route.label_input(False, 12)
    b = route.label_input(True, 12)
    c = route.label_input(True, 0)
    bad = []
    if not (a and a[1] == "待确认" and "OCR" in a[2]):
        bad.append(f"默认应「待确认 + OCR」：{a}")
    if not (b and b[1] == "事实" and "核对" in b[2]):
        bad.append(f"核过应升「事实」：{b}")
    if c is not None:
        bad.append(f"图上没标签应为 None：{c}")
    return (not bad), ("；".join(bad) if bad
                       else f"待确认 → 事实 ✅（{a[2]} → {b[2]}）")


def t_normal_layer5_final():
    """常规五层（左区没有命运所指）：终点仍然是**右区那一个**命运所指。

    2026-11-02 修：`second_ending_setup` 以前在 `marker=False` 时直接 return、`final` 留 None，
    route.py 于是走「五层读到命运所指但没能定出终点」分支 → **普通五层在全程追猎下必然 ❌**。
    这条锁「常规五层也有坐标型终点」，同时锁 marker 局的行为不被改坏。
    """
    import build_scenario as BS

    nodes = {"13,3": "命运所指", "2,1": "诡秘", "11,2": "右区作战"}
    se = BS.second_ending_setup(5, nodes, 9, answer=None)
    nodes2 = dict(nodes, **{"3,1": "命运所指", "5,3": "命运所指"})
    nodes2["2,1"] = "命运所指"
    se2 = BS.second_ending_setup(5, nodes2, 9, answer=False)
    bad = []
    if se["marker"]:
        bad.append("常规五层不该有 marker")
    if se["final"] != (13, 3):
        bad.append(f"常规五层 final 应为 (13,3)，实际 {se['final']}")
    if se2["final"] != (13, 3):
        bad.append(f"marker 局（不要）final 应为 (13,3)，实际 {se2['final']}")
    if se["clamp"] or se["must"]:
        bad.append(f"常规五层不该有钳制/必走点：{se['clamp']} {se['must']}")
    return (not bad), ("；".join(bad) if bad
                       else f"常规五层 final={se['final']}；marker 局 final={se2['final']} ✅")


def t_start_missing_is_clean_error():
    """小人没检出时必须是**干净判词**，不能 traceback（2026-11-02）。

    两个实际崩溃点：
      ① 滚动模式 `--visited` + 无起点 → `resolve_start` 里 `tuple(None)` → TypeError；
      ② 判词「输入可信度」`_start_weak` 里 `tuple(start)` → TypeError
         （二层构型1 的 (0,0) 恰好是合法格时，会一路走到这里）。
    """
    import route

    problems, notes = [], []
    s1, w1 = route.resolve_start(None, None, False, [], set(), set(),
                                 set(), [], 4, "L5", problems, notes)
    problems2, notes2 = [], []
    s2, _w2 = route.resolve_start(None, (0, 0), False, [], {(0, 0)}, {(0, 0)},
                                  set(), [((0, 0), (1, 0))], 2, "1", problems2, notes2)
    bad = []
    if not (s1 is None and w1 and any("起点未检测到" in p for p in problems)):
        bad.append(f"滚动/无构型起点应判「起点未检测到」：{problems}")
    if not (s2 is None and any("起点未检测到" in p for p in problems2)):
        bad.append(f"有构型起点也应判「起点未检测到」：{problems2}")
    return (not bad), ("；".join(bad) if bad else "两种入口都干净判错 ✅")


def t_right_dots_left_zone_rejected():
    """`--right-dots` 报**左区**坐标必须被拦下（2026-11-02）。

    左区「空地」是 -99 硬墙；一旦把它当右区空地写进 `block_except` + 定价 0，
    就等于在左区**凭空开一条 0 分通道**（路线能从墙里穿过去，还不报错）。
    `build_scenario.right_zone()` 一直有这道过滤，route.py 漏了同一道闸。
    """
    import route

    keep, bad = route.split_right_dots([(9, 3), (7, 1), (10, 2)], 7)
    bad2, badzone = route.split_right_dots([], 7)
    ok = (keep == [(9, 3), (10, 2)] and bad == [(7, 1)]
          and bad2 == [] and badzone == [])
    return ok, f"keep={keep} 拦下={bad}（lm=7）"


def t_right_shape_dropped():
    """没报 `--right-dots` 时：端点落在**未探知空地**上的形状表边要如实报出来（2026-11-02）。

    以前 `dropped` **恒为空**（旧「空地=硬墙」口径的遗留）：`right_zone` 明明把那些边丢掉了，
    却从不报告 → `validation_status` 里 `if _rz_meta.get("dropped")` 是死分支，判词还写着
    「已核验」。现在锁三件事：
      · 无 dots → 已知格之间的边照连，**丢掉的边非空**（结论偏保守）；
      · 给了 dots（空地坐标已知）→ `dropped == []`，边数补齐；
      · `right_zone` 把 `dropped` 回填进 meta（validation_status / route.py 靠它降级）。
    """
    import build_scenario as BS

    sh = next(x for x in BS._parse_right_shapes(3) if x["name"] == "构型1")
    shift = 3
    cells = {(x, shift + dy) for (x, dy) in sh["cells"]}
    empty = (9, shift - 1)                 # 竖链中间那格 = 构型1 唯一的空地
    observed = cells - {empty}

    how1, _n1, _c1, eg1, dropped1 = BS.match_right_shape(3, observed)
    how2, _n2, _c2, eg2, dropped2 = BS.match_right_shape(3, observed, dots=[empty])

    grid = {q: ("未知的诡秘", 1.0, 0) for q in observed}
    meta, meta2 = {}, {}
    BS.right_zone(3, grid, set(), [], 6, meta=meta)
    BS.right_zone(3, grid, set(), [], 6, meta=meta2, dots=[empty])

    bad = []
    if how1 != "table" or len(eg1) != 4:
        bad.append(f"无 dots：应 table + 4 条已知边，实际 {how1} / {len(eg1)}")
    if not dropped1:
        bad.append("无 dots：dropped 不该为空（端点未知的边没接进图）")
    if not all(set(e) - observed for e in dropped1):
        bad.append(f"丢掉的边必须都碰到未探知格：{dropped1}")
    if how2 != "table" or len(eg2) != 7 or dropped2:
        bad.append(f"给了 dots：应 table + 7 条边 + dropped==[]，实际 {how2} / {len(eg2)} / {dropped2}")
    if not meta.get("dropped"):
        bad.append("right_zone 没把 dropped 回填进 meta")
    if meta2.get("dropped"):
        bad.append(f"给了 dots 后 meta['dropped'] 必须为空：{meta2.get('dropped')}")
    return (not bad), ("；".join(bad) if bad
                       else f"无 dots：4 边 + 丢 {len(dropped1)} 条；有 dots：7 边 + dropped=[] ✅")


def _json_load(path):
    import json as _json
    return _json.load(open(path, encoding="utf-8"))


TESTS = [
    ("final 必须停在终点", t_final_must_be_last),
    ("自动补的授予不能反复刷", t_auto_grant_not_farmable),
    ("免AP道具不扣行动力", t_free_item_no_ap),
    ("授予格只计一次", t_grant_once),
    ("花光AP后仍可用免AP道具", t_free_after_ap_exhausted),
    ("无道具时不崩", t_empty_items_no_crash),
    ("负价值节点不进路线", t_negative_value_guard),
    # ── 2026-10-28 审计：入口流水线（以前只测手写对的字段，测不出这些） ──
    ("portals 字符串坐标能传送", t_portal_string_coords),
    ("鸣笛=adj12 / 引擎=adj8", t_item_adj12_vs_adj8),
    ("入口 item 自带 cov+free", t_pipeline_item_fields),
    ("两件免AP件在 AP=0 都可用", t_two_free_items_at_zero_ap),
    ("没AP不能徒步", t_no_walk_without_ap),
    ("未知道具必须报错", t_unknown_item_errors),
    ("同分更省道具的路径必须保留", t_tie_cheaper_items_kept),
    ("道具价账目与路径一致", t_cost_reported_matches_path),
    ("右区按右区口径定价", t_right_zone_pricing),
    ("六层构型/起点/定点/道具1次", t_layer6_config),
    ("必走点是硬约束", t_must_is_hard_constraint),
    ("0 价值必走点也要生效", t_must_zero_value_enforced),
    ("必走点走不到时回落并报告", t_must_infeasible_falls_back),
    ("must 坐标错了要报错", t_must_bad_coord_errors),
    ("必走点的推断规则", t_derive_must_rules),
    ("分阶段右区失败要显式上报", t_staged_right_failure_surfaces),
    ("地标判据是子集不是交集", t_landmark_subset_not_intersection),
    ("派生名还原 + 带宽覆盖 1/2 层", t_base_type_and_band_coverage),
    ("右区固定节点数要校验", t_right_zone_count_checked),
    ("右区边表按形状表（不再四邻接）", t_right_zone_shape_table),
    ("DEFAULT_VALUES 与 rules 对齐", t_default_values_match_rules),
    ("solve_scan 扫描件保留 only/exclude", t_solve_scan_prep_keeps_land),
    # ── 2026-11-02 P0：限时/节点上限必须真的停 ──
    ("run(max_time) 真的按时停", t_run_time_limit_really_stops),
    ("跑完的搜索不带「撞上限」", t_run_complete_has_no_cut_reason),
    # ── 2026-11-02 P2/P4：--cov 优先级 + 扫描的收敛/证明语义 ──
    ("solve_scan --cov 压过原型件", t_solve_scan_cov_precedence),
    ("solve_scan 截断不报「不再增值」", t_solve_scan_truncated_is_not_converged),
    ("solve_scan 达界才算证明", t_solve_scan_bound_stop_is_proof),
    ("observe 六层自动构型 / config_cells 分层表", t_observe_layer6_and_config_cells),
    ("apply_config 认识险路尽头模式", t_apply_config_end_mode),
    ("check_scenario --allow-extra", t_check_scenario_allow_extra),
    ("L3 示例与构型表一致（幽灵边已清）", t_examples_conform_configs),
    ("五层L8左右区边界一致", t_l8_boundary_consistent),
    ("无右区局：左区宽度/边界判定", t_right_zone_absent_geometry),
    ("进区件按几何挑（便宜但够不着不行）", t_staged_entry_geometric),
    ("exclude 也要展开右区前缀", t_entry_exclude_right_prefix),
    ("触须能进五层右区行商", t_tentacle_enters_l5_merchant),
    ("未探明行商时触须不能当进区件", t_tentacle_needs_revealed_merchant),
    ("进区件跳过被 block 的格", t_entry_skips_blocked_cells),
    ("三/四层命运所指进 block", t_block_types_per_layer),
    ("回头步校验用删格后的图", t_backtrack_uses_blocked_graph),
    ("--items 裸数字是件数", t_items_piece_semantics),
    ("分阶段视图的道具价表要齐全", t_view_cost_of_merges),
    ("16 件加工品全部有价", t_all_items_priced),
    ("距离带与 06 表一致", t_bands_match_distance_table),
    ("可达域语义与格数（内=1~N）", t_cover_semantics),
    ("右区作战配额推断（独立于左区）", t_right_quota_inference),
    ("起点表（构型→起点）", t_start_table),
    ("起点+地标定构型", t_landmark_disambiguates_config),
    ("起点表等校验通过再写入", t_start_learn_gating),
    ("未知凶戾别名在推断之前", t_alias_before_inference),
    ("起点不能落在有标签的格上", t_start_not_on_labeled_cell),
    # ── 2026-10-29 审计修复 ──
    ("一层/二层普通分队凶戾期望 3+6/k", t_layer12_hazard_inference),
    ("apply_config 终点模式处理险路恶敌", t_apply_config_end_enemy),
    # ── 2026-10-30 玩家新规：险路小径 = 终点候选；分队模式改名 N16文明开化 ──
    ("险路小径同分/不 block + 模式别名", t_ending_types_and_mode_alias),
    ("二层四结局问询驱动定价/终点", t_four_ending_layer2),
    ("三层三结局问询驱动先行一步定价/必走", t_three_ending_layer3),
    ("徽章字形模板：6 种字形（含橡果/乌鸦）自匹配", t_badge_glyph),
    ("贴图标被合并的徽章也要认出（NCC 块内扫）", t_badge_merged),
    ("迷藏（橡果）：+12 并入节点 + 空地放行", t_acorn_value),
    ("迷藏（橡果）：放行/禁踩策略（恶敌·命运所指不放）", t_acorn_pass_policy),
    ("迷藏（橡果）：密道传送 + 只结算一次", t_acorn_portal),
    ("迷藏（橡果）：带橡果的终点可当中继（软终点）", t_acorn_soft_final),
    ("三结局六层「源流交汇处」：6×5 + 两种终点", t_l6_source_variant),
    ("二层小径=8 必须是硬终点（防双计）", t_layer2_small_path_hard_endpoint),
    ("险路小径是软终点（可停可续）", t_small_path_soft_final),
    ("坐标/道具写错要判 ❌ 不能 traceback", t_input_errors_are_clean),
    ("grant 只计首次 + 浮点分不截断", t_grant_once_and_float_fmt),
    # ── 2026-10-29 滚动重算（--visited）──
    ("visited 格不再计分/发 grant", t_visited_cell_not_refunded),
    ("滚动重算的起点不被误判构型错", t_rolling_start_not_rejected),
    ("apply_visited 改写 + 自动并入起点", t_apply_visited_rewrites),
    # ── 2026-10-31 五层二结局 ──
    ("五层二结局：定价/终点/必走/未答拦截", t_second_ending_layer5),
    ("五层二结局：进命运所指后加工品次数强制→1", t_clamp_items_after_fate),
    # ── 2026-11-02 求解器性能重构 ──
    ("阶段 B 真跑且标「最省已证明」", t_tie_phase_proves_cost),
    # ── 2026-11-02 判词语义：没搜完 ≠ 无解 ≠ 不可用 ──
    ("四态判词：❌ > ⏳ > ⚠️ > ✅（末行契约）", t_verdict_four_states),
    # ── 2026-11-02 输入可信度：玩家可声明标签已核 ──
    ("--labels-confirmed 只洗标签那一项", t_labels_confirmed),
    # ── 2026-11-02 审计修复：常规五层终点 / 起点缺失 / 左区圆点 ──
    ("常规五层：无左区命运所指也有终点", t_normal_layer5_final),
    ("起点没检出 → 干净判错，不 traceback", t_start_missing_is_clean_error),
    ("--right-dots 报左区坐标要拦下（左区空地被当墙）", t_right_dots_left_zone_rejected),
    ("右区形状表：未报 dots 时「没接进来的边」要如实报", t_right_shape_dropped),
]


def main():
    verbose = "-v" in sys.argv
    bad = 0
    print("== 求解器行为自检 ==")
    print(f"解释器：{sys.executable}（Python {sys.version.split()[0]}）")
    for name, fn in TESTS:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"异常：{exc!r}"
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")
        if verbose or not ok:
            print(f"     {detail}")
        if not ok:
            bad += 1
    print()
    if bad:
        print(f"❌ {bad} 项失败——有修复被改回去了")
        return 1
    print(f"✅ {len(TESTS)} 项全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
