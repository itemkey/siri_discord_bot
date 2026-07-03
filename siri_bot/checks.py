from __future__ import annotations

import discord
from discord import app_commands


def admin_only() -> app_commands.Check:
    async def predicate(interaction: discord.Interaction) -> bool:
        settings = getattr(interaction.client, "settings", None)
        user = interaction.user

        if settings is not None and settings.owner_id and user.id == settings.owner_id:
            return True

        if not isinstance(user, discord.Member):
            return False

        if user.guild_permissions.administrator:
            return True

        allowed_role_ids = getattr(settings, "admin_role_ids", frozenset()) if settings else frozenset()
        return any(role.id in allowed_role_ids for role in user.roles)

    return app_commands.check(predicate)
