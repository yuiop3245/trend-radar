"""야후 파이낸스에서 시세를 받아 data.json을 만든다.

tickers.json  : 종목 목록 (여기에 추가하면 다음 실행부터 반영)
signals.csv   : 직접 남기는 신호 기록 (date,symbol,signal,memo)
data.json     : 페이지가 읽는 결과 파일
"""
import csv
import datetime as dt
import json
import math
import os
import sys

import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data.json")

# (키, yfinance 기간, 간격, 장중 여부)
FRAMES = [
    ("d", "3y", "1d", False),
    ("h1", "60d", "1h", True),
    ("h30", "60d", "30m", True),
    ("m5", "5d", "5m", True),
]
MINUTES = {"1h": 60, "30m": 30, "5m": 5}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def to_bars(df, intraday, interval=None):
    out = []
    now = dt.datetime.now(dt.timezone.utc)
    if df is None or df.empty:
        return out
    idx = df.index
    if intraday and getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York")
    for ts, (_, row) in zip(idx, df.iterrows()):
        # 아직 마감되지 않은 장중 봉은 버린다 (원본처럼 확정봉만 사용)
        if intraday and interval in MINUTES:
            end = min(ts + dt.timedelta(minutes=MINUTES[interval]), ts.replace(hour=16, minute=0))
            if end.astimezone(dt.timezone.utc) > now:
                continue
        vals = [row.get("Open"), row.get("High"), row.get("Low"), row.get("Close")]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
            continue
        o, h, l, c = (round(float(v), 4) for v in vals)
        v = row.get("Volume")
        v = 0 if v is None or (isinstance(v, float) and math.isnan(v)) else int(v)
        t = ts.strftime("%Y-%m-%d %H:%M") if intraday else ts.strftime("%Y-%m-%d")
        out.append({"t": t, "o": o, "h": max(h, o, c), "l": min(l, o, c), "c": c, "v": v})
    # 중복 시각 제거 (마지막 값 유지)
    dedup = {b["t"]: b for b in out}
    return [dedup[k] for k in sorted(dedup)]


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
    prev = load_json(OUT, {}).get("tickers", {})
    recs = load_signals(os.path.join(ROOT, "signals.csv"))

    data = {"v": 1, "order": [], "tickers": {}}
    failed = []
    for item in tickers:
        sym = item["symbol"].strip().upper()
        entry = {"name": item.get("name", ""), "base": item.get("base"), "dir": item.get("dir", 1),
                 "rec": sorted(recs.get(sym, []), key=lambda r: r["t"])}
        tk = yf.Ticker(sym)
        for key, period, interval, intraday in FRAMES:
            try:
                bars = to_bars(tk.history(period=period, interval=interval, auto_adjust=False, prepost=False), intraday, interval)
            except Exception as e:  # 네트워크·야후 오류
                print(f"{sym} {interval} 실패: {e}", file=sys.stderr)
                bars = []
            if not bars:
                bars = prev.get(sym, {}).get(key, [])  # 이전 값 유지
                failed.append(f"{sym}:{interval}")
            entry[key] = bars
        data["order"].append(sym)
        data["tickers"][sym] = entry
        print(f"{sym}: 일봉 {len(entry['d'])} · 1시간 {len(entry['h1'])} · 30분 {len(entry['h30'])} · 5분 {len(entry['m5'])}")

    # 기준지수 (EWY / SOXX / QQQ 등): 5분·30분·1시간봉만
    prev_bases = load_json(OUT, {}).get("bases", {})
    data["bases"] = {}
    for bsym in sorted({(t.get("base") or "").upper() for t in tickers} - {""}):
        tk = yf.Ticker(bsym)
        entry = {}
        for key, period, interval, intraday in FRAMES:
            if key == "d":
                continue
            try:
                bars = to_bars(tk.history(period=period, interval=interval, auto_adjust=False, prepost=False), intraday, interval)
            except Exception as e:
                print(f"{bsym} {interval} 실패: {e}", file=sys.stderr)
                bars = []
            if not bars:
                bars = prev_bases.get(bsym, {}).get(key, [])
                failed.append(f"{bsym}:{interval}")
            entry[key] = bars
        data["bases"][bsym] = entry
        print(f"기준지수 {bsym}: 1시간 {len(entry['h1'])} · 30분 {len(entry['h30'])} · 5분 {len(entry['m5'])}")

    now = dt.datetime.now(dt.timezone.utc)
    data["updatedAt"] = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    data["savedAt"] = int(now.timestamp() * 1000)
    data["source"] = "Yahoo Finance via yfinance"
    if failed:
        data["failed"] = failed
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"data.json 저장 ({os.path.getsize(OUT) // 1024}KB)" + (f" · 실패 {failed}" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
