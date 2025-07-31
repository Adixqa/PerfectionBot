#lockdown.py

import asyncio
import discord
from discord.ext import commands
from collections import defaultdict

from PerfectionBot.config.yamlHandler import get_value

_pending_lockdowns: dict[int, dict[int, dict]] = defaultdict(dict)


def get_pending_lockdown(guild_id: int, channel_id: int) -> dict | None:
    return _pending_lockdowns.get(guild_id, {}).get(channel_id)


async def initiate_lockdown(
    guild: discord.Guild,
    member: discord.Member,
    word: str,
    evt: str
):
    lockdown_role = guild.get_role(int(get_value("roles", "lockdown_ID")))
    mod_role = guild.get_role(int(get_value("roles", "mod_ID")))

    await member.add_roles(lockdown_role, reason="Pre-punishment lockdown")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        lockdown_role:      discord.PermissionOverwrite(read_messages=True),
        mod_role:           discord.PermissionOverwrite(read_messages=True),
        guild.me:           discord.PermissionOverwrite(read_messages=True),
    }
    channel = await guild.create_text_channel(
        f"lockdown-{member.id}", overwrites=overwrites
    )

    _pending_lockdowns[guild.id][channel.id] = {
        "user_id": member.id,
        "word":    word,
        "action":  evt
    }

    await channel.send(
        f"🚨 **Lockdown** for {member.mention}\n"
        f"Triggered `{evt.upper()}` for word `{word}`.\n\n"
        "`!confirm` to execute, `!revoke` to cancel."
    )


async def handle_confirm(
    ctx: commands.Context,
    flag_memory: dict[int, dict[int, dict]],
    save_flags
):
    if not ctx.channel.name.startswith("lockdown-"):
        return

    pend = _pending_lockdowns.get(ctx.guild.id, {}).pop(ctx.channel.id, None)
    if not pend:
        return await ctx.send("Nothing pending here.")

    user_id = pend["user_id"]
    action = pend["action"]
    member = ctx.guild.get_member(user_id)

    try:
        if action == "kick":
            await ctx.guild.kick(member or user_id, reason="Lockdown confirmed")
            await ctx.send(f"👢 {member.mention if member else '<unknown>'} has been kicked.")
        else:
            await ctx.guild.ban(
                member or user_id,
                reason="Lockdown confirmed ✅",
                delete_message_days=0
            )
            await ctx.send(f"⛔ {member.mention if member else '<unknown>'} has been banned.")
            flag_memory.setdefault(ctx.guild.id, {}).pop(user_id, None)
            await save_flags(ctx.guild)
    except Exception as e:
        await ctx.send(f"❌ Failed to {action}: {e}")

    lockdown_role = ctx.guild.get_role(int(get_value("roles", "lockdown_ID")))
    if member:
        try:
            await member.remove_roles(lockdown_role, reason="Lockdown complete")
        except Exception:
            pass

    await asyncio.sleep(1)
    await ctx.channel.delete()


async def handle_revoke(
    ctx: commands.Context,
    flag_memory: dict[int, dict[int, dict]],
    save_flags
):
    if not ctx.channel.name.startswith("lockdown-"):
        return

    pend = _pending_lockdowns.get(ctx.guild.id, {}).pop(ctx.channel.id, None)
    if not pend:
        return await ctx.send("Nothing pending here.")

    user_id = pend["user_id"]
    word = pend["word"]
    member = ctx.guild.get_member(user_id)

    lockdown_role = ctx.guild.get_role(int(get_value("roles", "lockdown_ID")))
    if member:
        try:
            await member.remove_roles(lockdown_role, reason="Lockdown revoked")
        except Exception:
            pass

        user_mem = flag_memory.get(ctx.guild.id, {}).get(user_id)
        if user_mem:
            user_mem.setdefault("words", {})[word] = max(
                user_mem["words"].get(word, 1) - 1,
                0
            )
            await save_flags(ctx.guild)

    await ctx.send(f"🔄 Lockdown revoked.")

    await asyncio.sleep(1)
    await ctx.channel.delete()