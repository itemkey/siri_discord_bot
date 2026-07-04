from __future__ import annotations

import unittest

from siri_bot.bunker.permissions import PRIVATE_BUNKER_PERMISSION_PLAN
from siri_bot.bunker.permissions import (
    build_admin_text_overwrites,
    build_admin_voice_overwrites,
    build_lobby_text_overwrites,
    build_private_voice_overwrites,
)


class BunkerPermissionTests(unittest.TestCase):
    def test_private_bunker_permission_plan_closes_everyone_and_allows_members(self) -> None:
        plan = PRIVATE_BUNKER_PERMISSION_PLAN

        self.assertFalse(plan.everyone_view_channel)
        self.assertFalse(plan.everyone_connect)
        self.assertTrue(plan.member_view_channel)
        self.assertTrue(plan.member_send_messages)
        self.assertTrue(plan.member_connect)
        self.assertTrue(plan.member_speak)

    def test_admin_text_overwrites_allow_operator_role(self) -> None:
        default_role = object()
        bot_member = object()
        operator_role = object()
        host = object()
        guild = type("Guild", (), {"default_role": default_role, "me": bot_member})()

        overwrites = build_admin_text_overwrites(guild, operator_role, [host])

        self.assertFalse(overwrites[default_role].view_channel)
        self.assertTrue(overwrites[operator_role].view_channel)
        self.assertTrue(overwrites[operator_role].send_messages)
        self.assertNotIn(host, overwrites)

    def test_public_lobby_text_can_be_limited_to_interest_role(self) -> None:
        default_role = object()
        bot_member = object()
        interest_role = object()
        host = object()
        guild = type("Guild", (), {"default_role": default_role, "me": bot_member})()

        overwrites = build_lobby_text_overwrites(guild, [host], interest_role=interest_role)

        self.assertFalse(overwrites[default_role].view_channel)
        self.assertTrue(overwrites[interest_role].view_channel)
        self.assertFalse(overwrites[interest_role].send_messages)
        self.assertTrue(overwrites[host].send_messages)

    def test_interest_role_can_see_voice_but_cannot_connect_before_join(self) -> None:
        default_role = object()
        bot_member = object()
        interest_role = object()
        host = object()
        guild = type("Guild", (), {"default_role": default_role, "me": bot_member})()

        overwrites = build_private_voice_overwrites(guild, [host], spectator_role=interest_role)

        self.assertFalse(overwrites[default_role].view_channel)
        self.assertTrue(overwrites[interest_role].view_channel)
        self.assertFalse(overwrites[interest_role].connect)
        self.assertTrue(overwrites[host].connect)

    def test_admin_voice_overwrites_are_strictly_operator_and_bot(self) -> None:
        default_role = object()
        bot_member = object()
        operator_role = object()
        host = object()
        guild = type("Guild", (), {"default_role": default_role, "me": bot_member})()

        overwrites = build_admin_voice_overwrites(guild, operator_role, [host])

        self.assertFalse(overwrites[default_role].view_channel)
        self.assertTrue(overwrites[operator_role].connect)
        self.assertTrue(overwrites[bot_member].move_members)
        self.assertNotIn(host, overwrites)


if __name__ == "__main__":
    unittest.main()
