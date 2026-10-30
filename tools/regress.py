#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regress.py — 求解器回归：分数 / 步数 / 未截断 / 定价版本

为什么要这四项
--------------
2026-10-27 有会话对求解器做性能实验后，只用「分数还是 73」判断没坏。
但：

| 只看分数会漏掉 | 所以要断言 |
|---|---|
| 截断的运行也可能凑巧报 73（没搜完 ≠ 最优） | **未截断** `hit=False` |
| 定价一改，73 会**合法地**变化，断言就误报 | **定价版本**（表变了要显式接受） |
| 同分不同路（路线结构变了但分数一样） | **步数** |

定价版本用「定价表的 sha1 前 8 位」表示。表一变，这里会明确报告
「定价已变」，而不是笼统地报失败——**这是提醒你去复核期望值，不是让你改断言**。

用法
----
  python tools/regress.py                 # 跑全部
  python tools/regress.py --accept-pricing # 定价变了、已复核过期望值，更新记录
  python tools/regress.py -v
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import route_solver as RS       # noqa: E402
import build_scenario as BS     # noqa: E402

CASES_PATH = os.path.join(HERE, "..", "evals", "solver_regression.json")


def pricing_hash():
    blob = json.dumps(BS.PRICES, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept-pricing", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--quick", action="store_true",
                    help="只跑标了 quick 的迷你用例（毫秒级），用于改代码时反复自查；正式提交前仍要跑全量")
    a = ap.parse_args()

    data = json.load(open(CASES_PATH, encoding="utf-8"))
    cur_hash = pricing_hash()
    rec_hash = data.get("pricing_hash", "")
    stale = rec_hash != cur_hash

    print("== 求解器回归" + ("（quick：仅迷你用例）" if a.quick else "") + " ==")
    print(f"  定价指纹：当前 {cur_hash}　记录 {rec_hash}"
          + ("　⚠️ **定价已变**（期望值可能合法变化，需复核）" if stale else "　✅ 一致"))
    print()

    bad = 0
    cases = [c for c in data["cases"] if c.get("quick")] if a.quick else data["cases"]
    for case in cases:
        path = os.path.join(HERE, case["scenario"])
        if not os.path.exists(path):
            print(f"  ❌ {case['id']}: 找不到 {case['scenario']}")
            bad += 1
            continue
        sc = RS.load(path)
        s = RS.Solver(sc)
        total, route, hit = s.run(max_time=case.get("max_time", 120.0),
                                  node_limit=case.get("node_limit", 80000000))
        ok_score = total == case["expect_score"]
        ok_steps = len(route) == case["expect_steps"]
        ok_hit = (not hit) or case.get("allow_truncate", False)
        ok = ok_score and ok_steps and ok_hit
        print(f"  {'✅' if ok else '❌'} {case['id']}")
        if a.verbose or not ok:
            print(f"     分数 {total}（期望 {case['expect_score']}）"
                  f"　步数 {len(route)}（期望 {case['expect_steps']}）"
                  f"　截断 {'是' if hit else '否'}")
        if not ok:
            bad += 1

    print()
    if stale and not a.accept_pricing:
        print("⚠️ 定价指纹不一致：先复核这些期望值是否仍然正确，"
              "确认后用 --accept-pricing 更新记录。")
        bad += 1
    elif stale and a.accept_pricing:
        data["pricing_hash"] = cur_hash
        json.dump(data, open(CASES_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"已更新定价指纹为 {cur_hash}")

    if bad:
        print(f"❌ {bad} 处问题")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
