from __future__ import annotations

import os
import unittest
from uuid import uuid4

import asyncpg

from siri_bot.leveling.formula import FormulaConfig
from siri_bot.leveling.repository import LevelingRepository


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_DATABASE_URL, "Set TEST_DATABASE_URL to run PostgreSQL repository tests.")
class LevelingRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.schema = f"test_leveling_{uuid4().hex}"
        self.admin = await asyncpg.connect(TEST_DATABASE_URL)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.pool = await asyncpg.create_pool(
            TEST_DATABASE_URL,
            min_size=1,
            max_size=2,
            server_settings={"search_path": self.schema},
        )
        self.repository = LevelingRepository(self.pool)
        await self.repository.init_schema()

    async def asyncTearDown(self) -> None:
        await self.pool.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def test_defaults_and_xp_progress(self) -> None:
        settings = await self.repository.get_settings(100)

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.message_xp_min, 15)

        change = await self.repository.add_xp(100, 200, 120, FormulaConfig())

        self.assertEqual(change.old_total_xp, 0)
        self.assertEqual(change.new_total_xp, 120)
        self.assertEqual(change.old_level, 0)
        self.assertEqual(change.new_level, 1)
        self.assertEqual(await self.repository.get_member_rank(100, 200), 1)

    async def test_cooldown_acquire(self) -> None:
        self.assertTrue(await self.repository.try_acquire_cooldown(100, 200, "message", 60))
        self.assertFalse(await self.repository.try_acquire_cooldown(100, 200, "message", 60))

    async def test_role_rewards_and_boosters(self) -> None:
        await self.repository.upsert_role_reward(100, 5, 500)
        await self.repository.upsert_role_reward(100, 10, 1000)

        self.assertEqual(await self.repository.get_role_rewards(100), [(5, 500), (10, 1000)])

        await self.repository.add_booster(100, "global", None, 2.0, None)
        await self.repository.add_booster(100, "role", 500, 2.0, None)

        self.assertEqual(await self.repository.get_booster_multiplier(100, 200, [500]), 4.0)

    async def test_leaderboard_and_reset(self) -> None:
        config = FormulaConfig()
        await self.repository.add_xp(100, 1, 100, config)
        await self.repository.add_xp(100, 2, 200, config)

        entries = await self.repository.get_leaderboard(100, limit=10, offset=0)

        self.assertEqual([entry.user_id for entry in entries], [2, 1])

        await self.repository.reset_guild_progress(100)
        self.assertEqual(await self.repository.get_leaderboard(100, limit=10, offset=0), [])

    async def test_panel_result_message_id(self) -> None:
        self.assertIsNone(await self.repository.get_panel_result_message_id(100, 300))

        await self.repository.upsert_panel_result_message_id(100, 300, 900)
        self.assertEqual(await self.repository.get_panel_result_message_id(100, 300), 900)

        await self.repository.upsert_panel_result_message_id(100, 300, 901)
        self.assertEqual(await self.repository.get_panel_result_message_id(100, 300), 901)


if __name__ == "__main__":
    unittest.main()
