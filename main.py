import asyncio
import discord
from discord.ext import commands
from PerfectionBot.config.yamlHandler import get_value
from PerfectionBot.scripts import yt

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True

bot = commands.Bot(
    command_prefix=get_value("behaviour", "COMMAND_PREFIX"),
    intents=intents
)

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user} (ID: {bot.user.id})")
    bot.loop.create_task(yt.monitor_channel(bot))

bot.run(get_value("tokens", "bot"))