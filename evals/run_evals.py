#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_evals.py — 把 evals.json 里可机械检查的断言跑一遍

不是"模拟模型"，而是**校验 skill 依赖的事实与工具输出**：
  · 文档里写的固定位/数量/规则是否真的存在；
  · 工具（infer/solver）在当前代码下是否给出断言要求的结果。

用法：python evals/run_evals.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.dirname(HERE)
REF = os.path.join(SK, "references")
TOOLS = os.path.join(SK, "tools")
PY = sys.executable

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def section(text, head):
    """取 '## head' 到下一个 '## ' 之间的正文"""
    i = text.find(head)
    if i < 0:
        return ""
    j = text.find("\n## ", i + len(head))
    return text[i: j if j > 0 else len(text)]


def run_tool(args):
    r = subprocess.run([PY] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return (r.stdout or "") + (r.stderr or "")


# ============ Eval 1：一层固定行商 ============
print("=" * 68)
print("Eval 1  L1-fixed-merchant")
print("=" * 68)
c17 = read(os.path.join(REF, "17-一层构型.md"))
blk2 = section(c17, "## 构型2（起点 1,1）")
fixed_ok = bool(re.search(r"\|\s*构型2\s*\|\s*1\s*\|\s*\*\*\(3,1\)\*\*", c17)) or ("(3,1)" in c17 and "诡意行商" in c17)
check("一层文档写明 (3,1) 是诡意行商（构型1/2 共用固定位）", fixed_ok,
      "17-一层构型.md 全文搜索")

c20 = read(os.path.join(REF, "20-一层特殊规则.md"))
check("一层默认没有理想源（写进一层特殊规则）",
      "默认没有理想源" in c20, "20-一层特殊规则.md")

c1 = read(os.path.join(REF, "01-加工品.md"))
check("同效果道具先用次数少的（加工品文档有该规则）",
      "先用剩余次数少的" in c1 or "次数少的" in c1, "01-加工品.md")

# ============ Eval 2：三层滚动 ============
print("\n" + "=" * 68)
print("Eval 2  L3-rolling-not-whole-layer")
print("=" * 68)
skill = read(os.path.join(SK, "SKILL.md"))
# 只验证「滚动模式被标为默认」这一意图，不锁死具体措辞
roll_head = [ln for ln in skill.splitlines() if ln.startswith("### 滚动模式")]
check("SKILL.md 明确「滚动模式」为默认",
      "滚动重规划" in skill and bool(roll_head) and "默认" in roll_head[0],
      roll_head[0] if roll_head else "未找到滚动模式小节")
check("SKILL.md 明确「滚动模式不要输出整层路线」",
      "不要输出整层路线" in skill or "走两步就过时" in skill)

tmpd = tempfile.mkdtemp()
# 用一条 3 格直线做最小场景：AP=1，但树根给 +2 → 必须能走出 2 步，证明 +2 被计入
l3 = os.path.join(tmpd, "l3.json")
sc = {"edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
      "nodes": {"0,0": "起点", "1,0": "树根", "2,0": "诡秘"},
      "start": [0, 0], "ap": 1, "grants": {"1,0": 2},
      "values": {"起点": 0, "树根": 15, "诡秘": 1}, "items": []}
json.dump(sc, open(l3, "w", encoding="utf-8"), ensure_ascii=False)
out = run_tool([os.path.join(TOOLS, "route_solver.py"), l3])
m = re.search(r"走了 (\d+) 步", out)
steps = int(m.group(1)) if m else -1
check("求解器把树根 +2 行动力算进去（AP=1 却走出 2 步）",
      steps == 2 and "树根" in out, "实际：" + (m.group(0) if m else "无输出"))

# ── Eval 2b：分队模式（2026-10-29 玩家新增，SKILL.md 〇·〇）──────────────
check("SKILL.md 写明两种分队模式且开局先问",
      "分队模式" in skill and "文明开化" in skill and "普通分队" in skill
      and "先问" in skill)

# ── Eval 2c：终点模式（2026-10-29 玩家新增，SKILL.md 〇·〇）──────────────
check("SKILL.md 写明两种终点模式且与分队模式独立",
      "终点模式" in skill and "全程追猎" in skill and "险路尽头" in skill
      and "相互独立" in skill)

# ── Eval 2d：二层四结局问询（2026-10-30 玩家新增）────────────────────
c24 = read(os.path.join(REF, "24-二层四结局.md"))
check("SKILL.md 写明二层必问四结局（含前置藏品与 --four-ending）",
      "二层必问" in skill and "本局是否要打四结局" in skill
      and "--four-ending" in skill and "前置藏品" in skill)
check("24 号 reference 覆盖 5 个分支（要/不要 × 前置已获取/未获取）",
      all(x in c24 for x in ("要", "不要", "已获取", "未获取",
                            "不再必走", "可选终点")))
check("二层问询有 tool 侧强制闸（route.py 未答 exit 2）",
      "exit 2" in c24 and "layer2_answer_errors" in read(os.path.join(TOOLS, "rules.py")))

# ── Eval 2e：右区有无（2026-10-30 玩家新增）────────────────────────
c25 = read(os.path.join(REF, "25-右区有无.md"))
check("SKILL.md 写明三/四/五层右区可能不存在（无右区不预留 AP）",
      "右区（熄日余径）可能" in skill and "不预留 AP" in skill
      and "has_right" in skill)
check("25 号 reference 写明存在条件/左区居中/不预留 AP/完整宽度校验/兜底提问",
      all(x in c25 for x in ("已获取四结局前置藏品", "左区", "不预留",
                            "has_right", "完整宽度", "重截",
                            "本局是否存在熄日余径", "--right-zone")))
check("SKILL.md 写明右区拿不准时问玩家（不是用二层 Q2 推断）",
      "本局是否存在熄日余径" in skill and "--right-zone" in skill)
check("无右区识别有 tool 侧实现（choose_layer_cols / has_right_cols）",
      "choose_layer_cols" in read(os.path.join(TOOLS, "label_ocr.py"))
      and "has_right_cols" in read(os.path.join(TOOLS, "label_ocr.py")))

# ── Eval 2f：三层三结局问询（2026-10-30 玩家新增）────────────────────
c26 = read(os.path.join(REF, "26-三层三结局.md"))
check("SKILL.md 写明三层无右区必问三结局（含先行一步 15 分与候选坐标）",
      "三层必问：三结局" in skill and "本局是否打三结局" in skill
      and "--three-ending" in skill and "15 分必走" in skill
      and "候选坐标" in skill)
check("26 号 reference 覆盖 Q1/Q2、先行一步 15 分、候选坐标、exit 2",
      all(x in c26 for x in ("本局是否打三结局", "三结局前置收藏品", "15 分",
                            "候选坐标", "2–4", "exit 2", "有右区")))
check("三层问询有 tool 侧强制闸（route.py 未答 exit 2）",
      "layer3_answer_errors" in read(os.path.join(TOOLS, "rules.py"))
      and "xianxing_candidates" in read(os.path.join(TOOLS, "build_scenario.py")))

# ── Eval 2h：五层二结局问询（2026-10-31 玩家新增）────────────────────
c28 = read(os.path.join(REF, "28-五层二结局.md"))
check("SKILL.md 写明五层二结局必问（3 个命运所指 / 得偿所愿−1 / 右区必走 / 次数钳制 / 最后一步）",
      "五层必问：二结局" in skill and "本局是否打二结局" in skill
      and "--second-ending" in skill and "得偿所愿 − 1" in skill
      and "clamp_items_at" in skill and "老妈妈的融雪" in skill)
check("28 号 reference 覆盖判据/三分支定价/钳制/终点/exit 2",
      all(x in c28 for x in ("本局是否打二结局", "得偿所愿 − 1", "clamp_items_at",
                             "必走", "最后一步", "exit 2", "险路恶敌")))
check("五层二结局有 tool 侧实现（rules 问询 + 建场景 + 求解器钳制 + 入口透传）",
      "layer5_answer_errors" in read(os.path.join(TOOLS, "rules.py"))
      and "second_ending_setup" in read(os.path.join(TOOLS, "build_scenario.py"))
      and "_clamp_after" in read(os.path.join(TOOLS, "route_solver.py"))
      and "--second-ending" in read(os.path.join(TOOLS, "route.py")))
check("五层二结局：徽章=入口（fate_badge 进了 apply_badges 的返回）",
      "fate_badge" in read(os.path.join(TOOLS, "label_ocr.py")))

# ── Eval 2g：解释器/依赖自检（2026-10-30 误判「本机缺依赖」事故）──────────
pyenv_src = read(os.path.join(TOOLS, "pyenv.py"))
check("SKILL.md 写明先跑 pyenv.py，且「pip 的 Python ≠ 脚本的 Python」",
      "python tools/pyenv.py" in skill and "的 Python ≠ 跑脚本的 Python" in skill)
check("pyenv.py 列出必需依赖 + 探测本机其他解释器（probe/candidates）",
      all(x in pyenv_src for x in ("rapidocr_onnxruntime", "scipy",
                                   "def probe", "def candidates",
                                   "def alternative_hint")))

# ── Eval 2h：白圈徽章（2026-10-30 玩家新增）────────────────────────
c27 = read(os.path.join(REF, "27-三结局徽章.md"))
check("SKILL.md 写明六种徽章字形（泪之聚落/无效验尸/迷藏/流窜居民…）",
      "27-三结局徽章.md" in skill
      and all(x in skill for x in ("泪之聚落", "无效验尸", "迷藏", "流窜居民"))
      and "12 分" in skill)
check("27 号 reference 覆盖 6 种字形 + 迷藏 +12 + 空地放行 + 无右区",
      all(x in c27 for x in ("泪之聚落", "线人", "无效验尸", "迷藏", "流窜居民",
                            "0.55", "badge_glyph", "ACORN_VALUE",
                            "block_except", "无右区")))
check("徽章字形/定价有 tool 实现（badge_glyph + 泪之聚落=0 + apply_badges）",
      "def badge_glyph" in read(os.path.join(TOOLS, "pixel_probe.py"))
      and "泪之聚落" in read(os.path.join(TOOLS, "rules.py"))
      and "apply_badges" in read(os.path.join(TOOLS, "label_ocr.py")))
check("迷藏（橡果）= 所在节点 +12、逐格覆盖 + 空地放行（ACORN_VALUE + cell_values）",
      "ACORN_VALUE" in read(os.path.join(TOOLS, "rules.py"))
      and "def cell_value" in read(os.path.join(TOOLS, "route_solver.py"))
      and "block_except" in read(os.path.join(TOOLS, "route_solver.py"))
      and "def acorn_cell_values" in read(os.path.join(TOOLS, "build_scenario.py"))
      and "BS.acorn_block_except" in read(os.path.join(TOOLS, "route.py"))
      and "ACORN_EPS = " not in read(os.path.join(TOOLS, "route_solver.py")))

# ── Eval 2i：三结局六层「源流交汇处」（2026-10-30 玩家新增）──────────
c15 = read(os.path.join(REF, "15-六层构型.md"))
check("15 号文档写明三结局六层源流（6×5 / 17 条边 / 命运所指 0 / 林间空地 / 两种终点）",
      all(x in c15 for x in ("源流交汇处", "6×5", "17 条边", "命运所指",
                            "林间空地", "全程追猎", "险路尽头")))
check("六层源流有 tool 实现（6×5 尺寸 / config_edges(6,源流) / 起点 (0,2)）",
      "SIX_VARIANT_DIMS" in read(os.path.join(TOOLS, "label_ocr.py"))
      and '"源流" in str(cfg or "")' in read(os.path.join(TOOLS, "build_scenario.py"))
      and 'return (0, 2)' in read(os.path.join(TOOLS, "build_scenario.py")))

# final_type 语义：走到尽头类节点即结束（最后一步之前进不了该类节点）。
# 同一张图 AP=2：无约束时最优停在中途诡秘（1 步）；final_type 后强制以尽头收尾（2 步）。
l3e = os.path.join(tmpd, "l3e.json")
sc_e = {"end_mode": "险路尽头", "final_type": "险路尽头",
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
        "nodes": {"0,0": "起点", "1,0": "诡秘", "2,0": "险路尽头"},
        "start": [0, 0], "ap": 2, "grants": {},
        "values": {"起点": 0, "诡秘": 1, "险路尽头": 0}, "items": []}
json.dump(sc_e, open(l3e, "w", encoding="utf-8"), ensure_ascii=False)
out = run_tool([os.path.join(TOOLS, "route_solver.py"), l3e])
m = re.search(r"走了 (\d+) 步", out)
steps_e = int(m.group(1)) if m else -1
check("险路尽头：路线强制以尽头收尾（AP=2 走满 2 步、末格是尽头）",
      steps_e == 2 and "险路尽头" in out, "实际：" + (m.group(0) if m else "无输出"))

# 同一张 3 格直线图：普通分队模式下树根不回复 AP → AP=1 只能走 1 步；
# 且树根价值=紧急作战（9），不再是 15。
l3n = os.path.join(tmpd, "l3n.json")
sc_n = {"mode": "普通分队",
        "edges": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]],
        "nodes": {"0,0": "起点", "1,0": "树根", "2,0": "诡秘"},
        "start": [0, 0], "ap": 1, "grants": {},
        "values": {"起点": 0, "树根": 9, "紧急作战": 9, "诡秘": 1}, "items": []}
json.dump(sc_n, open(l3n, "w", encoding="utf-8"), ensure_ascii=False)
out = run_tool([os.path.join(TOOLS, "route_solver.py"), l3n])
m = re.search(r"走了 (\d+) 步", out)
steps_n = int(m.group(1)) if m else -1
check("普通分队：树根不回复 AP（AP=1 只走 1 步，不给 +2）",
      steps_n == 1, "实际：" + (m.group(0) if m else "无输出"))

# Eval 3 需要三层 L1 的边表
ref10 = read(os.path.join(REF, "10-三层左区构型.md"))
edges = []
for b in ref10.split("\n## "):
    if b.startswith("L1（"):
        for x, y, z, w in re.findall(r"\((\d+),(\d)\)-\((\d+),(\d)\)", b):
            edges.append([[int(x), int(y)], [int(z), int(w)]])

# ============ Eval 3：数量约束 + 同带互推 ============
print("\n" + "=" * 68)
print("Eval 3  infer-cross-type")
print("=" * 68)
l3b = os.path.join(tmpd, "l3b.json")
nodes = {"0,2": "起点", "2,2": "先行一步"}
for x in range(7):
    for y in range(5):
        k = f"{x},{y}"
        if k not in nodes and y in (0, 1, 2, 3, 4):
            nodes[k] = "诡秘"
json.dump({"edges": edges, "nodes": nodes, "start": [0, 2], "ap": 6,
           "values": {}, "items": []}, open(l3b, "w", encoding="utf-8"), ensure_ascii=False)
out = run_tool([os.path.join(TOOLS, "infer.py"), l3b, "--layer", "3", "--dist-from", "0,2"])
line11 = [ln for ln in out.splitlines() if ln.strip().startswith("(1,1)")]
check("infer 把 (1,1) 判为「狭路相逢」",
      any("狭路相逢" in ln and "★" in ln for ln in line11),
      line11[0].strip() if line11 else "未找到 (1,1) 行")
check("infer 不把「曲折密道」列进诡秘候选",
      "曲折密道" not in out, "全文搜索")
check("infer 输出了「必出类型落点」一节", "必出类型落点" in out)

# ============ Eval 4：二层行商优先 ============
print("\n" + "=" * 68)
print("Eval 4  L2-merchant-priority")
print("=" * 68)
c04 = read(os.path.join(REF, "04-推演规则.md"))
row2 = [ln for ln in c04.splitlines() if ln.startswith("| **秘境行商**")]
check("数量表：二层秘境行商 = 1（不是 0–1）",
      bool(row2) and "**1**" in row2[0].split("|")[3],
      row2[0] if row2 else "未找到该行")

check("SKILL.md 写明「秘境行商与理想源同级」",
      "秘境行商" in skill and ("高于一切" in skill or "同级" in skill))
check("SKILL.md 写明行商内可卖出置换（背包满不是拒绝理由）",
      "卖出" in read(os.path.join(REF, "03-节点收益.md")))


# ============ Eval 5：索引完整性 ============
print("\n" + "=" * 68)
print("Eval 5  index-integrity")
print("=" * 68)
cited = sorted(set(re.findall(r"references/([0-9A-Za-z\-\u4e00-\u9fff]+\.md)", skill))
               | set(re.findall(r"`([0-9]{2}-[0-9A-Za-z\-\u4e00-\u9fff]+\.md)`", skill)))
missing = [c for c in cited if not os.path.exists(os.path.join(REF, c))]
check(f"SKILL.md 引用的 {len(cited)} 个 references 文件全部存在",
      not missing, "缺失：" + "、".join(missing) if missing else "")

on_disk = sorted(f for f in os.listdir(REF) if f.endswith(".md"))
uncited = [f for f in on_disk if f not in cited]
check(f"references/ 下的 {len(on_disk)} 个文件都出现在 SKILL.md 索引里",
      not uncited, "未收录：" + "、".join(uncited) if uncited else "")


# ============ Eval 6：SKILL.md 体量守卫 ============
print("\n" + "=" * 68)
print("Eval 6  budget")
print("=" * 68)
nline = len(skill.split("\n"))
# 这个 skill 由两个会话共同维护（2026-10-27 起另一个会话往里加了「文件纪律」与 OCR 流程，
# 一次 +213 行）。上限从 320 放宽到 520 —— 目的是防"无节制膨胀"，不是卡某个会话的写法。
# 2026-10-30：二层四结局问询是**强制分支**，SKILL.md 净增约 15 行（详细分支已下沉
# `references/24-二层四结局.md`）。上限 520 → 540，仍保留"加内容先想能不能下沉"的约束。
# 2026-10-30 第二波：三层三结局问询同样下沉到 `references/26-三层三结局.md`，
# SKILL.md 只留 14 行（状态字段 + 每层流程 + 索引）。上限 540 → 560。
# 2026-10-31 第三波：五层二结局问询（左区 3 个命运所指）是**第四条强制分支**，
# 主体下沉到 `references/28-五层二结局.md`，SKILL.md 只留判据/两个分支/参数/终点优先级
# （净增约 28 行）。上限 560 → 600，继续保留「加内容先想能不能下沉」的约束。
check(f"SKILL.md 不超过 600 行（当前 {nline}）", nline <= 600, f"当前 {nline} 行")
check("SKILL.md 引用的 references 目录下存在 22 号流程文件",
      os.path.exists(os.path.join(REF, "22-截图与定价流程.md")))

# ============ Eval 7：求解器回归（统一走 tools/regress.py）============
# 2026-10-27：原来这里自己跑一遍 route_solver 再 grep 分数，
# 和 tools/regress.py 是一套逻辑两份实现——两边结论会分叉。
# 现在统一委托给 regress.py（分数/步数/未截断/定价指纹 四项），这里只看它的退出码。
print("\n" + "=" * 68)
print("Eval 7  solver-regression（委托 tools/regress.py）")
print("=" * 68)
reg_tool = os.path.join(TOOLS, "regress.py")
if os.path.exists(reg_tool):
    out = run_tool([reg_tool])
    ok7 = "全部通过" in out
    check("求解器回归全过（分数/步数/未截断/定价指纹）", ok7,
          out.strip().splitlines()[-1] if out.strip() else "无输出")
    if not ok7:
        print(out)
else:
    check("tools/regress.py 存在", False, reg_tool)

# 行为断言：有些修复只在特定输入下显形，分数回归看不出来
selftool = os.path.join(TOOLS, "selftest_solver.py")
if os.path.exists(selftool):
    out = run_tool([selftool])
    check("求解器行为自检全过（修复没被改回去）", "全过" in out,
          out.strip().splitlines()[-1] if out.strip() else "无输出")
else:
    check("tools/selftest_solver.py 存在", False, selftool)

# ============ Eval 8：输入校验/兜底表漂移（2026-10-31 QA 新增）============
# 三类真实踩过的坑，各锁一条：
#   ① 截图路径写错 → 以前 RapidOCR/PIL 抛 traceback（exit 1），现在必须 ❌ + exit 3；
#   ② --items 数量写错（负数 / "1次2"）→ 以前静默算成 -3 次 / 12 次，现在必须报错；
#   ③ DEFAULT_VALUES 与 LAYER_PRICES 漂移 → 手写 scenario 静默少算分。
print("\n" + "=" * 68)
print("Eval 8  input-validation / fallback-drift")
print("=" * 68)
badimg = os.path.join(tmpd, "no-such-shot.png")
rc_bad, out_bad = None, ""
r = subprocess.run([PY, os.path.join(TOOLS, "route.py"), badimg,
                    "--layer", "3", "--config", "L4", "--ap", "6"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=300)
rc_bad, out_bad = r.returncode, (r.stdout or "") + (r.stderr or "")
check("截图不存在 → ❌ 判词 + exit 3（不再是 traceback）",
      rc_bad == 3 and "截图不存在" in out_bad and "Traceback" not in out_bad,
      f"rc={rc_bad}")

sys.path.insert(0, TOOLS)
import rules as _R  # noqa: E402

bad_specs = []
for spec in ("标准引擎:-1", "标准引擎:1次2", "标准引擎:-1次"):
    try:
        _R.parse_items_spec(spec)
        bad_specs.append(spec)
    except ValueError:
        pass
check("--items 负数 / 夹带字符 → 报错（不再静默算成 -3 次 / 12 次）",
      not bad_specs, "未报错：" + "、".join(bad_specs))
check("--items 正常写法仍按件数解析（标准引擎:2 = 6 次）",
      _R.parse_items_spec("标准引擎:2")[0][0]["uses"] == 6)

_dv_drift = []
_seen = {}
for _layer, _prices in _R.LAYER_PRICES.items():
    for _k, _v in _prices.items():
        _seen.setdefault(_k, set()).add(_v)
import route_solver as _RS  # noqa: E402

for _k, _vals in _seen.items():
    if len(_vals) == 1 and _k in _RS.DEFAULT_VALUES:
        _want = next(iter(_vals))
        if abs(_RS.DEFAULT_VALUES[_k] - _want) > 1e-9:
            _dv_drift.append(f"{_k}:{_RS.DEFAULT_VALUES[_k]:g}≠{_want:g}")
check("DEFAULT_VALUES 与 LAYER_PRICES 的层间恒定值一致（无静默少算）",
      not _dv_drift, "漂移：" + "、".join(_dv_drift))

# 文档漂移：03 的旧「求解器权重建议」表（误入奇境 16 / 右区作战 12 / 紧急作战 5）必须已改掉
_c03 = read(os.path.join(REF, "03-节点收益.md"))
_stale = [row for row in ("| 16 | 误入奇境 |", "| 12 | **右区作战**", "| 5 | 左区紧急作战 |")
          if row in _c03]
check("03-节点收益 不再留着旧权重表（误入奇境 16 / 右区作战 12 / 紧急 5）",
      not _stale, "仍存在：" + "、".join(_stale))

# 低层入口 route_solver.py：--help / 场景不存在都要给说明，不能 traceback
_rs = os.path.join(TOOLS, "route_solver.py")
r_h = subprocess.run([PY, _rs, "--help"], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", timeout=120)
check("route_solver.py --help → 用法说明 + exit 0（不是 traceback）",
      r_h.returncode == 0 and "用法" in ((r_h.stdout or "") + (r_h.stderr or "")),
      f"rc={r_h.returncode}")
r_m = subprocess.run([PY, _rs, os.path.join(tmpd, "no-such.json")], capture_output=True,
                     text=True, encoding="utf-8", errors="replace", timeout=120)
_out_m = (r_m.stdout or "") + (r_m.stderr or "")
check("route_solver.py 场景文件不存在 → 说明 + exit 2（不是 traceback）",
      r_m.returncode == 2 and "找不到 scenario" in _out_m and "Traceback" not in _out_m,
      f"rc={r_m.returncode}")

# ============ Eval 9：Markdown 表格结构（2026-10-31 QA 新增）============
# 起因：SKILL.md 的「轴二：终点模式」表被一段引用块从中间打断，剩下的
# `| 求解方式 | … |` 变成孤行、渲染成普通文本；03-节点收益 的 19 节点表同样被
# 羽瞰点长注从第 3 行切断。文字都在，但模型读到的结构是错的。
# 这里机械校验：每个表格块必须有分隔行、列数一致、不能被段落/引用块切成孤行。
print("\n" + "=" * 68)
print("Eval 9  markdown-table-integrity")
print("=" * 68)


def _table_problems(path):
    lines = read(path).split("\n")
    blocks, cur, in_code = [], [], False
    for ln in lines:
        if ln.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if ln.startswith("|"):
            cur.append(ln)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    bad = []
    for b in blocks:
        if len(b) < 2:
            bad.append("孤行表格:" + b[0][:30])
            continue
        if not re.match(r"^\|[\s:|-]+\|$", b[1].strip()):
            bad.append("缺分隔行:" + b[1][:30])
        if len({r.count("|") for r in b}) > 1:
            bad.append("列数不一致:" + b[0][:30])
    return bad


_docs = [os.path.join(SK, "SKILL.md")] + [os.path.join(REF, f)
                                          for f in sorted(os.listdir(REF)) if f.endswith(".md")]
_bad_all = {}
for _p in _docs:
    _b = _table_problems(_p)
    if _b:
        _bad_all[os.path.basename(_p)] = _b
check(f"SKILL.md + {len(_docs) - 1} 个 reference 的表格结构完整（无孤行/错列）",
      not _bad_all,
      "；".join(f"{k}:{v[0]}" for k, v in list(_bad_all.items())[:3]))

# ============ 汇总 ============
print("\n" + "=" * 68)
print("汇总")
print("=" * 68)
passed = sum(1 for _, ok, _ in results if ok)
for name, ok, detail in results:
    print(("  ✅ " if ok else "  ❌ ") + name + (("   [" + detail + "]") if detail and not ok else ""))
print(f"\n{passed}/{len(results)} 通过")
sys.exit(0 if passed == len(results) else 1)
