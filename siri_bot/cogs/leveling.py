from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import UTC, datetime, timedelta

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from siri_bot.checks import admin_only
from siri_bot.leveling.formula import (
    FormulaConfig,
    SUPPORTED_FORMULAS,
    SUPPORTED_REWARD_MODES,
    first_place_changed,
    progress_for_total_xp,
    reward_roles_for_level,
)
from siri_bot.leveling.models import LevelingSettings, XpChange
from siri_bot.leveling.repository import LevelingRepository


LOGGER = logging.getLogger(__name__)
DURATION_PATTERN = re.compile(r"^(?P<amount>\d+)(?P<unit>s|m|h|d)$", re.IGNORECASE)
LEADERBOARD_PAGE_SIZE = 10


class Leveling(commands.Cog):
    leveling_group = app_commands.Group(name="leveling", description="Настройки leveling system.")
    reward_group = app_commands.Group(name="reward", description="Role rewards.", parent=leveling_group)
    levelup_group = app_commands.Group(name="levelup", description="Level-up сообщения.", parent=leveling_group)
    booster_group = app_commands.Group(name="booster", description="XP boosters.", parent=leveling_group)
    member_group = app_commands.Group(name="member", description="XP участника.", parent=leveling_group)

    def __init__(self, bot: commands.Bot, repository: LevelingRepository, pool: asyncpg.Pool) -> None:
        self.bot = bot
        self.repository = repository
        self.pool = pool
        self._voice_synced_once = False
        self.voice_xp_tick.start()

    def cog_unload(self) -> None:
        self.voice_xp_tick.cancel()
        asyncio.create_task(self.pool.close())

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._voice_synced_once:
            return

        self._voice_synced_once = True
        for guild in self.bot.guilds:
            await self._sync_guild_voice_sessions(guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        member = message.author if isinstance(message.author, discord.Member) else message.guild.get_member(message.author.id)
        if member is None:
            return

        settings = await self.repository.get_settings(message.guild.id)
        if not settings.enabled or settings.message_xp_max <= 0:
            return

        if not await self.repository.try_acquire_cooldown(
            message.guild.id,
            member.id,
            "message",
            settings.message_cooldown_seconds,
        ):
            return

        message_min = min(settings.message_xp_min, settings.message_xp_max)
        message_max = max(settings.message_xp_min, settings.message_xp_max)
        base_xp = random.randint(message_min, message_max)
        await self._award_xp(member, settings, base_xp)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return

        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        settings = await self.repository.get_settings(guild.id)
        if not settings.enabled or settings.reaction_xp <= 0:
            return

        reactor_is_bot = await self._is_user_bot(payload)
        if reactor_is_bot:
            return

        message = await self._fetch_reaction_message(payload)
        if message is None or message.author.bot or message.author.id == payload.user_id:
            return

        author = message.author if isinstance(message.author, discord.Member) else guild.get_member(message.author.id)
        if author is None:
            try:
                author = await guild.fetch_member(message.author.id)
            except discord.HTTPException:
                return

        registered = await self.repository.register_reaction_award(
            guild.id,
            payload.message_id,
            payload.user_id,
            str(payload.emoji),
            author.id,
        )
        if not registered:
            return

        if not await self.repository.try_acquire_cooldown(
            guild.id,
            author.id,
            "reaction",
            settings.reaction_cooldown_seconds,
        ):
            return

        await self._award_xp(author, settings, settings.reaction_xp)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or member.guild is None:
            return

        settings = await self.repository.get_settings(member.guild.id)
        if not settings.enabled or settings.voice_xp_per_minute <= 0:
            await self.repository.remove_voice_session(member.guild.id, member.id)
            return

        if after.channel is None or after.channel == member.guild.afk_channel:
            await self.repository.remove_voice_session(member.guild.id, member.id)
            return

        if before.channel != after.channel:
            await self.repository.upsert_voice_session(member.guild.id, member.id, after.channel.id)

    @tasks.loop(minutes=1.0)
    async def voice_xp_tick(self) -> None:
        sessions = await self.repository.get_voice_sessions()
        for session in sessions:
            guild = self.bot.get_guild(session.guild_id)
            if guild is None:
                await self.repository.remove_voice_session(session.guild_id, session.user_id)
                continue

            settings = await self.repository.get_settings(guild.id)
            if not settings.enabled or settings.voice_xp_per_minute <= 0:
                await self.repository.remove_voice_session(guild.id, session.user_id)
                continue

            member = guild.get_member(session.user_id)
            if member is None or member.bot or member.voice is None or member.voice.channel is None:
                await self.repository.remove_voice_session(guild.id, session.user_id)
                continue

            channel = member.voice.channel
            if channel.id != session.channel_id or channel == guild.afk_channel:
                await self.repository.remove_voice_session(guild.id, member.id)
                continue

            if not _has_voice_company(channel):
                continue

            await self._award_xp(member, settings, settings.voice_xp_per_minute)
            await self.repository.touch_voice_session(guild.id, member.id)

    @voice_xp_tick.before_loop
    async def before_voice_xp_tick(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="rank", description="Показать уровень и XP участника.")
    @app_commands.describe(user="Пользователь")
    async def rank(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        guild = await self._require_guild(interaction, ephemeral=False)
        if guild is None:
            return

        target = user or interaction.user
        settings = await self.repository.get_settings(guild.id)
        total_xp = await self.repository.get_member_xp(guild.id, target.id)
        rank_number = await self.repository.get_member_rank(guild.id, target.id)
        progress = progress_for_total_xp(total_xp, settings.formula)

        embed = discord.Embed(title=f"Rank: {target}", color=discord.Color.blurple())
        embed.add_field(name="Level", value=str(progress.level), inline=True)
        embed.add_field(name="XP", value=str(progress.total_xp), inline=True)
        embed.add_field(name="Place", value=f"#{rank_number}", inline=True)
        embed.add_field(
            name="Progress",
            value=f"{progress.current_level_xp}/{progress.next_level_xp} XP",
            inline=False,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Показать таблицу лидеров XP.")
    @app_commands.describe(page="Номер страницы")
    async def leaderboard(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 100] = 1) -> None:
        guild = await self._require_guild(interaction, ephemeral=False)
        if guild is None:
            return

        settings = await self.repository.get_settings(guild.id)
        entries = await self.repository.get_leaderboard(
            guild.id,
            limit=LEADERBOARD_PAGE_SIZE,
            offset=(page - 1) * LEADERBOARD_PAGE_SIZE,
        )
        if not entries:
            await interaction.response.send_message("Пока нет XP в таблице лидеров.", ephemeral=True)
            return

        lines: list[str] = []
        for entry in entries:
            progress = progress_for_total_xp(entry.total_xp, settings.formula)
            member = guild.get_member(entry.user_id)
            name = member.mention if member else f"<@{entry.user_id}>"
            lines.append(f"#{entry.rank} {name} - level {progress.level}, {entry.total_xp} XP")

        embed = discord.Embed(
            title=f"Leaderboard: {guild.name}",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Page {page}")
        await interaction.response.send_message(embed=embed)

    @leveling_group.command(name="settings", description="Показать настройки leveling.")
    @admin_only()
    async def settings_command(self, interaction: discord.Interaction) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        settings = await self.repository.get_settings(guild.id)
        rewards = await self.repository.get_role_rewards(guild.id)
        boosters = await self.repository.get_boosters(guild.id)
        unmanageable = [role_id for _, role_id in rewards if not self._can_manage_role_id(guild, role_id)]

        embed = discord.Embed(title="Leveling settings", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value=str(settings.enabled), inline=True)
        embed.add_field(
            name="Formula",
            value=f"{settings.formula_preset}: a={settings.formula_a:g}, b={settings.formula_b:g}, c={settings.formula_c:g}",
            inline=False,
        )
        embed.add_field(
            name="Message XP",
            value=f"{settings.message_xp_min}-{settings.message_xp_max}, cooldown {settings.message_cooldown_seconds}s",
            inline=False,
        )
        embed.add_field(name="Voice XP", value=f"{settings.voice_xp_per_minute}/min", inline=True)
        embed.add_field(
            name="Reaction XP",
            value=f"{settings.reaction_xp}, cooldown {settings.reaction_cooldown_seconds}s",
            inline=True,
        )
        embed.add_field(name="Rewards", value=f"{len(rewards)} ({settings.role_reward_mode})", inline=True)
        embed.add_field(name="Boosters", value=str(len(boosters)), inline=True)
        embed.add_field(name="Levelup Channel", value=_channel_text(settings.levelup_channel_id), inline=True)
        embed.add_field(name="First Place Role", value=_role_text(settings.first_place_role_id), inline=True)
        embed.add_field(
            name="Manage Roles",
            value="OK" if guild.me and guild.me.guild_permissions.manage_roles else "Missing permission",
            inline=True,
        )
        embed.add_field(name="Unmanageable reward roles", value=str(len(unmanageable)), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @leveling_group.command(name="enable", description="Включить или выключить leveling.")
    @app_commands.describe(enabled="Включено")
    @admin_only()
    async def enable_command(self, interaction: discord.Interaction, enabled: bool) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        await self.repository.update_enabled(guild.id, enabled)
        if enabled:
            await self._sync_guild_voice_sessions(guild)

        await interaction.response.send_message(f"Leveling enabled: {enabled}.", ephemeral=True)

    @leveling_group.command(name="formula", description="Настроить формулу уровня.")
    @app_commands.describe(preset="Тип формулы", a="Коэффициент a", b="Коэффициент b", c="Базовый XP")
    @app_commands.choices(
        preset=[
            app_commands.Choice(name="quadratic", value="quadratic"),
            app_commands.Choice(name="linear", value="linear"),
        ]
    )
    @admin_only()
    async def formula_command(
        self,
        interaction: discord.Interaction,
        preset: str,
        a: float | None = None,
        b: float | None = None,
        c: float | None = None,
    ) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        if preset not in SUPPORTED_FORMULAS:
            await interaction.response.send_message("Неизвестный preset формулы.", ephemeral=True)
            return

        if preset == "linear":
            config = FormulaConfig(preset=preset, a=a if a is not None else 0, b=b if b is not None else 100, c=c if c is not None else 100)
        else:
            config = FormulaConfig(preset=preset, a=a if a is not None else 5, b=b if b is not None else 50, c=c if c is not None else 100)

        if config.a < 0 or config.b < 0 or config.c < 1:
            await interaction.response.send_message("Коэффициенты должны быть неотрицательными, а c должен быть >= 1.", ephemeral=True)
            return

        await self.repository.update_formula(guild.id, config)
        await interaction.response.send_message(
            f"Formula: {config.preset}, a={config.a:g}, b={config.b:g}, c={config.c:g}.",
            ephemeral=True,
        )

    @leveling_group.command(name="xp-options", description="Настроить начисление XP.")
    @app_commands.describe(
        message_min="Минимум XP за сообщение",
        message_max="Максимум XP за сообщение",
        message_cooldown="Cooldown сообщений в секундах",
        voice_per_minute="XP за минуту voice",
        reaction_xp="XP автору сообщения за реакцию",
        reaction_cooldown="Cooldown reaction XP в секундах",
    )
    @admin_only()
    async def xp_options_command(
        self,
        interaction: discord.Interaction,
        message_min: int | None = None,
        message_max: int | None = None,
        message_cooldown: int | None = None,
        voice_per_minute: int | None = None,
        reaction_xp: int | None = None,
        reaction_cooldown: int | None = None,
    ) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        current = await self.repository.get_settings(guild.id)
        new_min = current.message_xp_min if message_min is None else message_min
        new_max = current.message_xp_max if message_max is None else message_max
        new_message_cooldown = current.message_cooldown_seconds if message_cooldown is None else message_cooldown
        new_voice = current.voice_xp_per_minute if voice_per_minute is None else voice_per_minute
        new_reaction = current.reaction_xp if reaction_xp is None else reaction_xp
        new_reaction_cooldown = current.reaction_cooldown_seconds if reaction_cooldown is None else reaction_cooldown

        values = [new_min, new_max, new_message_cooldown, new_voice, new_reaction, new_reaction_cooldown]
        if any(value < 0 for value in values):
            await interaction.response.send_message("XP и cooldown не могут быть отрицательными.", ephemeral=True)
            return

        if new_min > new_max:
            await interaction.response.send_message("message_min не может быть больше message_max.", ephemeral=True)
            return

        await self.repository.update_xp_options(
            guild.id,
            message_xp_min=new_min,
            message_xp_max=new_max,
            message_cooldown_seconds=new_message_cooldown,
            voice_xp_per_minute=new_voice,
            reaction_xp=new_reaction,
            reaction_cooldown_seconds=new_reaction_cooldown,
        )
        await interaction.response.send_message("XP options обновлены.", ephemeral=True)

    @leveling_group.command(name="first-place-role", description="Настроить роль первого места.")
    @app_commands.describe(role="Роль для первого места. Оставь пустым, чтобы выключить.")
    @admin_only()
    async def first_place_role_command(self, interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        if role and role.is_default():
            await interaction.response.send_message("Нельзя использовать @everyone.", ephemeral=True)
            return

        current = await self.repository.get_settings(guild.id)
        await self._remove_first_place_role(guild, current.first_place_user_id, current.first_place_role_id)
        await self.repository.update_first_place_role(guild.id, role.id if role else None)
        await self.repository.update_first_place_user(guild.id, None)
        await self._apply_first_place_role(guild)
        suffix = role.mention if role else "выключена"
        await interaction.response.send_message(f"First Place Role: {suffix}.", ephemeral=True)

    @reward_group.command(name="add", description="Добавить role reward.")
    @app_commands.describe(level="Уровень", role="Роль")
    @admin_only()
    async def reward_add_command(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 100000],
        role: discord.Role,
    ) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        if role.is_default():
            await interaction.response.send_message("Нельзя использовать @everyone.", ephemeral=True)
            return

        await self.repository.upsert_role_reward(guild.id, level, role.id)
        warning = "" if self._can_manage_role(guild, role) else " Внимание: бот сейчас не может управлять этой ролью."
        await interaction.response.send_message(f"Reward level {level}: {role.mention}.{warning}", ephemeral=True)

    @reward_group.command(name="remove", description="Удалить role reward.")
    @app_commands.describe(level="Уровень")
    @admin_only()
    async def reward_remove_command(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 100000]) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        removed = await self.repository.remove_role_reward(guild.id, level)
        message = f"Reward level {level} удалён." if removed else f"Reward level {level} не найден."
        await interaction.response.send_message(message, ephemeral=True)

    @reward_group.command(name="list", description="Показать role rewards.")
    @admin_only()
    async def reward_list_command(self, interaction: discord.Interaction) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        rewards = await self.repository.get_role_rewards(guild.id)
        if not rewards:
            await interaction.response.send_message("Role rewards не настроены.", ephemeral=True)
            return

        lines = [f"Level {level}: {_role_text(role_id)}" for level, role_id in rewards[:30]]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @reward_group.command(name="mode", description="Настроить режим выдачи reward ролей.")
    @app_commands.describe(mode="accumulative или highest_only")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="accumulative", value="accumulative"),
            app_commands.Choice(name="highest_only", value="highest_only"),
        ]
    )
    @admin_only()
    async def reward_mode_command(self, interaction: discord.Interaction, mode: str) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        if mode not in SUPPORTED_REWARD_MODES:
            await interaction.response.send_message("Неизвестный режим.", ephemeral=True)
            return

        await self.repository.update_reward_mode(guild.id, mode)
        await interaction.response.send_message(f"Reward mode: {mode}.", ephemeral=True)

    @levelup_group.command(name="channel", description="Настроить канал level-up сообщений.")
    @app_commands.describe(channel="Канал")
    @admin_only()
    async def levelup_channel_command(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        await self.repository.update_levelup_channel(guild.id, channel.id)
        await interaction.response.send_message(f"Level-up channel: {channel.mention}.", ephemeral=True)

    @levelup_group.command(name="message", description="Настроить шаблон level-up сообщения.")
    @app_commands.describe(message="Можно использовать {user}, {level}, {xp}, {guild}.")
    @admin_only()
    async def levelup_message_command(self, interaction: discord.Interaction, message: app_commands.Range[str, 1, 500]) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        await self.repository.update_levelup_message(guild.id, message)
        await interaction.response.send_message("Level-up message обновлён.", ephemeral=True)

    @levelup_group.command(name="disable", description="Выключить level-up сообщения.")
    @admin_only()
    async def levelup_disable_command(self, interaction: discord.Interaction) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        await self.repository.update_levelup_channel(guild.id, None)
        await interaction.response.send_message("Level-up сообщения выключены.", ephemeral=True)

    @booster_group.command(name="add", description="Добавить XP booster.")
    @app_commands.describe(
        scope="global, user или role",
        multiplier="Множитель от 0.1 до 5.0",
        user="Нужен для scope=user",
        role="Нужна для scope=role",
        duration="Например 30m, 2h, 7d. Пусто = без срока.",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="global", value="global"),
            app_commands.Choice(name="user", value="user"),
            app_commands.Choice(name="role", value="role"),
        ]
    )
    @admin_only()
    async def booster_add_command(
        self,
        interaction: discord.Interaction,
        scope: str,
        multiplier: float,
        user: discord.User | None = None,
        role: discord.Role | None = None,
        duration: str | None = None,
    ) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        if scope not in {"global", "user", "role"}:
            await interaction.response.send_message("scope должен быть global, user или role.", ephemeral=True)
            return

        if multiplier < 0.1 or multiplier > 5.0:
            await interaction.response.send_message("multiplier должен быть от 0.1 до 5.0.", ephemeral=True)
            return

        target_id: int | None = None
        target_text = "server"
        if scope == "user":
            if user is None:
                await interaction.response.send_message("Для scope=user нужно указать user.", ephemeral=True)
                return
            target_id = user.id
            target_text = user.mention
        elif scope == "role":
            if role is None:
                await interaction.response.send_message("Для scope=role нужно указать role.", ephemeral=True)
                return
            target_id = role.id
            target_text = role.mention

        expires_at = _parse_duration(duration)
        if duration and expires_at is None:
            await interaction.response.send_message("duration должен быть в формате 30m, 2h или 7d.", ephemeral=True)
            return

        booster = await self.repository.add_booster(guild.id, scope, target_id, multiplier, expires_at)
        expires_text = "без срока" if booster.expires_at is None else discord.utils.format_dt(booster.expires_at, style="R")
        await interaction.response.send_message(
            f"Booster #{booster.id}: {scope} {target_text} x{booster.multiplier:g}, {expires_text}.",
            ephemeral=True,
        )

    @booster_group.command(name="remove", description="Удалить XP booster.")
    @app_commands.describe(booster_id="ID booster")
    @admin_only()
    async def booster_remove_command(self, interaction: discord.Interaction, booster_id: int) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        removed = await self.repository.remove_booster(guild.id, booster_id)
        message = f"Booster #{booster_id} удалён." if removed else f"Booster #{booster_id} не найден."
        await interaction.response.send_message(message, ephemeral=True)

    @booster_group.command(name="list", description="Показать активные XP boosters.")
    @admin_only()
    async def booster_list_command(self, interaction: discord.Interaction) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        boosters = await self.repository.get_boosters(guild.id)
        if not boosters:
            await interaction.response.send_message("Активных boosters нет.", ephemeral=True)
            return

        lines = []
        for booster in boosters[:30]:
            target = "server" if booster.target_id is None else f"<@&{booster.target_id}>" if booster.scope == "role" else f"<@{booster.target_id}>"
            expires = "без срока" if booster.expires_at is None else discord.utils.format_dt(booster.expires_at, style="R")
            lines.append(f"#{booster.id}: {booster.scope} {target} x{booster.multiplier:g}, {expires}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @member_group.command(name="add-xp", description="Добавить XP участнику.")
    @app_commands.describe(member="Участник", amount="Сколько XP добавить")
    @admin_only()
    async def member_add_xp_command(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 1_000_000],
    ) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        settings = await self.repository.get_settings(guild.id)
        change = await self.repository.add_xp(guild.id, member.id, amount, settings.formula)
        await self._apply_member_level_side_effects(member, settings, change, send_levelup=False)
        await interaction.response.send_message(
            f"{member.mention}: {change.old_total_xp} -> {change.new_total_xp} XP.",
            ephemeral=True,
        )

    @member_group.command(name="set-xp", description="Установить XP участника.")
    @app_commands.describe(member="Участник", total_xp="Новое значение XP")
    @admin_only()
    async def member_set_xp_command(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        total_xp: app_commands.Range[int, 0, 100_000_000],
    ) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        settings = await self.repository.get_settings(guild.id)
        change = await self.repository.set_xp(guild.id, member.id, total_xp, settings.formula)
        await self._apply_member_level_side_effects(member, settings, change, send_levelup=False)
        await interaction.response.send_message(
            f"{member.mention}: level {change.old_level} -> {change.new_level}, {change.new_total_xp} XP.",
            ephemeral=True,
        )

    @member_group.command(name="reset", description="Сбросить XP участника.")
    @app_commands.describe(member="Участник")
    @admin_only()
    async def member_reset_command(self, interaction: discord.Interaction, member: discord.Member) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        settings = await self.repository.get_settings(guild.id)
        await self.repository.reset_member(guild.id, member.id)
        await self._apply_role_rewards(member, settings, 0, remove_unearned=True)
        await self._apply_first_place_role(guild)
        await interaction.response.send_message(f"XP для {member.mention} сброшен.", ephemeral=True)

    @leveling_group.command(name="reset-confirm", description="Сбросить XP-прогресс сервера.")
    @app_commands.describe(confirm="Напиши RESET")
    @admin_only()
    async def reset_confirm_command(self, interaction: discord.Interaction, confirm: str) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        if confirm != "RESET":
            await interaction.response.send_message("Для подтверждения напиши RESET.", ephemeral=True)
            return

        settings = await self.repository.get_settings(guild.id)
        await self._remove_first_place_role(guild, settings.first_place_user_id, settings.first_place_role_id)
        await self.repository.reset_guild_progress(guild.id)
        await interaction.response.send_message(
            "XP-прогресс сервера сброшен. Настройки, rewards и boosters сохранены.",
            ephemeral=True,
        )

    async def _award_xp(self, member: discord.Member, settings: LevelingSettings, base_xp: int) -> XpChange | None:
        if base_xp <= 0:
            return None

        role_ids = [role.id for role in member.roles]
        multiplier = await self.repository.get_booster_multiplier(member.guild.id, member.id, role_ids)
        amount = max(0, round(base_xp * multiplier))
        if amount <= 0:
            return None

        change = await self.repository.add_xp(member.guild.id, member.id, amount, settings.formula)
        await self._apply_member_level_side_effects(member, settings, change, send_levelup=True)
        return change

    async def _apply_member_level_side_effects(
        self,
        member: discord.Member,
        settings: LevelingSettings,
        change: XpChange,
        *,
        send_levelup: bool,
    ) -> None:
        if change.new_level != change.old_level:
            await self._apply_role_rewards(
                member,
                settings,
                change.new_level,
                remove_unearned=change.new_level < change.old_level,
            )
            if send_levelup and change.new_level > change.old_level:
                await self._send_levelup(member, settings, change)

        await self._apply_first_place_role(member.guild)

    async def _apply_role_rewards(
        self,
        member: discord.Member,
        settings: LevelingSettings,
        level: int,
        *,
        remove_unearned: bool = False,
    ) -> None:
        rewards = await self.repository.get_role_rewards(member.guild.id)
        desired_role_ids = reward_roles_for_level(rewards, level, settings.role_reward_mode)
        reward_role_ids = {role_id for _, role_id in rewards}
        current_role_ids = {role.id for role in member.roles}

        roles_to_add = desired_role_ids - current_role_ids
        roles_to_remove = set()
        if settings.role_reward_mode == "highest_only" or remove_unearned:
            roles_to_remove = (reward_role_ids - desired_role_ids) & current_role_ids

        for role_id in roles_to_add:
            role = member.guild.get_role(role_id)
            if role and self._can_manage_role(member.guild, role):
                try:
                    await member.add_roles(role, reason="Leveling role reward")
                except discord.HTTPException:
                    LOGGER.exception("Failed to add reward role %s to user %s", role_id, member.id)

        for role_id in roles_to_remove:
            role = member.guild.get_role(role_id)
            if role and self._can_manage_role(member.guild, role):
                try:
                    await member.remove_roles(role, reason="Leveling highest_only reward mode")
                except discord.HTTPException:
                    LOGGER.exception("Failed to remove reward role %s from user %s", role_id, member.id)

    async def _apply_first_place_role(self, guild: discord.Guild) -> None:
        settings = await self.repository.get_settings(guild.id)
        if settings.first_place_role_id is None:
            return

        leader = await self.repository.get_leader(guild.id)
        leader_user_id = leader.user_id if leader and leader.total_xp > 0 else None
        if not first_place_changed(settings.first_place_user_id, leader_user_id):
            return

        await self._remove_first_place_role(guild, settings.first_place_user_id, settings.first_place_role_id)

        role = guild.get_role(settings.first_place_role_id)
        if role is not None and leader_user_id is not None and self._can_manage_role(guild, role):
            member = guild.get_member(leader_user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(leader_user_id)
                except discord.HTTPException:
                    member = None

            if member is not None:
                try:
                    await member.add_roles(role, reason="Leveling first place role")
                except discord.HTTPException:
                    LOGGER.exception("Failed to add first place role %s to user %s", role.id, leader_user_id)

        await self.repository.update_first_place_user(guild.id, leader_user_id)

    async def _remove_first_place_role(self, guild: discord.Guild, user_id: int | None, role_id: int | None) -> None:
        if user_id is None or role_id is None:
            return

        role = guild.get_role(role_id)
        member = guild.get_member(user_id)
        if role is None or member is None or role not in member.roles or not self._can_manage_role(guild, role):
            return

        try:
            await member.remove_roles(role, reason="Leveling first place role moved")
        except discord.HTTPException:
            LOGGER.exception("Failed to remove first place role %s from user %s", role_id, user_id)

    async def _send_levelup(self, member: discord.Member, settings: LevelingSettings, change: XpChange) -> None:
        if settings.levelup_channel_id is None:
            return

        channel = member.guild.get_channel(settings.levelup_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return

        text = _format_levelup_message(member, settings, change)
        try:
            await channel.send(text)
        except discord.HTTPException:
            LOGGER.exception("Failed to send levelup message in channel %s", settings.levelup_channel_id)

    async def _fetch_reaction_message(self, payload: discord.RawReactionActionEvent) -> discord.Message | None:
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
            except discord.HTTPException:
                return None

        fetch_message = getattr(channel, "fetch_message", None)
        if fetch_message is None:
            return None

        try:
            return await fetch_message(payload.message_id)
        except discord.HTTPException:
            return None

    async def _is_user_bot(self, payload: discord.RawReactionActionEvent) -> bool:
        if payload.member is not None:
            return payload.member.bot

        try:
            user = await self.bot.fetch_user(payload.user_id)
        except discord.HTTPException:
            return False

        return user.bot

    async def _sync_guild_voice_sessions(self, guild: discord.Guild) -> None:
        settings = await self.repository.get_settings(guild.id)
        if not settings.enabled or settings.voice_xp_per_minute <= 0:
            return

        voice_channels = list(guild.voice_channels) + list(guild.stage_channels)
        for channel in voice_channels:
            if channel == guild.afk_channel:
                continue

            for member in channel.members:
                if not member.bot:
                    await self.repository.upsert_voice_session(guild.id, member.id, channel.id)

    async def _require_guild(self, interaction: discord.Interaction, *, ephemeral: bool = True) -> discord.Guild | None:
        if interaction.guild is not None:
            return interaction.guild

        await interaction.response.send_message("Эта команда работает только на сервере.", ephemeral=ephemeral)
        return None

    def _can_manage_role_id(self, guild: discord.Guild, role_id: int) -> bool:
        role = guild.get_role(role_id)
        return bool(role and self._can_manage_role(guild, role))

    def _can_manage_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        me = guild.me
        return bool(me and me.guild_permissions.manage_roles and role < me.top_role and not role.managed)


def _has_voice_company(channel: discord.abc.GuildChannel) -> bool:
    members = getattr(channel, "members", [])
    return sum(1 for member in members if not member.bot) >= 2


def _parse_duration(raw: str | None) -> datetime | None:
    if raw is None or not raw.strip():
        return None

    match = DURATION_PATTERN.match(raw.strip())
    if match is None:
        return None

    amount = int(match.group("amount"))
    if amount <= 0:
        return None

    unit = match.group("unit").lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return datetime.now(UTC) + timedelta(seconds=amount * multipliers[unit])


def _format_levelup_message(member: discord.Member, settings: LevelingSettings, change: XpChange) -> str:
    values = {
        "user": member.mention,
        "level": str(change.new_level),
        "xp": str(change.new_total_xp),
        "guild": member.guild.name,
    }

    text = settings.levelup_message
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)

    return text


def _channel_text(channel_id: int | None) -> str:
    return "off" if channel_id is None else f"<#{channel_id}>"


def _role_text(role_id: int | None) -> str:
    return "off" if role_id is None else f"<@&{role_id}>"


async def _create_pool(database_url: str) -> asyncpg.Pool:
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            return await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        except (OSError, asyncpg.PostgresError) as exc:
            last_error = exc
            LOGGER.warning("PostgreSQL connection attempt %s failed: %s", attempt, exc)
            await asyncio.sleep(attempt * 2)

    raise RuntimeError("Could not connect to PostgreSQL for leveling.") from last_error


async def setup(bot: commands.Bot) -> None:
    database_url = getattr(bot.settings, "database_url", "")
    pool = await _create_pool(database_url)
    repository = LevelingRepository(pool)
    await repository.init_schema()
    await bot.add_cog(Leveling(bot, repository, pool))
