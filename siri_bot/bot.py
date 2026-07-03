from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from siri_bot.config import Settings, load_settings


LOGGER = logging.getLogger(__name__)


class SiriBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.guild_messages = True
        intents.reactions = True
        intents.voice_states = True
        super().__init__(command_prefix=settings.command_prefix, intents=intents)
        self.settings = settings
        self.tree.on_error = self.on_tree_error

    async def setup_hook(self) -> None:
        await self._load_cogs()
        await self._sync_commands()

    async def on_ready(self) -> None:
        activity = discord.Game(name=self.settings.bot_status)
        await self.change_presence(activity=activity)
        LOGGER.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        LOGGER.error("Slash command failed", exc_info=(type(error), error, error.__traceback__))

        if isinstance(error, app_commands.MissingPermissions):
            message = "Для этой команды не хватает прав Discord."
        elif isinstance(error, app_commands.CheckFailure):
            message = "У тебя нет доступа к этой команде."
        else:
            message = "Команда сломалась при выполнении. Я уже записал ошибку в лог."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def _load_cogs(self) -> None:
        cogs_dir = Path(__file__).parent / "cogs"
        for file_path in sorted(cogs_dir.glob("*.py")):
            if file_path.name.startswith("_"):
                continue

            extension = f"siri_bot.cogs.{file_path.stem}"
            await self.load_extension(extension)
            LOGGER.info("Loaded extension %s", extension)

    async def _sync_commands(self) -> None:
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            LOGGER.info("Synced %s slash commands to guild %s", len(synced), self.settings.guild_id)
            return

        synced = await self.tree.sync()
        LOGGER.info("Synced %s global slash commands", len(synced))


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)

    async with SiriBot(settings) as bot:
        await bot.start(settings.token)


if __name__ == "__main__":
    asyncio.run(main())
