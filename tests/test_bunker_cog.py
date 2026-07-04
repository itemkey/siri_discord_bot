from __future__ import annotations

import unittest

import discord

from siri_bot.cogs.bunker import _missing_setup_panel_permissions


class BunkerCogTests(unittest.TestCase):
    def test_missing_setup_permissions_lists_human_readable_names(self) -> None:
        permissions = discord.Permissions.none()
        permissions.view_channel = True
        permissions.send_messages = False
        permissions.embed_links = False

        self.assertEqual(_missing_setup_panel_permissions(permissions), ["Send Messages", "Embed Links"])


if __name__ == "__main__":
    unittest.main()

