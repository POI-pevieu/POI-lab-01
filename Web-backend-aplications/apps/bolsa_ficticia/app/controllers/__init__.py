"""Controllers HTTP (um APIRouter por recurso): validam entrada, chamam o service, mapeiam erros."""

from . import (acoes_controller, admin_controller, health_controller, investidores_controller,
               livro_caixa_controller, mercado_controller, ordens_controller)

ROUTERS = [
    health_controller.router,
    acoes_controller.router,
    mercado_controller.router,
    investidores_controller.router,
    ordens_controller.router,
    livro_caixa_controller.router,
    admin_controller.router,
]
