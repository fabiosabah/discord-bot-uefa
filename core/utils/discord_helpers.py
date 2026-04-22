# -*- coding: utf-8 -*-
import discord


class PartialMember:
    """Stand-in for a Discord member that is no longer in the guild."""

    def __init__(self, id: int, display_name: str):
        self.id = id
        self.display_name = display_name
        self.name = display_name

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


async def resolve_member(guild: discord.Guild, member_id: int) -> discord.Member | PartialMember:
    """Return a guild member, fetching from the API if needed.

    Falls back to PartialMember if the user has left the guild.
    """
    member = guild.get_member(member_id)
    if member:
        return member
    try:
        return await guild.fetch_member(member_id)
    except discord.NotFound:
        return PartialMember(member_id, f"Usuário {member_id}")
