#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""solve_scan.py — 按「稀有道具预算」升序扫描：多花一件，到底还值不值？

为什么要有这个工具
-----------------
2026-10-26 四层那局，为了确认「多花 1 次结构性原理值几分」，顺手跑了「原理 4 次」的变体：
2000 万状态 / 75 秒，结果和「原理 1 次」一样是 89 分。纯空耗。
多点预算不可能降低总分（单调），所以只要**证明**到顶，后面的档位就不用跑。

v3（2026-11-02，重做「什么时候可以停」——这一版只认真正的证明）
----------------------------------------------------------------
0. **先纠正一个旧前提**：「相邻两档同分 = 到顶」**不是证明**。
   单调性只给 `best(k+1) ≥ best(k)`，给不出上界：完全可能 20 / 20 / 30。
   真正能收工的只有一条——**分数摸到可采纳上界**：
   `score ≥ ub_global`（`ub_global` 取所有档位里最大的 `root_ub()`）。
1. **每档都有状态**：OPTIMAL / TIMEOUT / NODE_LIMIT / ABANDONED / INFEASIBLE。
   截断档同分只说明「目前找到的一样」，不能说明更高预算不会更高。
2. **上界逐档重算**：`solver.root_ub()` 含免AP件/给AP件的 bonus。旧版拿 k=0 档的
   `s.ub(0, s.ap0)`（bonus=0）跨档复用，界偏低还被当成「全局上界」——
   实测 L3-regression-73：旧口径 73、同口径 77，据此收工是**无效证明**。
3. **截断不再冒充收敛**：实测分数低于上一档时，只把「下界」（单调性）钳到上一档，
   **不再 break**（旧版钳制后 `total == prev` 立刻触发停扫）。
4. **同分只是启发式**：相邻两档都未截断且同分时默认收工（省时间），但会明说
   「启发式到顶，未证明」并给出上界缺口；`--strict` 时只认达界，扫满 `--max`。
5. **预算拆开**：`--budget` 只给扫描，`--alts-budget` 另算；两边分别计时打印。
6. 计时全用 `time.monotonic()`（系统校时不会提前收工）。
7. **`--cov` 优先级**（P2）：显式 CLI > 原型件 > `rules` 标准定义 > 默认 `any`；
   不传 `--cov` 才沿用原型，显式传入则压过它（只覆盖 cov，道具固有的 exclude 保留）。

用法
----
  python solve_scan.py scenario.json                       # 扫「结构性原理」0..4
  python solve_scan.py scenario.json --item 气垫底座 --max 3
  python solve_scan.py scenario.json --cov adj8             # 指定可达域（压过原型件）
  python solve_scan.py scenario.json --budget 20 --alts 2   # 扫描 20s，替代路线另算
  python solve_scan.py scenario.json --strict               # 只认「达上界」，不信同分
  python solve_scan.py scenario.json --json                 # 末尾附机器可读结果

退出码：0 = 至少一档跑出可行解；1 = 没有任何可行档。
"""
import argparse
import copy
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import route_solver as RS
import rules as R

# 同分判定：run() 的分数已经 round 到 1e-6，比这个更小的差不是「真差」。
# **不要**写 `total == prev`：浮点加法顺序不同会差 1e-15，同分会被判成不同分；
# 也不要用 1e-9，否则「分数正好等于上界」会因舍入判不出来（已证最优被漏掉）。
SCORE_TOL = 1e-6
# 剩余时间低于这个数就不再开新档：开也跑不完，只会白占窗口。
MIN_TIER_TIME = 0.2


def prep(base, keep, k, item, cov):
    """构造第 k 档的 scenario（就地补默认值，不落盘）。

    2026-10-29 修复：扫描件原来只写 name/cov/uses，**丢掉 only/exclude/free/bonus**——
    扫「老妈妈的融雪」时它能落作战、「坎诺特的触须」能落任意格，分数系统性偏乐观。

    2026-11-02（P2）修 `--cov` 被原型静默覆盖：现在优先级是**显式 CLI > 原型件 >
    rules 标准定义 > 默认 any**。`cov=None` 表示「命令行没指定」——这是唯一能把
    「没指定」和「显式写了 --cov any」区分开的办法（后者要压过原型的 cov）。
    注意只覆盖 `cov`：`only/exclude/free/bonus` 是这件道具的固有规则（比如融雪落不了
    作战），命令行改可达域时也必须保留。
    """
    sc = copy.deepcopy(base)
    sc["items"] = list(keep)
    if k > 0:
        ent = {"name": "%s(扫描%d)" % (item, k), "uses": k}
        proto = next((it for it in base.get("items", [])
                      if str(it.get("name", "")).startswith(item)), None)
        if proto:
            for f in ("cov", "only", "exclude", "free", "bonus", "cost"):
                if f in proto:
                    ent[f] = proto[f]
        if cov is not None:                 # 显式 CLI 压过原型（P2 修复点）
            ent["cov"] = cov
        try:
            std = R.normalize_item({"name": item, "uses": k})
            for f in ("cov", "only", "exclude", "free", "bonus"):
                ent.setdefault(f, std.get(f))
            ent.setdefault("cost", R.PRICE.get(item, 2))
        except Exception:
            pass                   # 认不出的自定义件：保留已有字段（与旧行为一致）
        ent.setdefault("cov", "any")        # 原型/标准定义都没有 → 默认 any
        ent.setdefault("cost", R.PRICE.get(item, 2))
        sc["items"].append(ent)
    sc["values"] = dict(RS.DEFAULT_VALUES)
    sc["values"].update(base.get("values", {}))
    return sc


def score_same(a, b):
    """同分判定（带容差）。None 永远不算同分。"""
    return a is not None and b is not None and abs(a - b) <= SCORE_TOL


def fmt(x):
    return "-" if x is None else "%g" % x


def global_ub(base, keep, item, cov, kmax):
    """所有档位里最大的根上界 —— 唯一能用来「证明到顶」的数。

    `ub` 随免AP件/给AP件的次数单调不减，所以最大值一定在 k=kmax 那一档
    （扫描件不加 AP 时各档相同，取哪个都一样）。
    """
    best = None
    for k in (0, kmax) if kmax else (0,):
        u = RS.Solver(prep(base, keep, k, item, cov), ()).root_ub()
        best = u if best is None else max(best, u)
    return best


def run_tier(s, ub, max_time, node_limit=20000000):
    """在已建好的 Solver 上跑一档，返回状态/分数/路径/耗时。

    `proven`（**本档**已证最优）的两种来源，缺一不可：
      · `hit` 为假 —— A 阶段搜索穷尽了整个可达状态空间；
      · 或 分数 ≥ 本档可采纳上界 —— 即使被截断，也已经证明本档到顶。
    注意：本档到顶 ≠ 更高档不会更高（见文件头 0）。
    """
    t1 = time.monotonic()
    total, path, hit = s.run(slack=0, max_alts=0, node_limit=node_limit,
                             max_time=max(MIN_TIER_TIME, max_time), tie_break=False)
    dt = time.monotonic() - t1

    bounded = total >= ub - SCORE_TOL
    proven = (not hit) or bounded
    if total < 0:
        status = "INFEASIBLE"
    elif bounded:
        status = "OPTIMAL(达界)"
    elif proven:
        status = "OPTIMAL"
    elif s.cut_reason == "node_limit":
        status = "NODE_LIMIT"
    elif s.cut_reason == "timeout":
        status = "TIMEOUT"
    else:
        status = "UNKNOWN"          # hit 了但没给原因：不许当成最优

    note = ""
    if bounded and hit:
        note = "被截断但已达本档上界 %g" % ub
    elif not proven:
        note = "未穷尽（%s），分数只是下界" % (s.cut_reason or "未知原因")

    return {"score": total, "steps": len(path), "path": path, "elapsed": dt,
            "upper": ub, "proven": proven, "bounded": bounded,
            "status": status, "cut_reason": s.cut_reason, "note": note}


def print_table(rows):
    print("\nbudget   实测   下界   上界   步数   用时      状态 / 说明")
    for r in rows:
        extra = r["status"]
        if r.get("note"):
            extra += "：" + r["note"]
        print("%6d %6s %6s %6s %6s  %7.2fs   %s"
              % (r["k"], fmt(r.get("score")), fmt(r.get("lower")), fmt(r.get("upper")),
                 "-" if r.get("steps") is None else r["steps"],
                 r.get("elapsed") or 0.0, extra))


def main():
    ap = argparse.ArgumentParser(description="按稀有道具预算升序扫描，证明到顶即停")
    ap.add_argument("scenario")
    ap.add_argument("--item", default="结构性原理")
    ap.add_argument("--cov", default=None,
                    help="扫描件的可达域（adj8/adj12/line2/line3/row_col/any）。"
                         "不传 = 沿用原型件 / rules 定义；显式传入则压过原型。")
    ap.add_argument("--max", type=int, default=4)
    ap.add_argument("--budget", type=float, default=20.0, help="**扫描**阶段的总墙钟（秒）")
    ap.add_argument("--node-limit", type=int, default=20000000,
                    help="单档搜索的节点上限（默认 2e7；调小可做确定性截断测试）")
    ap.add_argument("--alts", type=int, default=2, help="对推荐档补跑的替代路线数（0=不跑）")
    ap.add_argument("--alts-budget", type=float, default=10.0,
                    help="替代路线复核的独立预算（秒），不计入 --budget")
    ap.add_argument("--strict", action="store_true",
                    help="只认「达可采纳上界」这个证明；相邻同分不再提前收工")
    ap.add_argument("--json", action="store_true", help="末尾附机器可读 JSON")
    a = ap.parse_args()

    # 参数校验：负数/零会让循环空转或预算瞬间用尽，早报错比事后看空表强。
    if a.max < 0:
        ap.error("--max 不能为负（0 表示只跑第 0 档）")
    if a.budget <= 0:
        ap.error("--budget 必须为正（秒）")
    if a.alts < 0:
        ap.error("--alts 不能为负")
    if a.node_limit <= 0:
        ap.error("--node-limit 必须为正")
    if a.alts_budget < 0:
        ap.error("--alts-budget 不能为负")

    base = json.load(open(a.scenario, encoding="utf-8"))
    raw_items = base.get("items") or []
    keep, removed = [], []
    for it in raw_items:
        nm = str(it.get("name", "")) if isinstance(it, dict) else ""
        if nm.startswith(a.item):
            removed.append(nm or "?")
        else:
            keep.append(it)
    if removed:
        print("扫描对象:", ", ".join(removed), "（替换为 0..%d 次的单件）" % a.max)
    print("扫描口径: k = 「%s」的剩余次数；上界 = root_ub()（含免AP件/给AP件的 bonus）"
          % a.item)
    if a.cov is not None:
        print("可达域(cov): 显式 --cov %s（压过原型件）" % a.cov)
    else:
        print("可达域(cov): 未指定 → 沿用原型件 / rules 定义（都缺时为 any）")

    t_all = time.monotonic()
    ub_all = global_ub(base, keep, a.item, a.cov, a.max)
    print("可采纳全局上界: %g（任何档都不可能超过它）" % ub_all)
    deadline = t_all + a.budget
    rows, prev, prev_lower = [], None, None
    stopped = None                  # ("bound"|"plateau", k)

    for k in range(a.max + 1):
        left = deadline - time.monotonic()
        if left <= MIN_TIER_TIME:
            rows.append({"k": k, "score": None, "steps": None, "elapsed": 0.0,
                         "lower": prev_lower, "upper": None, "proven": False,
                         "bounded": False, "status": "ABANDONED",
                         "note": "扫描预算用尽，未开跑"})
            break

        s = RS.Solver(prep(base, keep, k, a.item, a.cov), ())
        ub = s.root_ub()
        row = run_tier(s, ub, left, a.node_limit)
        row["k"] = k

        # 单调性：best(k) ≥ best(k-1) ≥ prev_lower。截断档实测更低时，
        # 只把**下界**钳上去，不改分数、更不因此停扫。
        low = row["score"] if row["score"] is not None and row["score"] >= 0 else None
        if low is not None and prev_lower is not None:
            low = max(low, prev_lower)
        if (row["score"] is not None and prev_lower is not None
                and row["score"] < prev_lower - SCORE_TOL):
            row["note"] = (row["note"] + "；" if row["note"] else "") + \
                "实测低于上一档下界 %g（截断，非收敛）" % prev_lower
        row["lower"] = low
        rows.append(row)

        # 唯一真正的证明：摸到所有档位的上界。
        if row["score"] is not None and row["score"] >= ub_all - SCORE_TOL:
            row["status"] = "OPTIMAL(达界)"
            row["note"] = "达可采纳全局上界 %g —— 已证没有更高档" % ub_all
            stopped = ("bound", k)
            break
        # 启发式（可关）：相邻两档都未截断且同分，收益已经连续为零。
        if (not a.strict and prev is not None and row["proven"] and prev["proven"]
                and score_same(row["score"], prev["score"])):
            row["note"] = ((row["note"] + "；") if row["note"] else "") + \
                "与上一档同分：启发式到顶（**未证明**，上界 %g）" % ub_all
            stopped = ("plateau", k)
            break
        prev, prev_lower = row, low

    t_scan = time.monotonic() - t_all
    print_table(rows)

    cand = [r for r in rows
            if isinstance(r.get("score"), (int, float)) and r["score"] >= 0]
    if not cand:
        print("\n没有任何一档跑出有效解（预算全耗尽或场景本身不可解）")
        print("（扫描 %.1fs / 预算 %.0fs）" % (t_scan, a.budget))
        return 1

    # 推荐：最高实测分；同分优先「已证最优」，再优先更小预算。
    bk = max(cand, key=lambda r: (r["score"], r["proven"], -r["k"]))
    if bk["proven"]:
        print("\n→ 推荐预算 %d：总分 %g（本档已证最优）。" % (bk["k"], bk["score"]))
    else:
        print("\n→ 推荐预算 %d：总分 %g（**未证最优**：%s，实际可达 ≥ %g）。"
              % (bk["k"], bk["score"], bk["status"], bk["score"]))

    stop_row = rows[stopped[1]] if stopped else None
    if stop_row is not None and bk["score"] >= stop_row["score"] - SCORE_TOL:
        if stopped[0] == "bound":
            print("   再多花「%s」不再增值（已证：总分 = 可采纳上界 %g）。"
                  % (a.item, ub_all))
        else:
            print("   再多花「%s」大概率不再增值（启发式：第 %d 档与上一档同为 %g，均未截断）；"
                  "但可采纳上界 %g > %g，理论仍有 %g 空间，要严格证明请加 --strict。"
                  % (a.item, stop_row["k"], stop_row["score"], ub_all,
                     bk["score"], ub_all - bk["score"]))
    elif bk["proven"] and bk["k"] >= a.max:
        print("   已到 --max %d：再往上没扫（要更大预算请调 --max）。" % a.max)
    elif bk["proven"]:
        # 指出**第一个没证完的档位**，别只报 bk+1（中间档可能已跑完却没达界）。
        nxt = next((r for r in rows if r["k"] > bk["k"] and not r.get("proven")), None)
        if nxt is None:
            print("   已扫满 --max %d，仍未到可采纳上界 %g：界偏松，或还有没被搜到的组合。"
                  % (a.max, ub_all))
        elif nxt["status"] == "ABANDONED":
            print("   未能证明更高预算不增值：第 %d 档没跑（预算用尽），可调大 --budget。"
                  % nxt["k"])
        else:
            print("   未能证明更高预算不增值：第 %d 档未证完（%s），可调大 --budget。"
                  % (nxt["k"], nxt["status"]))
    else:
        print("   分数只是下界：调大 --budget 或 --max 才可能给出定论。")

    t_alts = 0.0
    if a.alts:
        s2 = RS.Solver(prep(base, keep, bk["k"], a.item, a.cov), ())
        t2 = time.monotonic()
        tot2, path2, _ = s2.run(slack=0, max_alts=a.alts, node_limit=a.node_limit,
                                max_time=max(MIN_TIER_TIME, a.alts_budget), tie_break=True)
        t_alts = time.monotonic() - t2
        print("   末档带 --alts %d 复核（%.2fs）：%d 分，%d 步"
              % (a.alts, t_alts, tot2, len(path2)))
        for v, p in sorted(s2.alts.values(), key=lambda t: -t[0])[:a.alts]:
            print("     val=%g  %s" % (v, " > ".join("(%d,%d)" % q for q, _, _ in p)))

    print("（扫描 %.1fs / 预算 %.0fs；替代路线 %.1fs / 预算 %.1fs）"
          % (t_scan, a.budget, t_alts, a.alts_budget))

    if a.json:
        def _path(p):
            # 路径元素是 (坐标, 用的道具名/None, 本步得分)，不是纯坐标。
            return [[[int(q[0]), int(q[1])], nm, g] for q, nm, g in (p or [])]

        out = {"scenario": a.scenario, "item": a.item, "global_upper": ub_all,
               "rows": [{kk: (_path(r.get("path")) if kk == "path" else r.get(kk))
                         for kk in ("k", "score", "lower", "upper", "steps",
                                    "elapsed", "status", "proven", "note", "path")}
                        for r in rows],
               "recommend": {"k": bk["k"], "score": bk["score"], "proven": bk["proven"]},
               "stopped": ({"why": stopped[0], "k": stopped[1]} if stopped else None),
               "timing": {"scan": t_scan, "scan_budget": a.budget,
                          "alts": t_alts, "alts_budget": a.alts_budget}}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
