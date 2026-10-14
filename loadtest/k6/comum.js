// Partes comuns dos scripts k6: perfil de carga e gravação do resumo em JSON.
//
// Variáveis (-e NOME=valor):
//   VUS       usuários simultâneos no patamar
//   RAMPA     tempo de subida até VUS (padrão 10s)
//   DURACAO   tempo no patamar (padrão 2m)
//   SAIDA     caminho do JSON de resumo (sem SAIDA, só imprime no terminal)
//   APP, CENARIO, NIVEL   gravados no JSON para identificar a execução

function segundos(txt) {
  const m = /^(\d+(?:\.\d+)?)(ms|s|m|h)$/.exec(txt);
  if (!m) throw new Error(`duração inválida: ${txt} (use 30s, 2m...)`);
  return Number(m[1]) * { ms: 0.001, s: 1, m: 60, h: 3600 }[m[2]];
}

const RAMPA = __ENV.RAMPA || '10s';
const DURACAO = __ENV.DURACAO || '2m';

export function opcoes(vusPadrao) {
  const vus = Number(__ENV.VUS || vusPadrao);
  return {
    stages: [
      { duration: RAMPA, target: vus },
      { duration: DURACAO, target: vus },
    ],
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  };
}

export function resumo(data) {
  const fim = Date.now() / 1000;
  const m = data.metrics;
  const vus = Number(__ENV.VUS);
  const execucao = {
    app: __ENV.APP,
    cenario: __ENV.CENARIO,
    nivel: __ENV.NIVEL,
    vus,
    rampa_s: segundos(RAMPA),
    duracao_s: segundos(DURACAO),
    inicio_epoch: fim - data.state.testRunDurationMs / 1000,
    fim_epoch: fim,
    metricas: m,
  };

  const lat = m.http_req_duration.values;
  const chk = m.checks ? m.checks.values : { passes: 0, fails: 0 };
  const falhas = (100 * chk.fails) / Math.max(1, chk.passes + chk.fails);
  const linha = `  ${__ENV.NIVEL || '-'} (${vus} VUs): ${m.http_reqs.values.rate.toFixed(1)} req/s, ` +
    `p50 ${lat.med.toFixed(0)} ms, p95 ${lat['p(95)'].toFixed(0)} ms, falhas ${falhas.toFixed(1)}%\n`;

  const saida = { stdout: linha };
  if (__ENV.SAIDA) saida[__ENV.SAIDA] = JSON.stringify(execucao, null, 2);
  return saida;
}
