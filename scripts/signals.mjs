// 홈페이지(index.html)의 쭈야 신호 엔진을 그대로 서버에서 돌려, 종목별 최근 행동 신호를 JSON으로 출력한다.
// 사용: node scripts/signals.mjs index.html data.json  → stdout 에 {"SOXL": {...}, ...}
// 화면과 서버가 같은 코드로 신호를 내므로, 화면에서 본 신호와 가상·실제 주문이 어긋나지 않는다.
import fs from 'node:fs';
import vm from 'node:vm';

const [htmlPath, dataPath] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, 'utf8');
const code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
// 화면(DOM) 관련 호출은 아무것도 안 하는 가짜 객체로 흘려보낸다
const any = new Proxy(function () {}, {
  get: (t, p) => p === Symbol.toPrimitive ? () => '' : p === Symbol.iterator ? function* () {} : p === 'length' ? 0 : p === 'then' ? undefined : any,
  set: () => true, apply: () => any, construct: () => any, has: () => false
});
const ctx = {
  console: { log() {}, warn() {}, error() {} }, Math, Date, JSON, Intl, Map, Set, WeakMap, Number, String, Array, Object, Promise, Symbol, Error, RegExp,
  isFinite, isNaN, parseFloat, parseInt, structuredClone, Proxy, Reflect, Infinity, NaN, Boolean, Uint8Array, DataView, TextEncoder,
  document: any, navigator: any, location: {}, history: any, crypto: any,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, fetch: () => new Promise(() => {}),
  setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, requestAnimationFrame: () => 0,
  matchMedia: () => ({ matches: false, addEventListener() {} }), getComputedStyle: () => any, ResizeObserver: function () { return any },
  addEventListener() {}, performance
};
ctx.window = ctx;
vm.createContext(ctx);
try { vm.runInContext(code, ctx); } catch (e) { /* 화면 초기화 중 오류는 무시 — 엔진 함수만 쓰면 됨 */ }
for (const f of ['gobCalc', 'unpack', 'ACT', 'GN']) {
  if (vm.runInContext(`typeof ${f}`, ctx) === 'undefined') { process.stderr.write(`엔진 함수 ${f} 를 못 찾음\n`); process.exit(2); }
}
ctx.__data = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
const out = vm.runInContext(`(() => {
  D = { order: __data.order, tickers: {}, bases: {} };
  Object.entries(__data.tickers).forEach(([s, T]) => { D.tickers[s] = { kr: !!T.kr, d: unpack(T.d), h1: [], h30: [], m5: [] }; });
  Object.entries(__data.bases || {}).forEach(([s, B]) => { D.bases[s] = { kr: !!B.kr, d: unpack(B.d) }; });
  const res = {};
  Object.keys(D.tickers).forEach(s => {
    try {
      const G = gobCalc(s); if (!G) return;
      const b = D.tickers[s].d, n = b.length;
      res[s] = {
        kr: D.tickers[s].kr,
        bars: b.slice(-15).map(x => [x.t.slice(0, 10), x.o, x.c]),
        ev: G.ev.filter(e => e.i >= n - 15 && ACT[e.k] && ACT[e.k] !== 'hold').map(e => ({ t: e.t.slice(0, 10), k: e.k, a: ACT[e.k], nm: GN[e.k] ? GN[e.k][1] : e.k })),
        status: G.status
      };
    } catch (e) { res[s] = { err: String(e).slice(0, 200) }; }
  });
  return JSON.stringify(res);
})()`, ctx);
process.stdout.write(out);
