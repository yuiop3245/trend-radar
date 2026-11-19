"""코스피200 · 코스닥150 구성종목 일봉을 data_kr.json 으로 따로 만든다.

- '내 종목'(data.json)과 분리해서, 화면에서 그 그룹을 눌렀을 때만 불러온다 (사이트를 가볍게)
- 일봉만, 최근 약 2년 (쭈야 신호·240일선 계산에 충분)
- 구성종목 목록: FinanceDataReader → pykrx → 네이버(코스피200) → 시가총액 상위(근사) 순서로 시도, 받은 목록은 .uni_cache 에 저장해 재사용
  (구성종목은 6월·12월 정기변경 때만 바뀜 → 7일마다 다시 확인)
"""
import datetime as dt
import json
import math
import os
import re
import sys
import time

import requests
import yfinance as yf

GROUPS = {"kospi200": ("1028", ".KS", "코스피200"), "kosdaq150": ("2203", ".KQ", "코스닥150")}
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def _log(msg):
    print(msg, flush=True)


def _df_members(df):
    """표(데이터프레임)에서 6자리 코드와 이름 열을 찾아 [(코드, 이름)]"""
    if df is None or getattr(df, "empty", True):
        return []
    cols = list(df.columns)
    code_col = next((c for c in cols if str(c).lower() in ("code", "symbol", "종목코드", "isu_srt_cd", "ticker")), None)
    name_col = next((c for c in cols if str(c).lower() in ("name", "종목명", "isu_abbrv", "isu_nm")), None)
    out = []
    for _, r in df.iterrows():
        code = str(r[code_col] if code_col else "").strip().zfill(6)
        if re.fullmatch(r"\d{6}|\d{5}[A-Z0-9]", code):
            out.append((code, str(r[name_col]).strip() if name_col else ""))
    return out


def from_fdr(idx):
    import FinanceDataReader as fdr
    return _df_members(fdr.SnapDataReader(f"KRX/INDEX/STOCK/{idx}"))


def from_pykrx(idx):
    from pykrx import stock
    codes = stock.get_index_portfolio_deposit_file(idx)
    return [(c, stock.get_market_ticker_name(c)) for c in codes]


def from_naver_k200():
    out, seen = [], set()
    for page in range(1, 25):
        r = requests.get(f"https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page={page}", headers=UA, timeout=15)
        r.encoding = "euc-kr"
        rows = re.findall(r'code=(\d{6})"[^>]*>([^<]+)</a>', r.text)
        new = [(c, n.strip()) for c, n in rows if c not in seen]
        if not new:
            break
        for c, n in new:
            seen.add(c)
            out.append((c, n))
        time.sleep(0.2)
    return out


CAP_RAW = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/krx/"


def from_cap(g):
    """정확한 구성종목을 못 받을 때: 거래소 상장목록에서 시가총액 상위로 근사 (코스피 200 / 코스닥 150).
    보통주만, 스팩·리츠 제외. 실제 지수(업종 대표성·유동성 심사)와 몇 종목 다를 수 있음."""
    import csv
    import io
    want, n = ("KOSPI", 200) if g == "kospi200" else ("KOSDAQ", 150)
    day = dt.datetime.now(KST).date()
    for _ in range(12):
        rows = []
        try:
            r = requests.get(CAP_RAW + day.isoformat() + ".csv", headers=UA, timeout=30)
            text = r.content.decode("utf-8-sig") if r.status_code == 200 else ""
        except requests.RequestException:
            text = ""
        day -= dt.timedelta(days=1)
        for row in csv.DictReader(io.StringIO(text)):
            code, name, mk = (row.get("Code") or "").strip(), (row.get("Name") or "").strip(), row.get("Market") or ""
            try:
                cap = float(row.get("Marcap") or 0)
            except ValueError:
                cap = 0
            if cap <= 0 or not mk.startswith(want) or mk == "KONEX" or not re.fullmatch(r"\d{5}0", code):
                continue
            if "스팩" in name or name.endswith("리츠"):
                continue
            rows.append((cap, code, name))
        if len(rows) >= n * 2:  # 장 시작 전 파일은 시총이 비어 있음 → 전날 파일로
            break
    else:
        return []
    rows.sort(reverse=True)
    return [(c, nm) for _, c, nm in rows[:n]]


def members(cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "members.json")
    try:
        old = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    fresh = old.get("at") and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(old["at"])).days < 7
    approx = any("근사" in v for v in old.get("src", {}).values())
    if approx and old.get("at") and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(old["at"])).days >= 1:
        fresh = False
    if fresh and all(old.get("groups", {}).get(g) for g in GROUPS):
        return old, "저장된 목록"
    groups, src = {}, {}
    for g, (idx, _, nm) in GROUPS.items():
        tries = [("FinanceDataReader", lambda: from_fdr(idx)), ("pykrx", lambda: from_pykrx(idx))]
        if g == "kospi200":
            tries.append(("네이버", from_naver_k200))
        tries.append(("시총 상위 근사", lambda g=g: from_cap(g)))
        for name, fn in tries:
            try:
                m = fn()
                if len(m) >= (180 if g == "kospi200" else 130):
                    groups[g], src[g] = m, name
                    break
                _log(f"{nm} {name}: {len(m)}종목뿐 → 다음 방법")
            except Exception as ex:
                _log(f"{nm} {name} 실패: {type(ex).__name__} {str(ex)[:120]}")
        if g not in groups and old.get("groups", {}).get(g):
            groups[g], src[g] = old["groups"][g], "이전에 저장된 목록"
    res = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "groups": groups, "src": src}
    if groups:
        json.dump(res, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return res, "새로 받음"


def _bars(df):
    out = []
    if df is None or df.empty:
        return out
    for ts, r in df.iterrows():
        try:
            o, h, l, c = float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])
        except (KeyError, TypeError, ValueError):
            continue
        if any(math.isnan(x) for x in (o, h, l, c)) or c <= 0:
            continue
        if o <= 0 or h <= 0 or l <= 0:
            o = h = l = c
        v = r.get("Volume")
        v = 0 if v is None or (isinstance(v, float) and math.isnan(v)) else int(v)
        rd = (lambda x: int(round(x))) if c >= 100 else (lambda x: round(x, 2))
        out.append([ts.strftime("%Y-%m-%d"), rd(o), rd(max(h, o, c)), rd(min(l, o, c)), rd(c), v])
    return out



def _last_close(now):
    """가장 최근 국장 마감(15:40) 시각"""
    k = now.astimezone(KST)
    c = k.replace(hour=15, minute=40, second=0, microsecond=0)
    if k < c:
        c -= dt.timedelta(days=1)
    while c.weekday() >= 5:
        c -= dt.timedelta(days=1)
    return c


def build(root):
    t0 = time.time()
    cache = os.path.join(root, ".uni_cache", "data_kr.json")
    now = dt.datetime.now(dt.timezone.utc)
    k = now.astimezone(KST)
    in_session = k.weekday() < 5 and (8 * 60 + 50) <= k.hour * 60 + k.minute <= 16 * 60
    try:
        old_at = json.load(open(cache, encoding="utf-8")).get("updatedAt")
        old_at = dt.datetime.fromisoformat(old_at.replace("Z", "+00:00")) if old_at else None
    except (OSError, ValueError):
        old_at = None
    if not in_session and old_at and old_at >= _last_close(now):
        # 국장이 닫혀 있고 마감 뒤 데이터가 이미 있으면 다시 받지 않고 그대로 씀 (미국장 갱신 때 시간 절약)
        import shutil
        shutil.copy(cache, os.path.join(root, "data_kr.json"))
        _log("코스피200·코스닥150: 장 마감 뒤 데이터 그대로 사용")
        return True
    mem, how = members(os.path.join(root, ".uni_cache"))
    groups = mem.get("groups") or {}
    if not groups:
        _log("코스피200·코스닥150 목록을 못 받아서 이번엔 건너뜀")
        return None
    syms, names, grp = [], {}, {}
    for g, lst in groups.items():
        suf = GROUPS[g][1]
        grp[g] = []
        for code, name in lst:
            s = code + suf
            grp[g].append(s)
            names[s] = (name, code)
            syms.append(s)
    tickers = {}
    for i in range(0, len(syms), 100):  # 100개씩 나눠 받기
        chunk = syms[i:i + 100]
        try:
            df = yf.download(chunk, period="2y", interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False)
        except Exception as ex:
            _log(f"일봉 묶음 실패: {type(ex).__name__}")
            continue
        for s in chunk:
            try:
                sub = df[s] if len(chunk) > 1 else df
            except KeyError:
                continue
            b = _bars(sub)
            if len(b) >= 60:
                tickers[s] = {"label": names[s][0], "name": names[s][1], "kr": True, "base": "^KS11", "d": b}
    missing = [s for s in syms if s not in tickers]
    out = {"v": 1, "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
           "groups": {g: [s for s in lst if s in tickers] for g, lst in grp.items()},
           "names": {g: GROUPS[g][2] for g in grp}, "src": mem.get("src", {}), "memberAt": mem.get("at"),
           "missing": missing[:60], "tickers": tickers}
    raw = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with open(os.path.join(root, "data_kr.json"), "wb") as f:
        f.write(raw)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "wb") as f:
        f.write(raw)
    _log(f"코스피200·코스닥150: 목록 {how}({', '.join(f'{k} {len(v)}' for k, v in groups.items())}) · 일봉 {len(tickers)}종목 · 빠짐 {len(missing)} · {len(raw) // 1024}KB · {time.time() - t0:.0f}초")
    return out


if __name__ == "__main__":
    build(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
