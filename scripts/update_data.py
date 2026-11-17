"""야후 파이낸스에서 시세를 받아 data.json을 만든다.

tickers.json  : 종목 목록 (여기에 추가하면 다음 실행부터 반영)
signals.csv   : 직접 남기는 신호 기록 (date,symbol,signal,memo)
data.json     : 페이지가 읽는 결과 파일

봉 형식: [시각, 시가, 고가, 저가, 종가, 거래량] (용량을 줄이려고 배열로 저장)
한국 종목(.KS/.KQ/^KS11/^KQ11)은 장 시간이 09:00~15:30(6시간 30분)이라
미국 장(09:30~16:00)과 같은 틀로 계산하도록 장중 시각을 +30분 옮겨 저장하고
"kr": true 로 표시한다. 화면에서는 다시 30분을 빼서 한국 시간으로 보여준다.
"""
import csv
import datetime as dt
import json
import math
import os
import sys
import time

import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data.json")

# (키, yfinance 기간, 간격, 장중 여부)
FRAMES = [
    ("d", "5y", "1d", False),
    ("h1", "60d", "1h", True),
    ("h30", "60d", "30m", True),
    ("m5", "5d", "5m", True),
]
MINUTES = {"1h": 60, "30m": 30, "5m": 5}
BENCH = {"SPY", "^KS11"}  # RS 계산용 일봉을 받는 기준


def is_kr(sym):
    s = sym.upper()
    return s.endswith(".KS") or s.endswith(".KQ") or s in ("^KS11", "^KQ11")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def to_bars(df, intraday, interval, kr):
    out = {}
    now = dt.datetime.now(dt.timezone.utc)
    if df is None or df.empty:
        return []
    idx = df.index
    tzname = "Asia/Seoul" if kr else "America/New_York"
    if intraday and getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(tzname)
    for ts, (_, row) in zip(idx, df.iterrows()):
        if intraday:
            # 아직 마감되지 않은 장중 봉은 버린다 (확정봉만 사용)
            close_at = ts.replace(hour=15, minute=30) if kr else ts.replace(hour=16, minute=0)
            end = min(ts + dt.timedelta(minutes=MINUTES[interval]), close_at)
            if end.astimezone(dt.timezone.utc) > now:
                continue
        vals = [row.get("Open"), row.get("High"), row.get("Low"), row.get("Close")]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
            continue
        o, h, l, c = (round(float(v), 4) for v in vals)
        if c <= 0:
            continue
        v = row.get("Volume")
        v = 0 if v is None or (isinstance(v, float) and math.isnan(v)) else int(v)
        if intraday:
            t = (ts + dt.timedelta(minutes=30)) if kr else ts
            t = t.strftime("%Y-%m-%d %H:%M")
        else:
            t = ts.strftime("%Y-%m-%d")
        out[t] = [t, o, max(h, o, c), min(l, o, c), c, v]
    return [out[k] for k in sorted(out)]


def fetch(sym, keys, prev, failed):
    kr = is_kr(sym)
    tk = yf.Ticker(sym)
    entry = {}
    for key, period, interval, intraday in FRAMES:
        if key not in keys:
            continue
        bars = []
        for attempt in range(2):
            try:
                bars = to_bars(tk.history(period=period, interval=interval, auto_adjust=False, prepost=False), intraday, interval, kr)
                break
            except Exception as e:  # 네트워크·야후 오류
                print(f"{sym} {interval} 실패({attempt + 1}): {e}", file=sys.stderr)
                time.sleep(2)
        if not bars:
            bars = prev.get(key, [])  # 이전 값 유지
            failed.append(f"{sym}:{interval}")
        entry[key] = bars
        time.sleep(0.2)
    if kr:
        entry["kr"] = True
        if entry.get("d"):
            fill_kr_daily(tk, sym, entry)
    return entry


def fill_kr_daily(tk, sym, entry):
    """야후 국내 일봉은 하루씩 늦게 들어오는 날이 있다(삼성전자·SK하이닉스·코스피 등).
    1) 짧은 기간 일봉을 한 번 더 받아 더 최신 날짜가 있으면 붙이고
    2) 그래도 빠진 날은 30분봉(없으면 1시간봉)을 모아 일봉을 만든다. 만든 날짜는 dsyn에 적는다."""
    d = entry["d"]
    last = d[-1][0]
    try:
        recent = to_bars(tk.history(period="5d", interval="1d", auto_adjust=False, prepost=False), False, "1d", True)
        for b in recent:
            if b[0] > last:
                d.append(b)
                last = b[0]
    except Exception as e:
        print(f"{sym} 최근 일봉 재확인 실패: {e}", file=sys.stderr)
    src = entry.get("h30") or entry.get("h1") or []
    days = {}
    for t, o, h, l, c, v in src:
        day = t[:10]
        if day <= last:
            continue
        g = days.get(day)
        if g is None:
            days[day] = [day, o, h, l, c, v]
        else:
            g[2] = max(g[2], h); g[3] = min(g[3], l); g[4] = c; g[5] += v
    if days:
        syn = sorted(days)
        d.extend(days[k] for k in syn)
        entry["dsyn"] = syn
        print(f"{sym}: 일봉 지연 → 분봉으로 {', '.join(syn)} 채움")


def load_signals(path):
    recs = {}
    if not os.path.exists(path):
        return recs
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            sym = (row.get("symbol") or "").strip().upper()
            date = (row.get("date") or "").strip()
            sig = (row.get("signal") or "").strip()
            if not (sym and date and sig):
                continue
            recs.setdefault(sym, []).append({"t": date, "sig": sig, "memo": (row.get("memo") or "").strip()})
    return recs


def main():
    tickers = load_json(os.path.join(ROOT, "tickers.json"), [])
    if not tickers:
        print("tickers.json 이 비어 있어요", file=sys.stderr)
        return 1
    old = load_json(OUT, {})
    prev, prev_bases = old.get("tickers", {}), old.get("bases", {})
    recs = load_signals(os.path.join(ROOT, "signals.csv"))

    data = {"v": 2, "order": [], "tickers": {}, "bases": {}}
    failed = []
    all_keys = {k for k, *_ in FRAMES}
    for item in tickers:
        sym = item["symbol"].strip().upper()
        entry = fetch(sym, all_keys, prev.get(sym, {}), failed)
        entry.update({"name": item.get("name", ""), "label": item.get("label", ""), "base": (item.get("base") or "").upper() or None,
                      "dir": item.get("dir", 1), "tv": item.get("tv", ""), "rec": sorted(recs.get(sym, []), key=lambda r: r["t"])})
        data["order"].append(sym)
        data["tickers"][sym] = entry
        print(f"{sym}: " + " · ".join(f"{k} {len(entry[k])}" for k in ("d", "h1", "h30", "m5")))

    # 기준지수: 분봉(1시간·30분·5분)만. 단 SPY·코스피는 RS 계산용 일봉도.
    # 종목 목록에 이미 있는 기호는 다시 받지 않는다 (화면에서 종목 데이터를 그대로 씀).
    need = {(t.get("base") or "").upper() for t in tickers} - {""}
    need |= BENCH
    for bsym in sorted(need):
        if bsym in data["tickers"]:
            continue
        keys = {"h1", "h30", "m5"} if bsym in {(t.get("base") or "").upper() for t in tickers} else set()
        if bsym in BENCH:
            keys.add("d")
        entry = fetch(bsym, keys, prev_bases.get(bsym, {}), failed)
        data["bases"][bsym] = entry
        print(f"기준 {bsym}: " + " · ".join(f"{k} {len(v)}" for k, v in entry.items() if isinstance(v, list)))

    now = dt.datetime.now(dt.timezone.utc)
    data["updatedAt"] = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    data["savedAt"] = int(now.timestamp() * 1000)
    data["source"] = "Yahoo Finance via yfinance"
    if failed:
        data["failed"] = failed
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"data.json 저장 ({os.path.getsize(OUT) // 1024}KB)" + (f" · 실패 {len(failed)}건" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
