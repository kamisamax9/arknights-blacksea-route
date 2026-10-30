#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_configs.py — 构型边表自检：数量 / 连通 / 端点 / 起点 / 尽头

为什么要有这个
--------------
2026-10-27：有会话从 `09-二层构型.md` 抄构型1 的边表，**抄了 18 条，实际 19 条**，
漏掉的 `(2,2)-(3,2)` 正好夹在两个尽头边中间、单独成短语，最容易被跳过。
后果不只是少一条边——它据此认为 `(2,2)` 是孤点、这层打不到，还把这个错误结论写进了 notes。

**靠「下次仔细点」是解决不了的**（人眼在长列表里必然有漏检率），
必须让机器核对文档自己写的数字。

检查项
------
1. **边数**：正文解析出的边数必须等于标题里的「N 条边」
2. **自环/重复**：不允许 (a,a) 或重复边
3. **端点**：每条边的两个端点都必须是该构型的格子
4. **连通**：整张图应当连通（除孤立的空地外，至少要能从一个格子走到尽头）
5. **起点/尽头**：标题里声明的起点和尽头必须在格子集合里

用法
----
  python tools/check_configs.py                 # 检查全部层
  python tools/check_configs.py --layer 2       # 只查二层
  python tools/check_configs.py --verbose
"""
import argparse
import collections
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "..", "references")

# 层 → (文件, 段名正则)
FILES = {
    1: ("17-一层构型.md", r"构型(\d)（"),
    2: ("09-二层构型.md", r"构型(\d+)（"),
    3: ("10-三层左区构型.md", r"(L\d+)（"),
    4: ("11-四层左区构型.md", r"(L\d+)（"),
    5: ("13-五层左区构型.md", r"(L\d+)（"),
    # 六层只有 1 个构型，文件里没有「构型N」标题，边表就在「## 边表」这一节。
    # 它的三个块**故意互不相连**（15-六层构型「三个互不相连的块」），
    # 所以连通性期望和标题边数都要单独给。
    6: ("15-六层构型.md", r"(边表)"),
}

# 六层有两张图，期望值各一套：
#   盲目者所见（四结局，5×4）：15 条边 / 16 格 / 3 个互不相连块（10+3+3）
#   源流交汇处（三结局，6×5）：17 条边 / 14 格 / 1 个连通块
LAYER6_SPECIAL = {"want": 15, "comps": 3, "cells": 16}
LAYER6_SOURCE_SPECIAL = {"want": 17, "comps": 1, "cells": 14}


def parse_section(seg):
    """返回 (边列表, 标题里的边数 or None, 起点, 尽头列表)。"""
    head = seg.splitlines()[0]
    m = re.search(r"(\d+)\s*条边", head)
    want_edges = int(m.group(1)) if m else None

    start = None
    ms = re.search(r"起点\s*\(?(\d+),\s*(\d+)\)?", head)
    if ms:
        start = (int(ms.group(1)), int(ms.group(2)))

    ends = []
    me = re.search(r"尽头\s*([0-9,、/\s]+)", head)
    if me:
        ends = [(int(a), int(b)) for a, b in re.findall(r"(\d+),(\d+)", me.group(1))]

    edges = []
    for a, b, c, d in re.findall(r"\((\d+),(\d+)\)-\((\d+),(\d+)\)", seg):
        edges.append(((int(a), int(b)), (int(c), int(d))))
    if not edges:
        # 一层用的是**对称的**邻接表格：| (0,1) | (1,1) | 与 | (1,1) | (0,1) | 是同一对
        # 所以必须按「无序对」去重，否则边数会翻倍
        uniq = {}
        for line in seg.splitlines():
            if not line.strip().startswith("|"):
                continue
            pts = [(int(x), int(y)) for x, y in re.findall(r"\((\d+),(\d+)\)", line)]
            if len(pts) >= 2:
                for q in pts[1:]:
                    uniq[tuple(sorted((pts[0], q)))] = (pts[0], q)
        edges = list(uniq.values())
    return edges, want_edges, start, ends


def check(name, seg, verbose=False, special=None):
    edges, want, start, ends = parse_section(seg)
    cells = {p for e in edges for p in e}
    problems = []

    if want is None and special:
        want = special["want"]          # 六层：标题没写边数，用期望值核对
    if want is None:
        problems.append("标题没有写「N 条边」，无法核对数量")
    elif len(edges) != want:
        problems.append(f"**边数不符：解析 {len(edges)} 条，标题写 {want} 条**（差 {want - len(edges)}）")
    if special and special.get("cells") and len(cells) != special["cells"]:
        problems.append(f"格数不符：解析 {len(cells)}，应为 {special['cells']}")

    seen = set()
    for a, b in edges:
        if a == b:
            problems.append(f"自环 {a}")
        k = tuple(sorted((a, b)))
        if k in seen:
            problems.append(f"重复边 {a}-{b}")
        seen.add(k)
        for p in (a, b):
            if p not in cells:
                problems.append(f"端点 {p} 不在格子集合里")

    if start and start not in cells:
        problems.append(f"起点 {start} 不在格子集合里")
    for q in ends:
        if q not in cells:
            problems.append(f"尽头 {q} 不在格子集合里")

    # 连通分量（只统计有边的格子）
    adj = collections.defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    seen_n, comps = set(), []
    for p in sorted(adj):
        if p in seen_n:
            continue
        q, stack = [], [p]
        while stack:
            cur = stack.pop()
            if cur in seen_n:
                continue
            seen_n.add(cur)
            q.append(cur)
            stack.extend(adj[cur] - seen_n)
        comps.append(q)
    if special and special.get("comps"):
        # 六层：盲目者所见故意 3 块；源流交汇处 1 块（都按期望值核对，不要求全连通）
        if len(comps) != special["comps"]:
            problems.append(f"连通块数不符：解析 {len(comps)} 块，应为 {special['comps']} 块"
                            f"（各块 {[len(c) for c in comps]}）")
    elif len(comps) > 1:
        big = max(comps, key=len)
        isol = [c for c in comps if c is not big]
        problems.append(f"图不连通：{len(comps)} 个分量，孤立 {isol}")

    status = "OK " if not problems else "FAIL"
    print(f"  [{status}] {name:6s} 边 {len(edges):2d}/{want if want is not None else '?'}  "
          f"格 {len(cells):2d}  起点 {start}  尽头 {ends}")
    for p in problems:
        print(f"        - {p}")
    if verbose and not problems:
        print(f"        分量 {len(comps)}")
    return len(problems)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", type=int, choices=sorted(FILES))
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    layers = [a.layer] if a.layer else sorted(FILES)
    total = 0
    for L in layers:
        fn, pat = FILES[L]
        path = os.path.join(REF, fn)
        if not os.path.exists(path):
            print(f"层 {L}: 找不到 {fn}")
            continue
        txt = open(path, encoding="utf-8").read()
        print(f"\n=== 层 {L}  {fn} ===")
        for seg in re.split(r"(?m)^## ", txt)[1:]:
            m = re.match(pat, seg)
            if not m:
                continue
            special = None
            if L == 6:
                head = seg.splitlines()[0] if seg.splitlines() else ""
                special = (LAYER6_SOURCE_SPECIAL if "源流" in head else LAYER6_SPECIAL)
            total += check(f"层{L}/{m.group(1)}", seg, a.verbose, special)

    print(f"\n合计问题 {total} 处")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
