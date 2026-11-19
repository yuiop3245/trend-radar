// 장 마감 뒤 확정된 쭈야 매수·매도 신호를 텔레그램으로 보낸다. (내 종목 + 코스피200 + 코스닥150)
// 신호 계산은 index.html 안의 코드(gobCalc)를 그대로 써서 화면과 똑같이 맞춘다.
// 필요한 GitHub Secrets: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (없으면 아무것도 보내지 않음)
// ALERT_MARKET: auto (기본, 장이 닫힌 시장만) | test (연결 확인용 메시지 한 통)
// 이미 보낸 신호는 .alert_cache/sent.json 에 적어 두고 다시 보내지 않음
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const MARKET = (process.env.ALERT_MARKET || 'auto').toLowerCase();
const TOKEN = process.env.TELEGRAM_TOKEN, CHAT = process.env.TELEGRAM_CHAT_ID;
const SITE = process.env.SITE_URL || 'https://yuiop3245.github.io/trend-radar/';
const DRY = !!process.env.DRY_RUN;

// ── 화면 코드 불러오기 (DOM은 아무 일도 안 하는 가짜로 대신)
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
const any = new Proxy(function () {}, {
  get: (t, p) => p === Symbol.toPrimitive ? () => '' : p === Symbol.iterator ? function* () {} : p === 'length' ? 0 : p === 'then' ? undefined : any,
  set: () => true, apply: () => any, construct: () => any, has: () => false
});
const store = {};
const ctx = {
  console: { log() {}, warn() {}, error() {} }, Math, Date, JSON, Intl, Map, Set, WeakMap, Number, String, Array, Object, Promise, Symbol, Error, RegExp, isFinite, isNaN, parseFloat, parseInt, structuredClone, Proxy, Reflect, Infinity, NaN, undefined, Boolean, encodeURIComponent, decodeURIComponent,
  document: any, navigator: any, location: { href: '', search: '', hash: '' }, history: any,
  localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  fetch: () => new Promise(() => {}), setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  requestAnimationFrame: () => 0, cancelAnimationFrame() {}, matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  getComputedStyle: () => any, ResizeObserver: function () { return any; }, IntersectionObserver: function () { return any; }, MutationObserver: function () { return any; },
  devicePixelRatio: 1, innerWidth: 1400, innerHeight: 900, addEventListener() {}, removeEventListener() {}, performance: { now: () => Date.now() }
};
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);
try { vm.runInContext(code, ctx, { timeout: 20000 }); } catch (e) { /* 화면 그리기 쪽 오류는 무시 (계산 함수는 이미 정의됨) */ }


const data = JSON.parse(fs.readFileSync(path.join(ROOT, 'data.json'), 'utf8'));
let uni = null;
try { uni = JSON.parse(fs.readFileSync(path.join(ROOT, 'data_kr.json'), 'utf8')); } catch (e) { console.log('data_kr.json 없음 → 내 종목만'); }
ctx.__data = data; ctx.__uni = uni;
vm.runInContext(`
  D = { v: 2, order: __data.order || Object.keys(__data.tickers), tickers: {}, bases: {}, grpOf: {} };
  Object.entries(__data.tickers).forEach(([s, T]) => {
    const t = { name: T.name || '', label: T.label || '', kr: !!T.kr, base: T.base || null, dir: T.dir || 1, dsyn: T.dsyn || [], d: unpack(T.d), h1: unpack(T.h1), h30: unpack(T.h30), m5: unpack(T.m5), rec: [] };
    fillDaily(t); D.tickers[s] = t; D.grpOf[s] = '내 종목';
  });
  Object.entries(__data.bases || {}).forEach(([k, B]) => { D.bases[k] = { kr: !!B.kr, d: unpack(B.d), h1: unpack(B.h1), h30: unpack(B.h30), m5: unpack(B.m5) }; });
  const NM = { kospi200: '코스피200', kosdaq150: '코스닥150' };
  if (__uni) {
    Object.entries(__uni.groups || {}).forEach(([g, list]) => list.forEach((s, i) => {
      const T = __uni.tickers[s]; if (!T) return;
      if (D.grpOf[s]) { D.grpOf[s] += ' · ' + ((__uni.names || {})[g] || NM[g] || g) + ' ' + (i + 1) + '위'; return; }
      D.tickers[s] = { name: T.name || '', label: T.label || '', kr: true, base: T.base || '^KS11', dir: 1, dsyn: [], d: unpack(T.d), h1: [], h30: [], m5: [], rec: [] };
      D.grpOf[s] = ((__uni.names || {})[g] || NM[g] || g) + ' ' + (i + 1) + '위'; D.order.push(s);
    }));
  }
`, ctx);

const out = vm.runInContext(`(() => {
  const res = [];
  D.order.forEach(sym => {
    const T = D.tickers[sym]; if (!T || !T.d || !T.d.length) return;
    let G; try { G = gobCalc(sym); } catch (e) { return; } if (!G) return;
    const L = T.d[T.d.length - 1], last = L.t.slice(0, 10);
    const evs = G.ev.filter(e => e.t.slice(0, 10) === last && (EZK[e.k] === 'buy' || EZK[e.k] === 'sell'));
    if (!evs.length && !${MARKET === 'test'}) return;
    res.push({ sym, kr: !!T.kr, code: T.name, label: T.label || sym, grp: D.grpOf[sym] || '', last, fmtClose: fmt(L.c),
      evs: evs.map(e => ({ k: e.k, nm: (GN[e.k] || [e.k, e.k])[1], z: EZK[e.k], d: e.d || '' })) });
  });
  return JSON.stringify(res);
})()`, ctx);
const rows = JSON.parse(out);

// 장이 닫혔는지 (그날 봉이 확정됐는지): 국장 15:40 KST, 미장 16:10 뉴욕 이후
const parts = (tz) => Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(new Date()).map(p => [p.type, p.value]));
const closed = (kr, day) => {
  const p = parts(kr ? 'Asia/Seoul' : 'America/New_York'), today = `${p.year}-${p.month}-${p.day}`, hm = (+p.hour % 24) * 60 + +p.minute;
  return day < today || (day === today && hm >= (kr ? 15 * 60 + 40 : 16 * 60 + 10));
};

const CACHE = path.join(ROOT, '.alert_cache'), SENT = path.join(CACHE, 'sent.json');
let sent = {};
try { sent = JSON.parse(fs.readFileSync(SENT, 'utf8')); } catch (e) { /* 처음 */ }
const ZN = { buy: '🟢 매수', sell: '🔴 매도' };
let text, keys = [], count = 0;
if (MARKET === 'test') {
  text = `🔔 쭈야 신호 알림 연결 확인\n\n장 마감 뒤 매수·매도 신호가 뜨면 여기로 보내드려요.\n대상: 내 종목 ${data.order ? data.order.length : ''}개 + 코스피200 + 코스닥150\n\n${SITE}`;
} else {
  const fresh = rows.filter(r => closed(r.kr, r.last)).flatMap(r => r.evs.map(e => ({ r, e, key: `${r.sym}|${r.last}|${e.k}` }))).filter(x => !sent[x.key]);
  if (!fresh.length) { console.log('보낼 새 신호 없음'); process.exit(0); }
  const byDay = {};
  fresh.forEach(x => { const h = `${x.r.last} ${x.r.kr ? '국장' : '미장'} 마감 확정`; (byDay[h] = byDay[h] || []).push(x); });
  const blocks = [];
  Object.entries(byDay).forEach(([h, xs]) => {
    ['buy', 'sell'].forEach(z => {
      const ys = xs.filter(x => x.e.z === z); if (!ys.length) return;
      blocks.push(`${ZN[z]} ${ys.length}건 · ${h}`);
      ys.forEach(x => blocks.push(`${x.r.label}${x.r.kr ? ' ' + x.r.code : ''} — ${x.e.nm}\n   종가 ${x.r.fmtClose} · ${x.r.grp}`));
    });
  });
  text = `🔔 쭈야 신호\n\n${blocks.join('\n\n')}\n\n${SITE}`;
  keys = fresh.map(x => x.key); count = fresh.length;
}
if (DRY || !TOKEN || !CHAT) { console.log((TOKEN && CHAT) ? '[DRY]' : '[텔레그램 설정 없음 — 보내지 않음]'); console.log(text); process.exit(0); }
// 텔레그램 메시지 한 개는 4096자까지라 나눠 보낸다
const chunks = []; let cur = '';
for (const part of text.split('\n\n')) { if ((cur + '\n\n' + part).length > 3800) { chunks.push(cur); cur = part; } else cur = cur ? cur + '\n\n' + part : part; }
if (cur) chunks.push(cur);
for (const c of chunks) {
  const r = await fetch(`https://api.telegram.org/bot${TOKEN}/sendMessage`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat_id: CHAT, text: c, disable_web_page_preview: true }) });
  if (!r.ok) { console.error('텔레그램 전송 실패', r.status, (await r.text()).slice(0, 200)); process.exit(1); }
}
if (keys.length) {
  const cut = new Date(Date.now() - 20 * 864e5).toISOString().slice(0, 10); // 20일 지난 기록은 정리
  Object.keys(sent).forEach(k => { if (k.split('|')[1] < cut) delete sent[k]; });
  keys.forEach(k => { sent[k] = 1; });
  fs.mkdirSync(CACHE, { recursive: true }); fs.writeFileSync(SENT, JSON.stringify(sent));
}
console.log(`보냄: ${MARKET === 'test' ? '연결 확인' : count + '건'}`);
