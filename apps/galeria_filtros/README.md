# Galeria de Filtros (memory-bound, porta 8002)

Imagens sintéticas RGB (`numpy uint8`, R×R×3, gradientes + ruído, determinísticas a partir do `id`)
ficam num **cache LRU em memória** limitado por bytes. Filtros criam cópias temporárias e o
mosaico aloca um array grande. Não há upload nem disco: o recurso dominante é a RAM.

Uma imagem 4000×4000×3 ocupa 48 000 000 bytes (~45,8 MiB); 1000×1000×3 = 3 MB.

## Estrutura (padrão Service–Controller)

```
app/
  main.py                    # cria o FastAPI, inclui os routers, inicializa cache/services (entry point app.main:app)
  config.py                  # Settings lidas do ambiente (CACHE_MAX_MB/auto, CACHE_RAM_FRACTION, CACHE_MAX_ITEMS, MOSAICO_MAX_MB, WORKERS)
  dependencies.py            # providers p/ Depends: UMA instância do repositório (cache) por processo + services
  exceptions.py              # exceções de domínio: ImagemNaoEncontrada, ImagemMaiorQueCache, CacheVazio, ResolucaoInvalida, FiltroDesconhecido
  controllers/               # HTTP apenas: validação de parâmetros, chamada ao service, exceção → status
    erros.py                 #   ImagemNaoEncontrada→404, CacheVazio→409, ParametroInvalido→422, ImagemMaiorQueCache→507
    health_controller.py     #   GET /health
    imagens_controller.py    #   POST /imagens/{id}, GET /imagens/{id}/filtro
    mosaico_controller.py    #   GET /mosaico
    cache_controller.py      #   GET /cache/stats, DELETE /cache
  services/                  # regras de negócio (sem fastapi/starlette)
    imagem_service.py        #   geração determinística (gerar_imagem), resumo/checksum, ImagemService.criar
    filtro_service.py        #   negativo/cinza (cópia nova), PNG (Pillow), FiltroService
    mosaico_service.py       #   geometria/alocação do mosaico, MosaicoService
    cache_service.py         #   estatísticas (psutil), limpeza com gc.collect + malloc_trim
  repositories/
    imagem_cache_repository.py  # cache LRU thread-safe limitado por bytes/itens (numpy arrays)
  schemas/                   # modelos Pydantic de resposta (imagem, mosaico, cache, health)
```

Fluxo: controller → service → repositório. As funções puras (`gerar_imagem`, `filtro_cinza`,
`montar_mosaico`, ...) ficam nos services e são reutilizadas pelos testes para recalcular resultados.

## Endpoints

| método | rota | descrição |
|---|---|---|
| `POST` | `/imagens/{id}?resolucao=R` | gera a imagem (R em 500..4000, padrão 1000) e a guarda no cache (substitui se o id já existir). Retorna `id, shape, bytes, despejadas, cache{...}, pid`. **507** se a imagem sozinha for maior que a capacidade do cache. |
| `GET` | `/imagens/{id}/filtro?tipo=negativo\|cinza[&formato=resumo\|png]` | busca no cache (**404 = miss**, contabilizado), cria uma cópia filtrada (negativo = `255-img`; cinza = luminância BT.601 em ponto fixo `(77R+150G+29B)/256`, R×R). Por padrão retorna resumo `{shape, dtype, bytes, media, checksum_crc32}`; `formato=png` devolve a imagem codificada (Pillow) para demonstração. |
| `GET` | `/mosaico?quantidade=K` | K (1..64) imagens mais recentes do cache, recortadas para o menor lado e dispostas em grade ⌈√K⌉ colunas. Retorna resumo (`shape, bytes, ids, media, checksum_crc32`). Se o mosaico passar de `MOSAICO_MAX_MB`, o ladrilho é reduzido. **409** se o cache estiver vazio. |
| `GET` | `/cache/stats` | `bytes` (soma de `nbytes`), `itens`, `capacidade_bytes/itens`, `hits`, `misses`, `hit_ratio`, `evictions`, `evicted_bytes`, `inserts`, `ids_lru` (do menos ao mais recente), `pid`, `rss_bytes` (psutil) e memória do sistema. |
| `DELETE` | `/cache` | esvazia o cache, zera contadores, roda `gc.collect()` e, em Linux/glibc, `malloc_trim(0)`. Retorna RSS antes/depois. Use entre execuções de experimento. |
| `GET` | `/health` | `{status, app, pid}` |

Parâmetros inválidos retornam **422** (validação do FastAPI). `id` aceita `[A-Za-z0-9_-]{1,64}`.

## Variáveis de ambiente

| var | padrão | significado |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `8002` | bind |
| `WORKERS` | `1` | workers gunicorn (veja aviso abaixo) |
| `TIMEOUT` | `120` | timeout do worker (s) |
| `LOG_LEVEL` | `warning` | access log só em `info`/`debug` |
| `CACHE_MAX_MB` | `1024` | capacidade do cache **por processo** em MiB, ou `auto` |
| `CACHE_RAM_FRACTION` | `0.5` | com `CACHE_MAX_MB=auto`: capacidade = fração × RAM total ÷ `WORKERS` |
| `CACHE_MAX_ITEMS` | (sem limite) | limite adicional por número de imagens |
| `MOSAICO_MAX_MB` | `512` | tamanho máximo do array do mosaico |
| `MALLOC_ARENA_MAX` | `2` (definido em `run.sh`) | limita arenas do glibc malloc |

### Cache por processo (importante)
Cada worker do gunicorn é um processo separado com **seu próprio cache** — não há memória
compartilhada. Com `WORKERS=N`, o consumo pode chegar a N × capacidade, e um `POST` atendido
pelo worker A seguido de um `GET` atendido pelo worker B resulta em miss. `/cache/stats` e
`DELETE /cache` refletem apenas o worker que atendeu (veja o `pid`). Por isso o cenário de carga
usa `WORKERS=1`. O modo `CACHE_MAX_MB=auto` divide a fração de RAM pelo número de workers.

### Cenários de RAM
O knob central do experimento é a relação entre a RAM da VM e a capacidade do cache.
`CACHE_MAX_MB=auto` faz a capacidade acompanhar o cenário de provisionamento automaticamente.
Deixe folga (fração ≤ 0,5): cada filtro aloca uma cópia (negativo = 1× a imagem; cinza usa
dois arrays float32 temporários, pico ≈ 2,7× a imagem) e cada mosaico até `MOSAICO_MAX_MB`, tudo multiplicado pelo
número de requisições simultâneas. Com pouca RAM o SO vai para swap ou o OOM killer encerra o
worker — isso é comportamento real a ser medido, não um erro da aplicação.

### Memória devolvida ao SO
Arrays grandes do numpy (> limiar de `mmap` do glibc, máx. 32 MiB) são liberados direto ao SO;
arrays menores vêm do heap e podem ficar retidos (RSS não cai mesmo após despejo).
`MALLOC_ARENA_MAX=2` reduz a fragmentação entre threads; `DELETE /cache` chama `malloc_trim(0)`
de verdade (Linux/glibc; em macOS reporta `"indisponivel"`). Compare `bytes` (cache) com
`rss_bytes` (processo) em `/cache/stats`.

### Concorrência
Os handlers são `def` (não `async`): o FastAPI os executa no threadpool do AnyIO (40 threads
por padrão), então o event loop não é gargalo artificial. As operações numpy grandes liberam o
GIL. O cache usa um `threading.Lock`; a geração da imagem acontece fora do lock.

## Executar

```bash
cd apps/galeria_filtros
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
CACHE_MAX_MB=auto CACHE_RAM_FRACTION=0.4 ./run.sh        # gunicorn -c gunicorn.conf.py app.main:app

curl -X POST 'localhost:8002/imagens/foto1?resolucao=2000'
curl 'localhost:8002/imagens/foto1/filtro?tipo=negativo'
curl 'localhost:8002/imagens/foto1/filtro?tipo=cinza&formato=png' -o cinza.png
curl 'localhost:8002/mosaico?quantidade=4'
curl localhost:8002/cache/stats
curl -X DELETE localhost:8002/cache
```

## Testes
Funcionais, contra um servidor gunicorn real iniciado pelo `tests/conftest.py` em porta livre
(cache de 4 MiB, e um segundo servidor com `CACHE_MAX_MB=auto`). Sem mocks.
`tests/test_services.py` testa os services e o repositório diretamente (objetos reais, imagens pequenas).

```bash
.venv/bin/python -m pytest -q
```

## Carga
- Script k6: `loadtest/k6/galeria_filtros.js` (parâmetros `GALERIA_RES_MIN/MAX`, `GALERIA_ID_SPACE`,
  `GALERIA_MOSAICO_K`, pesos `GALERIA_W_*`). Misses (404 no filtro) e cache vazio (409 no mosaico)
  são esperados e não contam como falha.
- No experimento, o `loadtest/run_scenario.sh` sobe a app com `WORKERS=1 CACHE_MAX_MB=auto
  CACHE_RAM_FRACTION=0.4`, esvazia o cache (`DELETE /cache`) antes de cada nível e salva o
  `/cache/stats` depois dele.
