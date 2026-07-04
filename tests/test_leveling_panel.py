from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

import discord

from siri_bot.cogs.leveling import (
    RANK_PANEL_MODE_LEADERBOARD,
    RANK_PANEL_MODE_RANK,
    RANK_PANEL_REFRESH_CUSTOM_ID,
    Leveling,
    RankPanelResultView,
)


class FakeGuild:
    id = 100


class FakeChannel:
    id = 300

    def __init__(self) -> None:
        self.send = AsyncMock()


class FakeResponse:
    def __init__(self) -> None:
        self.defer = AsyncMock()
        self.send_message = AsyncMock()
        self.edit_message = AsyncMock()


class FakeMessage:
    def __init__(self) -> None:
        self.edit = AsyncMock()


class FakeInteraction:
    def __init__(self) -> None:
        self.guild = FakeGuild()
        self.user = object()
        self.channel = FakeChannel()
        self.response = FakeResponse()
        self.message = FakeMessage()


def _button(view: discord.ui.View, custom_id: str) -> discord.ui.Button:
    for child in view.children:
        if isinstance(child, discord.ui.Button) and child.custom_id == custom_id:
            return child

    raise AssertionError(f"Button {custom_id} not found")


def _assert_only_refresh_button(view: discord.ui.View) -> None:
    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
    assert len(buttons) == 1
    assert buttons[0].custom_id == RANK_PANEL_REFRESH_CUSTOM_ID
    assert buttons[0].label == "Обновить"


class LevelingPanelTests(unittest.TestCase):
    def test_public_rank_button_sends_private_result_menu(self) -> None:
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
        _assert_only_refresh_button(kwargs["view"])

    def test_public_leaderboard_button_sends_private_result_menu(self) -> None:
        cog = Leveling.__new__(Leveling)
        embed = discord.Embed(title="Leaderboard")
        cog._build_leaderboard_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_leaderboard_panel_response(cog, interaction))

        interaction.response.send_message.assert_awaited_once()
        interaction.response.edit_message.assert_not_called()
        interaction.channel.send.assert_not_called()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIs(kwargs["embed"], embed)
        self.assertIsInstance(kwargs["view"], RankPanelResultView)
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_LEADERBOARD)
        _assert_only_refresh_button(kwargs["view"])

    def test_public_leaderboard_button_handles_empty_table_privately(self) -> None:
        cog = Leveling.__new__(Leveling)
        cog._build_leaderboard_embed = AsyncMock(return_value=None)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_leaderboard_panel_response(cog, interaction))

        interaction.response.send_message.assert_awaited_once()
        args = interaction.response.send_message.await_args.args
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertEqual(args[0], "Пока нет XP в таблице лидеров.")
        self.assertTrue(kwargs["ephemeral"])
        self.assertIsInstance(kwargs["view"], RankPanelResultView)
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_LEADERBOARD)
        _assert_only_refresh_button(kwargs["view"])

    def test_refresh_updates_rank_private_menu(self) -> None:
        cog = Leveling.__new__(Leveling)
        embed = discord.Embed(title="Rank")
        cog._build_rank_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()
        view = RankPanelResultView(cog, RANK_PANEL_MODE_RANK)

        asyncio.run(_button(view, RANK_PANEL_REFRESH_CUSTOM_ID).callback(interaction))

        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIsNone(kwargs["content"])
        self.assertIs(kwargs["embed"], embed)
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_RANK)
        _assert_only_refresh_button(kwargs["view"])

    def test_refresh_updates_empty_leaderboard_private_menu(self) -> None:
        cog = Leveling.__new__(Leveling)
        cog._build_leaderboard_embed = AsyncMock(return_value=None)
        interaction = FakeInteraction()
        view = RankPanelResultView(cog, RANK_PANEL_MODE_LEADERBOARD)

        asyncio.run(_button(view, RANK_PANEL_REFRESH_CUSTOM_ID).callback(interaction))

        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(kwargs["content"], "Пока нет XP в таблице лидеров.")
        self.assertIsNone(kwargs["embed"])
        self.assertEqual(kwargs["view"].mode, RANK_PANEL_MODE_LEADERBOARD)
        _assert_only_refresh_button(kwargs["view"])


if __name__ == "__main__":
    unittest.main()
