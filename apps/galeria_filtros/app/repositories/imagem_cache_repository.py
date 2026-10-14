"""Repositorio: cache LRU em memoria (thread-safe), limitado por bytes e/ou por numero de itens.

Apenas acesso a dados (inserir/buscar/listar/limpar/estatisticas); nenhuma
regra de negocio ou HTTP aqui.

Cada processo (worker do gunicorn) possui a SUA PROPRIA instancia deste cache:
nao ha memoria compartilhada entre workers. Com WORKERS=N o consumo maximo de
memoria do cache e ~N x capacidade, e hits/misses dependem de qual worker
atende a requisicao.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from ..exceptions import ImagemMaiorQueCache

@dataclass
class Contadores:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    inserts: int = 0
    evicted_bytes: int = 0


@dataclass
class ImagemCacheRepository:
    max_bytes: int
    max_items: int | None = None
    _dados: "OrderedDict[str, np.ndarray]" = field(default_factory=OrderedDict)
    _bytes: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    contadores: Contadores = field(default_factory=Contadores)

    def put(self, chave: str, arr: np.ndarray) -> list[str]:
        """Insere/substitui `chave`. Retorna a lista de chaves despejadas (LRU primeiro)."""
        tamanho = int(arr.nbytes)
        if tamanho > self.max_bytes:
            raise ImagemMaiorQueCache(tamanho, self.max_bytes)
        despejadas: list[str] = []
        with self._lock:
            antigo = self._dados.pop(chave, None)
            if antigo is not None:
                self._bytes -= int(antigo.nbytes)
            while self._dados and (
                self._bytes + tamanho > self.max_bytes
                or (self.max_items is not None and len(self._dados) + 1 > self.max_items)
            ):
                k, v = self._dados.popitem(last=False)
                self._bytes -= int(v.nbytes)
                self.contadores.evictions += 1
                self.contadores.evicted_bytes += int(v.nbytes)
                despejadas.append(k)
            self._dados[chave] = arr
            self._bytes += tamanho
            self.contadores.inserts += 1
        return despejadas

    def get(self, chave: str) -> np.ndarray | None:
        with self._lock:
            arr = self._dados.get(chave)
            if arr is None:
                self.contadores.misses += 1
                return None
            self._dados.move_to_end(chave)
            self.contadores.hits += 1
            return arr

    def mais_recentes(self, k: int) -> list[tuple[str, np.ndarray]]:
        """Snapshot das k entradas usadas mais recentemente (nao altera a ordem LRU)."""
        with self._lock:
            itens = list(self._dados.items())
        return list(reversed(itens[-k:])) if k > 0 else []

    def limpar(self) -> int:
        with self._lock:
            n = len(self._dados)
            self._dados.clear()
            self._bytes = 0
            self.contadores = Contadores()
        return n

    def snapshot(self, max_ids: int = 200) -> dict:
        with self._lock:
            ids = list(self._dados.keys())
            c = self.contadores
            total = c.hits + c.misses
            return {
                "bytes": self._bytes,
                "itens": len(ids),
                "capacidade_bytes": self.max_bytes,
                "capacidade_itens": self.max_items,
                "hits": c.hits,
                "misses": c.misses,
                "hit_ratio": (c.hits / total) if total else None,
                "evictions": c.evictions,
                "evicted_bytes": c.evicted_bytes,
                "inserts": c.inserts,
                # ordem LRU: primeiro = menos recentemente usado (proximo a ser despejado)
                "ids_lru": ids[:max_ids],
                "ids_truncado": len(ids) > max_ids,
            }
