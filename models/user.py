from .base import BaseModel


class User(BaseModel):
    id: int
    full_name: str
    username: str | None
    telegram_id: int
