from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from siri_bot.bunker.board import render_board_png
from siri_bot.bunker.content import BUILTIN_PACK
from siri_bot.bunker.engine import (
    assign_cards,
    can_start_game,
    final_epilogue,
    format_card,
    generate_profile,
    normalize_settings,
    phase_deadline,
    pick_chaos_event,
    apply_chaos_to_resources,
    recommended_rounds,
    reveal_stat,
    selectable_reveal_stats,
    should_enter_final,
    tally_votes,
)
from siri_bot.bunker.models import (
    BunkerGame,
    BunkerPlayer,
    BunkerProfile,
    BunkerSettings,
    CARD_STAT_LABELS,
    GameMode,
    GameState,
    Vote,
    VotePolicy,
)
from siri_bot.bunker.permissions import (
    build_private_text_overwrites,
    build_private_voice_overwrites,
    grant_member_access,
)
from siri_bot.bunker.repository import ActiveBunkerGameError, BunkerRepository
from siri_bot.checks import admin_only


LOGGER = logging.getLogger(__name__)

SETUP_BUILD_ID = "siri:bunker:setup:build"
SETUP_JOIN_ID = "siri:bunker:setup:join"
SETUP_SETTINGS_ID = "siri:bunker:setup:settings"
SETUP_RULES_ID = "siri:bunker:setup:rules"
SETUP_PACKS_ID = "siri:bunker:setup:packs"

GAME_JOIN_ID = "siri:bunker:game:join"
GAME_READY_ID = "siri:bunker:game:ready"
GAME_START_ID = "siri:bunker:game:start"
GAME_LEAVE_ID = "siri:bunker:game:leave"
GAME_CARD_ID = "siri:bunker:game:card"
GAME_REVEAL_ID = "siri:bunker:game:reveal"
GAME_ACTION_ID = "siri:bunker:game:action"
GAME_VOTE_ID = "siri:bunker:game:vote"
GAME_RULES_ID = "siri:bunker:game:rules"
GAME_CHAOS_ID = "siri:bunker:game:chaos"


class Bunker(commands.Cog):
    bunker_group = app_commands.Group(name="bunker", description="Команды игры Бункер.")

    def __init__(self, bot: commands.Bot, repository: BunkerRepository, pool: asyncpg.Pool) -> None:
        self.bot = bot
        self.repository = repository
        self.pool = pool
        self.bot.add_view(BunkerSetupView(self))
        self.bot.add_view(BunkerGameView(self))
        self.phase_tick.start()

    def cog_unload(self) -> None:
        self.phase_tick.cancel()
        asyncio.create_task(self.pool.close())

    @app_commands.command(name="createbunker", description="Отправить setup-панель Бункера в выбранный канал комнаты.")
    @app_commands.describe(channel="Существующий текстовый канал комнаты, например БУНКЕР - КОМНАТА 1")
    @admin_only()
    async def createbunker(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild = interaction.guild
        if guild is None or channel.guild.id != guild.id:
            await interaction.response.send_message("Выбери текстовый канал на этом же сервере.", ephemeral=True)
            return

        embed = _setup_embed(channel.name)
        message = await channel.send(embed=embed, view=BunkerSetupView(self))
        await self.repository.upsert_room_setup(
            guild_id=guild.id,
            setup_channel_id=channel.id,
            category_id=channel.category_id,
            setup_message_id=message.id,
            room_name=channel.name,
        )
        await interaction.response.send_message(f"Панель Бункера отправлена в {channel.mention}.", ephemeral=True)

    @bunker_group.command(name="card", description="Показать свою карточку в текущей партии.")
    async def card_command(self, interaction: discord.Interaction) -> None:
        await self.show_card(interaction)

    @bunker_group.command(name="reveal", description="Раскрыть характеристику персонажа.")
    async def reveal_command(self, interaction: discord.Interaction) -> None:
        await self.show_reveal_menu(interaction)

    @bunker_group.command(name="vote", description="Проголосовать в текущем раунде.")
    async def vote_command(self, interaction: discord.Interaction) -> None:
        await self.show_vote_menu(interaction)

    @bunker_group.command(name="action", description="Использовать одноразовое спец-действие.")
    async def action_command(self, interaction: discord.Interaction) -> None:
        await self.show_action_menu(interaction)

    @bunker_group.command(name="settings", description="Показать настройки текущей партии.")
    async def settings_command(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        await interaction.response.send_message(embed=_settings_embed(game.settings), ephemeral=True)

    @bunker_group.command(name="pause", description="Поставить текущую партию на паузу.")
    async def pause_command(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None or not await self._require_host_or_admin(interaction, game):
            return

        if game.paused_at is not None:
            await interaction.response.send_message("Партия уже на паузе.", ephemeral=True)
            return

        paused = await self.repository.set_game_state(
            game.id,
            game.state,
            round_number=game.round_number,
            phase_started_at=game.phase_started_at,
            phase_ends_at=game.phase_ends_at,
            paused_at=datetime.now(UTC),
        )
        await self.repository.add_event(paused.id, paused.round_number, "pause", "Хост поставил бункер на паузу.")
        await self.refresh_game_message(paused.id)
        await interaction.response.send_message("Пауза включена.", ephemeral=True)

    @bunker_group.command(name="resume", description="Снять текущую партию с паузы.")
    async def resume_command(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None or not await self._require_host_or_admin(interaction, game):
            return

        if game.paused_at is None:
            await interaction.response.send_message("Партия не на паузе.", ephemeral=True)
            return

        now = datetime.now(UTC)
        remaining = 60
        if game.phase_ends_at is not None:
            remaining = max(30, int((game.phase_ends_at - game.paused_at).total_seconds()))
        resumed = await self.repository.set_game_state(
            game.id,
            game.state,
            round_number=game.round_number,
            phase_started_at=now,
            phase_ends_at=now + timedelta(seconds=remaining) if game.phase_ends_at else None,
            paused_at=None,
        )
        await self.repository.add_event(resumed.id, resumed.round_number, "resume", "Бункер снят с паузы.")
        await self.refresh_game_message(resumed.id)
        await interaction.response.send_message("Продолжаем.", ephemeral=True)

    @bunker_group.command(name="end", description="Завершить текущую партию.")
    async def end_command(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None or not await self._require_host_or_admin(interaction, game):
            return

        await self.repository.add_event(game.id, game.round_number, "end", "Партия завершена вручную.")
        await self.repository.finish_game(game.id)
        await self.refresh_game_message(game.id)
        await self.refresh_setup_message(game)
        await interaction.response.send_message("Партия завершена. Временные каналы оставлены на месте.", ephemeral=True)

    @bunker_group.command(name="packs", description="Показать встроенный контент-пак Бункера.")
    async def packs_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_packs_embed(), ephemeral=True)

    @bunker_group.command(name="invite", description="Пригласить игрока в приватный бункер.")
    @app_commands.describe(user="Кого пригласить")
    async def invite_command(self, interaction: discord.Interaction, user: discord.Member) -> None:
        game = await self._require_game_channel(interaction)
        if game is None or not await self._require_host_or_admin(interaction, game):
            return

        await self.repository.add_or_restore_player(game.id, user.id, user.display_name)
        text_channel = self.bot.get_channel(game.game_text_channel_id or 0)
        voice_channel = self.bot.get_channel(game.voice_channel_id or 0)
        await grant_member_access(
            text_channel if isinstance(text_channel, discord.TextChannel) else None,
            voice_channel if isinstance(voice_channel, discord.VoiceChannel) else None,
            user,
        )
        await self.repository.add_event(game.id, game.round_number, "invite", f"{user.mention} приглашен в бункер.")
        await self.refresh_game_message(game.id)
        await interaction.response.send_message(f"{user.mention} получил доступ к бункеру.", ephemeral=True)

    async def build_bunker(self, interaction: discord.Interaction) -> None:
        setup = await self._setup_from_interaction_message(interaction)
        if setup is None:
            await interaction.response.send_message("Не нашел setup этой комнаты. Создай панель через /createbunker заново.", ephemeral=True)
            return

        active = await self.repository.get_active_game_by_setup(setup.id)
        if active is not None:
            await interaction.response.send_message("В этой комнате уже построен активный бункер.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Бункер можно строить только на сервере.", ephemeral=True)
            return

        setup_channel = guild.get_channel(setup.setup_channel_id)
        if not isinstance(setup_channel, discord.TextChannel):
            await interaction.response.send_message("Setup-канал больше недоступен.", ephemeral=True)
            return

        settings = normalize_settings(await self.repository.get_draft(setup.id, interaction.user.id))
        room_number = _room_number(setup.room_name, setup.id)
        text_name = f"бункер-комната-{room_number}"
        voice_name = f"Собрание бункера {room_number}"
        category = setup_channel.category

        text_channel: discord.TextChannel | None = None
        voice_channel: discord.VoiceChannel | None = None
        try:
            text_channel = await guild.create_text_channel(
                text_name,
                category=category,
                overwrites=build_private_text_overwrites(guild, [interaction.user]),
                reason="Bunker room built",
            )
            voice_channel = await guild.create_voice_channel(
                voice_name,
                category=category,
                overwrites=build_private_voice_overwrites(guild, [interaction.user]),
                reason="Bunker room built",
            )
            game = await self.repository.create_game(
                setup=setup,
                host_id=interaction.user.id,
                settings=settings,
                text_channel_id=text_channel.id,
                voice_channel_id=voice_channel.id,
                host_display_name=interaction.user.display_name,
            )
        except ActiveBunkerGameError:
            if text_channel is not None:
                await text_channel.delete(reason="Bunker duplicate build rollback")
            if voice_channel is not None:
                await voice_channel.delete(reason="Bunker duplicate build rollback")
            await interaction.response.send_message("В этой комнате уже есть активная партия.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                "Не хватает прав Discord. Нужны Manage Channels и права на создание text/voice каналов.",
                ephemeral=True,
            )
            return

        board_message = await text_channel.send(embed=_game_embed(game, await self.repository.list_players(game.id)), view=BunkerGameView(self))
        await self.repository.set_board_message(game.id, board_message.id)
        await self.repository.add_event(game.id, game.round_number, "build", f"{interaction.user.mention} построил(а) бункер.")
        await self.refresh_game_message(game.id)
        await self.refresh_setup_message(game)

        moved = await self._move_member_to_voice(interaction.user, voice_channel)
        suffix = "Я перенес тебя в голосовой." if moved else f"Я открыл доступ к {voice_channel.mention}; зайди туда вручную, если сейчас не был в voice."
        await interaction.response.send_message(
            f"Бункер построен: {text_channel.mention}. {suffix}",
            ephemeral=True,
        )

    async def join_from_setup(self, interaction: discord.Interaction) -> None:
        setup = await self._setup_from_interaction_message(interaction)
        if setup is None:
            await interaction.response.send_message("Эта панель не привязана к комнате.", ephemeral=True)
            return

        game = await self.repository.get_active_game_by_setup(setup.id)
        if game is None:
            await interaction.response.send_message("Бункер еще не построен. Сначала нажмите 'Построить бункер'.", ephemeral=True)
            return

        await self.join_game(interaction, game)

    async def join_from_game(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        await self.join_game(interaction, game)

    async def join_game(self, interaction: discord.Interaction, game: BunkerGame) -> None:
        if game.state != GameState.LOBBY:
            await interaction.response.send_message("Вход уже закрыт: игра стартовала.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Зайти в бункер можно только на сервере.", ephemeral=True)
            return

        players = await self.repository.list_players(game.id)
        existing = next((player for player in players if player.user_id == interaction.user.id), None)
        if existing is None and len(players) >= game.settings.slots:
            await interaction.response.send_message("Бункер заполнен.", ephemeral=True)
            return

        if not game.settings.is_public and existing is None and interaction.user.id != game.host_id:
            await interaction.response.send_message("Это приватный бункер. Попроси хоста пригласить тебя.", ephemeral=True)
            return

        player = await self.repository.add_or_restore_player(game.id, interaction.user.id, interaction.user.display_name)
        text_channel = await self._fetch_text_channel(game.game_text_channel_id)
        voice_channel = await self._fetch_voice_channel(game.voice_channel_id)
        await grant_member_access(text_channel, voice_channel, interaction.user)
        moved = await self._move_member_to_voice(interaction.user, voice_channel)

        if player.is_host:
            message = "Ты уже хост этого бункера."
        else:
            message = f"Ты в бункере. Перейди в {text_channel.mention if text_channel else 'игровой чат'} и нажми 'Готов'."
        if voice_channel is not None:
            message += f" Голосовой: {voice_channel.mention}."
        if moved:
            message += " Я перенес тебя в голосовой."

        await self.repository.add_event(game.id, game.round_number, "join", f"{interaction.user.mention} зашел(ла) в бункер.")
        await self.refresh_game_message(game.id)
        await interaction.response.send_message(message, ephemeral=True)

    async def mark_ready(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        player = await self._require_player(interaction, game)
        if player is None:
            return

        await self.repository.set_ready(game.id, interaction.user.id, ready=True)
        await self.repository.add_event(game.id, game.round_number, "ready", f"{interaction.user.mention} готов(а).")
        await self.refresh_game_message(game.id)
        await interaction.response.send_message("Готовность принята.", ephemeral=True)

    async def leave_game(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        if interaction.user.id == game.host_id:
            await interaction.response.send_message("Хост не может выйти из своего бункера. Можно завершить игру через /bunker end.", ephemeral=True)
            return

        await self.repository.mark_left(game.id, interaction.user.id)
        await self.repository.add_event(game.id, game.round_number, "leave", f"{interaction.user.mention} вышел(ла) из бункера.")
        await self.refresh_game_message(game.id)
        await interaction.response.send_message("Ты вышел из бункера.", ephemeral=True)

    async def start_game(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None or not await self._require_host_or_admin(interaction, game):
            return

        if game.state != GameState.LOBBY:
            await interaction.response.send_message("Игра уже стартовала.", ephemeral=True)
            return

        players = await self.repository.list_players(game.id)
        ok, reason = can_start_game(players)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        rng = random.Random()
        profile = generate_profile(game.settings, rng)
        await self.repository.set_profile(game.id, profile)
        cards = assign_cards(players, game.settings, rng)
        await self.repository.assign_cards(game.id, cards)
        now = datetime.now(UTC)
        started = await self.repository.set_game_state(
            game.id,
            GameState.REVEAL_PHASE,
            round_number=1,
            phase_started_at=now,
            phase_ends_at=phase_deadline(game.settings, GameState.REVEAL_PHASE, now),
            paused_at=None,
        )
        await self.repository.add_event(started.id, started.round_number, "start", "Бункер закрыт. Карточки выданы, начинается раскрытие.")
        if game.settings.explain_for_newbies:
            await self.repository.add_event(started.id, started.round_number, "tutorial", "Фаза раскрытия: нажми 'Раскрыть стату' и выбери характеристику.")
        await self.refresh_game_message(started.id)
        await interaction.response.send_message("Игра началась. Карточки доступны через 'Моя карточка'.", ephemeral=True)

    async def show_card(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        player = await self._require_player(interaction, game)
        if player is None:
            return

        if player.card is None:
            await interaction.response.send_message("Карточка появится после старта игры.", ephemeral=True)
            return

        embed = discord.Embed(title="Твоя карточка Бункера", description=format_card(player.card), color=discord.Color.dark_teal())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def show_reveal_menu(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        if game.state != GameState.REVEAL_PHASE:
            await interaction.response.send_message("Сейчас не фаза раскрытия.", ephemeral=True)
            return

        player = await self._require_player(interaction, game)
        if player is None:
            return

        if player.is_eliminated:
            await interaction.response.send_message("Выгнанные игроки уже не раскрывают характеристики.", ephemeral=True)
            return

        stats = selectable_reveal_stats(player)
        if not stats:
            await interaction.response.send_message("Ты уже раскрыл все обычные характеристики.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Выбери характеристику для публичного раскрытия.",
            view=BunkerRevealView(self, game.id, interaction.user.id, stats),
            ephemeral=True,
        )

    async def reveal_selected_stat(self, interaction: discord.Interaction, game_id: int, user_id: int, stat: str) -> None:
        if interaction.user.id != user_id:
            await interaction.response.send_message("Это меню не для тебя.", ephemeral=True)
            return

        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.send_message("Партия не найдена.", ephemeral=True)
            return

        player = await self.repository.get_player(game_id, user_id)
        if player is None:
            await interaction.response.send_message("Ты не участник этой партии.", ephemeral=True)
            return

        ok, message = reveal_stat(player, stat)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return

        await self.repository.reveal_stat(game_id, user_id, stat)
        await self.repository.add_event(game_id, game.round_number, "reveal", message)
        await self.refresh_game_message(game_id)
        await interaction.response.edit_message(content="Раскрыто.", view=None)

    async def show_vote_menu(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        if game.state != GameState.VOTING_PHASE:
            await interaction.response.send_message("Сейчас не фаза голосования.", ephemeral=True)
            return

        player = await self._require_player(interaction, game)
        if player is None or player.is_eliminated:
            await interaction.response.send_message("Голосовать могут только живые участники.", ephemeral=True)
            return

        players = [player for player in await self.repository.list_players(game.id) if player.is_alive]
        await interaction.response.send_message(
            "Кого выгнать из бункера?",
            view=BunkerVoteView(self, game, players, interaction.user.id),
            ephemeral=True,
        )

    async def save_vote(self, interaction: discord.Interaction, game_id: int, voter_id: int, raw_target: str) -> None:
        if interaction.user.id != voter_id:
            await interaction.response.send_message("Это меню не для тебя.", ephemeral=True)
            return

        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.send_message("Партия не найдена.", ephemeral=True)
            return

        if raw_target == "abstain":
            vote = Vote(game_id=game.id, round_number=game.round_number, voter_id=voter_id, target_user_id=None, is_abstain=True)
            message = "Ты воздержался."
        else:
            target_id = int(raw_target)
            vote = Vote(game_id=game.id, round_number=game.round_number, voter_id=voter_id, target_user_id=target_id, is_abstain=False)
            message = f"Голос принят против <@{target_id}>."

        await self.repository.save_vote(vote)
        await interaction.response.edit_message(content=message, view=None)

    async def show_action_menu(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        player = await self._require_player(interaction, game)
        if player is None:
            return

        if player.card is None:
            await interaction.response.send_message("Спец-действие появится после выдачи карточки.", ephemeral=True)
            return

        if player.used_special_action:
            await interaction.response.send_message("Эта карта уже использована.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"Твое действие: {player.card.special_action}. Использовать сейчас?",
            view=BunkerActionView(self, game.id, player.user_id),
            ephemeral=True,
        )

    async def use_special_action(self, interaction: discord.Interaction, game_id: int, user_id: int) -> None:
        if interaction.user.id != user_id:
            await interaction.response.send_message("Это действие не для тебя.", ephemeral=True)
            return

        game = await self.repository.get_game(game_id)
        player = await self.repository.get_player(game_id, user_id)
        if game is None or player is None or player.card is None:
            await interaction.response.send_message("Не нашел действие.", ephemeral=True)
            return

        if player.used_special_action:
            await interaction.response.send_message("Эта карта уже использована.", ephemeral=True)
            return

        event = await self._apply_special_action(game, player)
        await self.repository.mark_special_used(game.id, player.user_id)
        await self.repository.add_event(game.id, game.round_number, "action", event)
        await self.refresh_game_message(game.id)
        await interaction.response.edit_message(content="Действие использовано.", view=None)

    async def trigger_chaos(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        if game.state != GameState.CHAOS_PHASE:
            await interaction.response.send_message("Сейчас не фаза хаоса.", ephemeral=True)
            return

        await self._trigger_chaos_event(game)
        await self.refresh_game_message(game.id)
        await interaction.response.send_message("Хаос зафиксирован на табло.", ephemeral=True)

    async def show_rules(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_rules_embed(), ephemeral=True)

    async def show_packs(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_packs_embed(), ephemeral=True)

    async def show_setup_settings(self, interaction: discord.Interaction) -> None:
        setup = await self._setup_from_interaction_message(interaction)
        if setup is None:
            await interaction.response.send_message("Эта панель не привязана к комнате.", ephemeral=True)
            return

        settings = normalize_settings(await self.repository.get_draft(setup.id, interaction.user.id))
        await self.repository.save_draft(setup.id, interaction.user.id, settings)
        await interaction.response.send_message(
            embed=_settings_embed(settings),
            view=BunkerSettingsView(self, setup.id, interaction.user.id, settings),
            ephemeral=True,
        )

    async def update_draft_settings(
        self,
        interaction: discord.Interaction,
        setup_id: int,
        user_id: int,
        settings: BunkerSettings,
    ) -> None:
        if interaction.user.id != user_id:
            await interaction.response.send_message("Эти настройки открыты другим пользователем.", ephemeral=True)
            return

        settings = normalize_settings(settings)
        await self.repository.save_draft(setup_id, user_id, settings)
        await interaction.response.edit_message(
            embed=_settings_embed(settings),
            view=BunkerSettingsView(self, setup_id, user_id, settings),
        )

    @tasks.loop(seconds=10.0)
    async def phase_tick(self) -> None:
        for game in await self.repository.list_due_games():
            try:
                await self.advance_phase(game)
            except Exception:
                LOGGER.exception("Failed to advance bunker game %s", game.id)

    @phase_tick.before_loop
    async def before_phase_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def advance_phase(self, game: BunkerGame) -> None:
        now = datetime.now(UTC)
        if game.state == GameState.REVEAL_PHASE:
            next_game = await self.repository.set_game_state(
                game.id,
                GameState.DISCUSSION_PHASE,
                round_number=game.round_number,
                phase_started_at=now,
                phase_ends_at=phase_deadline(game.settings, GameState.DISCUSSION_PHASE, now),
                paused_at=None,
            )
            if game.settings.explain_for_newbies:
                await self.repository.add_event(game.id, game.round_number, "tutorial", "Фаза обсуждения: спорьте, защищайтесь и ищите пользу каждого игрока.")
            await self.refresh_game_message(next_game.id)
            return

        if game.state == GameState.DISCUSSION_PHASE:
            next_game = await self.repository.set_game_state(
                game.id,
                GameState.CHAOS_PHASE,
                round_number=game.round_number,
                phase_started_at=now,
                phase_ends_at=phase_deadline(game.settings, GameState.CHAOS_PHASE, now),
                paused_at=None,
            )
            await self._trigger_chaos_event(next_game)
            await self.refresh_game_message(next_game.id)
            return

        if game.state == GameState.CHAOS_PHASE:
            next_game = await self.repository.set_game_state(
                game.id,
                GameState.VOTING_PHASE,
                round_number=game.round_number,
                phase_started_at=now,
                phase_ends_at=phase_deadline(game.settings, GameState.VOTING_PHASE, now),
                paused_at=None,
            )
            if game.settings.explain_for_newbies:
                await self.repository.add_event(game.id, game.round_number, "tutorial", "Фаза голосования: нажмите 'Голосовать' и выберите кандидата.")
            await self.refresh_game_message(next_game.id)
            return

        if game.state == GameState.VOTING_PHASE:
            players = await self.repository.list_players(game.id)
            votes = await self.repository.list_votes(game.id, game.round_number)
            eliminated_id, event = tally_votes(players, votes, game.settings.missing_vote_policy)
            if eliminated_id is not None:
                eliminated = next((player for player in players if player.user_id == eliminated_id), None)
                if eliminated and eliminated.immune_round == game.round_number:
                    event = f"{event} Но иммунитет спасает <@{eliminated_id}>."
                else:
                    await self.repository.mark_eliminated(game.id, eliminated_id)
            await self.repository.add_event(game.id, game.round_number, "vote", event)
            next_game = await self.repository.set_game_state(
                game.id,
                GameState.ELIMINATION_PHASE,
                round_number=game.round_number,
                phase_started_at=now,
                phase_ends_at=phase_deadline(game.settings, GameState.ELIMINATION_PHASE, now),
                paused_at=None,
            )
            await self.refresh_game_message(next_game.id)
            return

        if game.state == GameState.ELIMINATION_PHASE:
            players = await self.repository.list_players(game.id)
            if should_enter_final(game, players):
                await self.finish_with_epilogue(game)
                return

            next_round = game.round_number + 1
            next_game = await self.repository.set_game_state(
                game.id,
                GameState.REVEAL_PHASE,
                round_number=next_round,
                phase_started_at=now,
                phase_ends_at=phase_deadline(game.settings, GameState.REVEAL_PHASE, now),
                paused_at=None,
            )
            await self.repository.add_event(game.id, next_round, "round", f"Начался раунд {next_round}.")
            await self.refresh_game_message(next_game.id)

    async def finish_with_epilogue(self, game: BunkerGame) -> None:
        players = await self.repository.list_players(game.id)
        epilogue = final_epilogue(game, players)
        await self.repository.add_event(game.id, game.round_number, "final", epilogue)
        await self.repository.set_game_state(
            game.id,
            GameState.FINAL_PHASE,
            round_number=game.round_number,
            phase_started_at=datetime.now(UTC),
            phase_ends_at=None,
            paused_at=None,
        )
        await self.refresh_game_message(game.id)
        await self.repository.finish_game(game.id)
        await self.refresh_setup_message(game)

    async def refresh_game_message(self, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None or game.board_message_id is None:
            return

        channel = await self._fetch_text_channel(game.game_text_channel_id)
        if channel is None:
            return

        try:
            message = await channel.fetch_message(game.board_message_id)
        except discord.HTTPException:
            return

        players = await self.repository.list_players(game.id)
        embed = _game_embed(game, players)
        try:
            image_bytes = render_board_png(game, players)
            file = discord.File(BytesIO(image_bytes), filename="bunker_board.png")
            embed.set_image(url="attachment://bunker_board.png")
            await message.edit(embed=embed, attachments=[file], view=BunkerGameView(self))
        except Exception:
            LOGGER.exception("Could not render bunker board for game %s", game.id)
            await message.edit(embed=embed, view=BunkerGameView(self))

    async def refresh_setup_message(self, game: BunkerGame) -> None:
        if game.setup_message_id is None:
            return

        channel = await self._fetch_text_channel(game.setup_channel_id)
        if channel is None:
            return

        try:
            message = await channel.fetch_message(game.setup_message_id)
        except discord.HTTPException:
            return

        active = await self.repository.get_active_game_by_setup(game.setup_id)
        embed = _setup_embed(channel.name, active_game=active)
        await message.edit(embed=embed, view=BunkerSetupView(self))

    async def _trigger_chaos_event(self, game: BunkerGame) -> None:
        event = pick_chaos_event()
        if game.profile is not None:
            profile = BunkerProfile(
                apocalypse=game.profile.apocalypse,
                layout=game.profile.layout,
                defect=game.profile.defect,
                resources=apply_chaos_to_resources(game.profile.resources),
            )
            await self.repository.set_profile(game.id, profile)
        await self.repository.add_event(game.id, game.round_number, "chaos", event)

    async def _apply_special_action(self, game: BunkerGame, player: BunkerPlayer) -> str:
        action = player.card.special_action if player.card else ""
        players = await self.repository.list_players(game.id)
        alive_targets = [target for target in players if target.is_alive and target.user_id != player.user_id]
        rng = random.Random()
        target = rng.choice(alive_targets) if alive_targets else None

        if action == "Детектор кринжа" and target is not None:
            stats = selectable_reveal_stats(target)
            if stats:
                stat = rng.choice(stats)
                await self.repository.reveal_stat(game.id, target.user_id, stat)
                return f"{player.display_name} включает Детектор кринжа: {target.display_name} раскрывает {CARD_STAT_LABELS[stat]}."
        if action == "Адвокат дьявола" and target is not None:
            await self.pool.execute(
                "UPDATE bunker_players SET immune_round = $3 WHERE game_id = $1 AND user_id = $2",
                game.id,
                target.user_id,
                game.round_number,
            )
            return f"{player.display_name} дает иммунитет игроку {target.display_name} на этот раунд."
        if action == "Смена легенды":
            await self.pool.execute(
                """
                UPDATE bunker_players
                SET revealed_stats = '[]'::jsonb
                WHERE game_id = $1 AND user_id = $2
                """,
                game.id,
                player.user_id,
            )
            return f"{player.display_name} меняет легенду: раскрытые характеристики снова спрятаны."
        if action == "Украл ложку" and target is not None and target.card is not None and player.card is not None:
            new_player_card = replace(player.card, item=target.card.item)
            new_target_card = replace(target.card, item="Украденный предмет: ложка с чувством вины")
            await self.repository.assign_cards(game.id, {player.user_id: new_player_card, target.user_id: new_target_card})
            return f"{player.display_name} крадет предмет у {target.display_name}. Факт кражи публично раскрыт."
        if action == "Красная кнопка":
            await self._trigger_chaos_event(game)
            return f"{player.display_name} нажимает Красную кнопку. Бункер делает вид, что это было запланировано."
        if action == "Факт-чек" and target is not None and target.card is not None and rng.random() < 0.6:
            await self.repository.reveal_stat(game.id, target.user_id, "secret")
            return f"{player.display_name} проводит факт-чек: секрет {target.display_name} всплывает наружу."
        if action == "Тихий саботаж" and game.profile is not None:
            profile = BunkerProfile(
                apocalypse=game.profile.apocalypse,
                layout=game.profile.layout,
                defect=game.profile.defect,
                resources=replace(game.profile.resources, electricity=game.profile.resources.electricity - 8).clamp(),
            )
            await self.repository.set_profile(game.id, profile)
            return f"{player.display_name} устраивает тихий саботаж: электричество просело, личный бонус записан в легенду."
        if action == "Я передумал":
            return f"{player.display_name} активирует 'Я передумал': теперь можно изменить голос до конца фазы."

        return f"{player.display_name} использует {action}, но бункер отвечает загадочным скрипом."

    async def _require_game_channel(self, interaction: discord.Interaction) -> BunkerGame | None:
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("Не вижу канал этой команды.", ephemeral=True)
            return None

        game = await self.repository.get_active_game_by_text_channel(channel.id)
        if game is None:
            await interaction.response.send_message("Эта команда работает в игровом text-канале бункера.", ephemeral=True)
            return None

        return game

    async def _require_player(self, interaction: discord.Interaction, game: BunkerGame) -> BunkerPlayer | None:
        player = await self.repository.get_player(game.id, interaction.user.id)
        if player is None or not player.is_active:
            await interaction.response.send_message("Ты не участник этого бункера.", ephemeral=True)
            return None

        return player

    async def _require_host_or_admin(self, interaction: discord.Interaction, game: BunkerGame) -> bool:
        if interaction.user.id == game.host_id:
            return True

        user = interaction.user
        settings = getattr(interaction.client, "settings", None)
        if settings is not None and settings.owner_id and user.id == settings.owner_id:
            return True
        if isinstance(user, discord.Member):
            if user.guild_permissions.administrator:
                return True
            allowed_role_ids = getattr(settings, "admin_role_ids", frozenset()) if settings else frozenset()
            if any(role.id in allowed_role_ids for role in user.roles):
                return True

        await interaction.response.send_message("Это может сделать только хост бункера или админ.", ephemeral=True)
        return False

    async def _setup_from_interaction_message(self, interaction: discord.Interaction):
        message = interaction.message
        if message is not None:
            setup = await self.repository.get_setup_by_message(message.id)
            if setup is not None:
                return setup
        if interaction.channel is not None:
            return await self.repository.get_setup_by_channel(interaction.channel.id)
        return None

    async def _fetch_text_channel(self, channel_id: int | None) -> discord.TextChannel | None:
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _fetch_voice_channel(self, channel_id: int | None) -> discord.VoiceChannel | None:
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        return channel if isinstance(channel, discord.VoiceChannel) else None

    async def _move_member_to_voice(self, member: discord.Member, voice_channel: discord.VoiceChannel | None) -> bool:
        if voice_channel is None or member.voice is None or member.voice.channel is None:
            return False

        try:
            await member.move_to(voice_channel, reason="Bunker voice join")
        except discord.HTTPException:
            return False
        return True


class BunkerSetupView(discord.ui.View):
    def __init__(self, cog: Bunker) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Построить бункер", style=discord.ButtonStyle.primary, custom_id=SETUP_BUILD_ID)
    async def build(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.build_bunker(interaction)

    @discord.ui.button(label="Зайти в бункер", style=discord.ButtonStyle.success, custom_id=SETUP_JOIN_ID)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.join_from_setup(interaction)

    @discord.ui.button(label="Настроить бункер", style=discord.ButtonStyle.secondary, custom_id=SETUP_SETTINGS_ID)
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_setup_settings(interaction)

    @discord.ui.button(label="Как играть", style=discord.ButtonStyle.secondary, custom_id=SETUP_RULES_ID)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_rules(interaction)

    @discord.ui.button(label="Паки/контент", style=discord.ButtonStyle.secondary, custom_id=SETUP_PACKS_ID)
    async def packs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_packs(interaction)


class BunkerGameView(discord.ui.View):
    def __init__(self, cog: Bunker) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Зайти", style=discord.ButtonStyle.success, custom_id=GAME_JOIN_ID, row=0)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.join_from_game(interaction)

    @discord.ui.button(label="Готов", style=discord.ButtonStyle.primary, custom_id=GAME_READY_ID, row=0)
    async def ready(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.mark_ready(interaction)

    @discord.ui.button(label="Начать", style=discord.ButtonStyle.danger, custom_id=GAME_START_ID, row=0)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.start_game(interaction)

    @discord.ui.button(label="Покинуть", style=discord.ButtonStyle.secondary, custom_id=GAME_LEAVE_ID, row=0)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.leave_game(interaction)

    @discord.ui.button(label="Моя карточка", style=discord.ButtonStyle.secondary, custom_id=GAME_CARD_ID, row=1)
    async def card(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_card(interaction)

    @discord.ui.button(label="Раскрыть стату", style=discord.ButtonStyle.primary, custom_id=GAME_REVEAL_ID, row=1)
    async def reveal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_reveal_menu(interaction)

    @discord.ui.button(label="Использовать действие", style=discord.ButtonStyle.secondary, custom_id=GAME_ACTION_ID, row=1)
    async def action(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_action_menu(interaction)

    @discord.ui.button(label="Голосовать", style=discord.ButtonStyle.primary, custom_id=GAME_VOTE_ID, row=1)
    async def vote(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_vote_menu(interaction)

    @discord.ui.button(label="Правила", style=discord.ButtonStyle.secondary, custom_id=GAME_RULES_ID, row=2)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_rules(interaction)

    @discord.ui.button(label="Событие хаоса", style=discord.ButtonStyle.secondary, custom_id=GAME_CHAOS_ID, row=2)
    async def chaos(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.trigger_chaos(interaction)


class BunkerSettingsView(discord.ui.View):
    def __init__(self, cog: Bunker, setup_id: int, user_id: int, settings: BunkerSettings) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.setup_id = setup_id
        self.user_id = user_id
        self.settings = settings
        self.add_item(BunkerModeSelect(self))
        self.add_item(BunkerSlotsSelect(self))
        self.add_item(BunkerTimerSelect(self))
        self.add_item(BunkerRoundsSelect(self))

    @discord.ui.button(label="Публичный/приватный", style=discord.ButtonStyle.secondary)
    async def toggle_visibility(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.update_draft_settings(interaction, self.setup_id, self.user_id, replace(self.settings, is_public=not self.settings.is_public))

    @discord.ui.button(label="Подсказки новичкам", style=discord.ButtonStyle.secondary)
    async def toggle_newbies(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.update_draft_settings(
            interaction,
            self.setup_id,
            self.user_id,
            replace(self.settings, explain_for_newbies=not self.settings.explain_for_newbies),
        )

    @discord.ui.button(label="Пропущенный голос", style=discord.ButtonStyle.secondary)
    async def toggle_vote_policy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        next_policy = VotePolicy.RANDOM if self.settings.missing_vote_policy == VotePolicy.ABSTAIN else VotePolicy.ABSTAIN
        await self.cog.update_draft_settings(
            interaction,
            self.setup_id,
            self.user_id,
            replace(self.settings, missing_vote_policy=next_policy),
        )


class BunkerModeSelect(discord.ui.Select):
    def __init__(self, owner: BunkerSettingsView) -> None:
        self.owner = owner
        super().__init__(
            placeholder=f"Режим: {owner.settings.mode.value}",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=mode.value, value=mode.value, default=mode == owner.settings.mode) for mode in GameMode],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        mode = GameMode(self.values[0])
        rounds = recommended_rounds(self.owner.settings.slots, mode)
        await self.owner.cog.update_draft_settings(interaction, self.owner.setup_id, self.owner.user_id, replace(self.owner.settings, mode=mode, rounds=rounds))


class BunkerSlotsSelect(discord.ui.Select):
    def __init__(self, owner: BunkerSettingsView) -> None:
        self.owner = owner
        super().__init__(
            placeholder=f"Слоты: {owner.settings.slots}",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=str(slots), value=str(slots), default=slots == owner.settings.slots)
                for slots in range(6, 17)
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        slots = int(self.values[0])
        rounds = recommended_rounds(slots, self.owner.settings.mode)
        await self.owner.cog.update_draft_settings(
            interaction,
            self.owner.setup_id,
            self.owner.user_id,
            replace(self.owner.settings, slots=slots, rounds=rounds),
        )


class BunkerTimerSelect(discord.ui.Select):
    def __init__(self, owner: BunkerSettingsView) -> None:
        self.owner = owner
        values = (60, 90, 120, 180, 240, 300, 420)
        super().__init__(
            placeholder=f"Таймер: {owner.settings.timer_seconds} сек.",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=f"{seconds} сек.", value=str(seconds), default=seconds == owner.settings.timer_seconds)
                for seconds in values
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner.cog.update_draft_settings(
            interaction,
            self.owner.setup_id,
            self.owner.user_id,
            replace(self.owner.settings, timer_seconds=int(self.values[0])),
        )


class BunkerRoundsSelect(discord.ui.Select):
    def __init__(self, owner: BunkerSettingsView) -> None:
        self.owner = owner
        values = (3, 4, 5, 6)
        super().__init__(
            placeholder=f"Раунды: {owner.settings.rounds}",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=str(rounds), value=str(rounds), default=rounds == owner.settings.rounds)
                for rounds in values
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner.cog.update_draft_settings(
            interaction,
            self.owner.setup_id,
            self.owner.user_id,
            replace(self.owner.settings, rounds=int(self.values[0])),
        )


class BunkerRevealView(discord.ui.View):
    def __init__(self, cog: Bunker, game_id: int, user_id: int, stats: list[str]) -> None:
        super().__init__(timeout=900)
        self.add_item(BunkerRevealSelect(cog, game_id, user_id, stats))


class BunkerRevealSelect(discord.ui.Select):
    def __init__(self, cog: Bunker, game_id: int, user_id: int, stats: list[str]) -> None:
        self.cog = cog
        self.game_id = game_id
        self.user_id = user_id
        options = [discord.SelectOption(label=CARD_STAT_LABELS[stat], value=stat) for stat in stats]
        super().__init__(placeholder="Что раскрыть?", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.reveal_selected_stat(interaction, self.game_id, self.user_id, self.values[0])


class BunkerVoteView(discord.ui.View):
    def __init__(self, cog: Bunker, game: BunkerGame, players: list[BunkerPlayer], voter_id: int) -> None:
        super().__init__(timeout=900)
        self.add_item(BunkerVoteSelect(cog, game, players, voter_id))


class BunkerVoteSelect(discord.ui.Select):
    def __init__(self, cog: Bunker, game: BunkerGame, players: list[BunkerPlayer], voter_id: int) -> None:
        self.cog = cog
        self.game_id = game.id
        self.voter_id = voter_id
        options = [discord.SelectOption(label="Воздержаться", value="abstain")]
        for player in players[:24]:
            options.append(discord.SelectOption(label=player.display_name, value=str(player.user_id)))
        super().__init__(placeholder="Выбери голос", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.save_vote(interaction, self.game_id, self.voter_id, self.values[0])


class BunkerActionView(discord.ui.View):
    def __init__(self, cog: Bunker, game_id: int, user_id: int) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.game_id = game_id
        self.user_id = user_id

    @discord.ui.button(label="Использовать", style=discord.ButtonStyle.danger)
    async def use(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.use_special_action(interaction, self.game_id, self.user_id)


def _setup_embed(room_name: str, active_game: BunkerGame | None = None) -> discord.Embed:
    status = "Комната свободна. Нажми 'Построить бункер', чтобы стать хостом." if active_game is None else "Комната занята: идет набор или партия."
    embed = discord.Embed(
        title=f"Бункер - {room_name}",
        description=(
            "Party/RP-игра про выживание, сомнительные профессии и совет, который слишком долго спорит о пайках.\n\n"
            f"Статус: {status}"
        ),
        color=discord.Color.dark_teal(),
    )
    embed.add_field(name="Как начать", value="Настрой режим, построй бункер, дождись готовности игроков и запускай партию.", inline=False)
    embed.add_field(name="Voice", value="После входа бот откроет закрытый голосовой канал. Если ты уже в voice, он попробует перенести тебя.", inline=False)
    return embed


def _game_embed(game: BunkerGame, players: list[BunkerPlayer]) -> discord.Embed:
    alive = sum(1 for player in players if player.is_alive)
    ready = sum(1 for player in players if player.ready_at is not None and not player.is_host)
    non_hosts = sum(1 for player in players if not player.is_host and player.is_active)
    status = f"{game.state.value}, раунд {game.round_number}/{game.settings.rounds}"
    if game.phase_ends_at is not None and game.paused_at is None:
        status += f", до фазы: {discord.utils.format_dt(game.phase_ends_at, style='R')}"
    if game.paused_at is not None:
        status += ", пауза"

    embed = discord.Embed(title="Бункер", description=status, color=discord.Color.blurple())
    embed.add_field(name="Игроки", value=f"{len(players)}/{game.settings.slots}, живых: {alive}, готово: {ready}/{non_hosts}", inline=True)
    embed.add_field(name="Хост", value=f"<@{game.host_id}>", inline=True)
    embed.add_field(name="Режим", value=game.settings.mode.value, inline=True)
    if game.recent_events:
        embed.add_field(name="Последние события", value="\n".join(game.recent_events[-5:])[:1024], inline=False)
    else:
        embed.add_field(name="Лобби", value="Игроки заходят в бункер и нажимают 'Готов'.", inline=False)
    return embed


def _settings_embed(settings: BunkerSettings) -> discord.Embed:
    embed = discord.Embed(title="Настройки Бункера", color=discord.Color.dark_teal())
    embed.add_field(name="Тип", value="публичный" if settings.is_public else "приватный", inline=True)
    embed.add_field(name="Режим", value=settings.mode.value, inline=True)
    embed.add_field(name="Слоты", value=str(settings.slots), inline=True)
    embed.add_field(name="Раунды", value=str(settings.rounds), inline=True)
    embed.add_field(name="Таймер", value=f"{settings.timer_seconds} сек.", inline=True)
    embed.add_field(name="Подсказки", value="вкл" if settings.explain_for_newbies else "выкл", inline=True)
    embed.add_field(name="Нет голоса", value=settings.missing_vote_policy.value, inline=True)
    return embed


def _rules_embed() -> discord.Embed:
    embed = discord.Embed(title="Как играть в Бункер", color=discord.Color.gold())
    embed.description = (
        "1. Хост строит бункер и запускает набор.\n"
        "2. Игроки заходят, получают закрытый text/voice и нажимают 'Готов'.\n"
        "3. После старта каждый видит личную карточку только в ephemeral-ответе.\n"
        "4. В раундах раскрывайте характеристики, обсуждайте, переживайте хаос и голосуйте.\n"
        "5. После финального голосования бот пишет эпилог выживания."
    )
    return embed


def _packs_embed() -> discord.Embed:
    counts = BUILTIN_PACK.counts()
    lines = [f"{name}: {count}" for name, count in counts.items()]
    embed = discord.Embed(title="Встроенный контент-пак", description="\n".join(lines), color=discord.Color.green())
    return embed


def _room_number(name: str, fallback: int) -> str:
    match = re.search(r"(\d+)", name)
    return match.group(1) if match else str(fallback)


async def _create_pool(database_url: str) -> asyncpg.Pool:
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            return await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        except (OSError, asyncpg.PostgresError) as exc:
            last_error = exc
            LOGGER.warning("PostgreSQL connection attempt %s failed for bunker: %s", attempt, exc)
            await asyncio.sleep(attempt * 2)

    raise RuntimeError("Could not connect to PostgreSQL for bunker.") from last_error


async def setup(bot: commands.Bot) -> None:
    database_url = getattr(bot.settings, "database_url", "")
    pool = await _create_pool(database_url)
    repository = BunkerRepository(pool)
    await repository.init_schema()
    await bot.add_cog(Bunker(bot, repository, pool))

