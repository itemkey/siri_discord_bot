from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

import discord

from siri_bot.cogs.bunker import (
    GAME_PANEL_ID,
    PUBLIC_SECTION_REFRESH_ID,
    PUBLIC_SECTION_TOGGLE_ID,
    Bunker,
    BunkerPrivatePlayerPanelView,
    BunkerPublicAbilityView,
    BunkerPublicGameView,
    BunkerPublicRevealView,
    BunkerPublicSectionView,
    BunkerSettingsView,
    BunkerSetupIdleView,
    BunkerSetupNavView,
    format_player_name,
    _missing_setup_panel_permissions,
    _players_table_embed,
    _setup_embed,
)
from siri_bot.bunker.models import BunkerPlayer, BunkerSettings, RoomKind, RoomSetup
from siri_bot.bunker.models import BunkerGame, GameState
from siri_bot.bunker.engine import generate_card


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
        self.edit_original_response = AsyncMock()
        self.followup = FakeFollowup(followup_message)


class FakeBunkerRepository:
    def __init__(self, setup: RoomSetup) -> None:
        self.setup = setup
        self.repaired_setup = RoomSetup(
            id=setup.id,
            guild_id=setup.guild_id,
            setup_channel_id=setup.setup_channel_id,
            category_id=setup.category_id,
            setup_message_id=900,
            room_name=setup.room_name,
            active_game_id=setup.active_game_id,
        )
        self.get_setup_by_message = AsyncMock(side_effect=self._get_setup_by_message)
        self.get_setup_by_channel = AsyncMock(return_value=setup)
        self.repair_setup_message_id = AsyncMock(return_value=self.repaired_setup)
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

    def test_public_section_view_uses_persistent_custom_ids(self) -> None:
        view = BunkerPublicSectionView(cog=object(), game_id=55, key="players", collapsed=False)
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertEqual([button.custom_id for button in buttons], [PUBLIC_SECTION_TOGGLE_ID, PUBLIC_SECTION_REFRESH_ID])
        self.assertNotIn("55", buttons[0].custom_id)
        self.assertNotIn("players", buttons[0].custom_id)

    def test_setup_embed_is_factory_not_busy_state(self) -> None:
        embed = _setup_embed("build-a-bunker")

        self.assertIn("Один пользователь может хостить только один активный бункер", embed.description)
        self.assertNotIn("занята", embed.description.lower())

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
        selects = [child for child in view.children if isinstance(child, discord.ui.Select)]

        self.assertIn("Как играть", labels)
        self.assertIn("Контент", labels)
        self.assertNotIn("Тип комнаты", labels)
        room_kind_select = next(select for select in selects if select.placeholder == "Тип комнаты")
        self.assertEqual([option.value for option in room_kind_select.options], [RoomKind.RANKED.value])
        self.assertNotIn("Админ-режим", labels)

    def test_operator_settings_room_kind_select_has_only_ranked_and_admin_game(self) -> None:
        view = BunkerSettingsView(cog=object(), setup_id=1, user_id=2, settings=BunkerSettings(), is_operator=True)
        selects = [child for child in view.children if isinstance(child, discord.ui.Select)]

        room_kind_select = next(select for select in selects if select.placeholder == "Тип комнаты")

        self.assertEqual([option.value for option in room_kind_select.options], [RoomKind.RANKED.value, RoomKind.ADMIN_TEST.value])
        self.assertNotIn("casual", [option.value for option in room_kind_select.options])

    def test_setup_lookup_repairs_deleted_setup_message_binding(self) -> None:
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
        interaction = FakeInteraction(message=FakeMessage(900))

        repaired = asyncio.run(cog._setup_from_interaction_message(interaction))

        self.assertEqual(repaired.setup_message_id, 900)
        cog.repository.repair_setup_message_id.assert_awaited_once_with(10, 900)

    def test_stale_active_game_is_finished_when_channels_are_missing(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=200,
            state=GameState.LOBBY,
            settings=BunkerSettings(),
            round_number=0,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
        )
        cog = Bunker.__new__(Bunker)
        cog.repository = type("Repo", (), {"finish_game": AsyncMock()})()
        cog._fetch_text_channel = AsyncMock(return_value=None)
        cog._fetch_voice_channel = AsyncMock(return_value=None)

        live = asyncio.run(cog._ensure_game_discord_state(game))

        self.assertIsNone(live)
        cog.repository.finish_game.assert_awaited_once_with(55)

    def test_host_panel_can_close_own_bunker(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=200,
            state=GameState.LOBBY,
            settings=BunkerSettings(),
            round_number=0,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
        )
        host = BunkerPlayer(
            game_id=55,
            user_id=200,
            display_name="Host",
            is_host=True,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=None,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )

        view = BunkerPrivatePlayerPanelView(object(), game, host, is_operator=False, can_close=True)
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertIn("Закрыть бункер", labels)

    def test_lobby_panel_hides_active_game_actions(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=200,
            state=GameState.LOBBY,
            settings=BunkerSettings(),
            round_number=0,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
        )
        player = BunkerPlayer(
            game_id=55,
            user_id=201,
            display_name="Player",
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
        )

        view = BunkerPrivatePlayerPanelView(object(), game, player, is_operator=False, can_close=False)
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertIn("Готов", labels)
        self.assertNotIn("Моя карточка", labels)
        self.assertNotIn("Раскрыть стату", labels)
        self.assertNotIn("Голосовать", labels)

    def test_lobby_settings_panel_returns_editable_view(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=200,
            state=GameState.LOBBY,
            settings=BunkerSettings(),
            round_number=0,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
        )
        host = BunkerPlayer(
            game_id=55,
            user_id=200,
            display_name="Host",
            is_host=True,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=None,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )
        cog = Bunker.__new__(Bunker)
        cog.repository = type("Repo", (), {"list_players": AsyncMock(return_value=[host])})()
        cog._is_bunker_operator = AsyncMock(return_value=False)
        interaction = FakeInteraction()

        _, view = asyncio.run(cog._game_panel_payload(interaction, game, screen="settings"))

        self.assertIsInstance(view, BunkerSettingsView)
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]
        selects = [child for child in view.children if isinstance(child, discord.ui.Select)]
        self.assertNotIn("Тип комнаты", labels)
        self.assertTrue(any(select.placeholder == "Тип комнаты" for select in selects))

    def test_ranked_active_panel_hides_debug_operator_controls(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=201,
            state=GameState.REVEAL_PHASE,
            settings=BunkerSettings(is_ranked=True),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(201,),
        )
        player = BunkerPlayer(
            game_id=55,
            user_id=201,
            display_name="Player",
            is_host=True,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=generate_card(),
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )

        view = BunkerPrivatePlayerPanelView(object(), game, player, is_operator=True, can_close=True, players=[player])
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertNotIn("Личная информация", labels)
        self.assertNotIn("Спец. возможности", labels)
        self.assertNotIn("Раскрыть", labels)
        self.assertNotIn("Добавить тест-ботов", labels)
        self.assertNotIn("Очистить тест-ботов", labels)
        self.assertNotIn("Форс-старт", labels)
        self.assertNotIn("Следующая фаза", labels)
        self.assertNotIn("Правила", labels)

    def test_public_reveal_view_has_nine_gray_stat_buttons_with_only_next_enabled(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=201,
            state=GameState.REVEAL_PHASE,
            settings=BunkerSettings(),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(201,),
        )
        player = BunkerPlayer(
            game_id=55,
            user_id=201,
            display_name="Player",
            is_host=True,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=generate_card(),
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )

        view = BunkerPublicRevealView(object(), game, player)
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertEqual(len(buttons), 9)
        self.assertTrue(all(button.style == discord.ButtonStyle.secondary for button in buttons))
        self.assertFalse(buttons[0].disabled)
        self.assertTrue(all(button.disabled for button in buttons[1:]))

    def test_public_ability_view_has_two_gray_buttons(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=201,
            state=GameState.REVEAL_PHASE,
            settings=BunkerSettings(),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
        )
        player = BunkerPlayer(
            game_id=55,
            user_id=201,
            display_name="Player",
            is_host=True,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=generate_card(),
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )

        view = BunkerPublicAbilityView(object(), game, player)
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertEqual(len(buttons), 2)
        self.assertTrue(all(button.style == discord.ButtonStyle.secondary for button in buttons))

    def test_players_table_is_readable_embed_fields_not_one_long_row(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=201,
            state=GameState.REVEAL_PHASE,
            settings=BunkerSettings(),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
        )
        players = [
            BunkerPlayer(
                game_id=55,
                user_id=200 + index,
                display_name=f"Player {index}",
                is_host=index == 1,
                ready_at=None,
                invited_at=None,
                joined_at=None,
                left_at=None,
                is_eliminated=False,
                card=generate_card(),
                revealed_stats=("gender", "profession"),
                used_special_action=False,
                immune_round=None,
            )
            for index in range(1, 9)
        ]

        embed = _players_table_embed(game, players)

        self.assertNotIn("```text", embed.description or "")
        self.assertEqual(len(embed.fields), 8)
        self.assertTrue(all("\n" in field.value for field in embed.fields))

    def test_admin_test_lobby_shows_test_controls_only_to_operator(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=201,
            state=GameState.LOBBY,
            settings=BunkerSettings(room_kind=RoomKind.ADMIN_TEST, is_ranked=False, is_public=False, min_players=1),
            round_number=0,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            is_admin_game=True,
            room_kind=RoomKind.ADMIN_TEST,
        )
        host = BunkerPlayer(
            game_id=55,
            user_id=201,
            display_name="Host",
            is_host=True,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=None,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )

        operator_view = BunkerPrivatePlayerPanelView(object(), game, host, is_operator=True, can_close=True, players=[host])
        regular_view = BunkerPrivatePlayerPanelView(object(), game, host, is_operator=False, can_close=True, players=[host])
        operator_labels = [child.label for child in operator_view.children if isinstance(child, discord.ui.Button)]
        regular_labels = [child.label for child in regular_view.children if isinstance(child, discord.ui.Button)]

        self.assertIn("Добавить тест-ботов", operator_labels)
        self.assertIn("Форс-старт", operator_labels)
        self.assertNotIn("Добавить тест-ботов", regular_labels)
        self.assertNotIn("Форс-старт", regular_labels)

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
