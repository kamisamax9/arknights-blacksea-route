#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""solve_scan.py — 按「稀有道具预算」升序扫描最优解，爬坡即停（v2：同进程 + 总墙钟）

为什么要有这个工具
------------------
2026-10-26 四层那局，为了确认「多花 1 次结构性原理值几分」，顺手跑了「原理 4 次」的变体：
2000 万状态 / 75 秒，结果和「原理 1 次」一样是 89 分。纯空耗。
多点预算不可能降低总分 → **连续两档同分就是到顶**，后面的档位一律不用跑。

v2 相对 v1（按 grok 的复审意见）
--------------------------------
1. **扫描阶段 alts=0**：`--alts` 要在每个搜索节点做一次 `tot >= best-slack` 比较，
   并维护最多 4000 条替代路径——扫描阶段根本用不到。只在最后对选定档位补跑一次。
2. **同进程调用 `Solver`**：原来每档 `subprocess` 起一个新 Python，光 import numpy 就 0.2s+。
   改成直接 import 类、就地跑；安全性由 `run(max_time=...)` 保证，不必靠杀进程。
3. **整次扫描共用一个总墙钟**（默认 20s）：原来每档独立 25s，最坏 4 档 × 25s。
   现在从总预算里扣，剩多少给多少。
4. **根节点上界早停**：每档跑完比一次 `ub(空 mask, ap0)`——摸到**全局**乐观上界就收工。
   这条很少触发（根 ub 很松，忽略可达性），但检查成本近乎为零。

用法
----
  python solve_scan.py scenario.json                      # 扫「结构性原理」0..4
  python solve_scan.py scenario.json --item 气垫底座 --max 3
  python solve_scan.py scenario.json --budget 20 --alts 2 # 总墙钟 20s，末档带替代路线
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


def prep(base, keep, k, item, cov):
    """构造第 k 档的 scenario（就地补默认值，不落盘）。

    2026-10-29 修复：扫描件原来只写 name/cov/uses，**丢掉 only/exclude/free/bonus**——
    扫「老妈妈的融雪」时它能落作战、「坎诺特的触须」能落任意格，分数系统性偏乐观。
    现在按优先级补全形状：scenario 里的原型件 > rules.ITEM_DEFS 的标准定义 > 裸 cov。
    """
    sc = copy.deepcopy(base)
    sc["items"] = list(keep)
    if k > 0:
        ent = {"name": "%s(扫描%d)" % (item, k), "cov": cov, "uses": k}
        proto = next((it for it in base.get("items", [])
                      if str(it.get("name", "")).startswith(item)), None)
        if proto:
            for f in ("cov", "only", "exclude", "free", "bonus", "cost"):
                if f in proto:
                    ent[f] = proto[f]
            ent.setdefault("cov", cov)
        try:
            std = R.normalize_item({"name": item, "uses": k})
            for f in ("cov", "only", "exclude", "free", "bonus"):
                ent.setdefault(f, std.get(f))
            ent.setdefault("cost", R.PRICE.get(item, 2))
        except Exception:
            pass                   # 认不出的自定义件：保留裸 cov（与旧行为一致）
        sc["items"].append(ent)
    sc["values"] = dict(RS.DEFAULT_VALUES)
    sc["values"].update(base.get("values", {}))
    return sc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--item", default="结构性原理")
    ap.add_argument("--cov", default="any")
    ap.add_argument("--max", type=int, default=4)
    ap.add_argument("--budget", type=float, default=20.0, help="整次扫描的总墙钟（秒）")
    ap.add_argument("--alts", type=int, default=2, help="最后对选定档位补跑的替代数")
    a = ap.parse_args()

    base = json.load(open(a.scenario, encoding="utf-8"))
    keep = [it for it in base.get("items", []) if not it["name"].startswith(a.item)]
    removed = [it["name"] for it in base.get("items", []) if it["name"].startswith(a.item)]
    if removed:
        print("扫描对象:", ", ".join(removed), "（替换为 0..%d 次的单件）" % a.max)

    t_all = time.time()
    deadline = t_all + a.budget
    rows, prev, root_ub = [], None, None

    for k in range(a.max + 1):
        left = deadline - time.time()
        if left <= 0.2:
            rows.append((k, None, None, 0.0, "总墙钟用尽，放弃该档"))
            break
        sc = prep(base, keep, k, a.item, a.cov)
        s = RS.Solver(sc, ())
        if root_ub is None:
            root_ub = s.ub(0, s.ap0)
        t1 = time.time()
        total, path, hit = s.run(slack=0, max_alts=0, max_time=max(0.2, left))
        dt = time.time() - t1
        tag = "最优"
        if prev is not None:
            if total < prev:
                # 预算单调 ⇒ best(k) ≥ best(k-1)。出现倒退只可能是「撞墙截断」，
                # 不是模型问题：把结果钳到上一档，并明确标注，别让调用方当成真值。
                tag = "截断（实际 ≥ %g）" % prev
                total = prev
            elif total == prev:
                tag = "= 上一档，收敛"
        if hit and "截断" not in tag:
            tag += " 撞上限"
        if root_ub is not None and total >= root_ub:
            tag += " 已达全局上界 %g" % root_ub
        rows.append((k, total, len(path), dt, tag))
        if prev is not None and total == prev:
            break
        if root_ub is not None and total >= root_ub:
            break
        prev = total

    print("\nbudget   总分    步数   用时      状态")
    for k, total, steps, dt, tag in rows:
        # 分数常是浮点（诡秘 5.7 / 右区凶戾 18.5）：%g 才不会打出 18.500000001
        print("%6d %6s %6s  %6.2fs   %s"
              % (k, "-" if total is None else "%g" % total,
                 "-" if steps is None else steps, dt, tag))
    print("（总墙钟 %.1fs / 预算 %.0fs）" % (time.time() - t_all, a.budget))

    # 2026-10-29 修复：以前 `isinstance(r[1], int)` 会把浮点总分**全部丢掉**
    # （诡秘 5.7 / 右区凶戾 18.5 的场景总分必是 float）→ ok 为空 → exit 1，
    # 一行表之后没有推荐档位。改成 (int, float)。
    ok = [r for r in rows if isinstance(r[1], (int, float))]
    if not ok:
        print("\n没有任何一档跑出有效解（预算全耗尽或场景本身不可解）")
        return 1
    bk = max(ok, key=lambda r: (r[1], -r[0]))
    print("\n→ 推荐预算 %d：总分 %g。再多花「%s」不再增值。" % (bk[0], bk[1], a.item))

    if a.alts:
        sc = prep(base, keep, bk[0], a.item, a.cov)
        s2 = RS.Solver(sc, ())
        left = max(0.5, min(15.0, deadline + 10 - time.time()))
        tot2, path2, _ = s2.run(slack=0, max_alts=a.alts, max_time=left)
        print("   末档带 --alts %d 复核：%d 分，%d 步" % (a.alts, tot2, len(path2)))
        for v, p in sorted(s2.alts.values(), key=lambda t: -t[0])[:a.alts]:
            print("     val=%g  %s" % (v, " > ".join("(%d,%d)" % q for q, _, _ in p)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
