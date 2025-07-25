import discord
from PerfectionBot.config.yamlHandler import get_value

ICON_URLS = {
    "warn": get_value("ICONS", "icon_warn"),
    "mute": get_value("ICONS", "icon_mute"),
    "kick": get_value("ICONS", "icon_kick"),
    "ban": get_value("ICONS", "icon_ban"),
    "clear": get_value("ICONS", "icon_clear"),
    "adjust": get_value("ICONS", "icon_adjust"),
    "info": get_value("ICONS", "icon_info"),
    "fail": get_value("ICONS", "icon_fail")
}

async def log_to_channel(
    guild: discord.Guild,
    message: str,
    color: discord.Color = discord.Color.greyple(),
    event_type: str = "info"
):
    try:
        log_channel_id = get_value("behaviour", "LOG_ID")
        channel = guild.get_channel(int(log_channel_id))
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        icon = ICON_URLS.get(event_type.lower(), ICON_URLS["info"])

        embed = discord.Embed(
            description=message,
            color=color,
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        embed.set_thumbnail(url=icon)

        await channel.send(embed=embed)

    except Exception as e:
        print(f"[log.py] Logging failed: {e}")