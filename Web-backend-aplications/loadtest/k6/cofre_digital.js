// Secondary load tool for Cofre Digital (k6). Primary tool is Locust.
// Usage: k6 run -e BASE_URL=http://127.0.0.1:8001 -e VUS=8 -e DURATION=60s loadtest/k6/cofre_digital.js
// Knobs mirror the locustfile: COFRE_N_MIN/MAX, COFRE_ROUNDS_MIN/MAX, COFRE_MATRIZ_MIN/MAX,
// LT_WAIT_MIN/MAX (seconds).
import http from 'k6/http';
import { check, sleep } from 'k6';

const E = (k, d) => (__ENV[k] !== undefined ? Number(__ENV[k]) : d);
const BASE = __ENV.BASE_URL || 'http://127.0.0.1:8001';
const N = [E('COFRE_N_MIN', 50000), E('COFRE_N_MAX', 500000)];
const R = [E('COFRE_ROUNDS_MIN', 50000), E('COFRE_ROUNDS_MAX', 500000)];
const M = [E('COFRE_MATRIZ_MIN', 60), E('COFRE_MATRIZ_MAX', 160)];
const W = [E('LT_WAIT_MIN', 0.5), E('LT_WAIT_MAX', 1.5)];

export const options = {
  vus: E('VUS', 8),
  duration: __ENV.DURATION || '60s',
  thresholds: { checks: ['rate==1.0'] },
};

const rnd = ([a, b]) => a + Math.floor(Math.random() * (b - a + 1));
const hex64 = (s) => typeof s === 'string' && /^[0-9a-f]{64}$/.test(s);

export default function () {
  const p = Math.random() * 8; // weights 3:3:2 like the locustfile
  if (p < 3) {
    const n = rnd(N);
    const r = http.get(`${BASE}/primos/${n}`, { tags: { name: '/primos/[n]' } });
    check(r, {
      'primos 200': (x) => x.status === 200,
      'primos body': (x) => { const b = x.json(); return b.n === n && b.maior_primo <= n && b.quantidade > 0; },
    });
  } else if (p < 6) {
    const rounds = rnd(R);
    const r = http.post(`${BASE}/assinar`, JSON.stringify({ texto: `doc-${__VU}-${__ITER}`, rounds }),
      { headers: { 'Content-Type': 'application/json' }, tags: { name: '/assinar' } });
    check(r, {
      'assinar 200': (x) => x.status === 200,
      'assinar body': (x) => { const b = x.json(); return b.rounds === rounds && hex64(b.assinatura); },
    });
  } else {
    const t = rnd(M);
    const seed = Math.floor(Math.random() * 2 ** 31);
    const r = http.get(`${BASE}/cifrar-matriz/${t}?seed=${seed}`, { tags: { name: '/cifrar-matriz/[tamanho]' } });
    check(r, {
      'matriz 200': (x) => x.status === 200,
      'matriz body': (x) => { const b = x.json(); return b.tamanho === t && b.seed === seed && b.checksum >= b.traco; },
    });
  }
  sleep(W[0] + Math.random() * (W[1] - W[0]));
}
