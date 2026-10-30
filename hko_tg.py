#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HKO 实况 -> Telegram。可单跑一次（hko_tg.py）或按更新时刻轮询（hko_tg_sched.py）。"""
import os, math, json, hashlib, urllib.request

BASE = "https://www.hko.gov.hk/wxinfo/awsgis/"
HOSTS = ["https://www.hko.gov.hk/wxinfo/awsgis/", "https://www.weather.gov.hk/wxinfo/awsgis/"]
UA = {"User-Agent": "Mozilla/5.0 (compatible; hko-tg/1.0)",
      "Cache-Control": "no-cache", "Pragma": "no-cache"}

def get(url):
    # 加时间戳绕开 CDN（max-age=60）的边缘缓存，否则会晚 10-60 秒才看到新数据
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}_t={int(__import__('time').time()*1000)}", headers=UA)
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")

def head_lm(url):
    """取 Last-Modified；HEAD 比 GET 更容易绕过 CDN 正缓存，用来快速发现文件已更新"""
    try:
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        return urllib.request.urlopen(req, timeout=10).headers.get("Last-Modified")
    except Exception:
        return None

def read_all():
    """两个域名各取一份，取观测时间最新的那份（两者缓存刷新时刻不同，合并可提前数十秒）"""
    best = None
    for base in HOSTS:
        try:
            raw = get(base + "latestReadings_AWS1_v2.txt")
        except Exception:
            continue
        lines = raw.splitlines()
        try:
            hhmm = lines[0].split("at ")[1].split(" Hong Kong")[0]
        except Exception:
            continue
        key = int(hhmm.replace(":", ""))
        if best is None or key > best[0]:
            best = (key, lines)
    if best is None:
        raise RuntimeError("no data from any host")
    lines = best[1]
    head = lines[0]
    t = head.split("at ")[1].split(" Hong Kong")[0]
    d = head.split("Time ")[1].strip()
    rows = {}
    for ln in lines[2:]:
        p = [x.strip() for x in ln.split(",")]
        if p and p[0]:
            rows[p[0].lower()] = p
    return t, d, rows

def read_latest(st="hko"):
    t, d, rows = read_all()
    if st.lower() not in rows:
        raise RuntimeError("station not found: " + st)
    return t, d, rows[st.lower()]

WIND_DIRS = ["北","东北偏北","东北","东北偏东","东","东南偏东","东南","东南偏南",
             "南","西南偏南","西南","西南偏西","西","西北偏西","西北","西北偏北"]

def compass(deg):
    return WIND_DIRS[int((deg % 360) / 22.5 + 0.5) % 16]

def wind_line(rows, st="hka", label="赤鱲角"):
    p = rows.get(st)
    if not p: return ""
    spd, deg, gst = n(p[2]), n(p[1]), n(p[3])
    if spd is None: return ""
    s = f"\n风 {label} {compass(deg) if deg is not None else ''} {int(spd)} km/h"
    if gst: s += f"（阵风 {int(gst)}）"
    return s

def n(v):
    v = (v or "").strip()
    return None if v in ("", "M", "9999", "32767", "T") else float(v)

def dew(t, rh):
    if t is None or not rh: return None
    a, b = 17.625, 243.04
    g = math.log(rh / 100) + a * t / (b + t)
    return round(b * g / (a - g), 1)

def build_message(st="hko", wind_from="sf"):
    t, d, rows = read_all()
    p = rows[st]
    d = d
    temp, rh, mslp, hi = n(p[4]), n(p[5]), n(p[11]), n(p[13])
    msg = (f"🌡 天文台 {t}（{d}）\n"
           f"气温 {temp:.1f}°C　湿度 {rh:.0f}%\n"
           f"露点 {dew(temp, rh)}°C（算）\n"
           f"今日 {n(p[7]) or 0:.1f} ~ {n(p[6]) or 0:.1f}°C\n"
           f"气压 {mslp if mslp else '—'} hPa" + (f"　暑热指数 {hi}" if hi else ""))
    if n(p[2]) is not None:          # 该站自己有风就用自己的
        msg += wind_line(rows, st, "本站")
    elif wind_from and wind_from in rows:
        msg += wind_line(rows, wind_from, "天星码头")
    return t, msg

def send(msg, dry=False):
    if dry:
        print(msg); return True
    token, chat = os.getenv("TG_TOKEN"), os.getenv("TG_CHAT")
    if not (token and chat): raise SystemExit("need TG_TOKEN / TG_CHAT")
    body = json.dumps({"chat_id": chat, "text": msg, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read()).get("ok")

if __name__ == "__main__":
    t, msg = build_message(os.getenv("HKO_STATION", "hko"))
    ok = send(msg, dry="--dry-run" in os.sys.argv)
    print(("sent " if ok else "failed ") + t)
