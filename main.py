import discord
from discord.ext import commands
from discord.utils import get
import asyncio

from config.yamlHandler import get_value

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True

TOKEN = get_value("tokens", "bot")

bot = commands.Bot(command_prefix=get_value("behaviour", "COMMAND_PREFIX"), intents=intents)

@bot.event
async def on_ready():
    print(f'Bot online. ID: {bot.user}')

bot.run(TOKEN)