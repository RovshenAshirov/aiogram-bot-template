import unittest
from contextlib import asynccontextmanager
from unittest import mock

import asyncpg
import structlog
from structlog.testing import capture_logs

from tests.helpers import FakeDB, user_row
from db.db_api.postgresql import PostgresConnection
from db.db_api.users import CREATE_USERS_TABLE, UsersRepo
from models.user import User


def pool_with(con) -> mock.Mock:
    @asynccontextmanager
    async def acquire():
        yield con

    return mock.Mock(acquire=acquire)


class PostgresConnectionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.con = mock.AsyncMock()
        self.db = PostgresConnection(pool_with(self.con), structlog.get_logger(), {"type": "db"})

    async def test_fetch(self):
        self.con.fetch.return_value = [user_row(5), user_row(6)]
        users = await self.db._fetch("SELECT $1", (1,), User)
        self.assertEqual([u.telegram_id for u in users], [5, 6])
        self.con.fetch.assert_awaited_once_with("SELECT $1", 1)

    async def test_fetch_without_params_and_no_rows(self):
        self.con.fetch.return_value = []
        self.assertEqual(await self.db._fetch("SELECT 1", None, User), [])
        self.con.fetch.assert_awaited_once_with("SELECT 1")

    async def test_fetchrow(self):
        self.con.fetchrow.return_value = user_row(5)
        self.assertEqual((await self.db._fetchrow("SELECT", (5,), User)).telegram_id, 5)
        self.con.fetchrow.assert_awaited_once_with("SELECT", 5)

    async def test_fetchrow_no_row(self):
        self.con.fetchrow.return_value = None
        self.assertIsNone(await self.db._fetchrow("SELECT", None, User))
        self.con.fetchrow.assert_awaited_once_with("SELECT")

    async def test_execute_variants(self):
        await self.db._execute("A", None)
        self.con.execute.assert_awaited_with("A")
        await self.db._execute("B", (1, 2))
        self.con.execute.assert_awaited_with("B", 1, 2)
        await self.db._execute("C", [(1,), (2,)])
        self.con.executemany.assert_awaited_once_with("C", [(1,), (2,)])

    async def test_errors_are_logged_and_reraised(self):
        error = asyncpg.PostgresError("boom")
        self.con.fetch.side_effect = self.con.fetchrow.side_effect = self.con.execute.side_effect = error
        calls = [self.db._fetch("Q", None, User), self.db._fetchrow("Q", None, User), self.db._execute("Q", None)]
        for call in calls:
            with self.subTest(call=call), capture_logs() as logs, self.assertRaises(asyncpg.PostgresError):
                await call
            self.assertEqual(logs[-1]["log_level"], "error")
            self.assertEqual(logs[-1]["sql"], "Q")
            self.assertIs(logs[-1]["error"], error)

    async def test_logger_context_is_reset_per_query(self):
        self.con.fetch.return_value = []
        with capture_logs() as logs:
            await self.db._fetch("Q1", (1,), User)
            await self.db._fetch("Q2", None, User)
        self.assertEqual(logs[1], {"event": "Making query to DB", "log_level": "debug", "type": "db", "sql": "Q2", "params": None})


class UsersRepoTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pool = FakeDB()
        self.repo = UsersRepo(self.pool, structlog.get_logger(), {})

    async def test_add_user_new_and_existing(self):
        user = await self.repo.add_user(5, "Ali", "ali")
        self.assertEqual((user.telegram_id, user.full_name, user.username), (5, "Ali", "ali"))
        self.assertIsNone(await self.repo.add_user(5, "Ali", "ali"))
        self.assertIn("ON CONFLICT (telegram_id) DO NOTHING", self.pool.queries[0][0])
        self.assertEqual(self.pool.queries[0][1], ("Ali", "ali", 5))

    async def test_add_user_without_username(self):
        self.assertIsNone((await self.repo.add_user(5, "Ali", None)).username)

    async def test_count_users(self):
        self.assertEqual(await self.repo.count_users(), 0)
        await self.repo.add_user(5, "A", None)
        await self.repo.add_user(6, "B", None)
        self.assertEqual(await self.repo.count_users(), 2)

    async def test_count_users_no_row(self):
        con = mock.AsyncMock()
        con.fetchrow.return_value = None
        self.assertEqual(await UsersRepo(pool_with(con), structlog.get_logger(), {}).count_users(), 0)

    async def test_all_users(self):
        self.assertEqual(await self.repo.all_users(), [])
        await self.repo.add_user(5, "A", None)
        self.assertEqual([u.telegram_id for u in await self.repo.all_users()], [5])

    async def test_db_error_is_not_no_rows(self):
        repo = UsersRepo(FakeDB(error=asyncpg.PostgresError("down")), structlog.get_logger(), {})
        with self.assertRaises(asyncpg.PostgresError):
            await repo.all_users()

    def test_create_table_sql(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS users", CREATE_USERS_TABLE)
        self.assertIn("telegram_id BIGINT NOT NULL UNIQUE", CREATE_USERS_TABLE)  # ON CONFLICT needs the unique constraint


if __name__ == "__main__":
    unittest.main()
