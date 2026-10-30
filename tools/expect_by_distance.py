#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""expect_by_distance.py — 用距离表给「未知的诡秘 / 凶戾」算候选类型与期望值

原理
----
`references/06-距离表.md` 规定：每类节点在某层**只可能**出现在「距起点徒步 N–M 步」的格子上。
于是：BFS 求出每格距离 → 把距离区间不覆盖的类型划掉 → 剩下的是候选集 → 按当量取期望。

用法
----
  python expect_by_distance.py scenario.json --layer 3
  python expect_by_distance.py scenario.json --layer 3 --cell "4,1"   # 只看某格

scenario.json 需要：`edges`、`start`；`nodes` 里标了「诡秘」/「凶戾」的格子会被分析。

输出：每格 → 距离 → 候选类型 → 期望/最低/最高
"""
import argparse
import collections
import json
import sys

try:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    import rules as R
    _base = R.base_type              # 诡秘@x,y / 右区诡秘 / 未知的诡秘 → 诡秘（2026-10-29 修复）
except Exception:
    def _base(t):
        if not isinstance(t, str):
            return t
        if "@" in t:
            t = t.split("@", 1)[0]
        if t.startswith("右区"):
            t = t[len("右区"):]
        return {"未知的诡秘": "诡秘", "未知的凶戾": "凶戾", "普通作战": "作战"}.get(t, t)

# ---- 距离表（层 -> 类型 -> (min, max)），max=None 表示无上限 ----
# 2026-10-29 修复：原来只有 3/4/5，一/二层调用直接 KeyError: 2。
# 一/二层的带取自 06-距离表 的 I / II 列（「—」= 该层不出现，不列）；六层 06 未收录 → 明确说明。
DIST = {
    1: {
        "诡意行商": (2, 4), "秘境行商": (2, 4),
        "不期而遇": (2, 5), "得偿所愿": (2, 5),
    },
    2: {
        "诡意行商": (3, 5), "秘境行商": (2, 6), "安全的角落": (3, 5),
        "不期而遇": (2, 7), "得偿所愿": (4, 7), "失与得": (3, 5),
        "先行一步": (2, 4), "应急助力": (2, 5),
    },
    3: {
        "误入奇境": (5, 6), "狭路相逢": (2, 4), "诡意行商": (3, 8), "秘境行商": (3, 6),
        "安全的角落": (3, 6), "不期而遇": (1, None), "得偿所愿": (4, None), "失与得": (3, 6),
        "先行一步": (2, 4), "应急助力": (4, None),
    },
    4: {
        "误入奇境": (5, 8), "狭路相逢": (2, None), "诡意行商": (2, None), "秘境行商": (2, 6),
        "安全的角落": (2, None), "不期而遇": (1, None), "得偿所愿": (4, None), "失与得": (2, 4),
        "先行一步": (2, 4), "应急助力": (2, 3),
    },
    5: {
        "误入奇境": (3, 8), "狭路相逢": (2, 4), "诡意行商": (1, None), "秘境行商": (2, None),
        "安全的角落": (2, None), "不期而遇": (1, None), "得偿所愿": (1, None), "失与得": (2, None),
        "应急助力": (3, 5),
    },
}

# ---- 当量（与 03-节点收益.md 对齐；只用于排序比较）----
VAL = {
    "误入奇境": 16, "狭路相逢": 14, "诡意行商": 8, "秘境行商": 11, "安全的角落": 4,
    "不期而遇": 2, "得偿所愿": 8, "失与得": 2, "先行一步": 2, "应急助力": 2, "曲折密道": 0,
}

# 每层至多出现一次的类型（确认出现后，同层的其它格就不必再考虑它）
ONE_PER_LAYER = {"误入奇境", "狭路相逢", "先行一步", "秘境行商", "诡意行商"}

RANGE = {"狭路相逢": (11, 17), "误入奇境": (16, 16), "得偿所愿": (8, 8), "诡意行商": (4, 12),
         "秘境行商": (4, 11), "安全的角落": (0, 8), "不期而遇": (0, 8), "失与得": (0, 4),
         "先行一步": (0, 4), "应急助力": (0, 4), "曲折密道": (0, 0)}


# 控制台编码兜底（GBK 终端下打印中文/符号不崩）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass



def infer(cands, revealed):
    """候选集 → (剩余候选, 一次性类型里仅剩的那个, 已排除掉的一次性类型)"""
    gone = [t for t in ONE_PER_LAYER if t in revealed]
    rest = [t for t in cands if t not in gone]
    lonely = [t for t in rest if t in ONE_PER_LAYER]
    return rest, (lonely[0] if len(lonely) == 1 else None), gone


def bfs(edges, start):
    adj = collections.defaultdict(list)
    for a, b in edges:
        a, b = tuple(a), tuple(b)
        adj[a].append(b)
        adj[b].append(a)
    dist = {tuple(start): 0}
    q = collections.deque([tuple(start)])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist


def candidates(layer, d):
    dist_tbl = DIST.get(layer)
    if dist_tbl is None:
        return None                    # 该层无距离带表（如六层），调用方负责提示
    out = []
    for t, (lo, hi) in dist_tbl.items():
        if d >= lo and (hi is None or d <= hi):
            out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--layer", type=int, required=True)
    ap.add_argument("--cell", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--dist-from", default="",
                    help='计算距离的基准格，默认用 scenario 的 start。距离表规定是「距本层起点」，中途重算时必须显式给起点的坐标')
    ap.add_argument("--revealed", default="",
                    help='已探明的格，如 "2,2=先行一步,4,2=诡秘"（跨类型推断必需）')
    a = ap.parse_args()

    revealed = set()
    for part in a.revealed.split(","):
        if "=" in part:
            revealed.add(part.split("=", 1)[1].strip())

    sc = json.load(open(a.scenario, encoding="utf-8"))
    base_pos = sc["start"]
    if a.dist_from:
        base_pos = [int(v) for v in a.dist_from.split(",")]
    dist = bfs(sc["edges"], base_pos)
    nodes = sc.get("nodes", {})

    rows = []
    no_band = a.layer not in DIST
    if no_band:
        print(f"⚠️ {a.layer} 层暂无距离带表（06-距离表未收录）——只列格子，不给候选/期望")
    for key, typ in nodes.items():
        if _base(typ) not in ("诡秘", "凶戾"):
            continue
        x, y = map(int, key.split(","))
        d = dist.get((x, y))
        if d is None:
            rows.append((key, typ, None, [], 0, 0, ""))
            continue
        cs = candidates(a.layer, d) if _base(typ) == "诡秘" else []
        hint = ""
        if cs:
            rest, lonely, gone = infer(cs, revealed)
            if gone:
                hint = f"  [已排除:{'/'.join(sorted(gone))}]"
            if lonely:
                hint += f"  ★本层一次性类型仅剩 {lonely}（全层最多一格）→ 优先按它估值"
            cs = rest or cs
        vals = [VAL[t] for t in (cs or [])] or [0]
        lo = min(RANGE[t][0] for t in cs) if cs else 0
        hi = max(RANGE[t][1] for t in cs) if cs else 0
        rows.append((key, typ, d, cs, round(sum(vals) / len(vals), 2), (lo, hi), hint))

    rows.sort(key=lambda r: (r[1], -(r[4] if isinstance(r[4], float) else 0)))
    buf = []
    print(f"{'格':<8}{'类型':<6}{'距离':<6}{'期望':<7}{'区间':<10}候选")
    for key, typ, d, cs, ev, rg, hint in rows:
        if a.cell and key != a.cell:
            continue
        dstr = "-" if d is None else str(d)
        buf.append(f"{key:<8}{typ:<6}{dstr:<6}{ev:<7}{str(rg):<10}{'、'.join(cs) if cs else '(凶戾，按层定)'}{hint}")
        print(buf[-1])


    if a.out:
        open(a.out, "w", encoding="utf-8").write("\n".join(buf) + "\n")


if __name__ == "__main__":
    main()
