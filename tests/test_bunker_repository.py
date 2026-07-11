from __future__ import annotations

import os
import unittest
from uuid import uuid4

import asyncpg

from siri_bot.bunker.models import PackSubmissionStatus
from siri_bot.bunker.repository import BunkerRepository


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_DATABASE_URL, "Set TEST_DATABASE_URL to run PostgreSQL repository tests.")
class BunkerRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.schema = f"test_bunker_{uuid4().hex}"
        self.admin = await asyncpg.connect(TEST_DATABASE_URL)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.pool = await asyncpg.create_pool(
            TEST_DATABASE_URL,
            min_size=1,
            max_size=2,
            server_settings={"search_path": self.schema},
        )
        self.repository = BunkerRepository(self.pool)
        await self.repository.init_schema()

    async def asyncTearDown(self) -> None:
        await self.pool.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def test_builder_settings_and_progress(self) -> None:
        settings = await self.repository.set_builder_reward_role(100, 500)
        self.assertEqual(settings.builder_reward_role_id, 500)

        settings = await self.repository.set_builder_info_channel(100, 900)
        self.assertEqual(settings.builder_reward_role_id, 500)
        self.assertEqual(settings.builder_info_channel_id, 900)

        progress = await self.repository.get_builder_progress(100, 200)
        self.assertFalse(progress.agreement_accepted)
        self.assertFalse(progress.tutorial_completed)

        progress = await self.repository.accept_builder_agreement(100, 200)
        self.assertTrue(progress.agreement_accepted)
        self.assertFalse(progress.tutorial_completed)

        progress = await self.repository.complete_builder_tutorial(100, 200)
        self.assertTrue(progress.tutorial_completed)

    async def test_pack_submission_accept_creates_content_pack(self) -> None:
        submission = await self.repository.create_pack_submission(
            guild_id=100,
            author_id=200,
            name="Пак автора",
            description="desc",
            content={"professions": ("Инженер",), "items": ("Фильтр",)},
            source_filename="pack.bunker-pack.json",
        )

        pending = await self.repository.list_pack_submissions(100)
        self.assertEqual([item.id for item in pending], [submission.id])
        self.assertEqual(await self.repository.count_pack_submissions(100), 1)

        accepted = await self.repository.accept_pack_submission(submission.id, guild_id=100, reviewer_id=300)
        self.assertIsNotNone(accepted)
        reviewed, pack = accepted

        self.assertEqual(reviewed.status, PackSubmissionStatus.ACCEPTED)
        self.assertEqual(reviewed.content_pack_id, pack.id)
        self.assertEqual(pack.name, "Пак автора")
        self.assertEqual(pack.content["professions"], ("Инженер",))
        self.assertEqual(await self.repository.count_pack_submissions(100), 0)
        self.assertIsNone(await self.repository.accept_pack_submission(submission.id, guild_id=100, reviewer_id=300))

    async def test_pack_submission_reject_leaves_content_packs_untouched(self) -> None:
        submission = await self.repository.create_pack_submission(
            guild_id=100,
            author_id=200,
            name="Пак на отказ",
            content={"professions": ("Инженер",)},
            source_filename="pack.bunker-pack.json",
        )

        rejected = await self.repository.reject_pack_submission(submission.id, guild_id=100, reviewer_id=300)

        self.assertIsNotNone(rejected)
        self.assertEqual(rejected.status, PackSubmissionStatus.REJECTED)
        self.assertEqual(await self.repository.list_content_packs(100), [])
        self.assertIsNone(await self.repository.reject_pack_submission(submission.id, guild_id=100, reviewer_id=300))


if __name__ == "__main__":
    unittest.main()
