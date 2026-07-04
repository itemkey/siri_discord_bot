from __future__ import annotations

import unittest

from siri_bot.bunker.permissions import PRIVATE_BUNKER_PERMISSION_PLAN


class BunkerPermissionTests(unittest.TestCase):
    def test_private_bunker_permission_plan_closes_everyone_and_allows_members(self) -> None:
        plan = PRIVATE_BUNKER_PERMISSION_PLAN

        self.assertFalse(plan.everyone_view_channel)
        self.assertFalse(plan.everyone_connect)
        self.assertTrue(plan.member_view_channel)
        self.assertTrue(plan.member_send_messages)
        self.assertTrue(plan.member_connect)
        self.assertTrue(plan.member_speak)


if __name__ == "__main__":
    unittest.main()

