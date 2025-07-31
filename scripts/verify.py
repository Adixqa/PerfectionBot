#verify

import discord
from PerfectionBot.scripts.log import log_to_channel
from PerfectionBot.config.yamlHandler import get_value

async def GetVerifyMsg(channel: discord.TextChannel) -> discord.Message | None:
    if not channel:
        await log_to_channel(
            channel.guild,
            "Verify channel not found. Is the ID correct and does bot have access?",
            discord.Color.red(),
            "fail"
        )
        return None

    try:
        pins = await channel.pins()
        for msg in pins:
            if "Verify here" in msg.content:
                return msg
    except Exception as e:
        await log_to_channel(channel.guild, f"❌ Failed to get pinned verify message: {e}", discord.Color.red(), "fail")

    try:
        msg = await channel.send("Verify here\n\n" + get_value("VERIFY_CHNL_MESSAGE"))
        await msg.add_reaction("✅")
        await msg.pin()

        async for m in channel.history(limit=5, after=msg.created_at):
            if m.type == discord.MessageType.pins_add:
                try:
                    await m.delete()
                except:
                    pass

        return msg
    except Exception as e:
        await log_to_channel(channel.guild, f"❌ Could not create verify message: {e}", discord.Color.red(), "fail")
        return None

async def add_role(guild: discord.Guild, user: discord.Member):
    try:
        verified_id = int(get_value("roles", "verified_ID"))
        verified = discord.utils.get(guild.roles, id=verified_id)
        if verified:
            await user.add_roles(verified)
        else:
            await log_to_channel(guild, f"❌ Verified role ID {verified_id} not found", discord.Color.red(), "fail")
    except Exception as e:
        await log_to_channel(guild, f"❌ Failed to assign verified role: {e}", discord.Color.red(), "fail")

async def ResetVerification(guild: discord.Guild, verify_msg_ids: dict) -> str:
    verified_id = get_value("roles", "verified_ID")
    if not verified_id:
        return "❌ No verification role is currently set."

    verified_role = discord.utils.get(guild.roles, id=int(verified_id))
    if not verified_role:
        return "❌ Could not find the verified role in this server."

    count = 0
    for member in guild.members:
        if verified_role in member.roles:
            try:
                await member.remove_roles(verified_role, reason="Verification reset")
                count += 1
            except Exception as e:
                await log_to_channel(guild, f"⚠️ Couldn't remove role from {member}: {e}", discord.Color.orange(), "warn")

    verify_channel_id = get_value("VERIFY_ID")
    if not verify_channel_id:
        return "❌ No verify channel ID is set."

    ch = guild.get_channel(int(verify_channel_id))
    if not ch:
        return "❌ Could not find verification channel."

    old_msg_id = verify_msg_ids.get(guild.id)
    if old_msg_id:
        try:
            msg = await ch.fetch_message(old_msg_id)
            await msg.unpin()
            await msg.delete()
        except Exception as e:
            await log_to_channel(guild, f"⚠️ Failed to delete old verify message: {e}", discord.Color.orange(), "warn")

    new_msg = await GetVerifyMsg(ch)
    if new_msg:
        verify_msg_ids[guild.id] = new_msg.id
        return f"✅ Reset complete."
    else:
        return "❌ Failed to create new verify message."