from pydantic import BaseModel


class HealthResposta(BaseModel):
    status: str
    app: str
    pid: int
