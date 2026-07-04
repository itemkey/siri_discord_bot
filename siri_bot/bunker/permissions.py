from __future__ import annotations

from dataclasses import dataclass

import discord


@dataclass(frozen=True)
class PermissionPlan:
    everyone_view_channel: bool
    everyone_connect: bool
    member_view_channel: bool
    member_send_messages: bool
    member_connect: bool
    member_speak: bool


PRIVATE_BUNKER_PERMISSION_PLAN = PermissionPlan(
    everyone_view_channel=False,
    everyone_connect=False,
    member_view_channel=True,
    member_send_messages=True,
    member_connect=True,
    member_speak=True,
)


def build_private_text_overwrites(
    guild: discord.Guild,
    members: list[discord.Member],
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False)
    }
    for member in members:
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )

    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
        )

    return overwrites


def build_lobby_text_overwrites(
    guild: discord.Guild,
    members: list[discord.Member],
    interest_role: discord.Role | None = None,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=interest_role is None,
            send_messages=False,
            read_message_history=True,
        )
    }
    if interest_role is not None:
        overwrites[interest_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        )
    for member in members:
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )

    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
        )

    return overwrites


def build_admin_text_overwrites(
    guild: discord.Guild,
    operator_role: discord.Role,
    members: list[discord.Member],
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False)
    }
    overwrites[operator_role] = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
    )
    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
        )
    return overwrites


def build_private_voice_overwrites(
    guild: discord.Guild,
    members: list[discord.Member],
    spectator_role: discord.Role | None = None,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False)
    }
    if spectator_role is not None:
        overwrites[spectator_role] = discord.PermissionOverwrite(
            view_channel=True,
            connect=False,
            speak=False,
            stream=False,
        )
    for member in members:
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
        )

    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            manage_channels=True,
            move_members=True,
        )

    return overwrites


def build_admin_voice_overwrites(
    guild: discord.Guild,
    operator_role: discord.Role,
    members: list[discord.Member],
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False)
    }
    overwrites[operator_role] = discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
    )
    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            manage_channels=True,
            move_members=True,
        )
    return overwrites


async def grant_member_access(
    text_channel: discord.TextChannel | None,
    voice_channel: discord.VoiceChannel | None,
    member: discord.Member,
) -> None:
    if text_channel is not None:
        await text_channel.set_permissions(
            member,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            reason="Bunker player joined",
        )

    if voice_channel is not None:
        await voice_channel.set_permissions(
            member,
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            reason="Bunker player joined",
        )
