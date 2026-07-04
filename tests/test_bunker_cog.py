from __future__ import annotations

import unittest

import discord

from siri_bot.cogs.bunker import (
    GAME_PANEL_ID,
    BunkerPublicGameView,
    BunkerSetupIdleView,
    format_player_name,
    _missing_setup_panel_permissions,
)
from siri_bot.bunker.models import BunkerPlayer


class BunkerCogTests(unittest.TestCase):
    def test_missing_setup_permissions_lists_human_readable_names(self) -> None:
        permissions = discord.Permissions.none()
        permissions.view_channel = True
        permissions.send_messages = False
        permissions.embed_links = False

        self.assertEqual(_missing_setup_panel_permissions(permissions), ["Send Messages", "Embed Links"])

    def test_setup_view_does_not_show_join_button(self) -> None:
        view = BunkerSetupIdleView(cog=object())
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertIn("Построить бункер", labels)
        self.assertIn("Настроить бункер", labels)
        self.assertNotIn("Зайти в бункер", labels)

    def test_public_game_view_only_shows_panel_button(self) -> None:
        view = BunkerPublicGameView(cog=object())
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].label, "Панель")
        self.assertEqual(buttons[0].custom_id, GAME_PANEL_ID)

    def test_fake_player_name_has_no_discord_mention(self) -> None:
        player = BunkerPlayer(
            game_id=1,
            user_id=-1001,
            display_name="Тестовый выживший 1",
            is_host=False,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=None,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
            is_fake=True,
        )

        self.assertEqual(format_player_name(player), "Тестовый выживший 1")


if __name__ == "__main__":
    unittest.main()
