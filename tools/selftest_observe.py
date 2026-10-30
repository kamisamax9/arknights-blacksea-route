#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_observe.py — 锁住「不许拿旧图规划」和「adb 定位不能退回写死」

起因（2026-10-29 事故）
----------------------
observe.py 把 adb 路径写死 → 抓图失败 → 执行方去翻 runs/shots 里的旧截图，
把上一局的「血色空脉」三层当成当前局面出方案。这类错误**算得越准越糟**，
所以必须有机械的回归测试，而不是靠「下次注意」。

跑法：``python tools/selftest_observe.py``（不需要模拟器；不会碰真实 runs/shots）
"""
import os
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import observe as O          # noqa: E402
import adb_find as AF        # noqa: E402

FAILED = []
PASSED = []


def check(name, fn):
    try:
        fn()
        PASSED.append(name)
        print(f"  ✅ {name}")
    except AssertionError as e:
        FAILED.append((name, str(e)))
        print(f"  ❌ {name}：{e}")
    except Exception:
        FAILED.append((name, traceback.format_exc()))
        print(f"  ❌ {name}：异常\n{traceback.format_exc()}")


def _mk(path, age_sec):
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    t = time.time() - age_sec
    os.utime(path, (t, t))


def _isolated(root, name):
    """每个用例一个独立目录：否则上一个用例留下的「更新的一张」会把下一个用例顶掉。"""
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    O.RUNS = d
    return d


def _expect_exit(fn, must_contain=()):
    try:
        fn()
    except SystemExit as e:
        msg = str(e)
        for kw in must_contain:
            assert kw in msg, f"拒绝信息里缺少「{kw}」，实际：{msg[:300]}"
        return msg
    raise AssertionError("应当拒绝，但它放行了")


def main():
    tmp = tempfile.mkdtemp(prefix="bs_observe_selftest_")
    old_runs = O.RUNS
    old_cache = AF.CACHE
    O.RUNS = tmp
    AF.CACHE = os.path.join(tmp, "adb.cache.json")   # 绝不污染真实缓存
    try:
        print("── 旧图闸（Freshness gate）──")

        def t_fresh_ok():
            d = _isolated(tmp, "fresh_ok")
            p = os.path.join(d, "20261029-200000.png")
            _mk(p, 5)                       # 5 秒前，且是最新
            O.check_image_freshness(p)

        def t_old_rejected():
            d = _isolated(tmp, "old")
            p = os.path.join(d, "20261029-190000.png")
            _mk(p, 3600)                    # 1 小时前（该目录里唯一一张）
            msg = _expect_exit(lambda: O.check_image_freshness(p),
                               ["旧图闸", "分钟", "正确做法"])
            assert "不许换一张更旧的图绕过" in msg, "缺少反绕行提示"

        def t_not_newest_rejected():
            d = _isolated(tmp, "not_newest")
            newest = os.path.join(d, "20261029-201000.png")
            older = os.path.join(d, "20261029-200500.png")
            _mk(newest, 30)                 # 30 秒前（最新）
            _mk(older, 400)                 # 400 秒前 → 非最新；且 400s > 300s 也太旧
            msg = _expect_exit(lambda: O.check_image_freshness(older),
                               ["runs/shots 里有更新的截图", "20261029-201000.png"])
            assert "你传的" in msg

        def t_not_newest_but_fresh():
            # 只差 80 秒、但都在阈值内：非最新这一条要能单独触发（不靠「太旧」兜住）
            d = _isolated(tmp, "not_newest_fresh")
            newest = os.path.join(d, "20261029-210000.png")
            older = os.path.join(d, "20261029-205900.png")
            _mk(newest, 20)
            _mk(older, 100)
            _expect_exit(lambda: O.check_image_freshness(older, max_age=600),
                         ["runs/shots 里有更新的截图"])

        def t_derived_files_ignored():
            # _title / annotated / _b 是派生图，不能算「最新截图」把正常图顶掉
            d = _isolated(tmp, "derived")
            newest = os.path.join(d, "20261029-202000.png")
            _mk(newest, 20)
            _mk(os.path.join(d, "20261029-202000_title.png"), 1)
            _mk(os.path.join(d, "20261029-202000_b.png"), 1)
            _mk(os.path.join(d, "_scene_annotated.png"), 1)
            p, _t = O.newest_shot()
            assert os.path.basename(p) == "20261029-202000.png", f"最新图被派生图顶掉了：{p}"

        def t_allow_stale_passes():
            d = _isolated(tmp, "allow_stale")
            p = os.path.join(d, "20261029-180000.png")
            _mk(p, 7200)
            O.check_image_freshness(p, allow_stale=True)   # 放行但不抛

        def t_max_age_param():
            d = _isolated(tmp, "max_age")
            p = os.path.join(d, "20261029-181000.png")
            _mk(p, 120)
            O.check_image_freshness(p, max_age=600)        # 120s < 600s 且是最新 → 放行
            _expect_exit(lambda: O.check_image_freshness(p, max_age=10),
                         ["旧图闸"])

        check("新鲜且最新 → 放行", t_fresh_ok)
        check("1 小时前的图 → 拒绝并给出处置顺序", t_old_rejected)
        check("不是 runs/shots 里最新的一张 → 拒绝并点名更新那张", t_not_newest_rejected)
        check("非最新但没超龄 → 单独由「非最新」拦下", t_not_newest_but_fresh)
        check("_title/_b/annotated 派生图不参与「最新」判定", t_derived_files_ignored)
        check("--allow-stale → 放行（不静默）", t_allow_stale_passes)
        check("--max-age 阈值生效", t_max_age_param)

        print("── adb 定位（永不写死单一路径）──")

        def t_explicit_missing_falls_back():
            # 显式路径贴错不该卡死：打 ⚠️ 后继续自动找
            p = AF.find_adb(os.path.join(tmp, "nope", "adb.exe"))
            assert os.path.isfile(p), f"没有回退到可用的 adb：{p}"

        def t_none_found():
            # 把「所有候选」清空，模拟一台完全没有 adb 的机器
            old_cp, old_cache = AF.candidate_paths, AF._load_cache
            AF.candidate_paths = lambda: []
            AF._load_cache = lambda: {}
            try:
                msg = _expect_exit(lambda: AF.find_adb(),
                                   ["找不到 adb.exe", "已尝试", "--adb", "ARKNIGHTS_ADB",
                                    "不要改用 runs/shots/ 里的旧截图"])
                assert "①" in msg and "②" in msg, "缺少分步骤自救指引"
            finally:
                AF.candidate_paths, AF._load_cache = old_cp, old_cache

        def t_explicit_ok():
            p = AF.find_adb(sys.executable)
            assert os.path.isfile(p), p
            assert os.path.isabs(p), p

        def t_static_list_not_empty():
            # 关键回归：候选里必须包含本机实际路径（E 盘 MuMu），否则又会退回「写死一个路径」
            joined = "|".join(p for _s, p in AF.candidate_paths())
            assert "MuMuPlayer" in joined, "候选里没有 MuMu 路径"
            assert "shell\\adb.exe" in joined or "shell\\\\adb.exe" in joined, joined[:200]

        def t_env_var():
            key = "ARKNIGHTS_ADB"
            old = os.environ.get(key)
            fake = os.path.join(tmp, "env_adb.exe")
            open(fake, "wb").close()
            os.environ[key] = fake
            try:
                assert AF.find_adb() == os.path.abspath(fake)
            finally:
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old

        def t_temp_adb_not_cached():
            # 单测造的假 adb 落在临时目录 → 绝不许写进真实缓存
            # （2026-10-29 实际发生：缓存被写成假路径，之后 grab.py 一直找不到设备）
            fake = os.path.join(tmp, "fake_adb.exe")
            open(fake, "wb").close()
            AF._save_cache(adb=fake)
            data = AF._load_cache()
            assert data.get("adb") != fake, f"临时 adb 被写进缓存：{data.get('adb')}"

        check("显式路径贴错 → ⚠️ 后自动回退（不卡死）", t_explicit_missing_falls_back)
        check("全机找不到 adb → 列全尝试过的地方 + 反旧图提示", t_none_found)
        check("显式路径存在 → 直接用", t_explicit_ok)
        check("候选清单含常见安装路径（不再只有一个写死路径）", t_static_list_not_empty)
        check("环境变量 ARKNIGHTS_ADB 生效", t_env_var)
        check("临时目录里的 adb 不写进缓存（防污染）", t_temp_adb_not_cached)

        print("── 地图闸（map_likeness 双界：太黑/太亮都不算地图）──")

        def _save_gray(path, arr):
            from PIL import Image
            import numpy as np
            Image.fromarray(np.asarray(arr, dtype="uint8"), mode="L").save(path)

        def t_map_loading_black():
            import numpy as np
            p = os.path.join(tmp, "loading.png")
            _save_gray(p, np.full((864, 1920), 16))
            ok, m, p90 = O.G.map_likeness(p)
            assert not ok, f"纯黑 Loading 画面被判成地图（p90={p90}）"

        def t_map_normal():
            import numpy as np
            rng = np.random.default_rng(7)
            p = os.path.join(tmp, "map.png")
            _save_gray(p, rng.integers(0, 70, size=(864, 1920)))
            ok, m, p90 = O.G.map_likeness(p)
            assert ok, f"正常地图灰阶分布被判成非地图（mean={m:.0f} p90={p90:.0f}）"

        def t_map_bright_ui():
            import numpy as np
            p = os.path.join(tmp, "menu.png")
            _save_gray(p, np.full((864, 1920), 190))
            ok, _m, _p90 = O.G.map_likeness(p)
            assert not ok, "亮色主界面被判成地图"

        check("Loading 黑屏 → 不是地图", t_map_loading_black)
        check("正常地图灰阶 → 是地图", t_map_normal)
        check("亮色主界面 → 不是地图", t_map_bright_ui)
    finally:
        O.RUNS = old_runs
        AF.CACHE = old_cache

    print()
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name, why in FAILED:
            print(f"  ❌ {name}\n{why}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
