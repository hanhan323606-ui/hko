#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hko_weather.py —— 香港天文台自动气象站（AWS）实况抓取器

抓取的正是你给的那个页面背后的数据源：
    页面  https://www.hko.gov.hk/sc/wxinfo/awsgis/regional_portal.html?loc=hko&ele=Temperature&show=chart
    实况  https://www.hko.gov.hk/wxinfo/awsgis/latestReadings_AWS1_v2.txt
    曲线  https://www.hko.gov.hk/wxinfo/awsgis/animate_J1.csv      (过去 24h 气温)
          https://www.hko.gov.hk/wxinfo/awsgis/animate_rh.csv      (过去 24h 相对湿度)
          https://www.hko.gov.hk/wxinfo/awsgis/animate_hrwind.csv  (过去 24h 风向/风速/阵风)

字段：气温、相对湿度、露点(计算值)、风向/风速/阵风、最高/最低气温、草温、
      能见度、海平面气压、过去24小时气温变化、暑热指数。

零第三方依赖（仅标准库），Python 3.7+。

用法示例：
    python3 hko_weather.py                          # 香港天文台总部的实况
    python3 hko_weather.py -s hka                    # 赤鱲角（机场，有风数据）
    python3 hko_weather.py -s hko --history 12       # 附带过去 12 小时气温/湿度/风速走势
    python3 hko_weather.py --all                     # 全港所有自动站排行
    python3 hko_weather.py --json                    # 输出结构化 JSON，便于接入其它程序
    python3 hko_weather.py --metar                   # 追加赤鱲角 METAR（官方露点/风）
    python3 hko_weather.py --watch 60                # 每 60 秒刷新一次
"""

import argparse
import json
import math
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://www.hko.gov.hk/wxinfo/awsgis/"
LATEST = BASE + "latestReadings_AWS1_v2.txt"
HIST = {
    "temp": BASE + "animate_J1.csv",        # 气温
    "rh": BASE + "animate_rh.csv",          # 相对湿度
    "wind": BASE + "animate_hrwind.csv",    # 风向,风速,阵风
    "mslp": BASE + "animate_S1.csv",        # 海平面气压
    "vis": BASE + "animate_m1.csv",         # 能见度
}
METAR_URL = "https://aviationweather.gov/api/data/metar?ids=VHHH&format=json&hours=1"

UA = {"User-Agent": "Mozilla/5.0 (compatible; hko-weather-scraper/1.0)"}

# 站点代码 -> 中文名（取自页面 station-config-aws.js）
STATIONS = {
    "hko": "香港天文台", "sha": "沙田", "lfs": "流浮山", "tkl": "打鼓岭", "tu1": "屯门",
    "hks": "黄竹坑", "wgl": "横澜岛", "gi": "青洲", "jkb": "将军澳", "ccb": "长洲泳滩",
    "kp": "京士柏", "plc": "大美督", "slw": "沙螺湾", "skg": "西贡", "tme": "塔门",
    "tyw": "北潭涌", "sek": "石岗", "tms": "大帽山", "hka": "赤鱲角", "tc": "大老山",
    "yct": "大埔", "ngp": "昂坪", "vp1": "山顶", "pen": "坪洲", "ssh": "上水",
    "twn": "荃湾可观", "wlp": "湿地公园", "hkp": "香港公园", "skw": "筲箕湾", "klt": "九龙城",
    "hpv": "跑马地", "ksc": "滘西洲", "wts": "黄大仙", "sty": "赤柱", "ktg": "观塘",
    "swh": "西湾河", "ssp": "深水埗", "tw": "荃湾城门谷", "ty1": "青衣", "tpk": "大埔滘",
    "kfb": "嘉道理农场", "se": "启德", "sf": "天星码头", "sc": "沙洲", "cch": "长洲",
    "cp1": "中环码头", "hss": "香港航海学校", "gsi": "德瑞国际学校", "tlc": "大榄涌",
    "lam": "南丫岛", "se1": "启德跑道公园", "ylp": "元朗公园", "elc": "上水风采中学",
    "vpa": "太平山", "cwb": "清水湾", "ic1": "环球贸易广场", "np": "北角", "tls": "大陇",
    "ks2": "滘西洲", "cs1": "长沙", "brc": "双鱼河", "zcp": "九龙湾",
}

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}

# 表头列序（0-based）
COLS = ["STN", "WINDDIRECTION", "WINDSPEED", "GUST", "TEMP", "RH", "MAXTEMP", "MINTEMP",
        "GRASSTEMP", "GRASSMINTEMP", "VISIBILITY", "PRESSURE", "TEMPDIFFERENCE", "HEATINDEX"]

MISSING = {"", "M", "m", "N/A", "9999", "32767", "T"}


# ---------------------------------------------------------------- 工具函数
def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def num(v, cast=float):
    """把 CSV 里的原始字符串转成数字；缺失值统一返回 None。"""
    v = (v or "").strip()
    if v in MISSING:
        return None
    try:
        return cast(v)
    except ValueError:
        return None


def dew_point(t, rh):
    """Magnus 公式：由气温(°C) + 相对湿度(%) 推算露点。"""
    if t is None or rh is None or rh <= 0:
        return None
    a, b = 17.625, 243.04
    g = math.log(rh / 100.0) + a * t / (b + t)
    return b * g / (a - g)


def compass(deg):
    if deg is None:
        return None
    names = ["北", "东北偏北", "东北", "东北偏东", "东", "东南偏东", "东南", "东南偏南",
             "南", "西南偏南", "西南", "西南偏西", "西", "西北偏西", "西北", "西北偏北"]
    return names[int((deg % 360) / 22.5 + 0.5) % 16]


def hkt(ts=None):
    return (ts or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8)))


def fmt(v, unit="", nd=1):
    if v is None:
        return "—"
    s = (f"{v:.{nd}f}").rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    return f"{s}{unit}"


def sparkline(vals, width=36):
    chars = "▁▂▃▄▅▆▇█"
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    if hi == lo:
        return chars[3] * len(v)
    step = max(1, len(v) // width)
    v = v[::step]
    return "".join(chars[min(7, int((x - lo) / (hi - lo) * 7.999))] for x in v)


# ---------------------------------------------------------------- 数据抓取
def parse_latest():
    """解析 latestReadings_AWS1_v2.txt -> (观测时间, {站号: 字段dict})"""
    raw = fetch(LATEST)
    lines = raw.splitlines()
    head = lines[0]
    m = re.search(r"at\s+(\d{1,2}:\d{2})\s+Hong Kong Time\s+(\d{1,2})\s+(\w+)\s+(\d{4})", head)
    if m:
        hh, mm = m.group(1).split(":")
        obs = datetime(int(m.group(4)), MONTHS.get(m.group(3), 1), int(m.group(2)),
                       int(hh), int(mm), tzinfo=timezone(timedelta(hours=8)))
    else:
        obs = hkt()

    rows = {}
    for line in lines[2:]:
        line = line.strip()
        if not line or line.upper().startswith("STN,"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(COLS):
            parts += [""] * (len(COLS) - len(parts))
        code = parts[0].lower()
        if not code:
            continue
        rows[code] = {
            "station": code,
            "name": STATIONS.get(code, code.upper()),
            "wind_dir_deg": num(parts[1], int),
            "wind_kmh": num(parts[2], int),
            "gust_kmh": num(parts[3], int),
            "temp": num(parts[4]),
            "rh": num(parts[5], int),
            "temp_max": num(parts[6]),
            "temp_min": num(parts[7]),
            "grass_temp": num(parts[8]),
            "grass_min": num(parts[9]),
            "visibility_m": num(parts[10], int),
            "pressure_hpa": num(parts[11]),
            "temp_change_24h": num(parts[12]),
            "heat_index": num(parts[13]),
        }
        td = dew_point(rows[code]["temp"], rows[code]["rh"])
        rows[code]["dew_point"] = round(td, 1) if td is not None else None
        rows[code]["wind_dir"] = compass(rows[code]["wind_dir_deg"])
    return obs, rows


def parse_history(key, station, hours=24):
    """解析 animate_*.csv -> [(datetime, value), ...]"""
    try:
        raw = fetch(HIST[key])
    except Exception:
        return []
    out = []
    for line in raw.splitlines():
        p = [x.strip() for x in line.split(",")]
        # wind 文件格式：时间,风向,风速,站号；其余：时间,数值,站号
        si = 3 if key == "wind" else 2
        if len(p) <= si or p[si].lower() != station:
            continue
        try:
            ts = datetime.strptime(p[0], "%Y%m%d%H%M").replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            continue
        if key == "wind":
            out.append((ts, num(p[1], int), num(p[2], int),
                        num(p[3], int) if si == 4 and len(p) > 4 else None))
        else:
            out.append((ts, num(p[1])))
    out.sort(key=lambda x: x[0])
    return out[-hours:] if hours else out


def parse_metar():
    try:
        d = json.loads(fetch(METAR_URL))
    except Exception:
        return None
    if not d:
        return None
    m = d[0]
    return {
        "station": "VHHH 赤鱲角国际机场（航空例行天气报告，非天文台总部）",
        "time": m.get("reportTime"),
        "temp": m.get("temp"),
        "dew_point": m.get("dewp"),
        "wind_dir_deg": m.get("wdir"),
        "wind_kmh": round(m.get("wspd", 0) * 1.852) if m.get("wspd") is not None else None,
        "wind_kt": m.get("wspd"),
        "raw": m.get("rawOb"),
    }


# ---------------------------------------------------------------- 输出
def render_current(code, rec, obs):
    L = []
    L.append(f"📍 {rec['name']}（{code.upper()}）　观测时间 {obs:%Y-%m-%d %H:%M} HKT")
    L.append("─" * 56)
    L.append(f"  气温          {fmt(rec['temp'], ' °C')}")
    L.append(f"  相对湿度      {fmt(rec['rh'], ' %', 0)}")
    L.append(f"  露点(计算)    {fmt(rec['dew_point'], ' °C')}")
    if rec["wind_kmh"] is not None:
        L.append(f"  风向风速      {rec['wind_dir'] or '—'} {fmt(rec['wind_kmh'], ' km/h', 0)}"
                 f"（{rec['wind_dir_deg']}°）")
        if rec["gust_kmh"] is not None:
            L.append(f"  阵风          {fmt(rec['gust_kmh'], ' km/h', 0)}")
    else:
        L.append("  风向风速      —（该站本次未上报风数据）")
    L.append(f"  今日高/低温   {fmt(rec['temp_max'], ' °C')} / {fmt(rec['temp_min'], ' °C')}")
    if rec["temp_change_24h"] is not None:
        L.append(f"  24h 气温变化  {rec['temp_change_24h']:+.1f} °C")
    if rec["pressure_hpa"] is not None:
        L.append(f"  海平面气压    {fmt(rec['pressure_hpa'], ' hPa')}")
    if rec["visibility_m"] is not None:
        L.append(f"  能见度        {rec['visibility_m'] / 1000:.1f} km")
    if rec["heat_index"] is not None:
        L.append(f"  暑热指数      {fmt(rec['heat_index'], '', 1)}")
    if rec["grass_temp"] is not None:
        L.append(f"  草温          {fmt(rec['grass_temp'], ' °C')}")
    return "\n".join(L)


def render_history(code, hours):
    L = ["", f"🕒 过去 {hours} 小时走势（{code.upper()}）", "─" * 56]
    t = parse_history("temp", code, hours)
    rh = parse_history("rh", code, hours)
    w = parse_history("wind", code, hours)
    if t:
        L.append("  气温  " + sparkline([x[1] for x in t]))
        L.append(f"        {t[0][0]:%m-%d %H:%M} → {t[-1][0]:%m-%d %H:%M}　"
                 f"最低 {min(x[1] for x in t if x[1] is not None):.1f}　"
                 f"最高 {max(x[1] for x in t if x[1] is not None):.1f}　"
                 f"最新 {t[-1][1]:.1f} °C")
    if rh:
        L.append("  湿度  " + sparkline([x[1] for x in rh]))
        L.append(f"        {rh[0][0]:%m-%d %H:%M} → {rh[-1][0]:%m-%d %H:%M}　最新 {rh[-1][1]:.0f} %")
    if w:
        spd = [(ts, s) for ts, d, s, g in w]
        L.append("  风速  " + sparkline([x[1] for x in spd]))
        L.append(f"        {spd[0][0]:%m-%d %H:%M} → {spd[-1][0]:%m-%d %H:%M}　"
                 f"最新 {compass(w[-1][1]) or '—'} {w[-1][2]} km/h"
                 + (f"（阵风 {w[-1][3]} km/h）" if w[-1][3] else ""))
    else:
        L.append("  （该站在过去 24 小时曲线中无风数据）")
    L.append("  注：曲线为每小时整点值，最新一点通常是上一整点。")
    return "\n".join(L)


def render_all(rows, obs):
    L = [f"🌐 全港自动气象站实况　{obs:%m-%d %H:%M} HKT", "─" * 56,
         f"{'站名':<10}{'气温':>8}{'露点':>8}{'湿度':>8}{'风速':>10}{'阵风':>8}"]
    for r in sorted([x for x in rows.values() if x["temp"] is not None],
                    key=lambda x: -x["temp"]):
        L.append(f"{r['name']:<10}"
                 f"{fmt(r['temp'], '°C'):>9}"
                 f"{fmt(r['dew_point'], '°C'):>9}"
                 f"{fmt(r['rh'], '%', 0):>8}"
                 f"{(str(r['wind_kmh']) + ' km/h') if r['wind_kmh'] is not None else '—':>11}"
                 f"{(str(r['gust_kmh']) + '') if r['gust_kmh'] is not None else '—':>8}")
    return "\n".join(L)


def render_metar(m):
    if not m:
        return "（METAR 拉取失败）"
    return "\n".join([
        "",
        f"🛬 参照：{m['station']}",
        "─" * 56,
        f"  气温 {fmt(m['temp'], ' °C', 0)}　露点 {fmt(m['dew_point'], ' °C', 0)}"
        f"　风 {compass(m['wind_dir_deg']) or '—'} {fmt(m['wind_kmh'], ' km/h', 0)}"
        f"（{m['wind_kt']} kt）",
        f"  {m['raw']}",
    ])


# ---------------------------------------------------------------- 主流程
def run_once(args):
    obs, rows = parse_latest()
    if not rows:
        print("未能解析到任何站点数据，请检查网络或数据源是否改版。")
        return 1

    if args.all:
        print(render_all(rows, obs))
    else:
        code = args.station.lower()
        rec = rows.get(code)
        if rec is None:
            print(f"数据源中没有站点 “{code}”。可用代码例如：hko, hka, kp, sha, tkl, yct …")
            return 2
        if args.json:
            payload = {"observed_at": obs.isoformat(), "station": rec,
                       "source": LATEST}
            if args.history:
                payload["history"] = {
                    "temp": [(ts.isoformat(), v) for ts, v in parse_history("temp", code, args.history)],
                    "rh": [(ts.isoformat(), v) for ts, v in parse_history("rh", code, args.history)],
                }
            if args.metar:
                payload["metar_vhhh"] = parse_metar()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(render_current(code, rec, obs))
        if args.history:
            print(render_history(code, args.history))
        if args.metar:
            print(render_metar(parse_metar()))
        if code == "hko" and rec["wind_kmh"] is None:
            print("\n  ⓘ 天文台总部站（hko）不对外发布风的读数；"
                  "要看同一份数据里的风，用 -s hka（赤鱲角）等有风的站。")
        print("\n  数据源：HKO AWS latestReadings_AWS1_v2.txt / animate_*.csv　"
              "露点为气温+湿度推算值")
    return 0


def main():
    p = argparse.ArgumentParser(description="香港天文台自动气象站实况抓取（气温/湿度/露点/风）")
    p.add_argument("-s", "--station", default="hko", help="站点代码，默认 hko（香港天文台总部）")
    p.add_argument("-a", "--all", action="store_true", help="输出全港所有站点")
    p.add_argument("-j", "--json", action="store_true", help="JSON 输出")
    p.add_argument("--history", type=int, default=0, metavar="H", help="附带过去 H 小时走势（最多 24）")
    p.add_argument("--metar", action="store_true", help="附带赤鱲角 METAR（官方露点与风）")
    p.add_argument("--watch", type=int, default=0, metavar="S", help="每 S 秒刷新")
    args = p.parse_args()

    while True:
        if args.watch:
            print(f"\n===== {hkt():%Y-%m-%d %H:%M:%S} HKT =====")
        rc = run_once(args)
        if not args.watch:
            return rc
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())
