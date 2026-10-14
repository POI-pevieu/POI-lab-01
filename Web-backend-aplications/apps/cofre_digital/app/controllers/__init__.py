"""HTTP layer: one APIRouter per resource.

Handlers are plain ``def`` on purpose: FastAPI runs them in the AnyIO
threadpool, keeping the event loop free (see ``app/main.py``).
"""
from __future__ import annotations

from fastapi import HTTPException

from app.exceptions import ParametroInvalidoError


def http_422(exc: ParametroInvalidoError) -> HTTPException:
    """Translate a domain validation error into HTTP 422."""
    return HTTPException(
        status_code=422,
        detail=[{"loc": [exc.campo], "msg": exc.mensagem, "type": "value_error"}],
    )
