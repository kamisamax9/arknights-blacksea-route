#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""resident_infer.py — 由「流窜居民」位置反推「居民据点」的候选范围

机制（references/02-地图与险路.md）
----------------------------------
居民据点会放出 3 个「流窜的『居民』」，它们**初始生成在距据点 5 步以内的林间空地上**。
所以反过来：

    据点 ∈ { 到**每一个**居民都 ≤5 步（路径距离）的格子 } ∩ { 未知的凶戾 }

「未知的凶戾」这一步是必须的——据点本身显示为「未知的凶戾」，不是空地。

用法
----
  python resident_infer.py scenario.json --residents "1,2;3,3;4,2"
  python resident_infer.py scenario.json --residents "1,2;3,3;4,2" --maxdist 5 --hazard 凶戾

  # 只想知道「距离约束」给出的范围、不叠加凶戾过滤：
  python resident_infer.py scenario.json --residents "1,2;3,3;4,2" --hazard ""

输出的是**候选集合**，不是唯一解；通常与凶戾求交后就只剩 1 个。

实战记录（2026-10-26 四层 L3）
------------------------------
居民 (1,2)(3,3)(4,2)，左区凶戾只有 (2,1)(1,4)：
  (1,4) → 2 / 3 / 5 步  全在 ≤5 内 ✓
  (2,1) → 6 / 3 / 3 步  (1,2) 超限 ✗  → 据点唯一 = (1,4)
"""
import argparse
import collections
import json
import sys

try:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    import rules as R
    _base = R.base_type              # 2026-10-29 修复：认得 `普通作战`/`右区凶戾` 等派生名
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


def bfs(adj, src):
    dist = {src: 0}
    dq = collections.deque([src])
    while dq:
        cur = dq.popleft()          # 队列才有最短路；用 pop() 会变成 DFS（踩过一次）
        for nb in adj[cur]:
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                dq.append(nb)
    return dist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--residents", required=True, help='例如 "1,2;3,3;4,2"')
    ap.add_argument("--maxdist", type=int, default=5, help="居民离据点的最大步数（default 5）")
    ap.add_argument("--hazard", default="凶戾", help="据点在图上显示成的类型关键字；空串=不过滤")
    a = ap.parse_args()

    sc = json.load(open(a.scenario, encoding="utf-8"))
    adj = collections.defaultdict(set)
    for e in sc["edges"]:
        u, v = tuple(e[0]), tuple(e[1])
        adj[u].add(v)
        adj[v].add(u)
    nodes = {tuple(map(int, k.split(","))): v for k, v in sc.get("nodes", {}).items()}
    residents = [tuple(map(int, s.split(","))) for s in a.residents.split(";") if s.strip()]

    print("居民:", residents, f"  搜距 ≤{a.maxdist} 步")
    dists = []
    for r in residents:
        d = bfs(adj, r)
        dists.append(d)
        print(f"  从 ({r[0]},{r[1]}) 出发可达 {len(d)} 格")

    cand = set(nodes)
    for d in dists:
        cand &= {q for q, v in d.items() if v <= a.maxdist}
    print(f"距离约束后的候选（{len(cand)} 格）:", sorted(cand))

    if a.hazard:
        # 2026-10-29 修复：按**归一化后的基名**比对。旧代码 `"凶戾" in 类型` 在
        # 配额推断把凶戾定性成「普通作战」后必然找不到据点（派生名失明）。
        hz_target = _base(a.hazard)
        hz = {q for q in cand if _base(nodes.get(q, "")) == hz_target}
        print(f"再与「{a.hazard}」（基名 {hz_target}）求交 → {len(hz)} 个：")
        for q in sorted(hz):
            ds = " / ".join(str(d.get(q, "-")) for d in dists)
            print(f"   ({q[0]},{q[1]})  {nodes.get(q)}   到各居民步数 {ds}")
        if len(hz) == 1:
            q = next(iter(hz))
            print(f"\n→ 据点唯一确定：({q[0]},{q[1]})")
        elif not hz:
            print("\n!! 交集为空：说明边表缺边、或某只居民不在 5 步内、或该层凶戾没标全。")
        else:
            print("\n→ 仍需排除：把其中一个踩掉就知道另一个（本层只有 1 个据点）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
