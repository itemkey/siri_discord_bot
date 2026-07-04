from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from siri_bot.bunker.board import render_board_png
from siri_bot.bunker.content import (
    BUILTIN_PACK,
    PACK_FIELD_LABELS,
    PACK_FIELDS,
    ContentPack,
    merge_content_packs,
    normalize_pack_content,
)
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
    BunkerContentPack,
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
    build_admin_text_overwrites,
    build_admin_voice_overwrites,
    build_lobby_text_overwrites,
    build_private_text_overwrites,
    build_private_voice_overwrites,
    grant_member_access,
)
from siri_bot.bunker.repository import ActiveBunkerGameError, BunkerRepository
from siri_bot.checks import admin_only


LOGGER = logging.getLogger(__name__)

SETUP_BUILD_ID = "siri:bunker:setup:build"
SETUP_SETTINGS_ID = "siri:bunker:setup:settings"
SETUP_RULES_ID = "siri:bunker:setup:rules"
SETUP_PACKS_ID = "siri:bunker:setup:packs"

ADMIN_PANEL_SCREEN_LIST = "list"
ADMIN_PANEL_SCREEN_PACK = "pack"
ADMIN_PANEL_SCREEN_CATEGORY = "category"
ADMIN_PANEL_SCREEN_ACCESS = "access"

GAME_PANEL_ID = "siri:bunker:game:panel"
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
    opbunker_group = app_commands.Group(name="opbunker", description="Админ-настройки Бункера.")

    def __init__(self, bot: commands.Bot, repository: BunkerRepository, pool: asyncpg.Pool) -> None:
        self.bot = bot
        self.repository = repository
        self.pool = pool
        self._setup_private_panels: dict[tuple[int, int, int], Any] = {}
        self._game_private_panels: dict[tuple[int, int, int], Any] = {}
        self._admin_private_panels: dict[tuple[int, int, int], Any] = {}
        self.bot.add_view(BunkerSetupIdleView(self))
        self.bot.add_view(BunkerPublicGameView(self))
        self.phase_tick.start()

    def cog_unload(self) -> None:
        self.phase_tick.cancel()
        asyncio.create_task(self.pool.close())

    @app_commands.command(name="createbunker", description="Отправить setup-панель Бункера в выбранный канал комнаты.")
    @app_commands.describe(channel="Существующий текстовый канал комнаты, например БУНКЕР - КОМНАТА 1")
    @admin_only()
    async def createbunker(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if guild is None or channel.guild.id != guild.id:
            await interaction.followup.send("Выбери текстовый канал на этом же сервере.", ephemeral=True)
            return

        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None:
            await interaction.followup.send("Не вижу себя в списке участников сервера. Попробуй перезапустить бота.", ephemeral=True)
            return

        missing_permissions = _missing_setup_panel_permissions(channel.permissions_for(bot_member))
        if missing_permissions:
            await interaction.followup.send(
                "Не могу отправить панель в выбранный канал. "
                f"Не хватает прав: {', '.join(missing_permissions)}.",
                ephemeral=True,
            )
            return

        existing_setup = await self.repository.get_setup_by_channel(channel.id)
        embed = _setup_embed(channel.name)
        setup_message: discord.Message | None = None
        if existing_setup and existing_setup.setup_message_id is not None:
            try:
                setup_message = await channel.fetch_message(existing_setup.setup_message_id)
                await setup_message.edit(embed=embed, view=BunkerSetupIdleView(self))
            except discord.HTTPException:
                setup_message = None

        if setup_message is not None:
            await self._delete_duplicate_setup_panels(channel, keep_message_id=setup_message.id)
            await interaction.followup.send(f"Панель Бункера обновлена в {channel.mention}.", ephemeral=True)
            return

        try:
            message = await channel.send(embed=embed, view=BunkerSetupIdleView(self))
        except discord.Forbidden:
            await interaction.followup.send(
                "Discord не дал отправить панель в выбранный канал. "
                "Проверь права `View Channel`, `Send Messages` и `Embed Links`.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            LOGGER.exception("Failed to send bunker setup panel to channel %s", channel.id)
            await interaction.followup.send("Discord отклонил отправку панели. Подробность будет в `docker compose logs discord-bot`.", ephemeral=True)
            return

        try:
            await self.repository.upsert_room_setup(
                guild_id=guild.id,
                setup_channel_id=channel.id,
                category_id=channel.category_id,
                setup_message_id=message.id,
                room_name=channel.name,
            )
            await self._delete_duplicate_setup_panels(channel, keep_message_id=message.id)
        except asyncpg.PostgresError:
            LOGGER.exception("Failed to save bunker setup for channel %s", channel.id)
            try:
                await message.delete()
            except discord.HTTPException:
                LOGGER.info("Could not delete bunker setup panel after database failure.", exc_info=True)
            await interaction.followup.send(
                "Панель отправилась, но я не смог записать комнату в PostgreSQL. "
                "Проверь `docker compose logs --tail=200 discord-bot` и доступность базы.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(f"Панель Бункера отправлена в {channel.mention}.", ephemeral=True)

    @opbunker_group.command(name="role", description="Назначить роль операторов Бункера для тестов и админки.")
    @app_commands.describe(role="Роль, которая получит bunker-admin функции")
    @admin_only()
    async def opbunker_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        guild = interaction.guild
        if guild is None or role.guild.id != guild.id:
            await interaction.response.send_message("Выбери роль на этом же сервере.", ephemeral=True)
            return

        await self.repository.set_operator_role(guild.id, role.id)
        await interaction.response.send_message(f"Роль операторов Бункера: {role.mention}.", ephemeral=True)

    @app_commands.command(name="bunkeradminpanel", description="Открыть закрытую админ-панель Бункера для паков и тестов.")
    async def bunkeradminpanel(self, interaction: discord.Interaction) -> None:
        if not await self._is_bunker_admin_or_operator(interaction):
            await interaction.response.send_message("Эта панель доступна только админу сервера или operator-role Бункера.", ephemeral=True)
            return

        await self.open_bunker_admin_panel(interaction)

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
        await self.open_game_panel(interaction, screen="settings")

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
        await interaction.response.send_message("Партия завершена. Временные каналы оставлены на месте; удалить их можно через /bunker close.", ephemeral=True)

    @bunker_group.command(name="close", description="Закрыть временную комнату Бункера и удалить ее каналы.")
    async def close_command(self, interaction: discord.Interaction) -> None:
        game = await self._game_from_interaction_channel(interaction, include_finished=True)
        if game is None:
            await interaction.response.send_message("Эта команда работает во временном text-канале бункера.", ephemeral=True)
            return
        if not await self._is_host_or_admin_user(interaction, game):
            await interaction.response.send_message("Закрыть бункер может только его хост, админ или оператор.", ephemeral=True)
            return

        await interaction.response.send_message(
            embed=_status_embed("Закрыть этот бункер и удалить временные text/voice каналы?"),
            view=BunkerCloseConfirmView(self, game.id),
            ephemeral=True,
        )

    @bunker_group.command(name="packs", description="Показать встроенный контент-пак Бункера.")
    async def packs_command(self, interaction: discord.Interaction) -> None:
        await self.open_game_panel(interaction, screen="packs")

    async def open_bunker_admin_panel(
        self,
        interaction: discord.Interaction,
        *,
        screen: str = ADMIN_PANEL_SCREEN_LIST,
        pack_id: int | None = None,
        field: str | None = None,
        status: str | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Админ-панель Бункера работает только на сервере.", ephemeral=True)
            return
        if not await self._is_bunker_admin_or_operator(interaction):
            await interaction.response.send_message("Эта панель доступна только админу сервера или operator-role Бункера.", ephemeral=True)
            return

        guild_settings = await self.repository.get_or_create_guild_settings(guild.id)
        packs = await self.repository.list_content_packs(guild.id)
        embed: discord.Embed
        view: discord.ui.View | None
        if screen == ADMIN_PANEL_SCREEN_ACCESS:
            embed = _admin_access_embed(guild, guild_settings, status=status)
            view = BunkerAdminAccessView(self, guild_settings)
        elif screen == ADMIN_PANEL_SCREEN_PACK and pack_id is not None:
            pack = await self.repository.get_content_pack(pack_id, guild_id=guild.id)
            if pack is None:
                embed = _admin_packs_embed(packs, status="Пак не найден.")
                view = BunkerAdminListView(self, packs)
            else:
                embed = _admin_pack_embed(pack, status=status)
                view = BunkerAdminPackView(self, pack)
        elif screen == ADMIN_PANEL_SCREEN_CATEGORY and pack_id is not None and field in PACK_FIELDS:
            pack = await self.repository.get_content_pack(pack_id, guild_id=guild.id)
            if pack is None:
                embed = _admin_packs_embed(packs, status="Пак не найден.")
                view = BunkerAdminListView(self, packs)
            else:
                embed = _admin_category_embed(pack, field, status=status)
                view = BunkerAdminCategoryView(self, pack, field)
        else:
            embed = _admin_packs_embed(packs, status=status)
            view = BunkerAdminListView(self, packs)

        await self._send_or_edit_private_message(
            interaction,
            self._admin_private_panels,
            self._admin_panel_key(interaction),
            embed=embed,
            view=view,
        )

    async def create_admin_pack(self, interaction: discord.Interaction, *, name: str, description: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Админ-панель Бункера работает только на сервере.", ephemeral=True)
            return
        pack = await self.repository.create_content_pack(
            guild_id=guild.id,
            name=name,
            description=description,
            created_by=interaction.user.id,
        )
        await self.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_PACK, pack_id=pack.id, status="Пак создан.")

    async def rename_admin_pack(self, interaction: discord.Interaction, pack_id: int, *, name: str, description: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Админ-панель Бункера работает только на сервере.", ephemeral=True)
            return
        pack = await self.repository.update_content_pack(
            pack_id,
            guild_id=guild.id,
            updated_by=interaction.user.id,
            name=name,
            description=description,
        )
        await self.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_PACK, pack_id=pack_id, status="Пак обновлен." if pack else "Пак не найден.")

    async def import_admin_pack_json(self, interaction: discord.Interaction, pack_id: int, raw_json: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Админ-панель Бункера работает только на сервере.", ephemeral=True)
            return
        try:
            raw = json.loads(raw_json)
            if not isinstance(raw, dict):
                raise ValueError("JSON должен быть объектом.")
            content_source = raw.get("content", raw)
            if not isinstance(content_source, dict):
                raise ValueError("Поле content должно быть объектом.")
            content = normalize_pack_content(content_source)
            name = str(raw["name"]) if isinstance(raw.get("name"), str) else None
            description = str(raw["description"]) if isinstance(raw.get("description"), str) else None
        except (json.JSONDecodeError, ValueError) as exc:
            await self.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_PACK, pack_id=pack_id, status=f"Импорт не принят: {exc}")
            return

        await self.repository.update_content_pack(
            pack_id,
            guild_id=guild.id,
            updated_by=interaction.user.id,
            name=name,
            description=description,
            content=content,
        )
        await self.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_PACK, pack_id=pack_id, status="JSON импортирован.")

    async def add_admin_pack_value(self, interaction: discord.Interaction, pack_id: int, field: str, value: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Админ-панель Бункера работает только на сервере.", ephemeral=True)
            return
        await self.repository.add_pack_value(pack_id, guild_id=guild.id, field=field, value=value, updated_by=interaction.user.id)
        await self.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_CATEGORY, pack_id=pack_id, field=field, status="Строка добавлена.")

    async def remove_admin_pack_value(self, interaction: discord.Interaction, pack_id: int, field: str, value: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Админ-панель Бункера работает только на сервере.", ephemeral=True)
            return
        await self.repository.remove_pack_value(pack_id, guild_id=guild.id, field=field, value=value, updated_by=interaction.user.id)
        await self.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_CATEGORY, pack_id=pack_id, field=field, status="Строка удалена.")

    async def toggle_admin_pack(self, interaction: discord.Interaction, pack_id: int) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Админ-панель Бункера работает только на сервере.", ephemeral=True)
            return
        pack = await self.repository.get_content_pack(pack_id, guild_id=guild.id)
        if pack is not None:
            await self.repository.update_content_pack(pack_id, guild_id=guild.id, updated_by=interaction.user.id, is_enabled=not pack.is_enabled)
        await self.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_PACK, pack_id=pack_id, status="Статус пака переключен.")

    async def delete_admin_pack(self, interaction: discord.Interaction, pack_id: int) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Админ-панель Бункера работает только на сервере.", ephemeral=True)
            return
        deleted = await self.repository.delete_content_pack(pack_id, guild_id=guild.id)
        await self.open_bunker_admin_panel(interaction, status="Пак удален." if deleted else "Пак не найден.")

    async def set_admin_interest_role(self, interaction: discord.Interaction, role_id: int | None) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Админ-панель Бункера работает только на сервере.", ephemeral=True)
            return
        await self.repository.set_interest_role(guild.id, role_id)
        status = "Роль интереса очищена." if role_id is None else "Роль интереса сохранена."
        await self.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_ACCESS, status=status)

    async def open_game_panel(self, interaction: discord.Interaction, *, screen: str = "main", status: str | None = None) -> None:
        game = await self._game_from_interaction_channel(interaction)
        if game is None:
            await interaction.response.send_message("Эта панель работает в игровом text-канале бункера.", ephemeral=True)
            return

        await self.send_or_edit_private_panel(interaction, game, screen=screen, status=status)

    async def send_or_edit_private_panel(
        self,
        interaction: discord.Interaction,
        game: BunkerGame,
        *,
        screen: str = "main",
        status: str | None = None,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
    ) -> None:
        panel_key = self._game_panel_key(interaction, game)
        if embed is None or view is None:
            default_embed, default_view = await self._game_panel_payload(interaction, game, screen=screen, status=status)
            embed = embed or default_embed
            view = view or default_view

        await self._send_or_edit_private_message(
            interaction,
            self._game_private_panels,
            panel_key,
            embed=embed,
            view=view,
        )

    async def update_current_game_panel(
        self,
        interaction: discord.Interaction,
        game: BunkerGame,
        *,
        screen: str = "main",
        status: str | None = None,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
    ) -> None:
        if embed is None or view is None:
            default_embed, default_view = await self._game_panel_payload(interaction, game, screen=screen, status=status)
            embed = embed or default_embed
            view = view or default_view
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _game_panel_payload(
        self,
        interaction: discord.Interaction,
        game: BunkerGame,
        *,
        screen: str,
        status: str | None = None,
    ) -> tuple[discord.Embed, discord.ui.View | None]:
        players = await self.repository.list_players(game.id)
        player = next((candidate for candidate in players if candidate.user_id == interaction.user.id), None)
        is_operator = await self._is_bunker_operator(interaction)
        can_close = (player is not None and player.is_host) or await self._is_bunker_admin_or_operator(interaction)
        back_view = BunkerPanelBackView(self, game.id)

        if screen == "settings":
            embed = _settings_embed(game.settings)
            if status:
                embed.description = status
            return embed, back_view
        if screen == "rules":
            embed = _rules_embed()
            if status:
                embed.set_footer(text=status)
            return embed, back_view
        if screen == "packs":
            embed = _packs_embed()
            if status:
                embed.set_footer(text=status)
            return embed, back_view
        if screen == "card":
            if player is None or player.card is None:
                return _status_embed("Карточка появится после старта игры."), back_view
            return discord.Embed(title="Твоя карточка Бункера", description=format_card(player.card), color=discord.Color.dark_teal()), back_view
        if screen == "reveal":
            if player is None:
                return _status_embed("Ты не участник этого бункера."), back_view
            if game.state != GameState.REVEAL_PHASE:
                return _status_embed("Сейчас не фаза раскрытия."), back_view
            if player.is_eliminated:
                return _status_embed("Выгнанные игроки уже не раскрывают характеристики."), back_view
            stats = selectable_reveal_stats(player)
            if not stats:
                return _status_embed("Ты уже раскрыл все обычные характеристики."), back_view
            return _status_embed("Выбери характеристику для публичного раскрытия."), BunkerRevealView(self, game.id, interaction.user.id, stats)
        if screen == "vote":
            if player is None or player.is_eliminated:
                return _status_embed("Голосовать могут только живые участники."), back_view
            if game.state != GameState.VOTING_PHASE:
                return _status_embed("Сейчас не фаза голосования."), back_view
            alive_players = [candidate for candidate in players if candidate.is_alive]
            return _status_embed("Кого выгнать из бункера?"), BunkerVoteView(self, game, alive_players, interaction.user.id)
        if screen == "action":
            if player is None or player.card is None:
                return _status_embed("Спец-действие появится после выдачи карточки."), back_view
            if player.used_special_action:
                return _status_embed("Эта карта уже использована."), back_view
            return _status_embed(f"Твое действие: {player.card.special_action}. Использовать сейчас?"), BunkerActionView(self, game.id, player.user_id)

        return _private_panel_embed(game, players, player, is_operator=is_operator, status=status), BunkerPrivatePlayerPanelView(self, game, player, is_operator=is_operator, can_close=can_close)

    async def _send_or_edit_private_message(
        self,
        interaction: discord.Interaction,
        registry: dict[tuple[int, int, int], Any],
        key: tuple[int, int, int],
        *,
        embed: discord.Embed,
        view: discord.ui.View | None,
    ) -> None:
        active_message = registry.get(key)
        if active_message is None:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            try:
                registry[key] = await interaction.original_response()
            except discord.HTTPException:
                LOGGER.info("Could not remember bunker private panel.", exc_info=True)
            return

        if _same_discord_message(active_message, getattr(interaction, "message", None)):
            try:
                await interaction.response.edit_message(content=None, embed=embed, view=view)
                return
            except discord.HTTPException:
                LOGGER.info("Could not edit current bunker private panel.", exc_info=True)

        await interaction.response.defer(ephemeral=True)
        try:
            await active_message.edit(content=None, embed=embed, view=view)
        except discord.HTTPException:
            message = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
            registry[key] = message

    async def panel_join_game(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        status = await self._join_game_core(interaction, game)
        fresh = await self.repository.get_game(game.id) or game
        await self.update_current_game_panel(interaction, fresh, status=status)

    async def panel_ready(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        player = await self.repository.get_player(game.id, interaction.user.id)
        if player is None or not player.is_active:
            await self.update_current_game_panel(interaction, game, status="Сначала зайди в бункер.")
            return

        await self.repository.set_ready(game.id, interaction.user.id, ready=True)
        await self.refresh_game_message(game.id)
        await self.update_current_game_panel(interaction, game, status="Готовность принята.")

    async def panel_leave(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        if interaction.user.id == game.host_id:
            await self.update_current_game_panel(interaction, game, status="Хост не может выйти. Заверши игру через /bunker end.")
            return

        await self.repository.mark_left(game.id, interaction.user.id)
        await self.refresh_game_message(game.id)
        await self.update_current_game_panel(interaction, game, status="Ты вышел из бункера.")

    async def panel_start(self, interaction: discord.Interaction, game_id: int, *, force: bool = False) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        if not force and not await self._is_host_or_admin_user(interaction, game):
            await self.update_current_game_panel(interaction, game, status="Стартовать может только хост или оператор.")
            return
        if force and not await self._is_bunker_operator(interaction):
            await self.update_current_game_panel(interaction, game, status="Форс-старт доступен только оператору Бункера.")
            return

        status = await self._start_game_core(game, force=force)
        fresh = await self.repository.get_game(game.id) or game
        await self.update_current_game_panel(interaction, fresh, status=status)

    async def panel_card(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        await self.update_current_game_panel(interaction, game, screen="card")

    async def panel_reveal(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        await self.update_current_game_panel(interaction, game, screen="reveal")

    async def panel_vote(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        await self.update_current_game_panel(interaction, game, screen="vote")

    async def panel_action(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        await self.update_current_game_panel(interaction, game, screen="action")

    async def panel_add_fake_players(self, interaction: discord.Interaction, game_id: int) -> None:
        if not await self._is_bunker_operator(interaction):
            await interaction.response.edit_message(embed=_status_embed("Это действие доступно только оператору Бункера."), view=BunkerPanelBackView(self, game_id))
            return
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        added = await self.repository.add_fake_players(game.id, game.settings.slots)
        await self.refresh_game_message(game.id)
        await self.update_current_game_panel(interaction, game, status=f"Добавлено тест-ботов: {len(added)}.")

    async def panel_remove_fake_players(self, interaction: discord.Interaction, game_id: int) -> None:
        if not await self._is_bunker_operator(interaction):
            await interaction.response.edit_message(embed=_status_embed("Это действие доступно только оператору Бункера."), view=BunkerPanelBackView(self, game_id))
            return
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        removed = await self.repository.remove_fake_players(game.id)
        await self.refresh_game_message(game.id)
        await self.update_current_game_panel(interaction, game, status=f"Удалено тест-ботов: {removed}.")

    async def panel_next_phase(self, interaction: discord.Interaction, game_id: int) -> None:
        if not await self._is_bunker_operator(interaction):
            await interaction.response.edit_message(embed=_status_embed("Это действие доступно только оператору Бункера."), view=BunkerPanelBackView(self, game_id))
            return
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        await self.advance_phase(game)
        fresh = await self.repository.get_game(game.id) or game
        await self.update_current_game_panel(interaction, fresh, status="Фаза сдвинута.")

    async def panel_close_channels(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return
        if not await self._is_host_or_admin_user(interaction, game):
            await interaction.response.edit_message(embed=_status_embed("Закрыть бункер может только его хост, админ или оператор."), view=BunkerPanelBackView(self, game_id))
            return

        await interaction.response.edit_message(
            embed=_status_embed("Закрыть этот бункер и удалить временные text/voice каналы?"),
            view=BunkerCloseConfirmView(self, game.id),
        )

    async def confirm_close_channels(self, interaction: discord.Interaction, game_id: int) -> None:
        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return
        if not await self._is_host_or_admin_user(interaction, game):
            await interaction.response.edit_message(embed=_status_embed("Закрыть бункер может только его хост, админ или оператор."), view=BunkerPanelBackView(self, game_id))
            return

        await interaction.response.edit_message(embed=_status_embed("Закрываю временные каналы."), view=None)
        await self._close_game_channels(game, reason="Bunker room closed")

    async def _close_game_channels(self, game: BunkerGame, *, reason: str) -> None:
        await self.repository.finish_game(game.id)
        await self.refresh_setup_message(game)
        voice_channel = await self._fetch_voice_channel(game.voice_channel_id)
        text_channel = await self._fetch_text_channel(game.game_text_channel_id)
        if voice_channel is not None:
            try:
                await voice_channel.delete(reason=reason)
            except discord.HTTPException:
                LOGGER.info("Could not delete bunker voice channel %s.", game.voice_channel_id, exc_info=True)
        if text_channel is not None:
            try:
                await text_channel.delete(reason=reason)
            except discord.HTTPException:
                LOGGER.info("Could not delete bunker text channel %s.", game.game_text_channel_id, exc_info=True)

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
        await self.refresh_game_message(game.id)
        await interaction.response.send_message(f"{user.mention} получил доступ к бункеру.", ephemeral=True)

    async def build_bunker(self, interaction: discord.Interaction, *, is_admin_game: bool = False) -> None:
        setup = await self._setup_from_interaction_message(interaction)
        if setup is None:
            await interaction.response.send_message("Не нашел setup этой комнаты. Создай панель через /createbunker заново.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await self.send_or_edit_setup_status(interaction, setup, "Бункер можно строить только на сервере.")
            return

        active_host_game = await self._live_active_game_for_host(guild.id, interaction.user.id)
        if active_host_game is not None:
            await self.send_host_conflict_status(interaction, setup, active_host_game)
            return

        setup_channel = guild.get_channel(setup.setup_channel_id)
        if not isinstance(setup_channel, discord.TextChannel):
            await self.send_or_edit_setup_status(interaction, setup, "Setup-канал больше недоступен.")
            return

        settings = normalize_settings(await self.repository.get_draft(setup.id, interaction.user.id))
        room_index = await self.repository.next_room_index(setup)
        text_name = f"бункер-комната-{room_index}"
        voice_name = f"Собрание бункера {room_index}"
        category = setup_channel.category

        text_channel: discord.TextChannel | None = None
        voice_channel: discord.VoiceChannel | None = None
        try:
            operator_role = await self._operator_role(guild) if is_admin_game else None
            if is_admin_game and operator_role is None:
                await self.send_or_edit_setup_status(interaction, setup, "Сначала назначь operator-role через /opbunker role.")
                return
            interest_role = None if is_admin_game or not settings.is_public else await self._interest_role(guild)
            if operator_role is not None:
                text_overwrites = build_admin_text_overwrites(guild, operator_role, [interaction.user])
            elif settings.is_public:
                text_overwrites = build_lobby_text_overwrites(guild, [interaction.user], interest_role=interest_role)
            else:
                text_overwrites = build_private_text_overwrites(guild, [interaction.user])
            voice_overwrites = (
                build_admin_voice_overwrites(guild, operator_role, [interaction.user])
                if operator_role is not None
                else build_private_voice_overwrites(
                    guild,
                    [interaction.user],
                    spectator_role=interest_role if settings.is_public else None,
                )
            )
            text_channel = await guild.create_text_channel(
                text_name,
                category=category,
                overwrites=text_overwrites,
                reason="Bunker room built",
            )
            voice_channel = await guild.create_voice_channel(
                voice_name,
                category=category,
                overwrites=voice_overwrites,
                reason="Bunker room built",
            )
            game = await self.repository.create_game(
                setup=setup,
                host_id=interaction.user.id,
                settings=settings,
                room_index=room_index,
                text_channel_id=text_channel.id,
                voice_channel_id=voice_channel.id,
                host_display_name=interaction.user.display_name,
                is_admin_game=is_admin_game,
            )
        except ActiveBunkerGameError as exc:
            if text_channel is not None:
                await text_channel.delete(reason="Bunker duplicate build rollback")
            if voice_channel is not None:
                await voice_channel.delete(reason="Bunker duplicate build rollback")
            conflicting = await self.repository.get_game(exc.game_id)
            live_conflict = await self._ensure_game_discord_state(conflicting)
            if live_conflict is not None:
                await self.send_host_conflict_status(interaction, setup, live_conflict)
            else:
                await self.send_or_edit_setup_status(interaction, setup, "Старый бункер был очищен. Нажми 'Построить бункер' еще раз.")
            return
        except discord.Forbidden:
            await self.send_or_edit_setup_status(
                interaction,
                setup,
                "Не хватает прав Discord. Нужны Manage Channels и права на создание text/voice каналов.",
            )
            return

        board_message = await self._send_board_message(text_channel, game, await self.repository.list_players(game.id))
        await self.repository.set_board_message(game.id, board_message.id)
        await self.refresh_game_message(game.id)
        await self.refresh_setup_message(game)

        moved = await self._move_member_to_voice(interaction.user, voice_channel)
        suffix = "Я перенес тебя в голосовой." if moved else f"Я открыл доступ к {voice_channel.mention}; зайди туда вручную, если сейчас не был в voice."
        await self.send_or_edit_setup_status(interaction, setup, f"Бункер построен: {text_channel.mention}. {suffix}", voice_channel=voice_channel)

    async def join_from_setup(self, interaction: discord.Interaction) -> None:
        setup = await self._setup_from_interaction_message(interaction)
        if setup is None:
            await interaction.response.send_message("Эта панель не привязана к комнате.", ephemeral=True)
            return

        game = await self._live_active_game_for_setup(setup)
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
        message = await self._join_game_core(interaction, game)
        await interaction.response.send_message(message, ephemeral=True)

    async def _join_game_core(self, interaction: discord.Interaction, game: BunkerGame) -> str:
        if game.state != GameState.LOBBY:
            return "Вход уже закрыт: игра стартовала."

        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return "Зайти в бункер можно только на сервере."

        players = await self.repository.list_players(game.id)
        existing = next((player for player in players if player.user_id == interaction.user.id), None)
        if existing is None and len(players) >= game.settings.slots:
            return "Бункер заполнен."

        if not game.settings.is_public and existing is None and interaction.user.id != game.host_id:
            return "Это приватный бункер. Попроси хоста пригласить тебя."

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

        await self.refresh_game_message(game.id)
        return message

    async def mark_ready(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        player = await self._require_player(interaction, game)
        if player is None:
            return

        await self.repository.set_ready(game.id, interaction.user.id, ready=True)
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
        await self.refresh_game_message(game.id)
        await interaction.response.send_message("Ты вышел из бункера.", ephemeral=True)

    async def start_game(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None or not await self._require_host_or_admin(interaction, game):
            return

        status = await self._start_game_core(game)
        await interaction.response.send_message(status, ephemeral=True)

    async def _start_game_core(self, game: BunkerGame, *, force: bool = False) -> str:
        if game.state != GameState.LOBBY:
            return "Игра уже стартовала."

        players = await self.repository.list_players(game.id)
        if not force:
            ok, reason = can_start_game(players)
            if not ok:
                return reason

        rng = random.Random()
        content_pack = await self._content_pack_for_game(game)
        profile = generate_profile(game.settings, rng, content_pack)
        await self.repository.set_profile(game.id, profile)
        cards = assign_cards(players, game.settings, rng, content_pack)
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
        return "Игра началась. Карточки доступны через 'Моя карточка'."

    async def show_card(self, interaction: discord.Interaction) -> None:
        await self.open_game_panel(interaction, screen="card")

    async def show_reveal_menu(self, interaction: discord.Interaction) -> None:
        await self.open_game_panel(interaction, screen="reveal")

    async def reveal_selected_stat(self, interaction: discord.Interaction, game_id: int, user_id: int, stat: str) -> None:
        if interaction.user.id != user_id:
            await interaction.response.edit_message(embed=_status_embed("Это меню не для тебя."), view=BunkerPanelBackView(self, game_id))
            return

        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        player = await self.repository.get_player(game_id, user_id)
        if player is None:
            await interaction.response.edit_message(embed=_status_embed("Ты не участник этой партии."), view=BunkerPanelBackView(self, game_id))
            return

        ok, message = reveal_stat(player, stat)
        if not ok:
            await interaction.response.edit_message(embed=_status_embed(message), view=BunkerPanelBackView(self, game_id))
            return

        await self.repository.reveal_stat(game_id, user_id, stat)
        await self.repository.add_event(game_id, game.round_number, "reveal", message)
        await self.refresh_game_message(game_id)
        fresh = await self.repository.get_game(game_id) or game
        await self.update_current_game_panel(interaction, fresh, status="Раскрыто.")

    async def show_vote_menu(self, interaction: discord.Interaction) -> None:
        await self.open_game_panel(interaction, screen="vote")

    async def save_vote(self, interaction: discord.Interaction, game_id: int, voter_id: int, raw_target: str) -> None:
        if interaction.user.id != voter_id:
            await interaction.response.edit_message(embed=_status_embed("Это меню не для тебя."), view=BunkerPanelBackView(self, game_id))
            return

        game = await self.repository.get_game(game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        if raw_target == "abstain":
            vote = Vote(game_id=game.id, round_number=game.round_number, voter_id=voter_id, target_user_id=None, is_abstain=True)
            message = "Ты воздержался."
        else:
            target_id = int(raw_target)
            vote = Vote(game_id=game.id, round_number=game.round_number, voter_id=voter_id, target_user_id=target_id, is_abstain=False)
            message = f"Голос принят против <@{target_id}>."

        await self.repository.save_vote(vote)
        await self.update_current_game_panel(interaction, game, status=message)

    async def show_action_menu(self, interaction: discord.Interaction) -> None:
        await self.open_game_panel(interaction, screen="action")

    async def use_special_action(self, interaction: discord.Interaction, game_id: int, user_id: int) -> None:
        if interaction.user.id != user_id:
            await interaction.response.edit_message(embed=_status_embed("Это действие не для тебя."), view=BunkerPanelBackView(self, game_id))
            return

        game = await self.repository.get_game(game_id)
        player = await self.repository.get_player(game_id, user_id)
        if game is None or player is None or player.card is None:
            await interaction.response.edit_message(embed=_status_embed("Не нашел действие."), view=BunkerPanelBackView(self, game_id))
            return

        if player.used_special_action:
            await interaction.response.edit_message(embed=_status_embed("Эта карта уже использована."), view=BunkerPanelBackView(self, game_id))
            return

        event = await self._apply_special_action(game, player)
        await self.repository.mark_special_used(game.id, player.user_id)
        await self.repository.add_event(game.id, game.round_number, "action", event)
        await self.refresh_game_message(game.id)
        fresh = await self.repository.get_game(game.id) or game
        await self.update_current_game_panel(interaction, fresh, status="Действие использовано.")

    async def trigger_chaos(self, interaction: discord.Interaction) -> None:
        game = await self._require_game_channel(interaction)
        if game is None:
            return

        if game.state != GameState.CHAOS_PHASE:
            await self.send_or_edit_private_panel(interaction, game, status="Сейчас не фаза хаоса.")
            return

        await self._trigger_chaos_event(game)
        await self.refresh_game_message(game.id)
        await self.send_or_edit_private_panel(interaction, game, status="Хаос зафиксирован на табло.")

    async def show_rules(self, interaction: discord.Interaction) -> None:
        await self.open_game_panel(interaction, screen="rules")

    async def show_packs(self, interaction: discord.Interaction) -> None:
        await self.open_game_panel(interaction, screen="packs")

    async def open_setup_panel(self, interaction: discord.Interaction, *, screen: str = "settings", status: str | None = None) -> None:
        setup = await self._setup_from_interaction_message(interaction)
        if setup is None:
            await interaction.response.send_message("Эта панель не привязана к комнате.", ephemeral=True)
            return

        settings = normalize_settings(await self.repository.get_draft(setup.id, interaction.user.id))
        await self.repository.save_draft(setup.id, interaction.user.id, settings)
        is_operator = await self._is_bunker_operator(interaction)

        if screen == "rules":
            embed = _rules_embed()
            view: discord.ui.View | None = BunkerSetupNavView(self, setup.id, interaction.user.id, settings, screen=screen, is_operator=is_operator)
        elif screen == "content":
            packs = await self.repository.list_content_packs(setup.guild_id, include_disabled=False)
            embed = _setup_content_embed(settings, packs)
            view = BunkerSetupContentView(self, setup.id, interaction.user.id, settings, packs, is_operator=is_operator)
        elif screen == "packs":
            embed = _packs_embed()
            view = BunkerSetupNavView(self, setup.id, interaction.user.id, settings, screen=screen, is_operator=is_operator)
        else:
            embed = _settings_embed(settings)
            view = BunkerSettingsView(self, setup.id, interaction.user.id, settings, is_operator=is_operator)

        if status:
            if embed.description:
                embed.description = f"{status}\n\n{embed.description}"
            else:
                embed.description = status

        await self._send_or_edit_private_message(
            interaction,
            self._setup_private_panels,
            self._setup_panel_key(interaction, setup),
            embed=embed,
            view=view,
        )

    async def show_setup_rules(self, interaction: discord.Interaction) -> None:
        await self.open_setup_panel(interaction, screen="rules")

    async def show_setup_packs(self, interaction: discord.Interaction) -> None:
        await self.open_setup_panel(interaction, screen="content")

    async def show_setup_settings(self, interaction: discord.Interaction) -> None:
        await self.open_setup_panel(interaction, screen="settings")

    async def send_or_edit_setup_status(
        self,
        interaction: discord.Interaction,
        setup,
        message: str,
        *,
        voice_channel: discord.VoiceChannel | None = None,
    ) -> None:
        settings = normalize_settings(await self.repository.get_draft(setup.id, interaction.user.id))
        is_operator = await self._is_bunker_operator(interaction)
        await self._send_or_edit_private_message(
            interaction,
            self._setup_private_panels,
            self._setup_panel_key(interaction, setup),
            embed=_status_embed(message),
            view=BunkerSetupNavView(
                self,
                setup.id,
                interaction.user.id,
                settings,
                screen="status",
                is_operator=is_operator,
                voice_url=_voice_channel_url(voice_channel),
            ),
        )

    async def send_host_conflict_status(
        self,
        interaction: discord.Interaction,
        setup,
        game: BunkerGame,
    ) -> None:
        settings = normalize_settings(await self.repository.get_draft(setup.id, interaction.user.id))
        is_operator = await self._is_bunker_operator(interaction)
        channel_hint = f"<#{game.game_text_channel_id}>" if game.game_text_channel_id is not None else f"#{game.id}"
        await self._send_or_edit_private_message(
            interaction,
            self._setup_private_panels,
            self._setup_panel_key(interaction, setup),
            embed=_status_embed(
                "У тебя уже есть активный бункер: "
                f"{channel_hint}. Закрой прошлый бункер, чтобы создать новый."
            ),
            view=BunkerSetupHostConflictView(
                self,
                setup.id,
                interaction.user.id,
                settings,
                game,
                is_operator=is_operator,
            ),
        )

    async def update_draft_settings(
        self,
        interaction: discord.Interaction,
        setup_id: int,
        user_id: int,
        settings: BunkerSettings,
        *,
        screen: str = "settings",
    ) -> None:
        if interaction.user.id != user_id:
            await interaction.response.send_message("Эти настройки открыты другим пользователем.", ephemeral=True)
            return

        settings = normalize_settings(settings)
        await self.repository.save_draft(setup_id, user_id, settings)
        is_operator = await self._is_bunker_operator(interaction)
        if screen == "content":
            guild_id = interaction.guild.id if interaction.guild is not None else 0
            packs = await self.repository.list_content_packs(guild_id, include_disabled=False)
            await interaction.response.edit_message(
                embed=_setup_content_embed(settings, packs),
                view=BunkerSetupContentView(self, setup_id, user_id, settings, packs, is_operator=is_operator),
            )
            return

        await interaction.response.edit_message(
            embed=_settings_embed(settings),
            view=BunkerSettingsView(self, setup_id, user_id, settings, is_operator=is_operator),
        )

    async def build_admin_bunker(self, interaction: discord.Interaction) -> None:
        if not await self._is_bunker_operator(interaction):
            await interaction.response.send_message("Админ-режим доступен только operator-role из /opbunker role.", ephemeral=True)
            return

        await self.build_bunker(interaction, is_admin_game=True)

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
                if eliminated is not None:
                    event = event.replace(f"<@{eliminated_id}>", format_player_name(eliminated))
                if eliminated and eliminated.immune_round == game.round_number:
                    event = f"{event} Но иммунитет спасает {format_player_name(eliminated)}."
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
        if game is None:
            return

        channel = await self._fetch_text_channel(game.game_text_channel_id)
        voice_channel = await self._fetch_voice_channel(game.voice_channel_id)
        if channel is None or voice_channel is None:
            await self.repository.finish_game(game.id)
            return

        message = await self._ensure_board_message(game, channel)
        if message is None:
            return
        game = await self.repository.get_game(game.id) or game

        players = await self.repository.list_players(game.id)
        embed = _game_embed(game, players)
        try:
            image_bytes = render_board_png(game, players)
            file = discord.File(BytesIO(image_bytes), filename="bunker_board.png")
            embed.set_image(url="attachment://bunker_board.png")
            await message.edit(embed=embed, attachments=[file], view=BunkerPublicGameView(self))
        except Exception:
            LOGGER.exception("Could not render bunker board for game %s", game.id)
            await message.edit(embed=embed, view=BunkerPublicGameView(self))

    async def _send_board_message(self, channel: discord.TextChannel, game: BunkerGame, players: list[BunkerPlayer]) -> discord.Message:
        embed = _game_embed(game, players)
        try:
            image_bytes = render_board_png(game, players)
            file = discord.File(BytesIO(image_bytes), filename="bunker_board.png")
            embed.set_image(url="attachment://bunker_board.png")
            return await channel.send(embed=embed, file=file, view=BunkerPublicGameView(self))
        except Exception:
            LOGGER.exception("Could not render bunker board for game %s", game.id)
            return await channel.send(embed=embed, view=BunkerPublicGameView(self))

    async def _ensure_board_message(self, game: BunkerGame, channel: discord.TextChannel) -> discord.Message | None:
        if game.board_message_id is not None:
            try:
                return await channel.fetch_message(game.board_message_id)
            except discord.HTTPException:
                LOGGER.info("Bunker board message %s is missing; recreating.", game.board_message_id, exc_info=True)

        players = await self.repository.list_players(game.id)
        try:
            message = await self._send_board_message(channel, game, players)
        except discord.HTTPException:
            LOGGER.info("Could not recreate bunker board message for game %s.", game.id, exc_info=True)
            return None
        await self.repository.set_board_message(game.id, message.id)
        return message

    async def _ensure_game_discord_state(self, game: BunkerGame | None) -> BunkerGame | None:
        if game is None:
            return None

        text_channel = await self._fetch_text_channel(game.game_text_channel_id)
        voice_channel = await self._fetch_voice_channel(game.voice_channel_id)
        if text_channel is None or voice_channel is None:
            await self.repository.finish_game(game.id)
            LOGGER.info("Finished stale bunker game %s because its Discord channels are missing.", game.id)
            return None

        await self._ensure_board_message(game, text_channel)
        return await self.repository.get_game(game.id) or game

    async def _live_active_game_for_setup(self, setup) -> BunkerGame | None:
        setup_id = getattr(setup, "setup_id", getattr(setup, "id"))
        active = await self.repository.get_active_game_by_setup(setup_id)
        return await self._ensure_game_discord_state(active)

    async def _live_active_game_for_host(self, guild_id: int, host_id: int) -> BunkerGame | None:
        for active in await self.repository.list_active_games_by_host(guild_id, host_id):
            live = await self._ensure_game_discord_state(active)
            if live is not None:
                return live
        return None

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

        embed = _setup_embed(channel.name)
        await message.edit(embed=embed, view=BunkerSetupIdleView(self))

    async def _trigger_chaos_event(self, game: BunkerGame) -> None:
        content_pack = await self._content_pack_for_game(game)
        event = pick_chaos_event(pack=content_pack)
        if game.profile is not None:
            profile = BunkerProfile(
                apocalypse=game.profile.apocalypse,
                layout=game.profile.layout,
                defect=game.profile.defect,
                resources=apply_chaos_to_resources(game.profile.resources),
            )
            await self.repository.set_profile(game.id, profile)
        await self.repository.add_event(game.id, game.round_number, "chaos", event)

    async def _content_pack_for_game(self, game: BunkerGame) -> ContentPack:
        custom_pack = await self.repository.get_enabled_content_pack(game.guild_id, game.settings.content_pack_id)
        if custom_pack is None:
            return BUILTIN_PACK
        return merge_content_packs(BUILTIN_PACK, ContentPack.from_json(custom_pack.content))

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
        game = await self._game_from_interaction_channel(interaction)
        if game is None:
            message = "Не вижу канал этой команды." if channel is None else "Эта команда работает в игровом text-канале бункера."
            await interaction.response.send_message(message, ephemeral=True)
            return None

        return game

    async def _game_from_interaction_channel(self, interaction: discord.Interaction, *, include_finished: bool = False) -> BunkerGame | None:
        channel = interaction.channel
        if channel is None:
            return None

        if include_finished:
            game = await self.repository.get_game_by_text_channel(channel.id)
        else:
            game = await self.repository.get_active_game_by_text_channel(channel.id)
        if game is None:
            return None

        return game

    async def _require_player(self, interaction: discord.Interaction, game: BunkerGame) -> BunkerPlayer | None:
        player = await self.repository.get_player(game.id, interaction.user.id)
        if player is None or not player.is_active:
            await interaction.response.send_message("Ты не участник этого бункера.", ephemeral=True)
            return None

        return player

    async def _require_host_or_admin(self, interaction: discord.Interaction, game: BunkerGame) -> bool:
        if await self._is_host_or_admin_user(interaction, game):
            return True

        await interaction.response.send_message("Это может сделать только хост бункера или админ.", ephemeral=True)
        return False

    async def _is_host_or_admin_user(self, interaction: discord.Interaction, game: BunkerGame) -> bool:
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

        return await self._is_bunker_operator(interaction)

    async def _is_bunker_operator(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            return False

        role_ids = [role.id for role in user.roles]
        return await self.repository.is_bunker_operator(guild.id, role_ids)

    async def _is_bunker_admin_or_operator(self, interaction: discord.Interaction) -> bool:
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
        return await self._is_bunker_operator(interaction)

    async def _operator_role(self, guild: discord.Guild) -> discord.Role | None:
        settings = await self.repository.get_or_create_guild_settings(guild.id)
        return guild.get_role(settings.operator_role_id) if settings.operator_role_id is not None else None

    async def _interest_role(self, guild: discord.Guild) -> discord.Role | None:
        settings = await self.repository.get_or_create_guild_settings(guild.id)
        return guild.get_role(settings.interest_role_id) if settings.interest_role_id is not None else None

    def _setup_panel_key(self, interaction: discord.Interaction, setup) -> tuple[int, int, int]:
        channel_id = setup.setup_channel_id
        if not channel_id and interaction.channel is not None:
            channel_id = interaction.channel.id
        return (setup.id, channel_id, interaction.user.id)

    def _game_panel_key(self, interaction: discord.Interaction, game: BunkerGame) -> tuple[int, int, int]:
        channel_id = interaction.channel.id if interaction.channel is not None else 0
        return (game.id, channel_id, interaction.user.id)

    def _admin_panel_key(self, interaction: discord.Interaction) -> tuple[int, int, int]:
        guild_id = interaction.guild.id if interaction.guild is not None else 0
        channel_id = interaction.channel.id if interaction.channel is not None else 0
        return (guild_id, channel_id, interaction.user.id)

    async def _delete_duplicate_setup_panels(self, channel: discord.TextChannel, *, keep_message_id: int) -> None:
        if self.bot.user is None:
            return

        try:
            async for message in channel.history(limit=50):
                if message.id == keep_message_id or message.author.id != self.bot.user.id:
                    continue
                if any(embed.title and embed.title.startswith("Бункер - ") for embed in message.embeds):
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        LOGGER.info("Could not delete duplicate bunker setup panel %s.", message.id, exc_info=True)
        except discord.HTTPException:
            LOGGER.info("Could not scan setup channel %s for duplicate bunker panels.", channel.id, exc_info=True)

    async def _setup_from_interaction_message(self, interaction: discord.Interaction):
        message = interaction.message
        if message is not None:
            setup = await self.repository.get_setup_by_message(message.id)
            if setup is not None:
                return setup
        if interaction.channel is not None:
            setup = await self.repository.get_setup_by_channel(interaction.channel.id)
            if setup is not None and message is not None and setup.setup_message_id != message.id:
                repaired = await self.repository.repair_setup_message_id(setup.id, message.id)
                return repaired or setup
            return setup
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


class BunkerSetupIdleView(discord.ui.View):
    def __init__(self, cog: Bunker) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Построить бункер", style=discord.ButtonStyle.primary, custom_id=SETUP_BUILD_ID)
    async def build(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.build_bunker(interaction)

    @discord.ui.button(label="Настроить бункер", style=discord.ButtonStyle.secondary, custom_id=SETUP_SETTINGS_ID)
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_setup_settings(interaction)

    @discord.ui.button(label="Как играть", style=discord.ButtonStyle.secondary, custom_id=SETUP_RULES_ID)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_setup_rules(interaction)

    @discord.ui.button(label="Паки/контент", style=discord.ButtonStyle.secondary, custom_id=SETUP_PACKS_ID)
    async def packs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_setup_packs(interaction)


class BunkerPublicGameView(discord.ui.View):
    def __init__(self, cog: Bunker) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Панель", style=discord.ButtonStyle.primary, custom_id=GAME_PANEL_ID)
    async def panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_game_panel(interaction)


class BunkerPrivatePlayerPanelView(discord.ui.View):
    def __init__(
        self,
        cog: Bunker,
        game: BunkerGame,
        player: BunkerPlayer | None,
        *,
        is_operator: bool,
        can_close: bool = False,
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.game = game
        self.player = player
        self.is_operator = is_operator
        self.can_close = can_close
        self._build()

    def _build(self) -> None:
        if self.game.state == GameState.LOBBY and self.player is None:
            self._add_button("Зайти в бункер", discord.ButtonStyle.success, self._join, row=0)

        if self.player is not None and self.player.is_active and not self.player.is_host and self.player.ready_at is None and self.game.state == GameState.LOBBY:
            self._add_button("Готов", discord.ButtonStyle.primary, self._ready, row=0)

        if self.player is not None and self.player.is_active and not self.player.is_host and self.game.state == GameState.LOBBY:
            self._add_button("Покинуть", discord.ButtonStyle.secondary, self._leave, row=0)

        if self.player is not None and self.player.is_host and self.game.state == GameState.LOBBY:
            self._add_button("Начать", discord.ButtonStyle.danger, self._start, row=0)

        if self.player is not None and self.player.is_active:
            self._add_button("Моя карточка", discord.ButtonStyle.secondary, self._card, row=1)
            self._add_button("Раскрыть стату", discord.ButtonStyle.primary, self._reveal, row=1)
            self._add_button("Действие", discord.ButtonStyle.secondary, self._action, row=1)
            self._add_button("Голосовать", discord.ButtonStyle.primary, self._vote, row=1)

        self._add_button("Правила", discord.ButtonStyle.secondary, self._rules, row=2)
        if self.player is not None and self.player.is_active and self.game.voice_channel_id is not None:
            self.add_item(
                discord.ui.Button(
                    label="Перейти в голосовой",
                    style=discord.ButtonStyle.link,
                    url=f"https://discord.com/channels/{self.game.guild_id}/{self.game.voice_channel_id}",
                    row=2,
                )
            )

        if self.is_operator:
            self._add_button("Добавить тест-ботов", discord.ButtonStyle.success, self._add_fakes, row=3)
            self._add_button("Очистить тест-ботов", discord.ButtonStyle.secondary, self._remove_fakes, row=3)
            self._add_button("Форс-старт", discord.ButtonStyle.danger, self._force_start, row=3)
            self._add_button("Следующая фаза", discord.ButtonStyle.primary, self._next_phase, row=4)
        if self.can_close:
            self._add_button("Закрыть бункер", discord.ButtonStyle.danger, self._close_channels, row=4)

    def _add_button(self, label: str, style: discord.ButtonStyle, callback, *, row: int) -> None:
        button = discord.ui.Button(label=label, style=style, row=row)
        button.callback = callback
        self.add_item(button)

    async def _join(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_join_game(interaction, self.game.id)

    async def _ready(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_ready(interaction, self.game.id)

    async def _leave(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_leave(interaction, self.game.id)

    async def _start(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_start(interaction, self.game.id)

    async def _card(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_card(interaction, self.game.id)

    async def _reveal(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_reveal(interaction, self.game.id)

    async def _action(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_action(interaction, self.game.id)

    async def _vote(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_vote(interaction, self.game.id)

    async def _rules(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=_rules_embed(), view=BunkerPanelBackView(self.cog, self.game.id))

    async def _add_fakes(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_add_fake_players(interaction, self.game.id)

    async def _remove_fakes(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_remove_fake_players(interaction, self.game.id)

    async def _force_start(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_start(interaction, self.game.id, force=True)

    async def _next_phase(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_next_phase(interaction, self.game.id)

    async def _close_channels(self, interaction: discord.Interaction) -> None:
        await self.cog.panel_close_channels(interaction, self.game.id)


class BunkerPanelBackView(discord.ui.View):
    def __init__(self, cog: Bunker, game_id: int) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.game_id = game_id

    @discord.ui.button(label="Назад к панели", style=discord.ButtonStyle.primary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        game = await self.cog.repository.get_game(self.game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return

        await self.cog.update_current_game_panel(interaction, game)


class BunkerCloseConfirmView(discord.ui.View):
    def __init__(self, cog: Bunker, game_id: int) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.game_id = game_id

    @discord.ui.button(label="Да, закрыть", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.confirm_close_channels(interaction, self.game_id)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        game = await self.cog.repository.get_game(self.game_id)
        if game is None:
            await interaction.response.edit_message(embed=_status_embed("Партия не найдена."), view=None)
            return
        await self.cog.update_current_game_panel(interaction, game)


class BunkerSetupHostConflictView(discord.ui.View):
    def __init__(
        self,
        cog: Bunker,
        setup_id: int,
        user_id: int,
        settings: BunkerSettings,
        game: BunkerGame,
        *,
        is_operator: bool,
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.setup_id = setup_id
        self.user_id = user_id
        self.settings = settings
        self.game = game
        self.is_operator = is_operator
        if game.game_text_channel_id is not None:
            self.add_item(
                discord.ui.Button(
                    label="Открыть бункер",
                    style=discord.ButtonStyle.link,
                    url=f"https://discord.com/channels/{game.guild_id}/{game.game_text_channel_id}",
                    row=0,
                )
            )
        self._add_button("Закрыть бункер", discord.ButtonStyle.danger, self._close, row=0)
        self._add_button("Настройки", discord.ButtonStyle.secondary, self._settings, row=1)

    def _add_button(self, label: str, style: discord.ButtonStyle, callback, *, row: int) -> None:
        button = discord.ui.Button(label=label, style=style, row=row)
        button.callback = callback
        self.add_item(button)

    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Эта приватная панель открыта другим пользователем.", ephemeral=True)
        return False

    async def _close(self, interaction: discord.Interaction) -> None:
        if not await self._ensure_owner(interaction):
            return
        await interaction.response.edit_message(
            embed=_status_embed("Закрыть твой активный бункер и удалить временные text/voice каналы?"),
            view=BunkerCloseConfirmView(self.cog, self.game.id),
        )

    async def _settings(self, interaction: discord.Interaction) -> None:
        if await self._ensure_owner(interaction):
            await self.cog.open_setup_panel(interaction, screen="settings")


class BunkerAdminListView(discord.ui.View):
    def __init__(self, cog: Bunker, packs: list[BunkerContentPack]) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.packs = packs
        if packs:
            self.add_item(BunkerAdminPackSelect(cog, packs))

    @discord.ui.button(label="Создать пак", style=discord.ButtonStyle.success, row=1)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(BunkerPackCreateModal(self.cog))

    @discord.ui.button(label="Обновить", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_bunker_admin_panel(interaction)

    @discord.ui.button(label="Доступ", style=discord.ButtonStyle.primary, row=2)
    async def access(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_ACCESS)


class BunkerAdminAccessView(discord.ui.View):
    def __init__(self, cog: Bunker, settings: Any) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.settings = settings
        self.add_item(BunkerInterestRoleSelect(cog))

    @discord.ui.button(label="Очистить роль интереса", style=discord.ButtonStyle.secondary, row=1)
    async def clear_interest(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.set_admin_interest_role(interaction, None)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_bunker_admin_panel(interaction)


class BunkerInterestRoleSelect(discord.ui.RoleSelect):
    def __init__(self, cog: Bunker) -> None:
        super().__init__(placeholder="Роль интересующихся Бункером", min_values=1, max_values=1, row=0)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        await self.cog.set_admin_interest_role(interaction, role.id)


class BunkerAdminPackSelect(discord.ui.Select):
    def __init__(self, cog: Bunker, packs: list[BunkerContentPack]) -> None:
        self.cog = cog
        options = [
            discord.SelectOption(
                label=pack.name[:100],
                value=str(pack.id),
                description=("включен" if pack.is_enabled else "выключен") + f"; {_pack_total(pack)} строк",
            )
            for pack in packs[:25]
        ]
        super().__init__(placeholder="Выбери пак", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_PACK, pack_id=int(self.values[0]))


class BunkerAdminPackView(discord.ui.View):
    def __init__(self, cog: Bunker, pack: BunkerContentPack) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.pack = pack
        self.add_item(BunkerAdminCategorySelect(cog, pack))

    @discord.ui.button(label="Переименовать", style=discord.ButtonStyle.secondary, row=1)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(BunkerPackRenameModal(self.cog, self.pack))

    @discord.ui.button(label="Импорт JSON", style=discord.ButtonStyle.primary, row=1)
    async def import_json(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(BunkerPackImportModal(self.cog, self.pack))

    @discord.ui.button(label="Экспорт JSON", style=discord.ButtonStyle.secondary, row=1)
    async def export_json(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=_pack_export_embed(self.pack), view=BunkerAdminExportView(self.cog, self.pack.id))

    @discord.ui.button(label="Вкл/выкл", style=discord.ButtonStyle.secondary, row=2)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.toggle_admin_pack(interaction, self.pack.id)

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.danger, row=2)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.delete_admin_pack(interaction, self.pack.id)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_bunker_admin_panel(interaction)


class BunkerAdminExportView(discord.ui.View):
    def __init__(self, cog: Bunker, pack_id: int) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.pack_id = pack_id

    @discord.ui.button(label="Назад к паку", style=discord.ButtonStyle.primary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_PACK, pack_id=self.pack_id)


class BunkerAdminCategorySelect(discord.ui.Select):
    def __init__(self, cog: Bunker, pack: BunkerContentPack) -> None:
        self.cog = cog
        self.pack = pack
        options = [
            discord.SelectOption(
                label=PACK_FIELD_LABELS[field],
                value=field,
                description=f"{len(pack.content.get(field, ()))} строк",
            )
            for field in PACK_FIELDS
        ]
        super().__init__(placeholder="Категория контента", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.open_bunker_admin_panel(
            interaction,
            screen=ADMIN_PANEL_SCREEN_CATEGORY,
            pack_id=self.pack.id,
            field=self.values[0],
        )


class BunkerAdminCategoryView(discord.ui.View):
    def __init__(self, cog: Bunker, pack: BunkerContentPack, field: str) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.pack = pack
        self.field = field
        values = list(pack.content.get(field, ()))
        if values:
            self.add_item(BunkerAdminRemoveValueSelect(cog, pack, field, values[:25]))

    @discord.ui.button(label="Добавить строку", style=discord.ButtonStyle.success, row=1)
    async def add_value(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(BunkerPackValueModal(self.cog, self.pack.id, self.field))

    @discord.ui.button(label="Назад к паку", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_bunker_admin_panel(interaction, screen=ADMIN_PANEL_SCREEN_PACK, pack_id=self.pack.id)


class BunkerAdminRemoveValueSelect(discord.ui.Select):
    def __init__(self, cog: Bunker, pack: BunkerContentPack, field: str, values: list[str]) -> None:
        self.cog = cog
        self.pack = pack
        self.field = field
        self.values_by_index = values
        options = [discord.SelectOption(label=value[:100], value=str(index)) for index, value in enumerate(values)]
        super().__init__(placeholder="Удалить строку", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.remove_admin_pack_value(interaction, self.pack.id, self.field, self.values_by_index[int(self.values[0])])


class BunkerPackCreateModal(discord.ui.Modal, title="Создать пак Бункера"):
    name = discord.ui.TextInput(label="Название", max_length=80)
    description = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, required=False, max_length=500)

    def __init__(self, cog: Bunker) -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.create_admin_pack(interaction, name=str(self.name.value), description=str(self.description.value))


class BunkerPackRenameModal(discord.ui.Modal, title="Настройки пака"):
    name = discord.ui.TextInput(label="Название", max_length=80)
    description = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, required=False, max_length=500)

    def __init__(self, cog: Bunker, pack: BunkerContentPack) -> None:
        super().__init__()
        self.cog = cog
        self.pack_id = pack.id
        self.name.default = pack.name
        self.description.default = pack.description

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.rename_admin_pack(interaction, self.pack_id, name=str(self.name.value), description=str(self.description.value))


class BunkerPackImportModal(discord.ui.Modal, title="Импорт JSON пака"):
    raw_json = discord.ui.TextInput(label="JSON", style=discord.TextStyle.paragraph, max_length=4000)

    def __init__(self, cog: Bunker, pack: BunkerContentPack) -> None:
        super().__init__()
        self.cog = cog
        self.pack_id = pack.id
        self.raw_json.default = _pack_json_dump(pack)[:4000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.import_admin_pack_json(interaction, self.pack_id, str(self.raw_json.value))


class BunkerPackValueModal(discord.ui.Modal, title="Добавить строку"):
    value = discord.ui.TextInput(label="Текст", style=discord.TextStyle.paragraph, max_length=300)

    def __init__(self, cog: Bunker, pack_id: int, field: str) -> None:
        super().__init__()
        self.cog = cog
        self.pack_id = pack_id
        self.field = field

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.add_admin_pack_value(interaction, self.pack_id, self.field, str(self.value.value))


class BunkerSetupNavView(discord.ui.View):
    def __init__(
        self,
        cog: Bunker,
        setup_id: int,
        user_id: int,
        settings: BunkerSettings,
        *,
        screen: str,
        is_operator: bool = False,
        voice_url: str | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.setup_id = setup_id
        self.user_id = user_id
        self.settings = settings
        self.screen = screen
        self.is_operator = is_operator
        self.voice_url = voice_url
        self._build()

    def _build(self) -> None:
        self._add_button("Настройки", discord.ButtonStyle.primary if self.screen == "settings" else discord.ButtonStyle.secondary, self._settings, row=0)
        self._add_button("Как играть", discord.ButtonStyle.primary if self.screen == "rules" else discord.ButtonStyle.secondary, self._rules, row=0)
        self._add_button("Контент", discord.ButtonStyle.primary if self.screen == "content" else discord.ButtonStyle.secondary, self._content, row=0)
        self._add_button("Назад", discord.ButtonStyle.secondary, self._settings, row=0)
        if self.is_operator:
            self._add_button("Админ-режим", discord.ButtonStyle.danger, self._admin, row=0)
        if self.voice_url:
            self.add_item(discord.ui.Button(label="Перейти в голосовой", style=discord.ButtonStyle.link, url=self.voice_url, row=1))

    def _add_button(self, label: str, style: discord.ButtonStyle, callback, *, row: int) -> None:
        button = discord.ui.Button(label=label, style=style, row=row)
        button.callback = callback
        self.add_item(button)

    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Эта приватная панель открыта другим пользователем.", ephemeral=True)
        return False

    async def _settings(self, interaction: discord.Interaction) -> None:
        if await self._ensure_owner(interaction):
            await self.cog.open_setup_panel(interaction, screen="settings")

    async def _rules(self, interaction: discord.Interaction) -> None:
        if await self._ensure_owner(interaction):
            await self.cog.open_setup_panel(interaction, screen="rules")

    async def _content(self, interaction: discord.Interaction) -> None:
        if await self._ensure_owner(interaction):
            await self.cog.open_setup_panel(interaction, screen="content")

    async def _admin(self, interaction: discord.Interaction) -> None:
        if await self._ensure_owner(interaction):
            await self.cog.build_admin_bunker(interaction)


class BunkerSetupContentView(discord.ui.View):
    def __init__(
        self,
        cog: Bunker,
        setup_id: int,
        user_id: int,
        settings: BunkerSettings,
        packs: list[BunkerContentPack],
        *,
        is_operator: bool,
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.setup_id = setup_id
        self.user_id = user_id
        self.settings = settings
        self.packs = packs
        self.is_operator = is_operator
        self.add_item(BunkerSetupPackSelect(self))
        self._add_button("Настройки", discord.ButtonStyle.secondary, self._settings, row=1)
        self._add_button("Как играть", discord.ButtonStyle.secondary, self._rules, row=1)
        if is_operator:
            self._add_button("Админка паков", discord.ButtonStyle.primary, self._admin_panel, row=1)

    def _add_button(self, label: str, style: discord.ButtonStyle, callback, *, row: int) -> None:
        button = discord.ui.Button(label=label, style=style, row=row)
        button.callback = callback
        self.add_item(button)

    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Эта приватная панель открыта другим пользователем.", ephemeral=True)
        return False

    async def _settings(self, interaction: discord.Interaction) -> None:
        if await self._ensure_owner(interaction):
            await self.cog.open_setup_panel(interaction, screen="settings")

    async def _rules(self, interaction: discord.Interaction) -> None:
        if await self._ensure_owner(interaction):
            await self.cog.open_setup_panel(interaction, screen="rules")

    async def _admin_panel(self, interaction: discord.Interaction) -> None:
        if await self._ensure_owner(interaction):
            await self.cog.open_bunker_admin_panel(interaction)


class BunkerSetupPackSelect(discord.ui.Select):
    def __init__(self, owner: BunkerSetupContentView) -> None:
        self.owner = owner
        options = [
            discord.SelectOption(
                label="Встроенный контент",
                value="none",
                description="Только базовый набор Бункера",
                default=owner.settings.content_pack_id is None,
            )
        ]
        for pack in owner.packs[:24]:
            counts = sum(len(values) for values in pack.content.values())
            options.append(
                discord.SelectOption(
                    label=pack.name[:100],
                    value=str(pack.id),
                    description=f"{counts} строк; смешивается со встроенным",
                    default=owner.settings.content_pack_id == pack.id,
                )
            )
        super().__init__(placeholder="Пак для этой партии", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner.user_id:
            await interaction.response.send_message("Этот выбор открыт другим пользователем.", ephemeral=True)
            return

        raw_value = self.values[0]
        pack_id = None if raw_value == "none" else int(raw_value)
        await self.owner.cog.update_draft_settings(
            interaction,
            self.owner.setup_id,
            self.owner.user_id,
            replace(self.owner.settings, content_pack_id=pack_id),
            screen="content",
        )


class BunkerSettingsView(discord.ui.View):
    def __init__(self, cog: Bunker, setup_id: int, user_id: int, settings: BunkerSettings, *, is_operator: bool = False) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.setup_id = setup_id
        self.user_id = user_id
        self.settings = settings
        self.is_operator = is_operator
        self.add_item(BunkerModeSelect(self, row=0))
        self.add_item(BunkerSlotsSelect(self, row=1))
        self.add_item(BunkerTimerSelect(self, row=2))
        self.add_item(BunkerRoundsSelect(self, row=3))
        content_button = discord.ui.Button(label="Контент", style=discord.ButtonStyle.secondary, row=4)
        content_button.callback = self.open_content
        self.add_item(content_button)
        if is_operator:
            admin_button = discord.ui.Button(label="Админ-режим", style=discord.ButtonStyle.danger, row=4)
            admin_button.callback = self.start_admin_game
            self.add_item(admin_button)
        else:
            rules_button = discord.ui.Button(label="Как играть", style=discord.ButtonStyle.secondary, row=4)
            rules_button.callback = self.open_rules
            self.add_item(rules_button)

    async def open_rules(self, interaction: discord.Interaction) -> None:
        await self.cog.open_setup_panel(interaction, screen="rules")

    async def open_content(self, interaction: discord.Interaction) -> None:
        await self.cog.open_setup_panel(interaction, screen="content")

    async def start_admin_game(self, interaction: discord.Interaction) -> None:
        await self.cog.build_admin_bunker(interaction)

    @discord.ui.button(label="Публичный/приватный", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_visibility(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.update_draft_settings(interaction, self.setup_id, self.user_id, replace(self.settings, is_public=not self.settings.is_public))

    @discord.ui.button(label="Подсказки новичкам", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_newbies(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.update_draft_settings(
            interaction,
            self.setup_id,
            self.user_id,
            replace(self.settings, explain_for_newbies=not self.settings.explain_for_newbies),
        )

    @discord.ui.button(label="Пропущенный голос", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_vote_policy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        next_policy = VotePolicy.RANDOM if self.settings.missing_vote_policy == VotePolicy.ABSTAIN else VotePolicy.ABSTAIN
        await self.cog.update_draft_settings(
            interaction,
            self.setup_id,
            self.user_id,
            replace(self.settings, missing_vote_policy=next_policy),
        )


class BunkerModeSelect(discord.ui.Select):
    def __init__(self, owner: BunkerSettingsView, *, row: int) -> None:
        self.owner = owner
        super().__init__(
            placeholder=f"Режим: {owner.settings.mode.value}",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=mode.value, value=mode.value, default=mode == owner.settings.mode) for mode in GameMode],
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        mode = GameMode(self.values[0])
        rounds = recommended_rounds(self.owner.settings.slots, mode)
        await self.owner.cog.update_draft_settings(interaction, self.owner.setup_id, self.owner.user_id, replace(self.owner.settings, mode=mode, rounds=rounds))


class BunkerSlotsSelect(discord.ui.Select):
    def __init__(self, owner: BunkerSettingsView, *, row: int) -> None:
        self.owner = owner
        super().__init__(
            placeholder=f"Слоты: {owner.settings.slots}",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=str(slots), value=str(slots), default=slots == owner.settings.slots)
                for slots in range(6, 17)
            ],
            row=row,
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
    def __init__(self, owner: BunkerSettingsView, *, row: int) -> None:
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
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner.cog.update_draft_settings(
            interaction,
            self.owner.setup_id,
            self.owner.user_id,
            replace(self.owner.settings, timer_seconds=int(self.values[0])),
        )


class BunkerRoundsSelect(discord.ui.Select):
    def __init__(self, owner: BunkerSettingsView, *, row: int) -> None:
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
            row=row,
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
    status = "Можно строить новые бункеры в этой категории. Один пользователь может хостить только один активный бункер."
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
    host = next((player for player in players if player.user_id == game.host_id), None)
    embed.add_field(name="Хост", value=format_player_name(host) if host else f"<@{game.host_id}>", inline=True)
    embed.add_field(name="Режим", value=game.settings.mode.value, inline=True)
    if game.recent_events:
        embed.add_field(name="Последние события", value="\n".join(game.recent_events[-5:])[:1024], inline=False)
    else:
        embed.add_field(name="Лобби", value="Игроки заходят в бункер и нажимают 'Готов'.", inline=False)
    return embed


def _private_panel_embed(
    game: BunkerGame,
    players: list[BunkerPlayer],
    player: BunkerPlayer | None,
    *,
    is_operator: bool,
    status: str | None = None,
) -> discord.Embed:
    alive = sum(1 for candidate in players if candidate.is_alive)
    ready = sum(1 for candidate in players if candidate.ready_at is not None and not candidate.is_host)
    non_hosts = sum(1 for candidate in players if not candidate.is_host and candidate.is_active)
    title = "Панель Бункера"
    if game.is_admin_game:
        title += " · админ-режим"
    embed = discord.Embed(title=title, color=discord.Color.dark_teal())
    embed.description = status or "Выбери доступное действие. Эта панель видна только тебе."
    embed.add_field(name="Фаза", value=game.state.value, inline=True)
    embed.add_field(name="Игроки", value=f"{len(players)}/{game.settings.slots}, живых: {alive}", inline=True)
    embed.add_field(name="Готовность", value=f"{ready}/{non_hosts}", inline=True)
    if player is None:
        embed.add_field(name="Твой статус", value="не в бункере", inline=False)
    else:
        flags: list[str] = []
        if player.is_host:
            flags.append("хост")
        if player.ready_at is not None and not player.is_host:
            flags.append("готов")
        if player.is_fake:
            flags.append("тест-бот")
        if player.is_eliminated:
            flags.append("выгнан")
        embed.add_field(name="Твой статус", value=", ".join(flags) or "участник", inline=False)
    if is_operator:
        embed.set_footer(text="Оператор Бункера: доступны тест-боты, форс-старт, смена фаз и очистка каналов.")
    return embed


def _status_embed(message: str) -> discord.Embed:
    return discord.Embed(title="Бункер", description=message, color=discord.Color.dark_teal())


def _settings_embed(settings: BunkerSettings) -> discord.Embed:
    embed = discord.Embed(title="Настройки Бункера", color=discord.Color.dark_teal())
    embed.add_field(name="Тип", value="публичный" if settings.is_public else "приватный", inline=True)
    embed.add_field(name="Режим", value=settings.mode.value, inline=True)
    embed.add_field(name="Слоты", value=str(settings.slots), inline=True)
    embed.add_field(name="Раунды", value=str(settings.rounds), inline=True)
    embed.add_field(name="Таймер", value=f"{settings.timer_seconds} сек.", inline=True)
    embed.add_field(name="Подсказки", value="вкл" if settings.explain_for_newbies else "выкл", inline=True)
    embed.add_field(name="Нет голоса", value=settings.missing_vote_policy.value, inline=True)
    embed.add_field(name="Пак", value=f"custom #{settings.content_pack_id}" if settings.content_pack_id else "встроенный", inline=True)
    return embed


def _setup_content_embed(settings: BunkerSettings, packs: list[BunkerContentPack]) -> discord.Embed:
    selected = next((pack for pack in packs if pack.id == settings.content_pack_id), None)
    embed = discord.Embed(title="Контент Бункера", color=discord.Color.dark_teal())
    embed.description = "Выбранный кастомный пак смешивается со встроенным контентом. Если пак выключен или удален, игра использует встроенный набор."
    embed.add_field(name="Текущий пак", value=selected.name if selected else "встроенный контент", inline=False)
    if packs:
        lines = [f"{pack.name}: {_pack_total(pack)} строк" for pack in packs[:10]]
        embed.add_field(name="Доступные паки", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Доступные паки", value="Кастомных паков пока нет. Создай их через /bunkeradminpanel.", inline=False)
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


def _admin_packs_embed(packs: list[BunkerContentPack], *, status: str | None = None) -> discord.Embed:
    embed = discord.Embed(title="Админ-панель Бункера", color=discord.Color.dark_teal())
    embed.description = status or "Создавай кастомные паки и наполняй их строками через категории или JSON."
    if packs:
        lines = [
            f"{pack.id}. {'вкл' if pack.is_enabled else 'выкл'} · {pack.name} · {_pack_total(pack)} строк"
            for pack in packs[:15]
        ]
        embed.add_field(name="Паки", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Паки", value="Паков пока нет.", inline=False)
    return embed


def _admin_access_embed(guild: discord.Guild, settings: Any, *, status: str | None = None) -> discord.Embed:
    operator_role = guild.get_role(settings.operator_role_id) if settings.operator_role_id is not None else None
    interest_role = guild.get_role(settings.interest_role_id) if settings.interest_role_id is not None else None
    embed = discord.Embed(title="Доступ Бункера", color=discord.Color.dark_teal())
    embed.description = status or "Настрой роли, которые управляют видимостью и админскими комнатами Бункера."
    embed.add_field(
        name="Operator-role",
        value=operator_role.mention if operator_role is not None else "не назначена; админ-лобби недоступно",
        inline=False,
    )
    embed.add_field(
        name="Роль интереса",
        value=interest_role.mention if interest_role is not None else "не назначена; public-комнаты видны всем",
        inline=False,
    )
    embed.add_field(
        name="Правило",
        value="Админ-лобби всегда видят только bot и operator-role. Роль интереса применяется только к обычным public-комнатам.",
        inline=False,
    )
    return embed


def _admin_pack_embed(pack: BunkerContentPack, *, status: str | None = None) -> discord.Embed:
    embed = discord.Embed(title=f"Пак: {pack.name}", color=discord.Color.dark_teal())
    embed.description = status or (pack.description or "Описание не задано.")
    embed.add_field(name="Статус", value="включен" if pack.is_enabled else "выключен", inline=True)
    embed.add_field(name="Всего строк", value=str(_pack_total(pack)), inline=True)
    counts = [f"{PACK_FIELD_LABELS[field]}: {len(pack.content.get(field, ()))}" for field in PACK_FIELDS]
    embed.add_field(name="Категории", value="\n".join(counts)[:1024], inline=False)
    return embed


def _admin_category_embed(pack: BunkerContentPack, field: str, *, status: str | None = None) -> discord.Embed:
    values = list(pack.content.get(field, ()))
    embed = discord.Embed(title=f"{pack.name} · {PACK_FIELD_LABELS[field]}", color=discord.Color.dark_teal())
    embed.description = status or f"Строк в категории: {len(values)}."
    if values:
        lines = [f"{index + 1}. {value}" for index, value in enumerate(values[:15])]
        if len(values) > 15:
            lines.append(f"...еще {len(values) - 15}")
        embed.add_field(name="Значения", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="Значения", value="Пока пусто.", inline=False)
    return embed


def _pack_export_embed(pack: BunkerContentPack) -> discord.Embed:
    raw = _pack_json_dump(pack)
    if len(raw) > 3900:
        raw = raw[:3900] + "\n..."
    embed = discord.Embed(title=f"JSON экспорт: {pack.name}", color=discord.Color.green())
    embed.description = f"```json\n{raw}\n```"
    return embed


def _pack_json_dump(pack: BunkerContentPack) -> str:
    payload = {
        "name": pack.name,
        "description": pack.description,
        "content": {field: list(pack.content.get(field, ())) for field in PACK_FIELDS},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _pack_total(pack: BunkerContentPack) -> int:
    return sum(len(values) for values in pack.content.values())


def format_player_name(player: BunkerPlayer | None) -> str:
    if player is None:
        return "неизвестный игрок"
    if player.is_fake:
        return player.display_name
    return f"<@{player.user_id}>"


def _same_discord_message(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    left_id = getattr(left, "id", None)
    right_id = getattr(right, "id", None)
    if left_id is not None and right_id is not None:
        return left_id == right_id
    return left is right


def _voice_channel_url(channel: discord.VoiceChannel | None) -> str | None:
    if channel is None:
        return None
    return f"https://discord.com/channels/{channel.guild.id}/{channel.id}"


def _room_number(name: str, fallback: int) -> str:
    match = re.search(r"(\d+)", name)
    return match.group(1) if match else str(fallback)


def _missing_setup_panel_permissions(permissions: discord.Permissions) -> list[str]:
    required = {
        "View Channel": permissions.view_channel,
        "Send Messages": permissions.send_messages,
        "Embed Links": permissions.embed_links,
    }
    return [name for name, allowed in required.items() if not allowed]


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
