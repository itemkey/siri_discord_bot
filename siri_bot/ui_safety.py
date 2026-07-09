from __future__ import annotations

import logging
from typing import Any

import discord


LOGGER = logging.getLogger(__name__)
GENERIC_INTERACTION_ERROR = "Не смог обработать нажатие. Открой панель заново и попробуй еще раз."


def interaction_response_done(interaction: discord.Interaction) -> bool:
    is_done = getattr(interaction.response, "is_done", None)
    return bool(is_done()) if callable(is_done) else False


def live_view_has_item(
    interaction: discord.Interaction,
    custom_id: str,
    *,
    component_type: discord.ComponentType = discord.ComponentType.button,
) -> bool:
    message = getattr(interaction, "message", None)
    message_id = getattr(message, "id", None)
    client = getattr(interaction, "client", None)
    connection = getattr(client, "_connection", None)
    view_store = getattr(connection, "_view_store", None)
    views = getattr(view_store, "_views", None)
    if message_id is None or not isinstance(views, dict):
        return False

    return (component_type.value, custom_id) in views.get(message_id, {})


async def send_safe_interaction_message(
    interaction: discord.Interaction,
    message: str = GENERIC_INTERACTION_ERROR,
    *,
    ephemeral: bool = True,
) -> None:
    try:
        if interaction_response_done(interaction):
            await interaction.followup.send(message, ephemeral=ephemeral)
            return

        await interaction.response.send_message(message, ephemeral=ephemeral)
    except discord.HTTPException:
        LOGGER.info("Could not send safe interaction fallback message.", exc_info=True)


async def send_safe_interaction_error(
    interaction: discord.Interaction,
    error: Exception,
    *,
    item: Any | None = None,
    message: str = GENERIC_INTERACTION_ERROR,
) -> None:
    LOGGER.error(
        "Discord UI interaction failed for %r.",
        item,
        exc_info=(type(error), error, error.__traceback__),
    )
    await send_safe_interaction_message(interaction, message)


class SafeView(discord.ui.View):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
        /,
    ) -> None:
        await send_safe_interaction_error(interaction, error, item=item)


class SafeModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        await send_safe_interaction_error(interaction, error, item=self)
