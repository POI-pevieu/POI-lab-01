"""Excecoes de dominio levantadas por services/repositories.

Nao dependem de FastAPI: os controllers as traduzem para codigos HTTP
(ver `controllers/erros.py`).
"""

from __future__ import annotations


class ErroGaleria(Exception):
    """Base de todas as excecoes de dominio da Galeria de Filtros."""


class ImagemNaoEncontrada(ErroGaleria):
    """A imagem pedida nao esta no cache (miss). -> HTTP 404"""

    def __init__(self, id_imagem: str) -> None:
        self.id_imagem = id_imagem
        super().__init__(f"imagem '{id_imagem}' nao esta no cache (miss)")


class ImagemMaiorQueCache(ErroGaleria):
    """A imagem sozinha excede a capacidade total do cache. -> HTTP 507"""

    def __init__(self, tamanho: int, capacidade: int) -> None:
        self.tamanho = tamanho
        self.capacidade = capacidade
        super().__init__(
            f"imagem de {tamanho} bytes excede a capacidade do cache ({capacidade} bytes)"
        )


class CacheVazio(ErroGaleria):
    """Nao ha imagens no cache para montar o mosaico. -> HTTP 409"""

    def __init__(self) -> None:
        super().__init__("cache vazio: nenhuma imagem para o mosaico")


class ParametroInvalido(ErroGaleria, ValueError):
    """Parametro fora do dominio (resolucao, filtro). -> HTTP 422

    Na pratica a validacao do FastAPI nos controllers barra esses casos antes;
    o service valida de novo para ser correto quando chamado diretamente.
    """


class ResolucaoInvalida(ParametroInvalido):
    pass


class FiltroDesconhecido(ParametroInvalido):
    pass
