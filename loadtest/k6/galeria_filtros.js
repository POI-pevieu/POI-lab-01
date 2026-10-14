// Carga da Galeria de Filtros (memory-bound). Cria imagens, aplica filtros, monta mosaicos.
// Ex.: k6 run -e BASE_URL=http://127.0.0.1:8002 -e VUS=10 loadtest/k6/galeria_filtros.js
// Parâmetros: GALERIA_RES_MIN/MAX, GALERIA_ID_SPACE, GALERIA_MOSAICO_K,
// GALERIA_W_CRIAR/FILTRO/MOSAICO/STATS, LT_WAIT_MIN/MAX (s). Perfil de carga e resumo: ver comum.js.
// 404 no /filtro é miss de cache (esperado); 409 no /mosaico é cache vazio.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { opcoes, resumo } from './comum.js';

const E = (k, d) => (__ENV[k] !== undefined ? Number(__ENV[k]) : d);
const BASE = __ENV.BASE_URL || 'http://127.0.0.1:8002';
const RES = [E('GALERIA_RES_MIN', 1000), E('GALERIA_RES_MAX', 3000)];
const IDS = E('GALERIA_ID_SPACE', 128);
const K = E('GALERIA_MOSAICO_K', 4);
const WT = [E('GALERIA_W_CRIAR', 3), E('GALERIA_W_FILTRO', 6), E('GALERIA_W_MOSAICO', 1), E('GALERIA_W_STATS', 1)];
const W = [E('LT_WAIT_MIN', 0.5), E('LT_WAIT_MAX', 1.5)];

export const options = opcoes(10);

export function handleSummary(data) {
  return resumo(data);
}

const rnd = ([a, b]) => a + Math.floor(Math.random() * (b - a + 1));
const id = () => `img${Math.floor(Math.random() * IDS)}`;
const total = WT.reduce((a, b) => a + b, 0);

export default function () {
  let p = Math.random() * total;
  if ((p -= WT[0]) < 0) {
    const i = id(), r = rnd(RES);
    const x = http.post(`${BASE}/imagens/${i}?resolucao=${r}`, null, { tags: { name: 'POST /imagens/[id]' } });
    check(x, { 'criar ok': (x) => x.status === 200 && x.json('bytes') === r * r * 3 });
  } else if ((p -= WT[1]) < 0) {
    const tipo = Math.random() < 0.5 ? 'negativo' : 'cinza';
    const x = http.get(`${BASE}/imagens/${id()}/filtro?tipo=${tipo}`, {
      tags: { name: `GET /imagens/[id]/filtro?tipo=${tipo}` },
      responseCallback: http.expectedStatuses(200, 404),
    });
    check(x, {
      'filtro ok ou miss': (x) => x.status === 404
        || (x.status === 200 && x.json('filtrada.shape').length === (tipo === 'negativo' ? 3 : 2)),
    });
  } else if ((p -= WT[2]) < 0) {
    const x = http.get(`${BASE}/mosaico?quantidade=${K}`, {
      tags: { name: 'GET /mosaico?quantidade=[k]' },
      responseCallback: http.expectedStatuses(200, 409),
    });
    check(x, { 'mosaico ok': (x) => x.status === 409 || (x.status === 200 && x.json('quantidade_usada') >= 1) });
  } else {
    const x = http.get(`${BASE}/cache/stats`, { tags: { name: 'GET /cache/stats' } });
    check(x, { 'stats ok': (x) => x.status === 200 && x.json('bytes') <= x.json('capacidade_bytes') });
  }
  sleep(W[0] + Math.random() * (W[1] - W[0]));
}
