// 장 마감 뒤 오늘 확정된 쭈야 신호를 텔레그램으로 보낸다.
// 신호 계산은 index.html 안의 코드(gobCalc)를 그대로 써서 화면과 똑같이 맞춘다.
// 필요한 GitHub Secrets: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (없으면 아무것도 보내지 않음)
// ALERT_MARKET: us | kr | test  (test = 시장 구분 없이 가장 최근 신호로 연결 확인)
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const MARKET = (process.env.ALERT_MARKET || 'test').toLowerCase();
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
ctx.__data = data;
vm.runInContext(`
  D = { v: 2, order: __data.order, tickers: {}, bases: {} };
  Object.entries(__data.tickers).forEach(([s, T]) => {
    const t = { name: T.name || '', label: T.label || '', kr: !!T.kr, base: T.base || null, dir: T.dir || 1, dsyn: T.dsyn || [], d: unpack(T.d), h1: unpack(T.h1), h30: unpack(T.h30), m5: unpack(T.m5), rec: [] };
    fillDaily(t); D.tickers[s] = t;
  });
  Object.entries(__data.bases || {}).forEach(([k, B]) => { D.bases[k] = { kr: !!B.kr, d: unpack(B.d), h1: unpack(B.h1), h30: unpack(B.h30), m5: unpack(B.m5) }; });
`, ctx);

const out = vm.runInContext(`(() => {
  const ALERT = new Set(['BO', 'R21', 'SRF', 'FIN', 'BOX', 'PY1', 'PY2', 'TP1', 'TP2', 'TRIM']);
  const res = [];
  D.order.forEach(sym => {
    const T = D.tickers[sym]; const G = gobCalc(sym); if (!G) return;
    const last = T.d[T.d.length - 1].t.slice(0, 10);
    const evs = G.ev.filter(e => e.t.slice(0, 10) === last && ALERT.has(e.k));
    res.push({ sym, kr: !!T.kr, name: T.name, label: T.label || sym, last, close: T.d[T.d.length - 1].c,
      evs: evs.map(e => ({ k: e.k, nm: (GN[e.k] || [e.k, e.k])[1], z: EZK[e.k] || '', d: e.d })),
      status: G.status, recent: G.ev.filter(e => ALERT.has(e.k)).slice(-1).map(e => ({ k: e.k, nm: (GN[e.k] || [e.k, e.k])[1], t: e.t.slice(0, 10) }))[0] || null,
      fmtClose: fmt(T.d[T.d.length - 1].c) });
  });
  return JSON.stringify(res);
})()`, ctx);
const rows = JSON.parse(out);

const today = kr => new Intl.DateTimeFormat('en-CA', { timeZone: kr ? 'Asia/Seoul' : 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const ZN = { buy: '🟢 매수', sell: '🔴 매도', add: '🔵 추가 매수', tp: '🟡 일부 익절' };
let lines = [], head;
if (MARKET === 'test') {
  head = `🔔 쭈야 신호 알림 연결 확인\n종목별 가장 최근 주요 신호:`;
  rows.forEach(r => { if (r.recent) lines.push(`• ${r.label} (${r.name}) ${r.recent.t} ${r.recent.nm} · 상태 ${r.status}`); });
} else {
  const kr = MARKET === 'kr', day = process.env.ALERT_DAY || today(kr);
  head = `🔔 쭈야 신호 · ${day} ${kr ? '국내장' : '미국장'} 마감 확정`;
  rows.filter(r => r.kr === kr && r.last === day && r.evs.length).forEach(r => {
    r.evs.forEach(e => lines.push(`${ZN[e.z] || '•'} ${r.label} (${r.name}) — ${e.nm}\n   종가 ${r.fmtClose} · ${e.d}`));
  });
}
if (!lines.length) { console.log(`보낼 신호 없음 (${MARKET})`); process.exit(0); }
const text = `${head}\n\n${lines.join('\n\n')}\n\n${SITE}`;
if (DRY || !TOKEN || !CHAT) { console.log((TOKEN && CHAT) ? '[DRY]' : '[텔레그램 설정 없음 — 보내지 않음]'); console.log(text); process.exit(0); }
// 텔레그램 메시지 한 개는 4096자까지라 나눠 보낸다
const chunks = []; let cur = '';
for (const part of text.split('\n\n')) { if ((cur + '\n\n' + part).length > 3800) { chunks.push(cur); cur = part; } else cur = cur ? cur + '\n\n' + part : part; }
if (cur) chunks.push(cur);
for (const c of chunks) {
  const r = await fetch(`https://api.telegram.org/bot${TOKEN}/sendMessage`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat_id: CHAT, text: c, disable_web_page_preview: true }) });
  if (!r.ok) { console.error('텔레그램 전송 실패', r.status, await r.text()); process.exit(1); }
}
console.log(`보냄: ${lines.length}건`);
