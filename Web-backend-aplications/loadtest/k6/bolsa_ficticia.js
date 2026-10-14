// Secondary load tool for Bolsa Fictícia (k6). Primary tool is Locust.
// Usage: k6 run -e BASE_URL=http://127.0.0.1:8003 -e VUS=20 -e DURATION=60s loadtest/k6/bolsa_ficticia.js
// Knobs mirror the locustfile: BOLSA_SALDO, BOLSA_*_WEIGHT (operar,ordem,tick,carteira,acoes,ordens),
// BOLSA_ORDEM_QTD_MAX, LT_WAIT_MIN/MAX (seconds). Call POST /admin/reset before a run for repeatability.
import http from 'k6/http';
import { check, sleep } from 'k6';

const E = (k, d) => (__ENV[k] !== undefined ? Number(__ENV[k]) : d);
const BASE = __ENV.BASE_URL || 'http://127.0.0.1:8003';
const SALDO = E('BOLSA_SALDO', 10000);
const QTD_MAX = E('BOLSA_ORDEM_QTD_MAX', 10);
const W = [E('LT_WAIT_MIN', 0.5), E('LT_WAIT_MAX', 1.5)];
const TICKERS = ['CAFE3', 'MINA3', 'TECN3', 'ENER3'];
const PESOS = [
  ['operar', E('BOLSA_OPERAR_WEIGHT', 10)],
  ['ordem', E('BOLSA_ORDEM_WEIGHT', 2)],
  ['tick', E('BOLSA_TICK_WEIGHT', 2)],
  ['carteira', E('BOLSA_CARTEIRA_WEIGHT', 3)],
  ['acoes', E('BOLSA_ACOES_WEIGHT', 3)],
  ['ordens', E('BOLSA_ORDENS_WEIGHT', 2)],
];
const TOTAL = PESOS.reduce((s, [, w]) => s + w, 0);
const JSONH = { headers: { 'Content-Type': 'application/json' } };

export const options = {
  vus: E('VUS', 20),
  duration: __ENV.DURATION || '60s',
  thresholds: { checks: ['rate==1.0'] },
};

let invId = null; // one investor per VU (module state is per-VU)

const ordemOk = (o) => o && o.investidor_id === invId && o.quantidade > 0 &&
  o.valor_centavos === o.preco_centavos * o.quantidade && o.saldo_apos_centavos >= 0;
const pick = (a) => a[Math.floor(Math.random() * a.length)];

function escolher() {
  let p = Math.random() * TOTAL;
  for (const [nome, w] of PESOS) { if ((p -= w) < 0) return nome; }
  return PESOS[0][0];
}

export default function () {
  if (invId === null) {
    const r = http.post(`${BASE}/investidores`, JSON.stringify({ nome: `k6-${__VU}`, saldo_inicial: SALDO }),
      { ...JSONH, tags: { name: '/investidores' } });
    if (check(r, { 'investidor 201': (x) => x.status === 201 && x.json('saldo_centavos') === Math.round(SALDO * 100) })) {
      invId = r.json('id');
    } else { sleep(1); return; }
  }
  const t = escolher();
  if (t === 'operar') {
    const r = http.post(`${BASE}/investidores/${invId}/operar`, null, { tags: { name: '/investidores/[id]/operar' } });
    check(r, {
      'operar ok': (x) => {
        if (x.status !== 200) return false;
        const b = x.json();
        if (b.decisao === 'manter') return b.ordem === null;
        const esperado = { compra: 'alta', venda: 'baixa' }[b.decisao];
        return b.tendencia === esperado && b.ordem.tipo === b.decisao && ordemOk(b.ordem);
      },
    });
  } else if (t === 'ordem') {
    const body = { investidor_id: invId, ticker: pick(TICKERS), tipo: pick(['compra', 'venda']),
      quantidade: 1 + Math.floor(Math.random() * QTD_MAX) };
    const r = http.post(`${BASE}/ordens`, JSON.stringify(body), { ...JSONH, tags: { name: '/ordens [POST]' },
      responseCallback: http.expectedStatuses(201, 409) });
    check(r, {
      'ordem ok': (x) => (x.status === 409 && x.json('detail').includes('insuficiente')) ||
        (x.status === 201 && ordemOk(x.json()) && x.json('tipo') === body.tipo && x.json('quantidade') === body.quantidade),
    });
  } else if (t === 'tick') {
    const r = http.post(`${BASE}/mercado/tick`, null, { tags: { name: '/mercado/tick' } });
    check(r, { 'tick ok': (x) => x.status === 200 && x.json('tick') >= 1 && x.json('acoes').length === 4 });
  } else if (t === 'carteira') {
    const r = http.get(`${BASE}/investidores/${invId}/carteira`, { tags: { name: '/investidores/[id]/carteira' } });
    check(r, {
      'carteira ok': (x) => {
        if (x.status !== 200) return false;
        const b = x.json();
        const soma = b.posicoes.reduce((s, p) => s + p.valor_mercado_centavos, 0);
        return b.id === invId && b.valor_acoes_centavos === soma && b.patrimonio_centavos === b.saldo_centavos + soma;
      },
    });
  } else if (t === 'acoes') {
    const r = http.get(`${BASE}/acoes`, { tags: { name: '/acoes' } });
    check(r, { 'acoes ok': (x) => x.status === 200 && x.json('acoes').length === 4 });
  } else {
    const r = http.get(`${BASE}/ordens?investidor_id=${invId}&limit=50`, { tags: { name: '/ordens?investidor_id=[id]' } });
    check(r, { 'ordens ok': (x) => x.status === 200 && x.json('ordens').every(ordemOk) });
  }
  sleep(W[0] + Math.random() * (W[1] - W[0]));
}
