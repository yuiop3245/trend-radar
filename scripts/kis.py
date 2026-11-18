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

    # ── 토큰 (가려서 저장) ──────────────────────────────
    def _mask(self, data: bytes) -> bytes:
        out, i = bytearray(), 0
        while len(out) < len(data):
            out += hashlib.sha256(f"{self.secret}|{i}".encode()).digest()
            i += 1
        return bytes(a ^ b for a, b in zip(data, out))

    def token(self):
        if self._token:
            return self._token
        t = self.cache.get("token") or {}
        now = time.time()
        if t.get("v") and t.get("exp", 0) > now + 1800:
            try:
                self._token = self._mask(bytes.fromhex(t["v"])).decode()
                return self._token
            except Exception:
                pass
        r = requests.post(BASE + "/oauth2/tokenP", timeout=15,
                          json={"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret})
        j = r.json()
        tok = j.get("access_token")
        if not tok:
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
            self._last = time.time()
            self.calls += 1
            r = requests.get(BASE + path, headers=h, params=params, timeout=15)
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
            if any(k in prev and abs(prev[k][4] - v[4]) > abs(v[4]) * .005 for k, v in got.items()):
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
