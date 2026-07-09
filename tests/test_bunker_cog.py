from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock

import discord

from siri_bot.cogs.bunker import (
    GAME_FINISH_SPEECH_ID,
    GAME_PANEL_ID,
    PUBLIC_REVEAL_SELF_ID,
    PUBLIC_SECTION_REFRESH_ID,
    PUBLIC_SECTION_TOGGLE_ID,
    Bunker,
    BunkerActionView,
    BunkerPrivatePlayerPanelView,
    BunkerPublicAbilityView,
    BunkerPublicGameView,
    BunkerPublicRevealView,
    BunkerPublicSectionView,
    BunkerRevealView,
    BunkerSettingsView,
    BunkerSetupIdleView,
    BunkerSetupNavView,
    _game_embed,
    _leader_view_for_game,
    format_player_name,
    _abilities_embed,
    _abilities_table_embed,
    _missing_setup_panel_permissions,
    _personal_card_embed,
    _players_table_embed,
    _public_personal_embed,
    _public_specials_embed,
    _setup_embed,
)
from siri_bot.bunker.models import BunkerPlayer, BunkerSettings, RoomKind, RoomSetup
from siri_bot.bunker.models import BunkerGame, GameState
from siri_bot.bunker.engine import generate_card


class FakeGuild:
    id = 100


class FakeUser:
    def __init__(self, user_id: int = 200) -> None:
        self.id = user_id


class FakeChannel:
    id = 300


class FakeResponse:
    def __init__(self) -> None:
        self._done = False

        async def mark_done(*args, **kwargs) -> None:
            self._done = True

        self.defer = AsyncMock(side_effect=mark_done)
        self.send_message = AsyncMock(side_effect=mark_done)
        self.edit_message = AsyncMock(side_effect=mark_done)

    def is_done(self) -> bool:
        return self._done


class FakeMessage:
    def __init__(self, message_id: int, *, embeds: list[discord.Embed] | None = None) -> None:
        self.id = message_id
        self.embeds = embeds or []
        self.edit = AsyncMock()
        self.delete = AsyncMock()


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
        user_id: int = 200,
    ) -> None:
        self.guild = FakeGuild()
        self.user = FakeUser(user_id)
        self.channel = FakeChannel()
        self.response = FakeResponse()
        self.message = message or FakeMessage(500)
        self.original_message = original_message or FakeMessage(900)
        self.original_response = AsyncMock(return_value=self.original_message)
        self.edit_original_response = AsyncMock(return_value=self.original_message)
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


class FakeRevealRepository:
    def __init__(self, game: BunkerGame, players: list[BunkerPlayer]) -> None:
        self.game = game
        self.players = players
        self.get_game = AsyncMock(side_effect=self._get_game)
        self.get_game_by_public_message = AsyncMock(side_effect=self._get_game_by_public_message)
        self.get_player = AsyncMock(side_effect=self._get_player)
        self.list_players = AsyncMock(side_effect=self._list_players)
        self.reveal_stat = AsyncMock(side_effect=self._reveal_stat)
        self.assign_cards = AsyncMock(side_effect=self._assign_cards)
        self.get_enabled_content_pack = AsyncMock(return_value=None)
        self.add_event = AsyncMock()
        self.remove_public_message_id = AsyncMock()
        self.set_reveal_progress = AsyncMock(side_effect=self._set_reveal_progress)
        self.set_speech_index = AsyncMock(side_effect=self._set_speech_index)
        self.set_game_state = AsyncMock(side_effect=self._set_game_state)

    async def _get_game(self, game_id: int) -> BunkerGame | None:
        return self.game if game_id == self.game.id else None

    async def _get_game_by_public_message(self, message_id: int) -> tuple[BunkerGame, str]:
        return self.game, "personal"

    async def _get_player(self, game_id: int, user_id: int) -> BunkerPlayer | None:
        if game_id != self.game.id:
            return None
        return next((player for player in self.players if player.user_id == user_id), None)

    async def _list_players(self, game_id: int) -> list[BunkerPlayer]:
        return list(self.players)

    async def _reveal_stat(self, game_id: int, user_id: int, stat: str) -> None:
        self.players = [
            replace(player, revealed_stats=(*player.revealed_stats, stat))
            if player.user_id == user_id and stat not in player.revealed_stats
            else player
            for player in self.players
        ]

    async def _assign_cards(self, game_id: int, cards: dict[int, object]) -> None:
        self.players = [
            replace(player, card=cards[player.user_id])
            if player.user_id in cards
            else player
            for player in self.players
        ]

    async def _set_reveal_progress(self, game_id: int, *, current_turn_index: int, reveals_done_this_turn: int) -> None:
        self.game = replace(
            self.game,
            current_turn_index=current_turn_index,
            reveals_done_this_turn=reveals_done_this_turn,
        )

    async def _set_speech_index(self, game_id: int, speech_index: int) -> None:
        self.game = replace(self.game, speech_index=speech_index)

    async def _set_game_state(
        self,
        game_id: int,
        state: GameState,
        *,
        round_number: int,
        phase_started_at,
        phase_ends_at,
        paused_at,
    ) -> BunkerGame:
        self.game = replace(
            self.game,
            state=state,
            round_number=round_number,
            phase_started_at=phase_started_at,
            phase_ends_at=phase_ends_at,
            paused_at=paused_at,
        )
        return self.game


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

    def test_public_game_view_can_show_finish_speech_button_for_leader(self) -> None:
        view = BunkerPublicGameView(cog=object(), show_finish_speech=True)
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertEqual([button.custom_id for button in buttons], [GAME_PANEL_ID, GAME_FINISH_SPEECH_ID])

    def test_leader_view_shows_finish_speech_only_during_speech_phase(self) -> None:
        base_game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=700,
            voice_channel_id=800,
            host_id=200,
            state=GameState.SPEECH_PHASE,
            settings=BunkerSettings(),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
        )

        speech_view = _leader_view_for_game(object(), base_game)
        pause_view = _leader_view_for_game(object(), replace(base_game, state=GameState.SPEECH_PAUSE))
        speech_ids = [child.custom_id for child in speech_view.children if isinstance(child, discord.ui.Button)]
        pause_ids = [child.custom_id for child in pause_view.children if isinstance(child, discord.ui.Button)]

        self.assertIn(GAME_FINISH_SPEECH_ID, speech_ids)
        self.assertNotIn(GAME_FINISH_SPEECH_ID, pause_ids)

    def test_public_panel_button_reuses_saved_private_panel_without_duplicate(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=300,
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
        hidden = FakeMessage(700)
        cog = Bunker.__new__(Bunker)
        cog.repository = type(
            "Repo",
            (),
            {
                "get_active_game_by_text_channel": AsyncMock(return_value=game),
                "list_players": AsyncMock(return_value=[host]),
            },
        )()
        cog._is_bunker_operator = AsyncMock(return_value=False)
        cog._is_bunker_admin_or_operator = AsyncMock(return_value=False)
        cog._game_private_panels = {(55, 300, 200): hidden}
        interaction = FakeInteraction(message=FakeMessage(500), original_message=FakeMessage(900))

        asyncio.run(cog.open_game_panel(interaction))

        hidden.edit.assert_awaited_once()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        interaction.response.edit_message.assert_not_called()
        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()
        self.assertIs(cog._game_private_panels[(55, 300, 200)], hidden)

    def test_public_section_view_uses_persistent_custom_ids(self) -> None:
        view = BunkerPublicSectionView(cog=object(), game_id=55, key="players", collapsed=False)
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertEqual([button.custom_id for button in buttons], [PUBLIC_SECTION_TOGGLE_ID, PUBLIC_SECTION_REFRESH_ID])
        self.assertNotIn("55", buttons[0].custom_id)
        self.assertNotIn("players", buttons[0].custom_id)

    def test_public_board_excludes_private_sections_and_removes_old_messages(self) -> None:
        class FakePublicChannel:
            def __init__(self) -> None:
                self.messages = {
                    901: FakeMessage(901),
                    902: FakeMessage(902),
                }
                self.sent_titles: list[str] = []
                self.next_id = 1000
                self.fetch_message = AsyncMock(side_effect=self._fetch_message)
                self.send = AsyncMock(side_effect=self._send)

            async def _fetch_message(self, message_id: int) -> FakeMessage:
                return self.messages[message_id]

            async def _send(self, *args, **kwargs) -> FakeMessage:
                embed = kwargs["embed"]
                self.sent_titles.append(embed.title)
                self.next_id += 1
                return FakeMessage(self.next_id, embeds=[embed])

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
            public_message_ids={"personal": 901, "specials": 902},
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
        repository = type(
            "Repo",
            (),
            {
                "set_public_message_id": AsyncMock(),
                "remove_public_message_id": AsyncMock(),
            },
        )()
        channel = FakePublicChannel()
        cog = Bunker.__new__(Bunker)
        cog.repository = repository

        asyncio.run(cog._sync_public_game_messages(game, channel, [player]))

        channel.messages[901].delete.assert_awaited_once()
        channel.messages[902].delete.assert_awaited_once()
        self.assertEqual(
            [call.args for call in repository.remove_public_message_id.await_args_list],
            [(55, "personal"), (55, "specials")],
        )
        self.assertEqual(
            channel.sent_titles,
            ["Катаклизм", "Бункер", "Желающие попасть в бункер", "Таблица спец. возможностей"],
        )
        self.assertNotIn("personal", game.public_message_ids)
        self.assertNotIn("specials", game.public_message_ids)

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

    def test_public_setup_button_reuses_saved_private_panel_without_duplicate(self) -> None:
        setup = RoomSetup(
            id=10,
            guild_id=100,
            setup_channel_id=300,
            category_id=None,
            setup_message_id=500,
            room_name="build-a-bunker",
            active_game_id=None,
        )
        hidden = FakeMessage(700)
        cog = Bunker.__new__(Bunker)
        cog.repository = FakeBunkerRepository(setup)
        cog._setup_private_panels = {(10, 300, 200): hidden}
        public_message = FakeMessage(500, embeds=[_setup_embed("build-a-bunker")])
        interaction = FakeInteraction(message=public_message, original_message=FakeMessage(900))

        asyncio.run(cog.open_setup_panel(interaction, screen="settings"))

        hidden.edit.assert_awaited_once()
        interaction.response.edit_message.assert_not_called()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()
        self.assertIs(cog._setup_private_panels[(10, 300, 200)], hidden)

    def test_public_setup_button_replaces_expired_private_panel_without_editing_public_message(self) -> None:
        setup = RoomSetup(
            id=10,
            guild_id=100,
            setup_channel_id=300,
            category_id=None,
            setup_message_id=500,
            room_name="build-a-bunker",
            active_game_id=None,
        )
        expired = FakeMessage(700)
        replacement = FakeMessage(901)
        response = type("Response", (), {"status": 404, "reason": "not found"})()
        expired.edit.side_effect = discord.HTTPException(response, "expired")
        cog = Bunker.__new__(Bunker)
        cog.repository = FakeBunkerRepository(setup)
        cog._setup_private_panels = {(10, 300, 200): expired}
        public_message = FakeMessage(500, embeds=[_setup_embed("build-a-bunker")])
        interaction = FakeInteraction(message=public_message, original_message=FakeMessage(900), followup_message=replacement)

        asyncio.run(cog.open_setup_panel(interaction, screen="settings"))

        expired.edit.assert_awaited_once()
        interaction.response.edit_message.assert_not_called()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertTrue(kwargs["wait"])
        self.assertIs(cog._setup_private_panels[(10, 300, 200)], replacement)

    def test_private_panel_uses_deferred_original_response_without_new_message(self) -> None:
        cog = Bunker.__new__(Bunker)
        registry = {}
        interaction = FakeInteraction()
        asyncio.run(interaction.response.defer(ephemeral=True))

        asyncio.run(
            cog._send_or_edit_private_message(
                interaction,
                registry,
                (1, 2, 3),
                embed=_setup_embed("build-a-bunker"),
                view=None,
            )
        )

        interaction.response.send_message.assert_not_called()
        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_not_called()
        self.assertIs(registry[(1, 2, 3)], interaction.original_message)

    def test_setup_status_after_ack_edits_saved_private_panel_without_duplicate(self) -> None:
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

        interaction = FakeInteraction(message=FakeMessage(500))
        asyncio.run(interaction.response.defer(ephemeral=True))
        asyncio.run(cog.send_or_edit_setup_status(interaction, setup, "Ð“Ð¾Ñ‚Ð¾Ð²Ð¾."))

        interaction.response.send_message.assert_not_called()
        interaction.edit_original_response.assert_not_called()
        interaction.followup.send.assert_not_called()
        saved.edit.assert_awaited_once()

    def test_setup_build_statuses_replace_current_response_without_duplicate(self) -> None:
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
        interaction = FakeInteraction(message=FakeMessage(500), original_message=FakeMessage(900))

        asyncio.run(cog.send_or_edit_setup_status(interaction, setup, "Строю.", prefer_current_response=True))
        asyncio.run(cog.send_or_edit_setup_status(interaction, setup, "Готово.", prefer_current_response=True))

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        interaction.response.send_message.assert_not_called()
        interaction.followup.send.assert_not_called()
        self.assertEqual(interaction.edit_original_response.await_count + interaction.original_message.edit.await_count, 2)
        self.assertIs(cog._setup_private_panels[(10, 300, 200)], interaction.original_message)

    def test_setup_build_status_from_public_panel_does_not_edit_saved_or_public_panel(self) -> None:
        setup = RoomSetup(
            id=10,
            guild_id=100,
            setup_channel_id=300,
            category_id=None,
            setup_message_id=500,
            room_name="build-a-bunker",
            active_game_id=None,
        )
        hidden = FakeMessage(700)
        cog = Bunker.__new__(Bunker)
        cog.repository = FakeBunkerRepository(setup)
        cog._setup_private_panels = {(10, 300, 200): hidden}
        public_message = FakeMessage(500, embeds=[_setup_embed("build-a-bunker")])
        interaction = FakeInteraction(message=public_message, original_message=FakeMessage(900))

        asyncio.run(
            cog.send_or_edit_setup_status(
                interaction,
                setup,
                "Building.",
                prefer_current_response=True,
                force_current_response=True,
            )
        )

        hidden.edit.assert_not_called()
        interaction.response.edit_message.assert_not_called()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        interaction.edit_original_response.assert_awaited_once()
        self.assertIs(cog._setup_private_panels[(10, 300, 200)], interaction.original_message)

    def test_setup_build_status_falls_back_to_current_response_when_saved_message_expired(self) -> None:
        setup = RoomSetup(
            id=10,
            guild_id=100,
            setup_channel_id=300,
            category_id=None,
            setup_message_id=500,
            room_name="build-a-bunker",
            active_game_id=None,
        )
        expired = FakeMessage(800)
        response = type("Response", (), {"status": 404, "reason": "not found"})()
        expired.edit.side_effect = discord.HTTPException(response, "expired")
        cog = Bunker.__new__(Bunker)
        cog.repository = FakeBunkerRepository(setup)
        cog._setup_private_panels = {(10, 300, 200): expired}
        interaction = FakeInteraction(message=FakeMessage(500), original_message=FakeMessage(900))

        asyncio.run(cog.send_or_edit_setup_status(interaction, setup, "Готово.", prefer_current_response=True))

        interaction.response.defer.assert_awaited_once()
        expired.edit.assert_awaited_once()
        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_not_called()
        self.assertIs(cog._setup_private_panels[(10, 300, 200)], interaction.original_message)

    def test_settings_view_uses_select_only_main_page(self) -> None:
        view = BunkerSettingsView(cog=object(), setup_id=1, user_id=2, settings=BunkerSettings())
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]
        selects = [child for child in view.children if isinstance(child, discord.ui.Select)]

        self.assertEqual(labels, ["Правила/таймеры", "Контент", "Как играть", "Назад к панели"])
        self.assertEqual(len(selects), 4)
        self.assertNotIn("Тип комнаты", labels)
        room_kind_select = next(select for select in selects if str(select.placeholder).startswith("Тип комнаты"))
        self.assertEqual([option.value for option in room_kind_select.options], [RoomKind.RANKED.value])
        visibility_select = next(select for select in selects if str(select.placeholder).startswith("Доступ"))
        self.assertEqual([option.value for option in visibility_select.options], ["public", "private"])
        slots_select = next(select for select in selects if str(select.placeholder).startswith("Игроки"))
        self.assertIn("8 игроков", [option.label for option in slots_select.options])
        self.assertFalse(any(str(option.label).isdigit() for option in slots_select.options))

    def test_settings_rules_view_uses_descriptive_select_labels(self) -> None:
        view = BunkerSettingsView(cog=object(), setup_id=1, user_id=2, settings=BunkerSettings(), screen="settings_rules")
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]
        selects = [child for child in view.children if isinstance(child, discord.ui.Select)]

        self.assertEqual(labels, ["Основные", "Контент", "Как играть", "Назад к панели"])
        self.assertEqual(len(selects), 4)
        reveal_select = next(select for select in selects if str(select.placeholder).startswith("Reveal-ход"))
        self.assertEqual([option.value for option in reveal_select.options], ["1", "2", "3"])
        self.assertIn("1 характеристика за ход", [option.label for option in reveal_select.options])
        self.assertFalse(any(str(option.label).isdigit() for option in reveal_select.options))
        timer_select = next(select for select in selects if str(select.placeholder).startswith("Таймер события"))
        self.assertIn("180 сек.", [option.label for option in timer_select.options])
        vote_select = next(select for select in selects if str(select.placeholder).startswith("Пропущенный голос"))
        self.assertEqual([option.label for option in vote_select.options], ["Воздержаться", "Случайная цель"])
        rounds_select = next(select for select in selects if str(select.placeholder).startswith("Раунды"))
        self.assertIn("4 раунда", [option.label for option in rounds_select.options])
        self.assertFalse(any(str(option.label).isdigit() for option in rounds_select.options))

    def test_operator_settings_room_kind_select_has_only_ranked_and_admin_game(self) -> None:
        view = BunkerSettingsView(cog=object(), setup_id=1, user_id=2, settings=BunkerSettings(), is_operator=True)
        selects = [child for child in view.children if isinstance(child, discord.ui.Select)]

        room_kind_select = next(select for select in selects if str(select.placeholder).startswith("Тип комнаты"))

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
        interaction = FakeInteraction(message=FakeMessage(900, embeds=[_setup_embed("build-a-bunker")]))

        repaired = asyncio.run(cog._setup_from_interaction_message(interaction))

        self.assertEqual(repaired.setup_message_id, 900)
        cog.repository.repair_setup_message_id.assert_awaited_once_with(10, 900)

    def test_setup_lookup_does_not_repair_binding_from_private_panel_message(self) -> None:
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
        private_message = FakeMessage(900, embeds=[discord.Embed(title="Private status")])
        interaction = FakeInteraction(message=private_message)

        found = asyncio.run(cog._setup_from_interaction_message(interaction))

        self.assertEqual(found.setup_message_id, 500)
        cog.repository.repair_setup_message_id.assert_not_awaited()

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

    def test_speech_turn_advances_to_handoff_pause_before_next_speaker(self) -> None:
        now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
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
            state=GameState.SPEECH_PHASE,
            settings=BunkerSettings(speech_seconds=60, explain_for_newbies=False),
            round_number=1,
            phase_started_at=now - timedelta(seconds=60),
            phase_ends_at=now,
            paused_at=None,
            board_message_id=None,
            profile=None,
            speech_index=0,
            turn_order=(200, 201),
        )
        players = [
            BunkerPlayer(55, 200, "Host", True, None, None, None, None, False, generate_card(), (), False, None),
            BunkerPlayer(55, 201, "Player", False, None, None, None, None, False, generate_card(), (), False, None),
        ]
        repository = FakeRevealRepository(game, players)
        cog = Bunker.__new__(Bunker)
        cog.repository = repository
        cog.refresh_game_message = AsyncMock()

        asyncio.run(cog._advance_speech_turn(game, now=now))

        self.assertEqual(repository.game.state, GameState.SPEECH_PAUSE)
        self.assertEqual(repository.game.speech_index, 1)
        self.assertEqual(repository.game.phase_ends_at, now + timedelta(seconds=15))
        cog.refresh_game_message.assert_awaited_once_with(55)

    def test_private_panel_does_not_show_finish_speech_button(self) -> None:
        now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
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
            state=GameState.SPEECH_PHASE,
            settings=BunkerSettings(speech_seconds=60, explain_for_newbies=False),
            round_number=1,
            phase_started_at=now - timedelta(seconds=5),
            phase_ends_at=now + timedelta(seconds=55),
            paused_at=None,
            board_message_id=None,
            profile=None,
            speech_index=0,
            turn_order=(200,),
        )
        player = BunkerPlayer(55, 200, "Host", True, None, None, None, None, False, generate_card(), (), False, None)

        view = BunkerPrivatePlayerPanelView(object(), game, player, is_operator=False, can_close=True, players=[player])
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertNotIn("Закончить речь", labels)

    def test_public_finish_speech_button_advances_without_editing_leader_message(self) -> None:
        now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=300,
            voice_channel_id=800,
            host_id=200,
            state=GameState.SPEECH_PHASE,
            settings=BunkerSettings(speech_seconds=60, explain_for_newbies=False),
            round_number=1,
            phase_started_at=now - timedelta(seconds=5),
            phase_ends_at=now + timedelta(seconds=55),
            paused_at=None,
            board_message_id=None,
            profile=None,
            speech_index=0,
            turn_order=(200,),
        )
        player = BunkerPlayer(55, 200, "Host", True, None, None, None, None, False, generate_card(), (), False, None)
        repository = FakeRevealRepository(game, [player])
        repository.get_active_game_by_text_channel = AsyncMock(return_value=game)
        cog = Bunker.__new__(Bunker)
        cog.repository = repository
        cog.refresh_game_message = AsyncMock()
        interaction = FakeInteraction(message=FakeMessage(900), user_id=200)

        asyncio.run(cog.public_finish_speech(interaction))

        repository.add_event.assert_awaited_once()
        interaction.response.send_message.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()

    def test_speech_pause_starts_next_speaker_with_fresh_timer(self) -> None:
        now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
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
            state=GameState.SPEECH_PAUSE,
            settings=BunkerSettings(speech_seconds=60),
            round_number=1,
            phase_started_at=now - timedelta(seconds=15),
            phase_ends_at=now,
            paused_at=None,
            board_message_id=None,
            profile=None,
            speech_index=1,
            turn_order=(200, 201),
        )
        repository = FakeRevealRepository(game, [])
        cog = Bunker.__new__(Bunker)
        cog.repository = repository
        cog.refresh_game_message = AsyncMock()

        asyncio.run(cog.advance_phase(game, now=now))

        self.assertEqual(repository.game.state, GameState.SPEECH_PHASE)
        self.assertEqual(repository.game.phase_ends_at, now + timedelta(seconds=60))
        cog.refresh_game_message.assert_awaited_once_with(55)

    def test_game_embed_never_formats_expired_timer_as_relative_past(self) -> None:
        now = datetime.now(UTC)
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
            state=GameState.SPEECH_PHASE,
            settings=BunkerSettings(),
            round_number=1,
            phase_started_at=now - timedelta(seconds=72),
            phase_ends_at=now - timedelta(seconds=12),
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(200,),
        )
        player = BunkerPlayer(55, 200, "Host", True, None, None, None, None, False, generate_card(), (), False, None)

        embed = _game_embed(game, [player])

        self.assertIn("...", embed.description)
        self.assertNotIn("<t:", embed.description)

    def test_overdue_phase_advances_before_board_render(self) -> None:
        now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
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
            state=GameState.SPEECH_PHASE,
            settings=BunkerSettings(),
            round_number=1,
            phase_started_at=now - timedelta(seconds=72),
            phase_ends_at=now - timedelta(seconds=12),
            paused_at=None,
            board_message_id=None,
            profile=None,
        )
        fresh = replace(game, state=GameState.SPEECH_PAUSE, phase_ends_at=now + timedelta(seconds=15))
        cog = Bunker.__new__(Bunker)
        cog.repository = type("Repo", (), {"get_game": AsyncMock(return_value=fresh)})()
        cog.advance_phase = AsyncMock()

        result = asyncio.run(cog._advance_overdue_phase_if_needed(game, now=now))

        cog.advance_phase.assert_awaited_once_with(game, now=now)
        self.assertIs(result, fresh)

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
        self.assertTrue(any(str(select.placeholder).startswith("Тип комнаты") for select in selects))
        self.assertIn("Назад к панели", labels)

    def test_active_panel_shows_private_card_buttons_but_hides_debug_operator_controls(self) -> None:
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

        self.assertIn("Личная информация", labels)
        self.assertIn("Спец возможности", labels)
        self.assertNotIn("Раскрыть", labels)
        self.assertNotIn("Добавить тест-ботов", labels)
        self.assertNotIn("Очистить тест-ботов", labels)
        self.assertNotIn("Форс-старт", labels)
        self.assertNotIn("Следующая фаза", labels)
        self.assertNotIn("Правила", labels)

    def test_public_reveal_view_uses_red_hidden_and_gray_revealed_buttons(self) -> None:
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
            round_number=2,
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
            revealed_stats=("health",),
            used_special_action=False,
            immune_round=None,
        )

        view = BunkerPublicRevealView(object(), game, player)
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
        hidden_buttons = [button for button in buttons if button.style == discord.ButtonStyle.danger]
        revealed_buttons = [
            button
            for button in buttons
            if button.style == discord.ButtonStyle.secondary and button.disabled
        ]

        self.assertEqual(len(buttons), 11)
        self.assertEqual(len(hidden_buttons), 9)
        self.assertTrue(all(not button.disabled for button in hidden_buttons))
        self.assertEqual(len(revealed_buttons), 1)
        self.assertIn(PUBLIC_REVEAL_SELF_ID, [button.custom_id for button in buttons])
        self.assertIn("Показать мои данные лично", [button.label for button in buttons])
        self.assertFalse(any(button.label == "Моя карточка" for button in buttons))

    def test_public_personal_embed_lists_round_stats_together(self) -> None:
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
            round_number=2,
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

        embed = _public_personal_embed(game, [player])
        self.assertIn("Можно открыть", embed.description)
        self.assertIn("Выбери любую скрытую характеристику", embed.description)
        self.assertNotIn("В этом раунде открываются", embed.description)

    def test_public_reveal_view_disables_stats_after_turn_limit(self) -> None:
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
            settings=BunkerSettings(reveal_stats_per_turn=1),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(201,),
            reveals_done_this_turn=1,
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
            revealed_stats=("profession",),
            used_special_action=False,
            immune_round=None,
        )

        view = BunkerPublicRevealView(object(), game, player)
        stat_buttons = [
            child
            for child in view.children
            if isinstance(child, discord.ui.Button) and child.style == discord.ButtonStyle.secondary
        ]

        self.assertEqual(len(stat_buttons), 10)
        self.assertTrue(all(button.disabled for button in stat_buttons))

    def test_private_reveal_view_uses_red_hidden_and_gray_revealed_buttons(self) -> None:
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
            revealed_stats=("health",),
            used_special_action=False,
            immune_round=None,
        )

        view = BunkerRevealView(object(), game, player.user_id, player)
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
        hidden_buttons = [button for button in buttons if button.style == discord.ButtonStyle.danger]
        revealed_buttons = [
            button
            for button in buttons
            if button.style == discord.ButtonStyle.secondary and button.disabled
        ]

        self.assertEqual(len(buttons), 11)
        self.assertEqual(len(hidden_buttons), 9)
        self.assertTrue(all(not button.disabled for button in hidden_buttons))
        self.assertEqual(len(revealed_buttons), 1)
        self.assertTrue(any(button.label == "Назад к панели" for button in buttons))

    def test_private_personal_card_shows_owner_values_with_visibility_markers(self) -> None:
        card = generate_card()
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
            card=card,
            revealed_stats=("health",),
            used_special_action=False,
            immune_round=None,
        )

        embed = _personal_card_embed(player)

        self.assertIn(card.profession, embed.description)
        self.assertIn(card.health, embed.description)
        self.assertIn("скрыта", embed.description)
        self.assertIn("раскрыто", embed.description)
        self.assertNotIn("?", embed.description)

    def test_private_abilities_show_owner_values_and_red_hidden_buttons(self) -> None:
        card = generate_card()
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
            card=card,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )

        embed = _abilities_embed(player)
        view = BunkerActionView(object(), 55, player)
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]

        self.assertIn(card.special_abilities[0].name, "\n".join(field.name for field in embed.fields))
        self.assertIn(card.special_abilities[1].name, "\n".join(field.name for field in embed.fields))
        self.assertIn("скрыта", "\n".join(str(field.value) for field in embed.fields))
        self.assertTrue(all(button.style == discord.ButtonStyle.danger for button in buttons[:2]))
        self.assertTrue(all(button.label.startswith("Раскрыть:") for button in buttons[:2]))

    def test_private_special_button_reveals_hidden_ability_first(self) -> None:
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
        repository = FakeRevealRepository(game, [player])
        cog = Bunker.__new__(Bunker)
        cog.repository = repository
        cog.refresh_game_message = AsyncMock()
        cog.update_current_game_panel = AsyncMock()
        interaction = FakeInteraction(message=FakeMessage(900), user_id=201)

        asyncio.run(cog.use_special_action(interaction, game.id, player.user_id, 0))

        repository.assign_cards.assert_awaited_once()
        updated_card = repository.assign_cards.await_args.args[1][player.user_id]
        self.assertTrue(updated_card.special_abilities[0].revealed)
        repository.add_event.assert_awaited_once()
        cog.refresh_game_message.assert_awaited_once_with(game.id)
        cog.update_current_game_panel.assert_awaited_once()
        self.assertEqual(cog.update_current_game_panel.await_args.kwargs["screen"], "action")

    def test_public_personal_embed_keeps_admin_test_fake_values_private(self) -> None:
        card = generate_card()
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
            settings=BunkerSettings(room_kind=RoomKind.ADMIN_TEST, is_ranked=False, min_players=1),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(201,),
            is_admin_game=True,
            room_kind=RoomKind.ADMIN_TEST,
        )
        player = BunkerPlayer(
            game_id=55,
            user_id=201,
            display_name="Test survivor",
            is_host=True,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=card,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
            is_fake=True,
        )

        embed = _public_personal_embed(game, [player])
        values = "\n".join(str(field.value) for field in embed.fields)

        self.assertNotIn(card.profession, values)
        self.assertNotIn(card.health, values)
        self.assertNotIn("Админ-игра", embed.description)
        self.assertIn("скрыто для остальных", values)
        self.assertIn("скрыто", values)

    def test_public_personal_embed_keeps_ranked_hidden_values_private(self) -> None:
        card = generate_card()
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
            card=card,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )

        embed = _public_personal_embed(game, [player])
        values = "\n".join(str(field.value) for field in embed.fields)

        self.assertNotIn(card.profession, values)
        self.assertIn("скрыто для остальных", values)
        self.assertIn("Показать мои данные лично", embed.description)

    def test_public_reveal_does_not_allow_operator_to_reveal_for_fake_player(self) -> None:
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
            state=GameState.REVEAL_PHASE,
            settings=BunkerSettings(room_kind=RoomKind.ADMIN_TEST, is_ranked=False, min_players=1),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(-1001,),
            is_admin_game=True,
            room_kind=RoomKind.ADMIN_TEST,
        )
        fake = BunkerPlayer(
            game_id=55,
            user_id=-1001,
            display_name="Test survivor",
            is_host=False,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=generate_card(),
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
            is_fake=True,
        )
        repository = FakeRevealRepository(game, [fake])
        cog = Bunker.__new__(Bunker)
        cog.repository = repository
        interaction = FakeInteraction(message=FakeMessage(900))

        asyncio.run(cog.public_reveal_selected_stat(interaction, "profession"))

        repository.reveal_stat.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("Личная информация", message)

    def test_stale_public_reveal_self_button_points_to_private_panel(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=300,
            voice_channel_id=800,
            host_id=200,
            state=GameState.REVEAL_PHASE,
            settings=BunkerSettings(),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(200,),
        )
        player = BunkerPlayer(
            game_id=55,
            user_id=200,
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
        repository = FakeRevealRepository(game, [player])
        cog = Bunker.__new__(Bunker)
        cog.repository = repository
        cog._game_private_panels = {}
        interaction = FakeInteraction(message=FakeMessage(900), original_message=FakeMessage(901), user_id=200)

        asyncio.run(cog.public_show_current_reveal_card(interaction))

        repository.reveal_stat.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()
        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("Личная информация", message)

    def test_stale_public_reveal_self_button_uses_same_hint_for_other_players(self) -> None:
        game = BunkerGame(
            id=55,
            guild_id=100,
            setup_id=10,
            setup_channel_id=300,
            setup_message_id=500,
            category_id=None,
            game_text_channel_id=300,
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
        repository = FakeRevealRepository(game, [player])
        cog = Bunker.__new__(Bunker)
        cog.repository = repository
        cog._game_private_panels = {}
        interaction = FakeInteraction(message=FakeMessage(900), user_id=200)

        asyncio.run(cog.public_show_current_reveal_card(interaction))

        interaction.response.send_message.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()
        repository.reveal_stat.assert_not_awaited()
        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("личную панель", message)

    def test_auto_reveal_fake_player_opens_random_stat_and_advances_to_real_player(self) -> None:
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
            state=GameState.REVEAL_PHASE,
            settings=BunkerSettings(room_kind=RoomKind.ADMIN_TEST, is_ranked=False, min_players=1),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(-1001, 200),
            is_admin_game=True,
            room_kind=RoomKind.ADMIN_TEST,
        )
        fake = BunkerPlayer(
            game_id=55,
            user_id=-1001,
            display_name="Test survivor",
            is_host=False,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=generate_card(),
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
            is_fake=True,
        )
        real = BunkerPlayer(
            game_id=55,
            user_id=200,
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
        repository = FakeRevealRepository(game, [fake, real])
        cog = Bunker.__new__(Bunker)
        cog.repository = repository

        asyncio.run(cog._auto_reveal_fake_turns(game.id))

        repository.reveal_stat.assert_awaited_once_with(55, -1001, ANY)
        repository.add_event.assert_awaited_once()
        self.assertEqual(repository.game.current_turn_index, 1)
        self.assertEqual(repository.game.reveals_done_this_turn, 0)
        self.assertEqual(len(repository.players[0].revealed_stats), 1)

    def test_auto_reveal_fake_player_respects_reveal_stats_per_turn(self) -> None:
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
            state=GameState.REVEAL_PHASE,
            settings=BunkerSettings(room_kind=RoomKind.ADMIN_TEST, is_ranked=False, min_players=1, reveal_stats_per_turn=2),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(-1001, 200),
            is_admin_game=True,
            room_kind=RoomKind.ADMIN_TEST,
        )
        fake = BunkerPlayer(
            game_id=55,
            user_id=-1001,
            display_name="Test survivor",
            is_host=False,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=generate_card(),
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
            is_fake=True,
        )
        real = BunkerPlayer(
            game_id=55,
            user_id=200,
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
        repository = FakeRevealRepository(game, [fake, real])
        cog = Bunker.__new__(Bunker)
        cog.repository = repository

        asyncio.run(cog._auto_reveal_fake_turns(game.id))

        self.assertEqual(repository.reveal_stat.await_count, 2)
        self.assertEqual(repository.add_event.await_count, 2)
        self.assertEqual(repository.game.current_turn_index, 1)
        self.assertEqual(len(repository.players[0].revealed_stats), 2)

    def test_auto_reveal_fake_players_assigns_missing_cards_before_revealing(self) -> None:
        fake_ids = tuple(-1000 - index for index in range(1, 8))
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
            state=GameState.REVEAL_PHASE,
            settings=BunkerSettings(room_kind=RoomKind.ADMIN_TEST, is_ranked=False, min_players=1),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=fake_ids,
            is_admin_game=True,
            room_kind=RoomKind.ADMIN_TEST,
        )
        players = [
            BunkerPlayer(
                game_id=55,
                user_id=user_id,
                display_name=f"Test survivor {index}",
                is_host=False,
                ready_at=None,
                invited_at=None,
                joined_at=None,
                left_at=None,
                is_eliminated=False,
                card=generate_card() if index <= 4 else None,
                revealed_stats=(),
                used_special_action=False,
                immune_round=None,
                is_fake=True,
            )
            for index, user_id in enumerate(fake_ids, start=1)
        ]
        repository = FakeRevealRepository(game, players)
        cog = Bunker.__new__(Bunker)
        cog.repository = repository

        asyncio.run(cog._auto_reveal_fake_turns(game.id))

        self.assertEqual(repository.reveal_stat.await_count, 7)
        self.assertEqual(repository.assign_cards.await_count, 3)
        self.assertTrue(all(player.card is not None for player in repository.players))
        self.assertTrue(all(len(player.revealed_stats) == 1 for player in repository.players))
        self.assertEqual(repository.game.state, GameState.SPEECH_PHASE)

    def test_public_specials_embed_shows_admin_test_fake_abilities(self) -> None:
        card = generate_card()
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
            settings=BunkerSettings(room_kind=RoomKind.ADMIN_TEST, is_ranked=False, min_players=1),
            round_number=1,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=None,
            profile=None,
            turn_order=(201,),
            is_admin_game=True,
            room_kind=RoomKind.ADMIN_TEST,
        )
        player = BunkerPlayer(
            game_id=55,
            user_id=201,
            display_name="Test survivor",
            is_host=True,
            ready_at=None,
            invited_at=None,
            joined_at=None,
            left_at=None,
            is_eliminated=False,
            card=card,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
            is_fake=True,
        )

        embed = _public_specials_embed(game, [player])
        names = "\n".join(field.name for field in embed.fields)

        self.assertIn(card.special_abilities[0].name, names)
        self.assertIn(card.special_abilities[1].name, names)
        self.assertIn("Админ-игра", embed.description)

    def test_public_specials_embed_keeps_ranked_hidden_abilities_private(self) -> None:
        card = generate_card()
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
            card=card,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )

        embed = _public_specials_embed(game, [player])
        names = "\n".join(field.name for field in embed.fields)

        self.assertNotIn(card.special_abilities[0].name, names)
        self.assertNotIn(card.special_abilities[1].name, names)

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

    def test_stale_public_ability_button_points_to_private_panel(self) -> None:
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
        repository = FakeRevealRepository(game, [player])
        cog = Bunker.__new__(Bunker)
        cog.repository = repository
        interaction = FakeInteraction(message=FakeMessage(900), user_id=201)

        asyncio.run(cog.public_use_special_ability(interaction, 0))

        repository.assign_cards.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("Спец возможности", message)

    def test_abilities_table_distinguishes_revealed_from_used(self) -> None:
        card = generate_card()
        abilities = (
            replace(card.special_abilities[0], revealed=True, used=False),
            replace(card.special_abilities[1], revealed=False, used=False),
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
            card=replace(card, special_abilities=abilities),
            revealed_stats=(),
            used_special_action=True,
            immune_round=None,
        )

        embed = _abilities_table_embed([player])

        self.assertIn("раскрыта", embed.description)
        self.assertNotIn("использована", embed.description)

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

        description = embed.description or ""
        self.assertNotIn("```text", description)
        self.assertIn("Мест в бункере: `4`", description)
        self.assertIn("Живые: `8`", description)
        self.assertIn("Выбыли: `0`", description)
        self.assertEqual(len(embed.fields), 8)
        player_fields = embed.fields
        self.assertEqual(len(player_fields), 8)
        self.assertTrue(all(not field.inline for field in player_fields))
        self.assertTrue(all("\n" in field.value for field in player_fields))
        self.assertEqual(player_fields[0].name, "1. Player 1")
        self.assertIn("━━━━━━━━", player_fields[0].value)
        self.assertIn("Статус:", player_fields[0].value)
        self.assertIn("Открыто:", player_fields[0].value)
        self.assertNotIn("```text", player_fields[0].value)
        self.assertNotIn("host", player_fields[0].name)

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
