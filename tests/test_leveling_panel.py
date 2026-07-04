from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

import discord

from siri_bot.cogs.leveling import Leveling


class FakeGuild:
    id = 100


class FakeChannel:
    id = 300

    def __init__(self, result_message: "FakeMessage | None" = None) -> None:
        self.result_message = result_message or FakeMessage(900)
        self.send = AsyncMock(return_value=FakeMessage(901))

    def get_partial_message(self, message_id: int) -> "FakeMessage":
        self.result_message.id = message_id
        return self.result_message


class FakeResponse:
    def __init__(self) -> None:
        self.defer = AsyncMock()
        self.send_message = AsyncMock()
        self.edit_message = AsyncMock()


class FakeMessage:
    def __init__(self, message_id: int = 0) -> None:
        self.id = message_id
        self.edit = AsyncMock()


class FakeNotFoundResponse:
    status = 404
    reason = "Not Found"


class FakeInteraction:
    def __init__(self, channel: FakeChannel | None = None) -> None:
        self.guild = FakeGuild()
        self.user = object()
        self.channel = channel or FakeChannel()
        self.response = FakeResponse()
        self.followup = type("FakeFollowup", (), {"send": AsyncMock()})()
        self.message = FakeMessage(700)


class FakeRepository:
    def __init__(self, saved_message_id: int | None) -> None:
        self.get_panel_result_message_id = AsyncMock(return_value=saved_message_id)
        self.upsert_panel_result_message_id = AsyncMock()


class LevelingPanelTests(unittest.TestCase):
    def test_rank_button_edits_saved_result_message(self) -> None:
        cog = Leveling.__new__(Leveling)
        cog.repository = FakeRepository(saved_message_id=800)
        embed = discord.Embed(title="Rank")
        cog._build_rank_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_rank_panel_response(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_called()
        interaction.response.edit_message.assert_not_called()
        interaction.message.edit.assert_not_called()
        interaction.channel.send.assert_not_called()
        interaction.channel.result_message.edit.assert_awaited_once()
        cog.repository.upsert_panel_result_message_id.assert_not_called()
        kwargs = interaction.channel.result_message.edit.await_args.kwargs
        self.assertIsNone(kwargs["content"])
        self.assertIs(kwargs["embed"], embed)
        self.assertIsNone(kwargs["view"])

    def test_rank_button_creates_result_message_when_missing(self) -> None:
        cog = Leveling.__new__(Leveling)
        cog.repository = FakeRepository(saved_message_id=None)
        embed = discord.Embed(title="Rank")
        cog._build_rank_embed = AsyncMock(return_value=embed)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_rank_panel_response(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_called()
        interaction.response.edit_message.assert_not_called()
        interaction.message.edit.assert_not_called()
        interaction.channel.send.assert_awaited_once()
        cog.repository.upsert_panel_result_message_id.assert_awaited_once_with(100, 300, 901)
        kwargs = interaction.channel.send.await_args.kwargs
        self.assertIsNone(kwargs["content"])
        self.assertIs(kwargs["embed"], embed)

    def test_rank_button_recreates_deleted_result_message(self) -> None:
        cog = Leveling.__new__(Leveling)
        cog.repository = FakeRepository(saved_message_id=800)
        embed = discord.Embed(title="Rank")
        cog._build_rank_embed = AsyncMock(return_value=embed)
        old_result_message = FakeMessage(800)
        old_result_message.edit.side_effect = discord.NotFound(FakeNotFoundResponse(), "missing")
        interaction = FakeInteraction(channel=FakeChannel(result_message=old_result_message))

        asyncio.run(Leveling._send_rank_panel_response(cog, interaction))

        old_result_message.edit.assert_awaited_once()
        interaction.channel.send.assert_awaited_once()
        cog.repository.upsert_panel_result_message_id.assert_awaited_once_with(100, 300, 901)

    def test_leaderboard_button_updates_result_message_when_empty(self) -> None:
        cog = Leveling.__new__(Leveling)
        cog.repository = FakeRepository(saved_message_id=800)
        cog._build_leaderboard_embed = AsyncMock(return_value=None)
        interaction = FakeInteraction()

        asyncio.run(Leveling._send_leaderboard_panel_response(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_called()
        interaction.response.edit_message.assert_not_called()
        interaction.message.edit.assert_not_called()
        interaction.channel.send.assert_not_called()
        interaction.channel.result_message.edit.assert_awaited_once()
        kwargs = interaction.channel.result_message.edit.await_args.kwargs
        self.assertEqual(kwargs["content"], "Пока нет XP в таблице лидеров.")
        self.assertIsNone(kwargs["embed"])
        self.assertIsNone(kwargs["view"])


if __name__ == "__main__":
    unittest.main()
