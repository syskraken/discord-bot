"""
Sharky - Discord Bot for The Trench
Features:
  1. Greets new members by posting in a #welcome channel
  2. Detects questions posted in #general and redirects the asker to the
     correct support channel (e.g. #clash-of-clans-support)

Setup:
  1. pip install -r requirements.txt
  2. Copy config.example.json to config.json and fill in your values
  3. python bot.py
"""

import json
import logging
import os
import time
import uvicorn
import discord
from discord.ext import commands
from threading import Thread
from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Setup & config
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("sharky-bot")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

TOKEN = os.environ.get("DISCORD_BOT_TOKEN") or config.get("token")
WELCOME_CHANNEL_NAME = config["welcome_channel_name"]
GENERAL_CHANNEL_NAMES = set(config["general_channel_names"])
SUPPORT_CHANNEL_NAME = config["support_channel_name"]
WELCOME_MESSAGE_TEMPLATE = config["welcome_message_template"]
REDIRECT_MESSAGE_TEMPLATE = config["redirect_message_template"]
COMMAND_PREFIX = config.get("command_prefix", "!")
REDIRECT_COOLDOWN_SECONDS = config.get("redirect_cooldown_seconds", 15)

intents = discord.Intents.default()
intents.members = True          # required to receive on_member_join
intents.message_content = True  # required to read message text for question detection

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# Tracks the last time we redirected *a given user*, so we don't pile on
# if they send several question-shaped messages in a row.
_last_redirect_at = {}


# ---------------------------------------------------------------------------
# Question detection
# ---------------------------------------------------------------------------

def is_question(message_content: str) -> bool:
    """
    Lightweight heuristic to detect whether a message is asking a question.
    Triggers on a '?' or on common question-starting words.
    """
    text = message_content.strip().lower()
    if "?" in text:
        return True
    question_starters = (
        "how ", "what ", "where ", "when ", "why ", "who ", "can i",
        "can you ", "does ", "is there", "are there", "do i", "should i",
        "anyone know", "anybody know", "not working ", "error ", 
    )
    return text.startswith(question_starters)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    log.info("Logged in as %s (id: %s)", bot.user, bot.user.id)
    log.info("Watching general channels: %s", GENERAL_CHANNEL_NAMES)
    log.info("Welcome channel: %s", WELCOME_CHANNEL_NAME)


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    welcome_channel = discord.utils.find(
        lambda c: c.name == WELCOME_CHANNEL_NAME, guild.text_channels
    )

    if welcome_channel is None:
        log.warning(
            "No channel named '%s' found in guild '%s' - cannot post welcome message",
            WELCOME_CHANNEL_NAME, guild.name,
        )
        return

    message = WELCOME_MESSAGE_TEMPLATE.format(
        user=member.mention,
        member_name=member.display_name,
        server=guild.name,
        member_count=guild.member_count,
    )

    try:
        await welcome_channel.send(message)
        log.info("Welcomed %s in #%s", member, welcome_channel.name)
    except discord.Forbidden:
        log.error(
            "Missing permission to send messages in #%s - check bot role permissions",
            welcome_channel.name,
        )


@bot.event
async def on_message(message: discord.Message):
    # Ignore the bot's own messages and other bots
    if message.author.bot:
        return

    # Let command processing still work (e.g. !ping below)
    await bot.process_commands(message)

    # Only redirect in configured "general" channels
    if message.channel.name not in GENERAL_CHANNEL_NAMES:
        return

    if not is_question(message.content):
        return

    # Per-user cooldown so we don't pile on if someone sends several
    # question-shaped messages in a row
    now = time.time()
    last = _last_redirect_at.get(message.author.id, 0)
    if now - last < REDIRECT_COOLDOWN_SECONDS:
        return
    _last_redirect_at[message.author.id] = now

    support_channel = discord.utils.find(
        lambda c: c.name == SUPPORT_CHANNEL_NAME, message.guild.text_channels
    )
    support_mention = support_channel.mention if support_channel else f"#{SUPPORT_CHANNEL_NAME}"

    reply_text = REDIRECT_MESSAGE_TEMPLATE.format(
        user=message.author.mention,
        support_channel=support_mention,
    )

    try:
        await message.reply(reply_text, mention_author=False)
        log.info("Redirected %s to #%s", message.author, SUPPORT_CHANNEL_NAME)
    except discord.Forbidden:
        log.error("Missing permission to send messages in #%s", message.channel.name)


# ---------------------------------------------------------------------------
# Optional: simple admin/utility command to confirm the bot is alive
# ---------------------------------------------------------------------------

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send(f"Pong! {round(bot.latency * 1000)}ms")


web_app = FastAPI(title="Sharky Health Check")

@web_app.get("/health")
def health_check():
    """Endpoint for Render health checks and UptimeRobot pings."""
    return {"status": "online", "bot": "Sharky"}

def run_health_server():
    # Render automatically passes the assigned port via the PORT environment variable
    port = int(os.environ.get("PORT", 8000))
    # Run a quiet uvicorn instance so it doesn't clutter your Discord logs
    uvicorn.run(web_app, host="0.0.0.0", port=port, log_level="warning")


# ---------------------------------------------------------------------------
# Execution Execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit(
            "No bot token found. Set DISCORD_BOT_TOKEN env var or 'token' in config.json"
        )
    
    # 1. Start the web server in a separate background thread
    log.info("Starting background health-check server...")
    Thread(target=run_health_server, daemon=True).start()
    
    # 2. Run the Discord Bot (This keeps the main process alive)
    log.info("Starting Sharky Discord Bot...")
    bot.run(TOKEN)