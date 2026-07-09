from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from siri_bot.cogs.leveling import (
    RANK_PANEL_MODE_LEADERBOARD,
    RANK_PANEL_MODE_RANK,
    Leveling,
    RankPanelLegacyRefreshDynamicButton,
    RankPanelRefreshDynamicButton,
    RankPanelResultView,
    _rank_panel_refresh_custom_id,
)
from siri_bot.leveling.models import LevelingSettings, PendingLevelupAnnouncement, XpChange


class FakeGuild:
    id = 100
    name = "Guild"

    def __init__(self, channel: object | None = None) -> None:
        self.channel = channel

    def get_channel(self, channel_id: int) -> object | None:
        return self.channel if self.channel is not None and getattr(self.channel, "id", None) == channel_id else None


class FakeUser:
    id = 200


class FakeChannel:
    id = 300
    mention = "<#300>"

    def __init__(self) -> None:
        self.send = AsyncMock()


class FakeMember:
    id = 200
    mention = "<@200>"
    roles: list[object] = []

    def __init__(self, guild: FakeGuild) -> None:
        self.guild = guild


class FakeBot:
    def __init__(self, guild: FakeGuild) -> None:
        self.guild = guild
        self.fetch_channel = AsyncMock(return_value=guild.channel)

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self.guild if guild_id == self.guild.id else None


class FakeResponse:
    def __init__(self) -> None:
        self.defer = AsyncMock()
        self.send_message = AsyncMock()
        self.edit_message = AsyncMock()


class FakeMessage:
    def __init__(self, *, content: str | None = None, embeds: list[discord.Embed] | None = None) -> None:
        self.content = content
        self.embeds = embeds or []
        self.edit = AsyncMock()


class FakeFollowup:
    def __init__(self, message: FakeMessage | None = None) -> None:
        self.message = message or FakeMessage()
        self.send = AsyncMock(return_value=self.message)


class FakeNotFoundResponse:
    status = 404
    reason = "Not Found"


class FakeClient:
    def __init__(self, cog: object | None) -> None:
        self.cog = cog

    def get_cog(self, name: str) -> object | None:
        return self.cog if name == "Leveling" else None


class FakeInteraction:
    def __init__(self, original_message: FakeMessage | None = None, followup_message: FakeMessage | None = None) -> None:
        self.guild = FakeGuild()
        self.user = FakeUser()
        self.channel = FakeChannel()
        self.response = FakeResponse()
        self.original_message = original_message or FakeMessage()
        self.original_response = AsyncMock(return_value=self.original_message)
        self.edit_original_response = AsyncMock(return_value=self.original_message)
        self.followup = FakeFollowup(followup_message)
        self.message = FakeMessage()
        self.client = None


def _button(view: discord.ui.View, custom_id: str) -> discord.ui.Button:
    for child in view.children:
        if isinstance(child, discord.ui.Button) and child.custom_id == custom_id:
            return child

    raise AssertionError(f"Button {custom_id} not found")


def _assert_only_refresh_button(view: discord.ui.View, mode: str) -> None:
    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
    assert len(buttons) == 1
    assert buttons[0].custom_id == _rank_panel_refresh_custom_id(mode)
    assert buttons[0].label == "Обновить"


def _settings(*, channel_id: int | None = 300, enabled: bool = True) -> LevelingSettings:
    return LevelingSettings(
        guild_id=100,
        enabled=enabled,
        formula_preset="quadratic",
        formula_a=5,
        formula_b=50,
        formula_c=100,
        message_xp_min=15,
        message_xp_max=25,
        message_cooldown_seconds=60,
        voice_xp_per_minute=2,
        reaction_xp=2,
        reaction_cooldown_seconds=60,
        role_reward_mode="accumulative",
        levelup_channel_id=channel_id,
        levelup_message="{user} reached level {level} with {xp} XP in {guild}.",
        first_place_role_id=None,
        first_place_user_id=None,
    )


class LevelingPanelTests(unittest.TestCase):
    def test_public_rank_button_sends_private_result_menu_and_remembers_it(self) -> None:
        cog = Leveling.__new__(Leveling)
        embed = discord.Embed(title="Rank")
        cog._build_rank_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_rank_panel_response(cog, interaction))

        interaction.response.send_message.assert_awaited_once()
        interaction.response.edit_message.assert_not_called()
        interaction.channel.send.assert_not_called()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIs(kwargs["embed"], embed)
        self.assertIsInstance(kwargs["view"], RankPanelResultView)
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_RANK)
        _assert_only_refresh_button(kwargs["view"], RANK_PANEL_MODE_RANK)
        interaction.original_response.assert_awaited_once()
        active = cog._rank_panel_results[(100, 300, 200)]
        self.assertIs(active.message, interaction.original_message)
        self.assertEqual(active.mode, RANK_PANEL_MODE_RANK)

    def test_public_leaderboard_button_edits_remembered_private_result(self) -> None:
        cog = Leveling.__new__(Leveling)
        saved_message = FakeMessage()
        cog._rank_panel_results = {
            (100, 300, 200): type("Active", (), {"message": saved_message, "mode": RANK_PANEL_MODE_RANK})()
        }
        embed = discord.Embed(title="Leaderboard")
        cog._build_leaderboard_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_leaderboard_panel_response(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_called()
        interaction.response.edit_message.assert_not_called()
        interaction.channel.send.assert_not_called()
        saved_message.edit.assert_awaited_once()
        kwargs = saved_message.edit.await_args.kwargs
        self.assertIsNone(kwargs["content"])
        self.assertIs(kwargs["embed"], embed)
        self.assertIsInstance(kwargs["view"], RankPanelResultView)
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_LEADERBOARD)
        _assert_only_refresh_button(kwargs["view"], RANK_PANEL_MODE_LEADERBOARD)
        self.assertEqual(cog._rank_panel_results[(100, 300, 200)].mode, RANK_PANEL_MODE_LEADERBOARD)

    def test_public_rank_button_edits_remembered_private_result_back(self) -> None:
        cog = Leveling.__new__(Leveling)
        saved_message = FakeMessage()
        cog._rank_panel_results = {
            (100, 300, 200): type("Active", (), {"message": saved_message, "mode": RANK_PANEL_MODE_LEADERBOARD})()
        }
        embed = discord.Embed(title="Rank Again")
        cog._build_rank_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_rank_panel_response(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_called()
        saved_message.edit.assert_awaited_once()
        kwargs = saved_message.edit.await_args.kwargs
        self.assertIsNone(kwargs["content"])
        self.assertIs(kwargs["embed"], embed)
        self.assertIsInstance(kwargs["view"], RankPanelResultView)
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_RANK)
        _assert_only_refresh_button(kwargs["view"], RANK_PANEL_MODE_RANK)
        self.assertEqual(cog._rank_panel_results[(100, 300, 200)].mode, RANK_PANEL_MODE_RANK)

    def test_public_leaderboard_button_replaces_expired_private_result(self) -> None:
        cog = Leveling.__new__(Leveling)
        saved_message = FakeMessage()
        saved_message.edit.side_effect = discord.NotFound(FakeNotFoundResponse(), "missing")
        cog._rank_panel_results = {
            (100, 300, 200): type("Active", (), {"message": saved_message, "mode": RANK_PANEL_MODE_RANK})()
        }
        replacement_message = FakeMessage()
        embed = discord.Embed(title="Leaderboard")
        cog._build_leaderboard_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction(followup_message=replacement_message)

        asyncio.run(Leveling._send_leaderboard_panel_response(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_called()
        saved_message.edit.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertTrue(kwargs["wait"])
        self.assertIs(kwargs["embed"], embed)
        active = cog._rank_panel_results[(100, 300, 200)]
        self.assertIs(active.message, replacement_message)
        self.assertEqual(active.mode, RANK_PANEL_MODE_LEADERBOARD)

    def test_refresh_updates_rank_private_menu(self) -> None:
        cog = Leveling.__new__(Leveling)
        embed = discord.Embed(title="Rank")
        cog._build_rank_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()
        view = RankPanelResultView(cog, RANK_PANEL_MODE_RANK)

        asyncio.run(_button(view, _rank_panel_refresh_custom_id(RANK_PANEL_MODE_RANK)).callback(interaction))

        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIsNone(kwargs["content"])
        self.assertIs(kwargs["embed"], embed)
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_RANK)
        _assert_only_refresh_button(kwargs["view"], RANK_PANEL_MODE_RANK)

    def test_refresh_updates_empty_leaderboard_private_menu(self) -> None:
        cog = Leveling.__new__(Leveling)
        cog._build_leaderboard_embed = AsyncMock(return_value=None)
        interaction = FakeInteraction()
        view = RankPanelResultView(cog, RANK_PANEL_MODE_LEADERBOARD)

        asyncio.run(_button(view, _rank_panel_refresh_custom_id(RANK_PANEL_MODE_LEADERBOARD)).callback(interaction))

        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(kwargs["content"], "Пока нет XP в таблице лидеров.")
        self.assertIsNone(kwargs["embed"])
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_LEADERBOARD)
        _assert_only_refresh_button(kwargs["view"], RANK_PANEL_MODE_LEADERBOARD)

    def test_stale_dynamic_refresh_updates_rank_private_menu(self) -> None:
        cog = Leveling.__new__(Leveling)
        embed = discord.Embed(title="Rank")
        cog._build_rank_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()
        interaction.client = FakeClient(cog)

        asyncio.run(RankPanelRefreshDynamicButton(RANK_PANEL_MODE_RANK).callback(interaction))

        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIs(kwargs["embed"], embed)
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_RANK)
        _assert_only_refresh_button(kwargs["view"], RANK_PANEL_MODE_RANK)

    def test_legacy_stale_refresh_infers_leaderboard_from_message(self) -> None:
        cog = Leveling.__new__(Leveling)
        embed = discord.Embed(title="Leaderboard")
        cog._build_leaderboard_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()
        interaction.client = FakeClient(cog)
        interaction.message = FakeMessage(embeds=[discord.Embed(title="Leaderboard: Guild")])

        asyncio.run(RankPanelLegacyRefreshDynamicButton().callback(interaction))

        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIs(kwargs["embed"], embed)
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_LEADERBOARD)

    def test_levelup_backfill_sends_pending_current_level_and_marks_it(self) -> None:
        channel = FakeChannel()
        guild = FakeGuild(channel)
        cog = Leveling.__new__(Leveling)
        cog.bot = FakeBot(guild)
        cog.repository = type(
            "Repo",
            (),
            {
                "get_settings": AsyncMock(return_value=_settings()),
                "get_pending_levelup_announcements": AsyncMock(
                    return_value=[
                        PendingLevelupAnnouncement(
                            guild_id=100,
                            user_id=200,
                            total_xp=1200,
                            current_level=5,
                            last_levelup_announced_level=0,
                        )
                    ]
                ),
                "mark_levelup_announced": AsyncMock(),
            },
        )()

        with patch("siri_bot.cogs.leveling.LEVELUP_BACKFILL_SEND_DELAY_SECONDS", 0):
            asyncio.run(Leveling._run_levelup_backfill(cog, 100))

        channel.send.assert_awaited_once_with("<@200> reached level 5 with 1200 XP in Guild.")
        cog.repository.mark_levelup_announced.assert_awaited_once_with(100, 200, 5)

    def test_levelup_backfill_skips_when_no_pending_announcements(self) -> None:
        channel = FakeChannel()
        guild = FakeGuild(channel)
        cog = Leveling.__new__(Leveling)
        cog.bot = FakeBot(guild)
        cog.repository = type(
            "Repo",
            (),
            {
                "get_settings": AsyncMock(return_value=_settings()),
                "get_pending_levelup_announcements": AsyncMock(return_value=[]),
                "mark_levelup_announced": AsyncMock(),
            },
        )()

        asyncio.run(Leveling._run_levelup_backfill(cog, 100))

        channel.send.assert_not_called()
        cog.repository.mark_levelup_announced.assert_not_called()

    def test_levelup_backfill_does_not_mark_failed_send(self) -> None:
        channel = FakeChannel()
        channel.send.side_effect = discord.HTTPException(FakeNotFoundResponse(), "missing")
        guild = FakeGuild(channel)
        cog = Leveling.__new__(Leveling)
        cog.bot = FakeBot(guild)
        cog.repository = type(
            "Repo",
            (),
            {
                "get_settings": AsyncMock(return_value=_settings()),
                "get_pending_levelup_announcements": AsyncMock(
                    return_value=[
                        PendingLevelupAnnouncement(
                            guild_id=100,
                            user_id=200,
                            total_xp=1200,
                            current_level=5,
                            last_levelup_announced_level=0,
                        )
                    ]
                ),
                "mark_levelup_announced": AsyncMock(),
            },
        )()

        asyncio.run(Leveling._run_levelup_backfill(cog, 100))

        channel.send.assert_awaited_once()
        cog.repository.mark_levelup_announced.assert_not_called()

    def test_send_levelup_marks_only_after_successful_send(self) -> None:
        channel = FakeChannel()
        guild = FakeGuild(channel)
        member = FakeMember(guild)
        cog = Leveling.__new__(Leveling)
        cog.bot = FakeBot(guild)
        cog.repository = type("Repo", (), {"mark_levelup_announced": AsyncMock()})()
        change = XpChange(
            guild_id=100,
            user_id=200,
            old_total_xp=99,
            new_total_xp=120,
            old_level=0,
            new_level=1,
            amount=21,
        )

        asyncio.run(Leveling._send_levelup(cog, member, _settings(), change))

        channel.send.assert_awaited_once_with("<@200> reached level 1 with 120 XP in Guild.")
        cog.repository.mark_levelup_announced.assert_awaited_once_with(100, 200, 1)

    def test_send_levelup_without_channel_leaves_announcement_pending(self) -> None:
        guild = FakeGuild()
        member = FakeMember(guild)
        cog = Leveling.__new__(Leveling)
        cog.bot = FakeBot(guild)
        cog.repository = type("Repo", (), {"mark_levelup_announced": AsyncMock()})()
        change = XpChange(
            guild_id=100,
            user_id=200,
            old_total_xp=99,
            new_total_xp=120,
            old_level=0,
            new_level=1,
            amount=21,
        )

        asyncio.run(Leveling._send_levelup(cog, member, _settings(channel_id=None), change))

        cog.repository.mark_levelup_announced.assert_not_called()

    def test_admin_xp_commands_mark_new_level_silently(self) -> None:
        guild = FakeGuild(FakeChannel())
        member = FakeMember(guild)
        interaction = FakeInteraction()
        interaction.guild = guild
        cog = Leveling.__new__(Leveling)
        cog.repository = type(
            "Repo",
            (),
            {
                "get_settings": AsyncMock(return_value=_settings()),
                "add_xp": AsyncMock(
                    return_value=XpChange(
                        guild_id=100,
                        user_id=200,
                        old_total_xp=0,
                        new_total_xp=1200,
                        old_level=0,
                        new_level=5,
                        amount=1200,
                    )
                ),
                "set_xp": AsyncMock(
                    return_value=XpChange(
                        guild_id=100,
                        user_id=200,
                        old_total_xp=1200,
                        new_total_xp=120,
                        old_level=5,
                        new_level=1,
                        amount=-1080,
                    )
                ),
                "set_levelup_announced_level": AsyncMock(),
            },
        )()
        cog._require_guild = AsyncMock(return_value=guild)
        cog._apply_member_level_side_effects = AsyncMock()

        asyncio.run(Leveling.member_add_xp_command.callback(cog, interaction, member, 1200))
        asyncio.run(Leveling.member_set_xp_command.callback(cog, interaction, member, 120))

        cog.repository.set_levelup_announced_level.assert_any_await(100, 200, 5)
        cog.repository.set_levelup_announced_level.assert_any_await(100, 200, 1)

    def test_levelup_channel_command_queues_backfill(self) -> None:
        channel = FakeChannel()
        guild = FakeGuild(channel)
        interaction = FakeInteraction()
        interaction.guild = guild
        cog = Leveling.__new__(Leveling)
        cog.repository = type("Repo", (), {"update_levelup_channel": AsyncMock(return_value=_settings())})()
        cog._require_guild = AsyncMock(return_value=guild)
        cog._schedule_levelup_backfill = Mock()

        asyncio.run(Leveling.levelup_channel_command.callback(cog, interaction, channel))

        cog.repository.update_levelup_channel.assert_awaited_once_with(100, 300)
        cog._schedule_levelup_backfill.assert_called_once_with(100)
        self.assertIn("Pending level-up announcements queued", interaction.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
