"""한국투자증권(KIS) Open API — 시세 조회용 최소 클라이언트.

비밀값은 코드에 두지 않고 환경변수(GitHub Secrets)로만 받는다.
  KIS_APP_KEY, KIS_APP_SECRET : 실전 계좌 앱키·앱시크릿

접근 토큰은 하루 유효하고, 발급할 때마다 카카오 알림톡이 가므로
.kis_cache/cache.json 에 (앱시크릿으로 가린 형태로) 저장해 다음 실행에서 다시 쓴다.
GitHub Actions 에서는 actions/cache 로 이 폴더를 실행 사이에 이어 붙인다.
"""
import datetime as dt
import hashlib
import json
import os
import re
import time

import requests

BASE = "https://openapi.koreainvestment.com:9443"
KST = dt.timezone(dt.timedelta(hours=9))
GAP = 0.08  # 호출 간격(초) — 실전 초당 한도 안쪽


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class KIS:
    def __init__(self, cache_path):
        self.key = (os.environ.get("KIS_APP_KEY") or "").strip()
        self.secret = (os.environ.get("KIS_APP_SECRET") or "").strip()
        self.ok = bool(self.key and self.secret)
        self.cache_path = cache_path
        self.cache = _load(cache_path)
        self.cache.setdefault("excd", {})
        self.cache.setdefault("d", {})
        self.notes = []
        self.calls = 0
        self._last = 0.0
        self._token = None
        self._t0 = time.time()
        self.dead = None   # 이유 문자열이 들어가면 이번 실행에선 한투를 더 부르지 않음
        self._fails = 0
        self.budget = 600  # 한 번 실행에서 한투에 쓰는 최대 시간(초) — 넘으면 나머지는 기존 출처로

    # ── 토큰 (가려서 저장) ──────────────────────────────
    def _mask(self, data: bytes) -> bytes:
        out, i = bytearray(), 0
        while len(out) < len(data):
            out += hashlib.sha256(f"{self.secret}|{i}".encode()).digest()
            i += 1
        return bytes(a ^ b for a, b in zip(data, out))

    def _check(self):
        if self.dead:
            raise RuntimeError("한투 건너뜀: " + self.dead)
        if time.time() - self._t0 > self.budget:
            self.dead = f"이번 실행 한투 시간 {self.budget}초 초과"
            self.notes.append(self.dead)
            raise RuntimeError(self.dead)

    def _net_fail(self, ex):
        self._fails += 1
        if self._fails >= 3:
            self.dead = f"연결 실패 연속 {self._fails}회 ({type(ex).__name__})"
            self.notes.append(self.dead)
        raise RuntimeError(f"연결 실패: {type(ex).__name__}")

    def token(self):
        if self._token:
            return self._token
        self._check()
        t = self.cache.get("token") or {}
        now = time.time()
        if t.get("v") and t.get("exp", 0) > now + 1800:
            try:
                self._token = self._mask(bytes.fromhex(t["v"])).decode()
                return self._token
            except Exception:
                pass
        try:
            r = requests.post(BASE + "/oauth2/tokenP", timeout=(5, 15),
                              json={"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret})
            j = r.json()
        except (requests.RequestException, ValueError) as ex:
            self.dead = f"토큰 요청 실패 ({type(ex).__name__})"
            self.notes.append(self.dead)
            raise RuntimeError(self.dead)
        tok = j.get("access_token")
        if not tok:
            # 발급이 거절되면 같은 실행에서 다시 조르지 않음 (1분 1회 제한·알림톡)
            self.dead = "토큰 발급 거절"
            self.notes.append(f"토큰 발급 실패: {j.get('error_code') or j.get('msg_cd') or r.status_code}")
            raise RuntimeError(f"토큰 발급 실패: {j.get('error_code') or j.get('msg_cd') or r.status_code} {j.get('error_description') or j.get('msg1') or ''}")
        try:
            exp = dt.datetime.strptime(j["access_token_token_expired"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).timestamp()
        except Exception:
            exp = now + 20 * 3600
        self.cache["token"] = {"v": self._mask(tok.encode()).hex(), "exp": exp}
        self.notes.append("토큰 새로 발급")
        self._token = tok
        return tok

    def save(self):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, separators=(",", ":"))

    # ── 공통 GET ───────────────────────────────────────
    def get(self, path, tr_id, params, tr_cont=""):
        wait = GAP - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        h = {"content-type": "application/json; charset=utf-8", "authorization": "Bearer " + self.token(),
             "appkey": self.key, "appsecret": self.secret, "tr_id": tr_id, "custtype": "P", "tr_cont": tr_cont}
        for attempt in range(3):
            self._check()
            self._last = time.time()
            self.calls += 1
            try:
                r = requests.get(BASE + path, headers=h, params=params, timeout=(5, 10))
            except requests.RequestException as ex:
                self._net_fail(ex)
            self._fails = 0
            try:
                j = r.json()
            except ValueError:
                j = {"rt_cd": "?", "msg1": f"HTTP {r.status_code}"}
            if j.get("msg_cd") == "EGW00201":  # 초당 거래건수 초과 → 잠깐 쉬고 다시
                time.sleep(1.0 + attempt)
                continue
            if j.get("rt_cd") != "0":
                raise RuntimeError(f"{tr_id} {j.get('msg_cd', '')} {j.get('msg1', '')}".strip())
            return j, r.headers.get("tr_cont", "")
        raise RuntimeError(f"{tr_id} 호출 한도 초과가 계속됨")

    # ── 일봉 ───────────────────────────────────────────
    @staticmethod
    def _f(x):
        try:
            return float(str(x).replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    def _bar(self, t, o, h, l, c, v):
        o, h, l, c = self._f(o), self._f(h), self._f(l), self._f(c)
        if c <= 0:
            return None
        if o <= 0 or h <= 0 or l <= 0:
            o = h = l = c
        return [t, round(o, 4), round(max(h, o, c), 4), round(min(l, o, c), 4), round(c, 4), int(self._f(v))]

    def _domestic_page(self, code, start, end):
        j, _ = self.get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
        out = []
        for r in j.get("output2") or []:
            d = (r.get("stck_bsop_date") or "").strip()
            if len(d) != 8:
                continue
            b = self._bar(f"{d[:4]}-{d[4:6]}-{d[6:]}", r.get("stck_oprc"), r.get("stck_hgpr"), r.get("stck_lwpr"), r.get("stck_clpr"), r.get("acml_vol"))
            if b:
                out.append(b)
        return out

    def _overseas_page(self, excd, sym, bymd):
        j, _ = self.get("/uapi/overseas-price/v1/quotations/dailyprice", "HHDFS76240000", {
            "AUTH": "", "EXCD": excd, "SYMB": sym, "GUBN": "0", "BYMD": bymd, "MODP": "1"})
        out = []
        for r in j.get("output2") or []:
            d = (r.get("xymd") or "").strip()
            if len(d) != 8:
                continue
            b = self._bar(f"{d[:4]}-{d[4:6]}-{d[6:]}", r.get("open"), r.get("high"), r.get("low"), r.get("clos"), r.get("tvol"))
            if b:
                out.append(b)
        return out

    def daily(self, sym, years=5):
        """일봉 [날짜, 시, 고, 저, 종, 거래량] 오름차순. 처음엔 years년치, 이후엔 최근 약 100일만 받아 캐시와 합친다."""
        kr = sym.upper().endswith(".KS") or sym.upper().endswith(".KQ")
        today = dt.datetime.now(KST if kr else dt.timezone(dt.timedelta(hours=-5))).date()
        start = today - dt.timedelta(days=int(365.25 * years) + 7)
        old = self.cache["d"].get(sym) or []
        have_recent = bool(old) and old[-1][0] >= (today - dt.timedelta(days=90)).isoformat() and old[0][0] <= (start + dt.timedelta(days=30)).isoformat()
        got = {}
        if kr:
            code = sym.split(".")[0]
            end = today
            for _ in range(20):
                rows = self._domestic_page(code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
                for b in rows:
                    got[b[0]] = b
                if have_recent or len(rows) < 90:
                    break
                first = min(b[0] for b in rows)
                end = dt.date.fromisoformat(first) - dt.timedelta(days=1)
                if end < start:
                    break
        else:
            excds = [self.cache["excd"][sym]] if sym in self.cache["excd"] else ["NAS", "AMS", "NYS"]
            excd, rows = None, []
            for ex in excds:
                rows = self._overseas_page(ex, sym, "")
                if rows:
                    excd = ex
                    break
            if not excd:
                raise RuntimeError(f"{sym}: 거래소(NAS·AMS·NYS)에서 시세를 못 찾음")
            self.cache["excd"][sym] = excd
            for b in rows:
                got[b[0]] = b
            for _ in range(20):
                if have_recent or len(rows) < 90:
                    break
                first = min(b[0] for b in rows)
                bymd = dt.date.fromisoformat(first) - dt.timedelta(days=1)
                if bymd < start:
                    break
                rows = self._overseas_page(excd, sym, bymd.strftime("%Y%m%d"))
                for b in rows:
                    got[b[0]] = b
        if have_recent:  # 캐시된 과거 + 새로 받은 최근(겹치는 날은 새 값)
            prev = {b[0]: b for b in old}
            # 분할·병합 등으로 과거 가격이 다시 계산됐으면(겹치는 날 종가가 0.5% 넘게 다름) 캐시를 버리고 처음부터
            # 캐시의 마지막 날은 장중(미확정)이었을 수 있으니 비교에서 뺀다
            last_old = old[-1][0]
            if any(k in prev and k < last_old and abs(prev[k][4] - v[4]) > abs(v[4]) * .005 for k, v in got.items()):
                self.cache["d"].pop(sym, None)
                self.notes.append(f"{sym}: 수정주가 변경 감지 → 전체 다시 받음")
                return self.daily(sym, years)
            merged = prev
            merged.update(got)
            got = merged
        bars = [got[k] for k in sorted(got) if k >= start.isoformat()]
        if bars:
            self.cache["d"][sym] = bars
        return bars


    # ── 계좌 (조회만 — 주문 기능은 여기 없음) ────────────────
    @staticmethod
    def account():
        """KIS_ACCOUNT = 계좌번호 앞 8자리 + 상품코드 2자리 (예: 12345678-01). 없으면 None."""
        raw = re.sub(r"\D", "", os.environ.get("KIS_ACCOUNT") or "")
        if len(raw) == 8:
            raw += "01"
        return (raw[:8], raw[8:]) if len(raw) == 10 else None

    def _paged(self, path, tr_id, params, fk, nk):
        rows, summ, cont = [], {}, ""
        for _ in range(10):
            j, tc = self.get(path, tr_id, params, cont)
            rows += j.get("output1") or []
            o2 = j.get("output2")
            if isinstance(o2, list):
                o2 = o2[0] if o2 else {}
            summ = o2 or summ
            if tc not in ("M", "F"):
                break
            params = {**params, fk.upper(): j.get(fk, ""), nk.upper(): j.get(nk, "")}
            cont = "N"
        return rows, summ

    def balance_kr(self, acc):
        rows, s = self._paged("/uapi/domestic-stock/v1/trading/inquire-balance", "TTTC8434R", {
            "CANO": acc[0], "ACNT_PRDT_CD": acc[1], "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""},
            "ctx_area_fk100", "ctx_area_nk100")
        f = self._f
        hold = [{"sym": r.get("pdno", ""), "name": r.get("prdt_name", ""), "qty": f(r.get("hldg_qty")), "avg": f(r.get("pchs_avg_pric")),
                 "px": f(r.get("prpr")), "buy": f(r.get("pchs_amt")), "eval": f(r.get("evlu_amt")), "pl": f(r.get("evlu_pfls_amt")),
                 "rt": f(r.get("evlu_pfls_rt"))} for r in rows if f(r.get("hldg_qty")) > 0]
        summ = {"cash": f(s.get("dnca_tot_amt")), "total": f(s.get("tot_evlu_amt")), "net": f(s.get("nass_amt")),
                "buy": f(s.get("pchs_amt_smtl_amt")), "eval": f(s.get("evlu_amt_smtl_amt")), "pl": f(s.get("evlu_pfls_smtl_amt")),
                "prev": f(s.get("bfdy_tot_asst_evlu_amt")), "chg": f(s.get("asst_icdc_amt"))}
        return hold, summ

    def balance_us(self, acc):
        seen = {}
        for ex in ("NASD", "NYSE", "AMEX"):
            rows, _ = self._paged("/uapi/overseas-stock/v1/trading/inquire-balance", "TTTS3012R", {
                "CANO": acc[0], "ACNT_PRDT_CD": acc[1], "OVRS_EXCG_CD": ex, "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""}, "ctx_area_fk200", "ctx_area_nk200")
            f = self._f
            for r in rows:
                q = f(r.get("ovrs_cblc_qty"))
                sym = (r.get("ovrs_pdno") or "").strip()
                if q > 0 and sym and sym not in seen:
                    seen[sym] = {"sym": sym, "name": r.get("ovrs_item_name", ""), "qty": q, "avg": f(r.get("pchs_avg_pric")),
                                 "px": f(r.get("now_pric2")), "buy": f(r.get("frcr_pchs_amt1")), "eval": f(r.get("ovrs_stck_evlu_amt")),
                                 "pl": f(r.get("frcr_evlu_pfls_amt")), "rt": f(r.get("evlu_pfls_rt")), "ex": r.get("ovrs_excg_cd", ex)}
        hold = list(seen.values())
        return hold, {"buy": sum(h["buy"] for h in hold), "eval": sum(h["eval"] for h in hold), "pl": sum(h["pl"] for h in hold)}

    # ── 3단계 실전 주문용 (live.py 에서만 부름) ─────────────────
    def post(self, path, tr_id, body):
        self._check()
        wait = GAP - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        base = {"content-type": "application/json; charset=utf-8", "appkey": self.key, "appsecret": self.secret}
        try:
            hk = requests.post(BASE + "/uapi/hashkey", headers=base, json=body, timeout=(5, 10)).json().get("HASH", "")
        except (requests.RequestException, ValueError):
            hk = ""
        h = {**base, "authorization": "Bearer " + self.token(), "tr_id": tr_id, "custtype": "P"}
        if hk:
            h["hashkey"] = hk
        self._last = time.time()
        self.calls += 1
        r = requests.post(BASE + path, headers=h, json=body, timeout=(5, 15))
        try:
            return r.json()
        except ValueError:
            return {"rt_cd": "?", "msg1": f"HTTP {r.status_code}"}

    def us_price(self, sym, daytime=False):
        """미국 현재가. daytime=True 면 주간거래 시세(BAQ·BAY·BAA)"""
        ex = self.cache["excd"].get(sym, "NAS")
        q = {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}.get(ex, "BAQ") if daytime else ex
        j, _ = self.get("/uapi/overseas-price/v1/quotations/price", "HHDFS00000300", {"AUTH": "", "EXCD": q, "SYMB": sym})
        return self._f((j.get("output") or {}).get("last")), ex
