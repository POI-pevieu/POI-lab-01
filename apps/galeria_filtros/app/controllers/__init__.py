"""Routers HTTP: validam entrada, chamam services e traduzem excecoes de dominio."""

from . import cache_controller, health_controller, imagens_controller, mosaico_controller

routers = [
    health_controller.router,
    imagens_controller.router,
    mosaico_controller.router,
    cache_controller.router,
]

__all__ = ["routers"]
