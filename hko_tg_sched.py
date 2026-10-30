#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按天文台更新节奏推送：观测在 :07/:17/:27… 发布，所以在 :06/:16/:26… 开窗，
每 30 秒探一次，抓到新观测就发一条，然后睡到下一个 :06，不再重复请求。"""
import os, sys, time, fcntl, datetime as dt
import hko_tg

INTERVAL_MIN = 10          # 更新间隔（10 分钟）
WINDOW_SEC   = 240         # 开窗后最多等 4 分钟
POLL_SEC     = 5
STATE = os.getenv("STATE_FILE", ".hko_tg_state")

def next_window(now=None):
    """下一个 分钟数 % 10 == 6 的整点时刻"""
    now = now or dt.datetime.now()
    m = now.replace(second=0, microsecond=0)
    delta = (6 - (m.minute % INTERVAL_MIN)) % INTERVAL_MIN
    t = m + dt.timedelta(minutes=delta)
    if t <= now:
        t += dt.timedelta(minutes=INTERVAL_MIN)
    return t

def last_obs():
    try: return open(STATE).read().strip()
    except Exception: return ""

_seen_lm = {"v": None}
LM_URL = hko_tg.BASE + "latestReadings_AWS1_v2.txt"

def poll_once(dry=False):
    lm = hko_tg.head_lm(LM_URL)
    t, msg = hko_tg.build_message()
    if t == last_obs():
        if lm: _seen_lm["v"] = lm      # 已推过这一版，记住 LM 免重复下载
        return False
    if lm: _seen_lm["v"] = lm
    lk = open(".hko_send.lock", "w")          # 多实例时串行化，避免重复推送
    fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        if last_obs() == t:                   # 拿到锁后再确认一次
            return False
        if hko_tg.send(msg, dry=dry):
            open(STATE, "w").write(t)
            print(f"[{dt.datetime.now():%H:%M:%S}] pushed {t} (lm={lm})", flush=True)
            return True
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()
    print(f"[{dt.datetime.now():%H:%M:%S}] send failed", flush=True)
    return False

def run_cont(dry=False):
    """连续模式：一直每 POLL_SEC 秒探一次，直到 MAX_RUN 秒（适合 GitHub Actions 每次跑 4~5 分钟）"""
    max_run = float(os.getenv("MAX_RUN", "280"))
    t0 = time.time()
    poll_once(dry)
    while time.time() - t0 < max_run:
        time.sleep(POLL_SEC)
        poll_once(dry)
    print("done", flush=True)

def run(dry=False):
    # 启动即补一次（拿到当前最新，避免等 10 分钟）
    poll_once(dry)
    max_run = float(os.getenv("MAX_RUN", "0"))
    t0 = time.time()
    while True:
        if max_run and time.time() - t0 > max_run:
            print("max runtime reached, exit", flush=True); return
        w = next_window()
        time.sleep(max(0, (w - dt.datetime.now()).total_seconds()))
        print(f"[{dt.datetime.now():%H:%M:%S}] window open, polling", flush=True)
        end = time.time() + WINDOW_SEC
        while time.time() < end:
            if poll_once(dry):
                break
            time.sleep(POLL_SEC)
        else:
            print(f"[{dt.datetime.now():%H:%M:%S}] no new obs in window", flush=True)

if __name__ == "__main__":
    # 单实例锁：重复启动时后者直接退出，避免重复推送
    import fcntl
    _lk = open(".hko_sched.lock", "w")
    try:
        fcntl.flock(_lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another scheduler is running, exit", flush=True)
        sys.exit(0)
    if os.getenv("MODE") == "cont":
        run_cont(dry="--dry-run" in sys.argv)
    else:
        run(dry="--dry-run" in sys.argv)
