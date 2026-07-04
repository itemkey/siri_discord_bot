from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

import discord

from siri_bot.cogs.leveling import Leveling, RankPanelView


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
        self.guild = object()
        self.user = object()
        self.response = FakeResponse()
        self.message = FakeMessage()


class LevelingPanelTests(unittest.TestCase):
    def test_rank_button_edits_panel_message(self) -> None:
        cog = Leveling.__new__(Leveling)
        embed = discord.Embed(title="Rank")
        cog._build_rank_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_rank_panel_response(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_called()
        interaction.response.edit_message.assert_not_called()
        interaction.message.edit.assert_awaited_once()
        kwargs = interaction.message.edit.await_args.kwargs
        self.assertIsNone(kwargs["content"])
        self.assertIs(kwargs["embed"], embed)
        self.assertIsInstance(kwargs["view"], RankPanelView)

    def test_leaderboard_button_edits_panel_message(self) -> None:
        cog = Leveling.__new__(Leveling)
        embed = discord.Embed(title="Leaderboard")
        cog._build_leaderboard_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_leaderboard_panel_response(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_called()
        interaction.response.edit_message.assert_not_called()
        interaction.message.edit.assert_awaited_once()
        kwargs = interaction.message.edit.await_args.kwargs
        self.assertIsNone(kwargs["content"])
        self.assertIs(kwargs["embed"], embed)
        self.assertIsInstance(kwargs["view"], RankPanelView)

    def test_leaderboard_button_edits_panel_message_when_empty(self) -> None:
        cog = Leveling.__new__(Leveling)
        cog._build_leaderboard_embed = AsyncMock(return_value=None)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_leaderboard_panel_response(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_called()
        interaction.response.edit_message.assert_not_called()
        interaction.message.edit.assert_awaited_once()
        kwargs = interaction.message.edit.await_args.kwargs
        self.assertEqual(kwargs["content"], "Пока нет XP в таблице лидеров.")
        self.assertIsNone(kwargs["embed"])
        self.assertIsInstance(kwargs["view"], RankPanelView)


if __name__ == "__main__":
    unittest.main()
