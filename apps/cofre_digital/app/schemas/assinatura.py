from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import settings


class AssinarRequest(BaseModel):
    texto: str = Field(..., max_length=settings.texto_max_len)
    rounds: int = Field(..., ge=settings.rounds_min, le=settings.rounds_max)


class AssinarResponse(BaseModel):
    assinatura: str
    rounds: int
    algoritmo: str
    elapsed_ms: float
