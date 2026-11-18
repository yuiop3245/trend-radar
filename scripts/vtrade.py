"""2단계: 가상 주문 — 쭈야 신호대로 '이렇게 주문했을 것'을 기록만 한다. 실제 주문은 절대 보내지 않는다.

흐름 (자동 갱신 때마다)
 1. 대기 중인 가상 주문을 '신호 다음 거래일 시가'로 체결 처리
 2. 각 종목의 가장 최근 '확정된' 일봉(장 마감 뒤)에 난 행동 신호를 읽어 새 가상 주문을 만든다
    사 = 2단위(보유 0일 때) · 불태워 = 1단위(최대 2번) · 분할매도 = 보유의 절반(1주면 유지) · 다정리 = 전부
 3. 하루 매수 횟수·금액 한도를 넘는 매수는 '한도 초과'로 건너뛴다 (매도는 위험을 줄이므로 항상 허용)
상태(보유·주문 기록)는 한투 캐시 안에 홈페이지 비밀번호로 암호화해서 저장한다.
설정은 저장소의 trade.json.
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import secure

KST, ET = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
DEFAULT = {"mode": "virtual", "unit": 1, "symbols": "all", "exclude": [], "unitBy": {},
           "maxBuysPerDay": 6, "maxBuyKRW": 3000000, "maxBuyUSD": 2000}


def load_conf(path):
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    return {**DEFAULT, **c}


def final_day(kr, bars, now):
    """장 마감으로 확정된 가장 최근 일봉 날짜"""
    loc = now.astimezone(KST if kr else ET)
    close = 15 * 60 + 40 if kr else 16 * 60 + 10
    last = bars[-1][0]
    if last == loc.date().isoformat() and loc.weekday() < 5 and loc.hour * 60 + loc.minute < close:
        return bars[-2][0] if len(bars) > 1 else None
    return last


def run(sigs, conf, cache, key, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    st = {}
    if cache.get("vt"):
        try:
            st = json.loads(secure.open_(bytes.fromhex(cache["vt"]), key))
        except Exception:
            st = {"note": "지난 가상 기록을 풀지 못해 새로 시작(비밀번호가 바뀌었을 수 있음)"}
    today = now.astimezone(KST).date().isoformat()
    # 처음 시작할 때: 하루 전부터의 신호만 (미국 날짜는 한국보다 하루 늦으니 하루 여유)
    st.setdefault("since", (now - dt.timedelta(days=1)).astimezone(KST).date().isoformat())
    pos, orders, done = st.setdefault("pos", {}), st.setdefault("orders", []), set(st.get("done", []))

    # 1) 대기 주문 체결 (신호 다음 거래일 시가)
    for o in orders:
        if o["st"] != "대기":
            continue
        bars = (sigs.get(o["sym"]) or {}).get("bars") or []
        nxt = next((b for b in bars if b[0] > o["sigT"]), None)
        if not nxt or not nxt[1]:
            continue
        p = pos.setdefault(o["sym"], {"q": 0, "avg": 0.0, "real": 0.0, "cur": o["cur"]})
        px = float(nxt[1])
        if o["side"] == "buy":
            p["avg"] = (p["q"] * p["avg"] + o["q"] * px) / (p["q"] + o["q"])
            p["q"] += o["q"]
        else:
            q = min(o["q"], p["q"])
            p["real"] += (px - p["avg"]) * q
            p["q"] -= q
            o["pl"] = round((px - p["avg"]) * q, 4)
        o.update(st="체결", fillT=nxt[0], px=round(px, 4), note="")

    # 2) 새 신호 → 가상 주문
    syms = list(sigs) if conf["symbols"] == "all" else [s for s in conf["symbols"] if s in sigs]
    syms = [s for s in syms if not s.startswith("^") and s not in conf["exclude"] and "err" not in sigs[s]]
    buys_today = [o for o in orders if o["side"] == "buy" and o.get("made", "") == today and o["st"] != "건너뜀"]
    spent = {"KRW": sum(o["q"] * o["est"] for o in buys_today if o["cur"] == "KRW"),
             "USD": sum(o["q"] * o["est"] for o in buys_today if o["cur"] == "USD")}
    nbuy = len(buys_today)
    for s in syms:
        S = sigs[s]
        bars = S.get("bars") or []
        if len(bars) < 2:
            continue
        F = final_day(S.get("kr"), bars, now)
        if not F or F < st["since"]:
            continue
        cur = "KRW" if S.get("kr") else "USD"
        close = next((b[2] for b in bars if b[0] == F), None)
        unit = int(conf["unitBy"].get(s, conf["unit"]))
        p = pos.get(s, {"q": 0})
        plan = p["q"] + sum(o["q"] if o["side"] == "buy" else -o["q"] for o in orders if o["sym"] == s and o["st"] == "대기")
        adds = st.setdefault("adds", {}).get(s, 0)
        for e in S.get("ev", []):
            k = f'{s}|{e["t"]}|{e["k"]}'
            if e["t"] != F or k in done:
                continue
            done.add(k)
            a, side, q, note = e["a"], None, 0, ""
            if a == "buy":
                if plan == 0:
                    side, q, adds = "buy", 2 * unit, 0
                else:
                    note = "이미 보유 중"
            elif a == "add":
                if plan > 0 and adds < 2:
                    side, q = "buy", unit
                    adds += 1
                else:
                    note = "보유 없음" if plan <= 0 else "불태워 2번 다 씀"
            elif a == "part":
                if plan >= 2:
                    side, q = "sell", plan // 2
                else:
                    note = "1주 이하라 유지" if plan == 1 else "보유 없음"
            elif a == "exit":
                if plan > 0:
                    side, q = "sell", plan
                else:
                    note = "보유 없음"
            o = {"id": k, "sym": s, "sigT": F, "k": e["k"], "nm": e.get("nm", e["k"]), "act": a, "cur": cur,
                 "est": round(float(close or 0), 4), "made": today}
            if not side:
                o.update(side="-", q=0, st="건너뜀", note=note)
            elif side == "buy" and (nbuy >= conf["maxBuysPerDay"] or spent[cur] + q * o["est"] > conf["maxBuy" + cur]):
                o.update(side=side, q=q, st="건너뜀", note="하루 매수 한도 초과")
            else:
                o.update(side=side, q=q, st="대기", note="다음 거래일 시가 체결 예정")
                plan += q if side == "buy" else -q
                if side == "buy":
                    nbuy += 1
                    spent[cur] += q * o["est"]
            if a == "exit" and side:
                adds = 0
            orders.append(o)
        st["adds"][s] = adds

    st["orders"] = orders[-400:]
    st["done"] = sorted(done)[-3000:]
    cache["vt"] = secure.seal(json.dumps(st).encode(), key).hex()

    # 3) 화면용 요약
    last = {s: (sigs[s].get("bars") or [[None, 0, 0]])[-1][2] for s in sigs if "err" not in sigs[s]}
    view = []
    for s, p in pos.items():
        if p["q"] or p["real"]:
            lp = last.get(s) or 0
            view.append({"sym": s, "q": p["q"], "avg": round(p["avg"], 4), "last": lp, "cur": p["cur"],
                         "unreal": round((lp - p["avg"]) * p["q"], 4) if p["q"] else 0, "real": round(p["real"], 4)})
    return {"mode": conf["mode"], "since": st["since"], "pos": view, "orders": st["orders"][-120:],
            "conf": {k: conf[k] for k in ("unit", "maxBuysPerDay", "maxBuyKRW", "maxBuyUSD")}, "note": st.get("note")}
