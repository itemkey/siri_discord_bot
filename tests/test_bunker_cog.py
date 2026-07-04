from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

import discord

from siri_bot.cogs.bunker import (
    GAME_PANEL_ID,
    Bunker,
    BunkerPublicGameView,
    BunkerSettingsView,
    BunkerSetupIdleView,
    BunkerSetupNavView,
    format_player_name,
    _missing_setup_panel_permissions,
)
from siri_bot.bunker.models import BunkerPlayer, BunkerSettings, RoomSetup


class FakeGuild:
    id = 100


class FakeUser:
    id = 200


class FakeChannel:
    id = 300


class FakeResponse:
    def __init__(self) -> None:
        self.defer = AsyncMock()
        self.send_message = AsyncMock()
        self.edit_message = AsyncMock()


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edit = AsyncMock()


class FakeFollowup:
    def __init__(self, message: FakeMessage | None = None) -> None:
        self.message = message or FakeMessage(901)
        self.send = AsyncMock(return_value=self.message)


class FakeInteraction:
    def __init__(
        self,
        *,
        message: FakeMessage | None = None,
        original_message: FakeMessage | None = None,
        followup_message: FakeMessage | None = None,
    ) -> None:
        self.guild = FakeGuild()
        self.user = FakeUser()
        self.channel = FakeChannel()
        self.response = FakeResponse()
        self.message = message or FakeMessage(500)
        self.original_message = original_message or FakeMessage(900)
        self.original_response = AsyncMock(return_value=self.original_message)
        self.followup = FakeFollowup(followup_message)


class FakeBunkerRepository:
    def __init__(self, setup: RoomSetup) -> None:
        self.setup = setup
        self.get_setup_by_message = AsyncMock(side_effect=self._get_setup_by_message)
        self.get_setup_by_channel = AsyncMock(return_value=setup)
        self.get_draft = AsyncMock(return_value=BunkerSettings())
        self.save_draft = AsyncMock()

    async def _get_setup_by_message(self, message_id: int) -> RoomSetup | None:
        return self.setup if message_id == self.setup.setup_message_id else None


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

    def test_setup_private_panel_edits_saved_message_for_next_screen(self) -> None:
        setup = RoomSetup(
            id=10,
            guild_id=100,
            setup_channel_id=300,
            category_id=None,
            setup_message_id=500,
            room_name="build-a-bunker",
            active_game_id=None,
        )
        cog = Bunker.__new__(Bunker)
        cog.repository = FakeBunkerRepository(setup)
        cog._setup_private_panels = {}

        first = FakeInteraction(message=FakeMessage(500), original_message=FakeMessage(900))
        asyncio.run(cog.open_setup_panel(first, screen="settings"))

        first.response.send_message.assert_awaited_once()
        first.original_response.assert_awaited_once()
        saved = cog._setup_private_panels[(10, 300, 200)]

        second = FakeInteraction(message=FakeMessage(500))
        asyncio.run(cog.open_setup_panel(second, screen="rules"))

        second.response.send_message.assert_not_called()
        second.response.defer.assert_awaited_once()
        saved.edit.assert_awaited_once()
        kwargs = saved.edit.await_args.kwargs
        self.assertEqual(kwargs["embed"].title, "Как играть в Бункер")
        self.assertIsInstance(kwargs["view"], BunkerSetupNavView)

    def test_setup_private_panel_edits_current_ephemeral_message_in_place(self) -> None:
        setup = RoomSetup(
            id=10,
            guild_id=100,
            setup_channel_id=300,
            category_id=None,
            setup_message_id=500,
            room_name="build-a-bunker",
            active_game_id=None,
        )
        saved = FakeMessage(900)
        cog = Bunker.__new__(Bunker)
        cog.repository = FakeBunkerRepository(setup)
        cog._setup_private_panels = {(10, 300, 200): saved}

        interaction = FakeInteraction(message=saved)
        asyncio.run(cog.open_setup_panel(interaction, screen="packs"))

        interaction.response.edit_message.assert_awaited_once()
        interaction.response.defer.assert_not_called()
        saved.edit.assert_not_called()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(kwargs["embed"].title, "Встроенный контент-пак")
        self.assertIsInstance(kwargs["view"], BunkerSetupNavView)

    def test_settings_view_has_compact_navigation_controls(self) -> None:
        view = BunkerSettingsView(cog=object(), setup_id=1, user_id=2, settings=BunkerSettings())
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertIn("Как играть", labels)
        self.assertIn("Паки/контент", labels)

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
