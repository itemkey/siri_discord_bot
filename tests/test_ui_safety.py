from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

import discord

from siri_bot.ui_safety import SafeView, send_safe_interaction_error


class FakeResponse:
    def __init__(self, *, done: bool = False) -> None:
        self._done = done

        async def mark_done(*args, **kwargs) -> None:
            self._done = True

        self.send_message = AsyncMock(side_effect=mark_done)

    def is_done(self) -> bool:
        return self._done


class FakeFollowup:
    def __init__(self) -> None:
        self.send = AsyncMock()


class FakeInteraction:
    def __init__(self, *, done: bool = False) -> None:
        self.response = FakeResponse(done=done)
        self.followup = FakeFollowup()


class FakeHttpResponse:
    status = 400
    reason = "bad request"


class SafeUiTests(unittest.TestCase):
    def test_safe_view_on_error_sends_initial_ephemeral_message(self) -> None:
        interaction = FakeInteraction()

        asyncio.run(SafeView().on_error(interaction, RuntimeError("boom"), object()))

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        interaction.followup.send.assert_not_called()

    def test_safe_view_on_error_uses_followup_after_response_done(self) -> None:
        interaction = FakeInteraction(done=True)

        asyncio.run(SafeView().on_error(interaction, RuntimeError("boom"), object()))

        interaction.response.send_message.assert_not_called()
        interaction.followup.send.assert_awaited_once()
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

    def test_safe_error_message_swallows_discord_http_failure(self) -> None:
        interaction = FakeInteraction()
        interaction.response.send_message.side_effect = discord.HTTPException(FakeHttpResponse(), "nope")

        asyncio.run(send_safe_interaction_error(interaction, RuntimeError("boom")))

        interaction.response.send_message.assert_awaited_once()
