#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sweep_runs.py — 清理 runs 目录里不符合命名规范的 scenario 文件

为什么要这个
------------
2026-10-27：一天之内 runs/ 里冒出 13 个一层 scenario（tmp_*、_A/_B/_up/_down、
L1-20261027-v2 …），其中 12 个是废的，还互相矛盾。根因是「每换一张图就新建一份坐标文件」。

标准做法（见 SKILL.md 〇·五 文件纪律）：
- 坐标不落盘，每次由 label_ocr 现算
- 每层只保留一份 scenario，命名 `L{层}-c{构型}-{HHMM}.json`
- 同一层重算就改这份，不新建

本工具把「符合规范的文件」留下，其余列出来并可删除。

用法
----
  python tools/sweep_runs.py --dry-run     # 只看会删什么
  python tools/sweep_runs.py               # 真删
  python tools/sweep_runs.py --runs 某目录 --keep-glob "L5-*"
"""
import argparse
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RUNS = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "runs"))

# 命名：L{层}-{构型}-{标签}.json   例 L4-L6-final.json / L2-c3-1535.json
NAME_PATTERN = re.compile(r"^L([1-6])-([A-Za-z0-9]+)-[A-Za-z0-9_.]+\.json$", re.IGNORECASE)

# 判据不是「名字好不好看」，而是**同一层同一构型有没有堆多份**：
# 一天的中间版本（v2/v3/v4/v5）留最新那份就够，其余的才是垃圾。


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=DEFAULT_RUNS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-glob", default="", help="额外保留的 glob，例如 'L5-*'")
    ap.add_argument("--since", default=None,
                    help="只清理这个日期之后修改的文件（默认今天）。历史文件一律不动")
    ap.add_argument("--all", action="store_true", help="连历史文件一起清（危险，慎用）")
    a = ap.parse_args()

    import datetime
    if a.since:
        since = datetime.datetime.strptime(a.since, "%Y-%m-%d")
    else:
        since = datetime.datetime.combine(datetime.date.today(), datetime.time.min)

    runs = os.path.abspath(a.runs)
    if not os.path.isdir(runs):
        print(f"目录不存在：{runs}")
        return 2

    keep_extra = set()
    if a.keep_glob:
        import fnmatch
        keep_extra = {n for n in os.listdir(runs) if fnmatch.fnmatch(n, a.keep_glob)}

    # ① 先按 (层, 构型) 分组；② 每组只留最新一份；③ 名字完全不合规范的也列出来
    groups = {}
    stray = []
    for name in sorted(os.listdir(runs)):
        if not name.lower().endswith(".json") or name in keep_extra:
            continue
        path = os.path.join(runs, name)
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        m = NAME_PATTERN.match(name)
        if not m:
            if a.all or mtime >= since:
                stray.append(name)
            continue
        groups.setdefault((m.group(1), m.group(2).lower()), []).append((mtime, name))

    bad = list(stray)
    for key, items in sorted(groups.items()):
        items.sort(reverse=True)          # 新的在前
        newest_t, newest_n = items[0]
        for t, n in items[1:]:
            if a.all or t >= since:
                bad.append(n)
        if len(items) > 1:
            kept = newest_n
            print(f"  [{key[0]}层/{key[1]}] 共 {len(items)} 份，保留最新的 {kept}")
    bad = sorted(bad)

    if not bad:
        # 2026-10-29：旧话术「全部合规」会误导——默认 --since 只清今天，
        # 历史里同层同构型堆了多份、或名字不合规的文件它根本没检查。
        hist = sum(max(0, len(v) - 1) for v in groups.values())
        tail = (f"；历史重复 {hist} 份未动（要清历史加 --all）" if hist
                else "；历史文件未检查（要清历史加 --all）")
        print(f"✅ 今天没有新增的待清理文件{tail}")
        return 0

    print(f"可以清掉的（同层同构型的旧版本 + 无规范命名的临时文件）：{len(bad)} 个")
    for n in bad:
        p = os.path.join(runs, n)
        size = os.path.getsize(p)
        print(f"  {n}   ({size} B)")

    if a.dry_run:
        print("\n（--dry-run，未删除）")
        return 0

    for n in bad:
        p = os.path.join(runs, n)
        full = os.path.abspath(p)
        if not full.startswith(runs + os.sep):
            print(f"  !! 越界，跳过 {full}")
            continue
        os.remove(full)
    print(f"\n已删除 {len(bad)} 个文件；保留合规文件：")
    for n in sorted(os.listdir(runs)):
        if n.lower().endswith(".json"):
            print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
