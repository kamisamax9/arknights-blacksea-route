#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""infer.py — 从「已探明信息」推断每个未知格可能是什么，并给出联合约束

区别于 expect_by_distance.py：
  那个只管「距离带 + 一次性类型已出现」；
  这个还管**层内的数量约束**，能跨格子联立。

用法
----
  python infer.py scene.json --layer 3 --revealed "3,0=应急助力,2,4=羽瞰点,..."

输出
----
  1. 凶戾格：哪些只能是普通作战、哪些"恰有一个是紧急作战"
  2. 诡秘格：候选类型 + 概率
  3. 联合结论（可直接拿去做决策的那几句）
"""
import argparse
import collections
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import rules as R
    _base = R.base_type               # 派生名（诡秘@x,y / 右区* / 普通作战）→ 基名
except Exception:                     # 允许从别的工作目录直接跑（兜底口径与 rules.base_type 一致）
    def _base(t):
        if not isinstance(t, str):
            return t
        if "@" in t:
            t = t.split("@", 1)[0]
        if t.startswith("右区"):
            t = t[len("右区"):]
        return {"未知的诡秘": "诡秘", "未知的凶戾": "凶戾", "普通作战": "作战"}.get(t, t)

# 距离带：只列「凶戾/作战」相关与一次性类型
# 2026-10-29 修复：原来只有 3/4/5——一/二层调用直接 KeyError: 2（COUNTS 明明写了 1/2/6）。
# 一/二层的带取自 06-距离表 的 I / II 列；六层 06 未收录 → 明确跳过并说明（不再崩）。
BAND = {
    1: {"作战": (1, 4), "紧急作战": (2, 4), "诡意行商": (2, 4), "秘境行商": (2, 4),
        "不期而遇": (2, 5), "得偿所愿": (2, 5), "险路小径": (0, 99), "险路尽头": (5, 5)},
    2: {"作战": (1, 7), "紧急作战": (3, 7), "诡意行商": (3, 5), "秘境行商": (2, 6),
        "安全的角落": (3, 5), "不期而遇": (2, 7), "得偿所愿": (4, 7), "失与得": (3, 5),
        "先行一步": (2, 4), "应急助力": (2, 5), "羽瞰点": (3, 6), "居民据点": (4, 6),
        "险路小径": (5, 6), "险路尽头": (3, 8)},
    3: {"作战": (1, None), "紧急作战": (3, None), "险路恶敌": (5, 8),
        "误入奇境": (5, 6), "狭路相逢": (2, 4), "诡意行商": (3, 8), "秘境行商": (3, 6),
        "安全的角落": (3, 6), "不期而遇": (1, None), "得偿所愿": (4, None),
        "失与得": (3, 6), "先行一步": (2, 4), "应急助力": (4, None), "曲折密道": (1, None)},
    4: {"作战": (1, None), "紧急作战": (3, None), "误入奇境": (5, 8), "狭路相逢": (2, None),
        "诡意行商": (2, None), "秘境行商": (2, 6), "安全的角落": (2, None), "不期而遇": (1, None),
        "得偿所愿": (4, None), "失与得": (2, 4), "先行一步": (2, 4), "应急助力": (2, 3),
        "曲折密道": (1, None)},
    5: {"作战": (1, None), "紧急作战": (3, None), "误入奇境": (3, 8), "狭路相逢": (2, 4),
        "诡意行商": (1, None), "秘境行商": (2, None), "安全的角落": (2, None), "不期而遇": (1, None),
        "得偿所愿": (1, None), "失与得": (2, None), "应急助力": (3, 5), "曲折密道": (1, None)},
}
# 各层「一次性节点」的出现次数（min, max）。玩家口径 2026-10-25。
# 右区另计：五层右区狭路相逢 1、诡意行商 1。
COUNTS = {
    1: {"诡意行商": (1, 2), "秘境行商": (0, 1), "险路小径": (0, 1)},
    2: {"诡意行商": (0, 1), "秘境行商": (1, 1), "先行一步": (1, 1), "应急助力": (0, 1), "失与得": (0, 1), "安全的角落": (0, 1),
        "居民据点": (0, 1), "险路小径": (0, 1)},
    3: {"误入奇境": (0, 1), "狭路相逢": (1, 1), "诡意行商": (1, 1), "秘境行商": (1, 1),
        "先行一步": (1, 1), "应急助力": (1, 1), "失与得": (1, 1), "安全的角落": (1, 1), "居民据点": (0, 0), "险路小径": (0, 0)},
    4: {"误入奇境": (0, 1), "狭路相逢": (1, 1), "诡意行商": (1, 1), "秘境行商": (1, 1),
        "先行一步": (1, 1), "应急助力": (1, 1), "失与得": (1, 1), "安全的角落": (1, 2), "居民据点": (0, 1), "险路小径": (0, 1)},
    5: {"误入奇境": (0, 1), "狭路相逢": (1, 1), "诡意行商": (1, 2), "秘境行商": (1, 2),
        "先行一步": (0, 0), "应急助力": (1, 1), "失与得": (1, 2), "安全的角落": (1, 2), "居民据点": (0, 1)},
    6: {},
}
ONE_PER_LAYER = set(COUNTS.get(3, {}))
# 每层「紧急作战」总数（含树根/理想源）。六层 = 2（定点 (0,2)/(4,3)，其一为树根）
URGENT_TOTAL = {1: 1, 2: 1, 3: 2, 4: 2, 5: None, 6: 2}   # 五层 1~3


def bfs(edges, start):
    adj = collections.defaultdict(list)
    for a, b in edges:
        a, b = tuple(a), tuple(b)
        adj[a].append(b)
        adj[b].append(a)
    d = {tuple(start): 0}
    q = collections.deque([tuple(start)])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in d:
                d[v] = d[u] + 1
                q.append(v)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--layer", type=int, required=True)
    ap.add_argument("--revealed", default="")
    ap.add_argument("--dist-from", default="")
    a = ap.parse_args()

    sc = json.load(open(a.scenario, encoding="utf-8"))
    nodes = sc.get("nodes", {})
    base = [int(v) for v in a.dist_from.split(",")] if a.dist_from else sc["start"]
    dist = bfs(sc["edges"], base)

    rev = {}
    for part in a.revealed.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            rev[k.strip()] = v.strip()
    for k, v in rev.items():
        nodes[k] = v
    # 已探明 = 命令行给的 + 场景文件里已经写成具体类型的。
    # 2026-10-29 修复：比较前先 `_base()` 归一——旧代码对 `诡秘@2,3`、`右区凶戾`、
    # `普通作战` 这些**工具自己产出的派生名**失明（PENDING 精确匹配不上），
    # 于是入口产出的场景在这里全被当成「已探明类型」或「认不出」。
    seen = {_base(v) for v in list(rev.values()) + list(nodes.values())}
    seen.discard(None)
    seen -= {"凶戾", "诡秘", "起点", "空地", "树根"}

    band = BAND.get(a.layer)
    if band is None:
        print(f"⚠️ {a.layer} 层暂无距离带表（06-距离表未收录该层）——跳过带过滤，仅给数量约束")
        band = {}
    inside = lambda t, d: (d is not None and t in band and d >= band[t][0]
                           and (band[t][1] is None or d <= band[t][1]))

    unknown_urgent, unknown_myst = [], []
    for k, t in sorted(nodes.items()):
        if "," not in k:
            continue
        x, y = map(int, k.split(","))
        if _base(t) == "凶戾":
            unknown_urgent.append(((x, y), dist.get((x, y))))
        elif _base(t) == "诡秘":
            unknown_myst.append(((x, y), dist.get((x, y))))

    print("=" * 70)
    print(f"层 {a.layer}｜已探明 {len(seen)} 类 / 指定 {len(rev)} 格")
    print("=" * 70)

    # ---------- 凶戾：数量约束 ----------
    urgent_total = URGENT_TOTAL.get(a.layer)
    root_known = any(v == "树根" for v in nodes.values())
    print("\n【凶戾 / 作战】")
    if not unknown_urgent:
        print("  无未知凶戾")
    elif urgent_total is None:
        print(f"  本层紧急作战 1~3 个（含理想源），无法唯一确定；剩余凶戾 {len(unknown_urgent)} 个")
    else:
        pool = urgent_total - (1 if root_known else 0)
        k = len(unknown_urgent)
        cells = "、".join(f"({x},{y})" for (x, y), _ in unknown_urgent)
        if pool <= 0:
            print(f"  {cells} 全部为**普通作战**（本层紧急作战已被理想源占满）")
        elif k == 0:
            print("  无")
        elif pool >= k:
            print(f"  {cells} 全部可能是紧急作战（名额 {pool} ≥ 格数 {k}）")
        else:
            print(f"  {cells} 中 **恰有 {pool} 个是紧急作战**，其余 {k - pool} 个是普通作战")
            for (x, y), d in unknown_urgent:
                if not band:
                    print(f"    ({x},{y}) 距离{d} → 紧急作战 / 普通作战 均可能（本层无距离带表）")
                    continue
                ok_u = inside("紧急作战", d)
                ok_n = inside("作战", d)
                cand = []
                if ok_u:
                    cand.append(f"紧急作战 {pool}/{k}")
                if ok_n:
                    cand.append(f"普通作战")
                print(f"    ({x},{y}) 距离{d} → {' / '.join(cand) if cand else '超出距离带'}")

    # ---------- 诡秘：距离带 + 精确数量 ----------
    print("\n【诡秘】")
    counts = COUNTS.get(a.layer, {})
    rows = []
    for (x, y), d in sorted(unknown_myst, key=lambda z: (z[1] is None, z[1] or 0)):
        if d is None:
            rows.append(f"  ({x},{y}) 不可达（需跳跃）")
            continue
        # 这些类型「固定显示在地图上」，永远不会伪装成「未知的诡秘」
        cs = [t for t in band if inside(t, d)
              and t not in ("作战", "紧急作战", "险路恶敌", "居民据点", "流窜居民",
                            "曲折密道", "羽瞰点", "险路尽头", "命运所指")]
        gone = [t for t in cs if counts.get(t, (0, 0))[1] == 1 and t in seen]
        cs = [t for t in cs if t not in gone]
        rows.append((((x, y), d, cs, gone)))
    for r in rows:
        if isinstance(r, str):
            print(r)
            continue
        (x, y), d, cs, gone = r
        tail = ""
        if gone:
            tail += f"  [已排除:{'/'.join(sorted(gone))}]"
        must = [t for t in cs if counts.get(t, (0, 0))[0] >= 1]
        if len(must) == 1:
            tail += f"  ★本层必出 {must[0]}"
        print(f"  ({x},{y}) 距离{d} → {'、'.join(cs) if cs else '无'}{tail}")

    # 必出类型的落点约束
    print("\n【必出类型落点】")
    mandatory = [(t, v) for t, v in counts.items() if v[0] >= 1 and t not in seen]
    if not mandatory:
        print("  （本层必出类型都已出现）")
    for t, (lo, hi) in mandatory:
        cells = []
        for r in rows:
            if isinstance(r, str):
                continue
            (x, y), d, cs, gone = r
            if t in cs:
                cells.append(f"({x},{y})")
        if len(cells) == 1:
            print(f"  {t}（必出 {lo}~{hi} 个）→ **只可能在 {cells[0]}**")
        elif cells:
            print(f"  {t}（必出 {lo}~{hi} 个）→ 在 {'、'.join(cells)} 之一")
        else:
            print(f"  {t}（必出 {lo}~{hi} 个）→ ⚠ 无候选格：说明它已在某个**已走过的未知格**里出现过")

    print("\n" + "=" * 70)
    print("提示：数量约束得出的「恰有 N 个」是最强的结论——")
    print("      它能把「二选一」变成「打完一个就知道另一个」。")
    print("=" * 70)


if __name__ == "__main__":
    main()
