from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from siri_bot.checks import admin_only


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="say", description="Отправить сообщение в канал от имени бота.")
    @app_commands.describe(channel="Канал для сообщения", text="Текст сообщения")
    @admin_only()
    async def say(self, interaction: discord.Interaction, channel: discord.TextChannel, text: str) -> None:
        await channel.send(text)
        await interaction.response.send_message(f"Отправил сообщение в {channel.mention}.", ephemeral=True)

    @app_commands.command(name="purge", description="Удалить последние сообщения в текущем канале.")
    @app_commands.describe(amount="Сколько последних сообщений удалить")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Эта команда работает только в текстовых каналах.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"Удалено сообщений: {len(deleted)}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
