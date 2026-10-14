"""Livro-caixa em disco (append-only, uma linha JSON por ordem executada).

Cada escrita: open(O_APPEND) -> flock(LOCK_EX) -> write -> fsync -> unlock -> close.
O flock serializa escritas de todos os workers/threads, então linhas nunca se
intercalam. O fsync garante que a linha está no disco antes da resposta.
``fsync=False`` (LEDGER_FSYNC=0) existe SÓ para experimentos de comparação.

Obs.: no macOS os.fsync não força o cache do disco (F_FULLFSYNC); no Linux
(VM alvo) os.fsync é a garantia real de durabilidade.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from pathlib import Path


class LedgerRepository:
    def __init__(self, path: Path, fsync: bool = True):
        self.path = Path(path)
        self.fsync = fsync
        self._lock = threading.Lock()
        self._stats = {"escritas": 0, "bytes_escritos": 0, "fsyncs": 0, "fsync_total_s": 0.0, "fsync_max_s": 0.0}

    def _abrir(self) -> int:
        return os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)

    def init(self) -> None:
        diretorio = self.path.parent
        diretorio.mkdir(parents=True, exist_ok=True)
        criado = not self.path.exists()
        os.close(self._abrir())
        if criado:  # persiste a entrada do diretório
            dfd = os.open(diretorio, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)

    def registrar(self, entrada: dict) -> None:
        """Acrescenta uma linha e faz fsync. Levanta OSError se write/fsync falhar."""
        linha = (json.dumps(entrada, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()
        fd = self._abrir()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                view = memoryview(linha)
                while view:
                    n = os.write(fd, view)
                    view = view[n:]
                dt = None
                if self.fsync:
                    t0 = time.perf_counter()
                    os.fsync(fd)
                    dt = time.perf_counter() - t0
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        with self._lock:
            self._stats["escritas"] += 1
            self._stats["bytes_escritos"] += len(linha)
            if dt is not None:
                self._stats["fsyncs"] += 1
                self._stats["fsync_total_s"] += dt
                self._stats["fsync_max_s"] = max(self._stats["fsync_max_s"], dt)

    def truncar(self) -> None:
        fd = self._abrir()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                os.ftruncate(fd, 0)
                os.fsync(fd)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        with self._lock:
            for k in self._stats:
                self._stats[k] = 0 if isinstance(self._stats[k], int) else 0.0

    def estatisticas(self) -> dict:
        tamanho = 0
        linhas = 0
        if self.path.exists():
            with open(self.path, "rb") as f:
                while bloco := f.read(1 << 20):
                    tamanho += len(bloco)
                    linhas += bloco.count(b"\n")
        with self._lock:
            proc = dict(self._stats)
        proc["fsync_medio_ms"] = (proc["fsync_total_s"] / proc["fsyncs"] * 1000.0) if proc["fsyncs"] else None
        proc["fsync_max_ms"] = proc.pop("fsync_max_s") * 1000.0
        proc["fsync_total_ms"] = proc.pop("fsync_total_s") * 1000.0
        return {
            "arquivo": str(self.path),
            "fsync_habilitado": self.fsync,
            "tamanho_bytes": tamanho,
            "linhas": linhas,
            "processo": {"pid": os.getpid(), **proc},
        }
