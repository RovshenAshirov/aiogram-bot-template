from models.base import BaseModel
from models.user import User

from .postgresql import PostgresConnection

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    username VARCHAR(255) NULL,
    telegram_id BIGINT NOT NULL UNIQUE
)
"""


class Count(BaseModel):
    count: int


class UsersRepo(PostgresConnection):
    async def add_user(
        self, telegram_id: int, full_name: str, username: str | None
    ) -> User | None:
        """Returns None if the user already exists."""
        return await self._fetchrow(
            "INSERT INTO users (full_name, username, telegram_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (telegram_id) DO NOTHING RETURNING *",
            (full_name, username, telegram_id),
            User,
        )

    async def count_users(self) -> int:
        row = await self._fetchrow("SELECT COUNT(*) AS count FROM users", None, Count)
        return row.count if row else 0
