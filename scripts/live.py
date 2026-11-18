"""3단계: 실전 주문 (최소 수량). 기본은 꺼져 있다.

실제 주문이 나가려면 아래가 '모두' 맞아야 한다.
 1. GitHub 저장소 변수(Variables) LIVE_TRADING = on   ← 사람이 직접 켜고 끄는 스위치
 2. trade.json 의 "live" 에 종목이 직접 적혀 있음 ("all" 은 안 받음)
 3. 그 종목의 가상 주문(vtrade)이 '오늘 장 시작 체결'로 처리된 주문일 것 — 화면 신호와 같은 주문만 보낸다
 4. 장 시간 안: 국내 09:00~15:15(시장가)
    미국은 주간거래(한국 10:00~15:30) 지정가를 먼저, 주간장 미지원·미체결이면 남은 걸 취소하고
    그날 정규장(현지 09:31~15:45)에서 다시 — 둘 다 현재가 ±1% 지정가
 5. 하루 주문 건수·매수 금액 한도 안
매도는 '이 프로그램이 산 수량'(bot)까지만, 그리고 실제 잔고 이하로만. 직접 산 주식은 건드리지 않는다.
스위치가 꺼져 있으면 '보냈을 주문'만 미리보기로 기록한다.
"""
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

import secure
from vtrade import daytime_now

KST, ET = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
LDEF = {"symbols": [], "unit": 1, "maxOrdersPerDay": 4, "maxBuyKRW": 500000, "maxBuyUSD": 500}
EX_ORDER = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}


def switch_on():
    return (os.environ.get("LIVE_TRADING") or "").strip().lower() == "on"


def in_window(kr, now):
    loc = now.astimezone(KST if kr else ET)
    if loc.weekday() >= 5:
        return False, loc.date().isoformat()
    m = loc.hour * 60 + loc.minute
    ok = (9 * 60 <= m <= 15 * 60 + 15) if kr else (9 * 60 + 31 <= m <= 15 * 60 + 45)
    return ok, loc.date().isoformat()


def _held(acct, sym, kr):
    code = sym.split(".")[0] if kr else sym
    for h in (acct or {}).get("kr" if kr else "us") or []:
        if h.get("sym") == code:
            return int(h.get("qty") or 0)
    return 0


def run(vt_orders, conf, kis, acct, key, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    L = {**LDEF, **(conf.get("live") or {})}
    syms = [s for s in (L["symbols"] if isinstance(L["symbols"], list) else []) if not s.startswith("^")]
    st = {}
    if kis.cache.get("lv"):
        try:
            st = json.loads(secure.open_(bytes.fromhex(kis.cache["lv"]), key))
        except Exception:
            # 기록을 못 풀면(비밀번호 변경 등) 프로그램이 산 수량을 모르므로 주문하지 않고, 기록도 덮어쓰지 않는다
            return {"on": switch_on(), "live": False, "symbols": syms, "bot": {}, "log": [],
                    "note": "지난 실전 기록을 풀지 못해 주문을 멈췄어요 (SITE_PASSWORD를 바꿨다면 원래대로 돌려주세요)"}
    bot, log, sent = st.setdefault("bot", {}), st.setdefault("log", []), set(st.get("sent", []))
    acc = kis.account()
    err = (acct or {}).get("err") or {}
    live = switch_on() and bool(syms) and kis.ok and bool(acc) and bool(acct)

    # 1) 지난 주문 확인: 실제 잔고가 늘었/줄었는지로 체결 판단 (주문 전 잔고 h0 와 비교)
    for o in log:
        if o.get("st") != "접수":
            continue
        kr = o["sym"].endswith((".KS", ".KQ"))
        if o.get("dt") and not o.get("fb") and in_window(False, now)[1] <= o["day"]:
            continue  # 주간장 주문은 그날 정규장에서 1-b 가 정리
        if not acct or ("kr" if kr else "us") in ((acct or {}).get("err") or {}):
            continue  # 잔고를 모르면 판단 보류
        h = _held(acct, o["sym"], kr)
        got = max(0, h - o["h0"]) if o["side"] == "buy" else max(0, o["h0"] - h)
        got = min(got, o["q"])
        _, today = in_window(kr, now)
        if got >= o["q"] or today > o["day"]:
            o["filled"] = got
            o["st"] = "체결" if got >= o["q"] else ("일부체결" if got else "미체결")
            b = bot.get(o["sym"], 0)
            bot[o["sym"]] = b + got if o["side"] == "buy" else max(0, b - got)

    def send(rec, kr, side, q, daytime=False):
        """실제 주문 1건. rec 에 결과를 적는다."""
        try:
            if kr:
                body = {"CANO": acc[0], "ACNT_PRDT_CD": acc[1], "PDNO": rec["sym"].split(".")[0], "ORD_DVSN": "01", "ORD_QTY": str(q),
                        "ORD_UNPR": "0", "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if side == "sell" else "", "CNDT_PRIC": ""}
                j = kis.post("/uapi/domestic-stock/v1/trading/order-cash", "TTTC0012U" if side == "buy" else "TTTC0011U", body)
            else:
                last, ex = kis.us_price(rec["sym"], daytime=daytime)
                if last <= 0:
                    raise RuntimeError("주간거래 시세 없음(미지원 종목일 수 있음)" if daytime else "현재가를 못 받음")
                px = last * (1.01 if side == "buy" else 0.99)
                body = {"CANO": acc[0], "ACNT_PRDT_CD": acc[1], "OVRS_EXCG_CD": EX_ORDER.get(ex, "NASD"), "PDNO": rec["sym"], "ORD_QTY": str(q),
                        "OVRS_ORD_UNPR": f"{px:.2f}" if px >= 1 else f"{px:.4f}", "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"}
                rec.update(px=round(px, 4), ex=EX_ORDER.get(ex, "NASD"))
                if daytime:
                    j = kis.post("/uapi/overseas-stock/v1/trading/daytime-order", "TTTS6036U" if side == "buy" else "TTTS6037U", body)
                else:
                    j = kis.post("/uapi/overseas-stock/v1/trading/order", "TTTT1002U" if side == "buy" else "TTTT1006U", body)
            out = j.get("output") or {}
            if j.get("rt_cd") == "0":
                rec.update(st="접수", odno=out.get("ODNO") or out.get("odno") or "", note=("주간장 · " if daytime else "") + (j.get("msg1") or "")[:80])
            else:
                rec.update(st="거부", note=("주간장 " if daytime else "") + f'{j.get("msg_cd", "")} {(j.get("msg1") or "")[:100]}')
        except Exception as ex:
            rec.update(st="오류", note=str(ex)[:150])

    def persist():
        # 보낸 직후 바로 저장 (중간에 멈춰도 같은 주문을 다시 보내지 않도록)
        st["sent"] = sorted(sent)[-3000:]
        kis.cache["lv"] = secure.seal(json.dumps(st).encode(), key).hex()
        try:
            kis.save()
        except OSError:
            pass

    # 1-b) 주간장 주문 중 정규장이 열렸는데 덜 산/덜 판 것 → 남은 주문 취소 후 정규장에서 나머지
    for o in list(log):
        if not o.get("dt") or o.get("st") not in ("접수", "거부", "오류") or o.get("fb"):
            continue
        ok, today = in_window(False, now)
        if not ok or today != o["day"]:
            continue
        if "us" in err:
            continue
        h = _held(acct, o["sym"], False)
        got = 0 if o["st"] != "접수" else min(o["q"], max(0, h - o["h0"]) if o["side"] == "buy" else max(0, o["h0"] - h))
        rest = o["q"] - got
        o["fb"] = True
        if o["st"] == "접수":
            o["filled"] = got
            o["st"] = "체결" if rest <= 0 else ("일부체결" if got else "미체결")
            b = bot.get(o["sym"], 0)
            bot[o["sym"]] = b + got if o["side"] == "buy" else max(0, b - got)
            if rest > 0 and o.get("odno") and live:
                try:  # 주간장에 남은 주문 취소 (이미 만료됐으면 오류는 무시)
                    kis.post("/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl", "TTTS6038U", {
                        "CANO": acc[0], "ACNT_PRDT_CD": acc[1], "OVRS_EXCG_CD": o.get("ex", "NASD"), "PDNO": o["sym"], "ORGN_ODNO": o["odno"],
                        "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": str(rest), "OVRS_ORD_UNPR": "0", "CTAC_TLNO": "", "MGCO_APTM_ODNO": "", "ORD_SVR_DVSN_CD": "0"})
                except Exception:
                    pass
        if rest <= 0:
            continue
        if o["side"] == "sell":
            owned = bot.get(o["sym"], 0) - sum(x["q"] for x in log if x["sym"] == o["sym"] and x["side"] == "sell" and x.get("st") == "접수")
            rest = min(rest, owned, _held(acct, o["sym"], False))
            if rest <= 0:
                continue
        rec = {"id": o["id"] + "|R", "sym": o["sym"], "day": today, "side": o["side"], "q": rest, "cur": "USD", "nm": o.get("nm"),
               "h0": h, "at": now.isoformat(timespec="minutes"), "est": o.get("est", 0)}
        if rec["id"] in sent:
            continue
        sent.add(rec["id"])
        if live:
            send(rec, False, o["side"], rest)
        else:
            rec.update(st="미리보기", note="정규장 재주문 — 스위치 꺼짐")
        log.append(rec)
        persist()

    # 2) 체결 처리된 가상 주문 → 실전 주문 (미국: 주간장 체결이면 지금 주간장으로, 아니면 정규장 시가 날)
    day_cnt = {}
    for o in log:
        if o.get("st") in ("접수", "체결", "일부체결", "미체결", "거부", "오류"):
            day_cnt[o["day"]] = day_cnt.get(o["day"], 0) + 1
    for v in vt_orders:
        s = v["sym"]
        if s not in syms or v.get("st") != "체결" or v["id"] in sent:
            continue
        kr = s.endswith((".KS", ".KQ"))
        dtm = (not kr) and v.get("dt") and daytime_now(now)
        if dtm:
            today = v.get("fillT")  # 주간장 주문은 '다음 정규장 날짜'로 묶어서 관리
        else:
            ok, today = in_window(kr, now)
            if v.get("fillT") != today:
                continue  # 오늘 시가 주문만 (지난 것은 보내지 않음)
            if not ok:
                continue  # 아직 장 시간 아님 → 다음 실행에서
        sent.add(v["id"])
        cur = "KRW" if kr else "USD"
        # 아직 확인 안 된(접수) 매도 수량은 빼고 계산 — 같은 주식을 두 번 팔지 않게
        owned = bot.get(s, 0) - sum(x["q"] for x in log if x["sym"] == s and x["side"] == "sell" and x.get("st") == "접수")
        held = _held(acct, s, kr)
        a = v["act"]
        if a in ("buy", "add"):
            side, q = "buy", int(L["unit"])
        elif a == "part":
            side, q = "sell", min(owned // 2, held)
        else:
            side, q = "sell", min(owned, held)
        rec = {"id": v["id"], "sym": s, "day": today, "side": side, "q": q, "cur": cur, "nm": v.get("nm"), "h0": held,
               "at": now.isoformat(timespec="minutes")}
        if dtm:
            rec["dt"] = True
        q = max(0, q)
        if q <= 0:
            rec.update(st="건너뜀", note="프로그램이 산 수량 없음" if side == "sell" else "수량 0")
            log.append(rec)
            continue
        est = float(v.get("px") or v.get("est") or 0)
        used = sum(x["q"] * x.get("est", 0) for x in log if x["day"] == today and x["side"] == "buy" and x["cur"] == cur and x.get("st") in ("접수", "체결", "일부체결"))
        rec["est"] = est
        if day_cnt.get(today, 0) >= L["maxOrdersPerDay"]:
            rec.update(st="건너뜀", note="하루 주문 건수 한도")
        elif side == "buy" and used + q * est > L["maxBuy" + cur]:
            rec.update(st="건너뜀", note="하루 매수 금액 한도")
        elif ("kr" if kr else "us") in err:
            rec.update(st="건너뜀", note="계좌 잔고 조회 실패 — 잔고를 모르면 주문 안 함")
        elif not live:
            rec.update(st="미리보기", note="스위치 꺼짐 — 켜져 있었다면 이 주문을 보냈음")
        else:
            send(rec, kr, side, q, daytime=bool(dtm))
        if rec["st"] not in ("미리보기", "건너뜀"):
            day_cnt[today] = day_cnt.get(today, 0) + 1
        log.append(rec)
        persist()

    st["log"] = log[-300:]
    st["sent"] = sorted(sent)[-3000:]
    kis.cache["lv"] = secure.seal(json.dumps(st).encode(), key).hex()
    return {"on": switch_on(), "live": live, "symbols": syms, "limits": {k: L[k] for k in ("unit", "maxOrdersPerDay", "maxBuyKRW", "maxBuyUSD")},
            "bot": bot, "log": st["log"][-80:], "note": st.get("note")}
