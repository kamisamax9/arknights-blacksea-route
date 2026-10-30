#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pool_candidates.py — 列出「未知的诡秘」里可能藏着哪些高价值一次性节点

为什么需要
----------
三/四/五层的「未知的诡秘」不是同质的：结合 `06-距离表` 的距离带，
可以把一部分格子**排除**掉某些类型。排路线前先把「最值钱的那几种」的候选点
列出来，玩家就能判断「这一格值不值得绕」。

关注的三种（都是高价值一次性节点）：

| 类型 | 三层 | 四层 | 五层 |
|---|---|---|---|
| 狭路相逢 | 2–4 | 2–10+ | 2–4 |
| 秘境行商 | 3–6 | 2–6 | 2–10+ |
| 误入奇境 | 5–6 | 5–8 | 3–8 |

用法
----
  python pool_candidates.py scenario.json --layer 5
  python pool_candidates.py scenario.json --layer 5 --route "5,3;2,3;3,3;3,0"
  python pool_candidates.py scenario.json --layer 3 --xianxing
      ↑ 三层·三结局：只列「先行一步」可能藏身的未知诡秘坐标（距离带 2–4）
"""
import argparse
import collections
import json
import sys

try:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    import rules as R
    _base = R.base_type              # 诡秘@x,y / 右区诡秘 → 诡秘（2026-10-29 修复）
except Exception:
    def _base(t):
        if not isinstance(t, str):
            return t
        if "@" in t:
            t = t.split("@", 1)[0]
        if t.startswith("右区"):
            t = t[len("右区"):]
        return {"未知的诡秘": "诡秘", "未知的凶戾": "凶戾", "普通作战": "作战"}.get(t, t)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

INF = 99
# 距离带：层 → {类型: (lo, hi)}；「10+」按 99 处理
BANDS = {
    3: {"狭路相逢": (2, 4), "秘境行商": (3, 6), "误入奇境": (5, 6)},
    4: {"狭路相逢": (2, 99), "秘境行商": (2, 6), "误入奇境": (5, 8)},
    5: {"狭路相逢": (2, 4), "秘境行商": (2, 99), "误入奇境": (3, 8)},
}
VALUE = {"狭路相逢": 10, "秘境行商": 11, "误入奇境": 10}


def bfs(adj, src):
    d = {src: 0}
    q = collections.deque([src])
    while q:
        cur = q.popleft()
        for nb in adj[cur]:
            if nb not in d:
                d[nb] = d[cur] + 1
                q.append(nb)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--layer", type=int, required=True, choices=[3, 4, 5])
    ap.add_argument("--route", default="", help='可选：本次路线 "5,3;2,3;..."，用于标注')
    ap.add_argument("--xianxing", action="store_true",
                    help="三层·三结局专用：只列「先行一步」可能藏身的未知诡秘坐标"
                         "（距离带 2–4；等价于 route.py/build_scenario.py 的候选清单）")
    a = ap.parse_args()

    sc = json.load(open(a.scenario, encoding="utf-8"))
    adj = collections.defaultdict(set)
    for e in sc["edges"]:
        u, v = tuple(e[0]), tuple(e[1])
        adj[u].add(v)
        adj[v].add(u)
    nodes = {tuple(map(int, k.split(","))): v for k, v in sc["nodes"].items()}
    start = tuple(sc["start"])
    dist = bfs(adj, start)
    bands = BANDS[a.layer]

    if a.xianxing:
        # 三结局问询：等价于「要 + 前置收藏品未获取」的候选清单。
        # 需要 build_scenario 的距离带/基名归一（那两处口径不能在这里再抄一遍）。
        if a.layer != 3:
            print("--xianxing 只对三层有意义（先行一步的距离带只在三层是 2–4）")
            return 2
        import build_scenario as BS
        R.set_three_ending("要")
        R.set_three_ending_pre("未获取")
        cand = BS.xianxing_candidates(a.layer, sc["nodes"], sc["edges"], start,
                                      visited=sc.get("visited") or ())
        if any(str(t) == R.THREE_XIANXING_NODE for t in nodes.values()):
            print("先行一步已探明 → 不是候选，直接按 15 分必走点走（must）")
            return 0
        if not cand:
            print("先行一步还没探明，但三层左区没有距离 2–4 步的未知诡秘 → "
                  "本层找不到它（核对截图漏检/已走格）")
            return 0
        print("三层「先行一步」可能藏身的未知诡秘（距离带 2–4，按步数排序）：")
        for q, d in cand:
            print(f"  ({q[0]},{q[1]})  距起点 {d} 步")
        if len(cand) == 1:
            print("  → 三层左区必出 1 个先行一步，只剩这一格 → 它就是先行一步（建议进场确认）")
        print(f"  → 请在这 {len(cand)} 个坐标里找「先行一步」；"
              "进去就拿到三结局前置收藏品（找到后按新截图重算）")
        return 0

    rows = []
    for q, t in nodes.items():
        # 2026-10-29 修复：入口会把诡秘改写成 `诡秘@x,y`（逐格期望）/`右区诡秘`，
        # 旧代码 `t != "诡秘"` 对它们全瞎 → 「没有落在距离带内的诡秘」假阴性
        if _base(t) != "诡秘":
            continue
        d = dist.get(q, INF)
        hits = [k for k, (lo, hi) in bands.items() if lo <= d <= hi]
        if hits:
            rows.append((d, q, hits))
    rows.sort()

    print(f"未知的诡秘里可能出现的三种高价值节点（按距起点步数排版，起点 {start}）：")
    print(f"{'格':>8} {'步':>3}  可能是什么")
    for d, q, hits in rows:
        tag = "/".join(hits)
        print(f"  ({q[0]},{q[1]})  {d:>3}   {tag}")

    if not rows:
        print("  （没有落在距离带内的诡秘——本层大概率不刷这三种）")

    if a.route:
        pts = [tuple(map(int, s.split(","))) for s in a.route.split(";") if s.strip()]
        marks = {q: hits for d, q, hits in rows}
        print("\n本次路线经过的诡秘格：")
        any_hit = False
        for q in pts:
            if _base(nodes.get(q, "")) == "诡秘" and q in marks:
                any_hit = True
                d = dist.get(q, INF)
                print(f"  ({q[0]},{q[1]}) 距离 {d} —— 可能是 {'/'.join(marks[q])}")
        if not any_hit:
            print("  （路线上的诡秘都不在候选带内，或没经过诡秘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
