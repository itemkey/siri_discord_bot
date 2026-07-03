from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands


class General(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.started_at = datetime.now(UTC)
        self.reminder_tasks: set[asyncio.Task[None]] = set()

    @app_commands.command(name="ping", description="Показать задержку и время работы бота.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        uptime = datetime.now(UTC) - self.started_at
        uptime_text = _format_duration(int(uptime.total_seconds()))
        await interaction.response.send_message(f"Pong: {latency_ms} ms. Uptime: {uptime_text}.")

    @app_commands.command(name="roll", description="Бросить один или несколько кубиков.")
    @app_commands.describe(count="Количество кубиков", sides="Количество граней у кубика")
    async def roll(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 20] = 1,
        sides: app_commands.Range[int, 2, 1000] = 6,
    ) -> None:
        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls)
        detail = ", ".join(str(roll) for roll in rolls)
        await interaction.response.send_message(f"d{sides} x {count}: {detail}. Total: {total}.")

    @app_commands.command(name="choose", description="Выбрать один вариант из списка через запятую.")
    @app_commands.describe(options="Например: чай, кофе, вода")
    async def choose(self, interaction: discord.Interaction, options: str) -> None:
        choices = [option.strip() for option in options.replace("|", ",").split(",") if option.strip()]
        if len(choices) < 2:
            await interaction.response.send_message("Дай хотя бы два варианта через запятую.", ephemeral=True)
            return

        await interaction.response.send_message(random.choice(choices))

    @app_commands.command(name="server", description="Показать основную информацию о сервере.")
    async def server(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Эта команда работает только на сервере.", ephemeral=True)
            return

        embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
        embed.add_field(name="Server ID", value=str(guild.id), inline=True)
        embed.add_field(name="Members", value=str(guild.member_count or "unknown"), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="D"), inline=True)

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Показать аватар пользователя.")
    @app_commands.describe(user="Пользователь")
    async def avatar(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        target = user or interaction.user
        embed = discord.Embed(title=f"Avatar: {target}", color=discord.Color.blurple())
        embed.set_image(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remind", description="Отправить напоминание в этот канал через заданное время.")
    @app_commands.describe(minutes="Задержка в минутах", text="Текст напоминания")
    async def remind(
        self,
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 1, 1440],
        text: app_commands.Range[str, 1, 1000],
    ) -> None:
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("Не вижу канал для напоминания.", ephemeral=True)
            return

        task = asyncio.create_task(self._send_reminder(channel, interaction.user.mention, minutes, text))
        self.reminder_tasks.add(task)
        task.add_done_callback(self.reminder_tasks.discard)
        await interaction.response.send_message(f"Окей, напомню через {minutes} мин.", ephemeral=True)

    async def _send_reminder(
        self,
        channel: discord.abc.Messageable,
        mention: str,
        minutes: int,
        text: str,
    ) -> None:
        await asyncio.sleep(minutes * 60)
        await channel.send(f"{mention} напоминание: {text}")


def _format_duration(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
