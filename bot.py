"""
Sharky - Discord Bot for The Trench
Features:
  1. Greets new members by posting in a #welcome channel
  2. Detects questions posted in #general and redirects the asker to the
     correct support channel (e.g. #clash-of-clans-support) - this always
     triggers, whether the owner is online or not
  3. In every other channel, detects questions and - only when the owner
     (Poseidon) is offline - tells the asker to post in #ask-poseidon

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
RULES_CHANNEL_NAME = config.get("rules_channel_name", "rules")
WELCOME_MESSAGE_TEMPLATE = config["welcome_message_template"]
REDIRECT_MESSAGE_TEMPLATE = config["redirect_message_template"]
COMMAND_PREFIX = config.get("command_prefix", "!")
REDIRECT_COOLDOWN_SECONDS = config.get("redirect_cooldown_seconds", 15)
OWNER_ID = int(config.get("owner_id") or 0)
ASK_POSEIDON_CHANNEL_NAME = config.get("ask_poseidon_channel_name", "ask-poseidon")
OFFLINE_REDIRECT_MESSAGE_TEMPLATE = config.get(
    "offline_redirect_message_template",
    "{user} Poseidon is not online right now. Please post your question in "
    "{ask_poseidon_channel} and Poseidon will answer as soon as they are back.",
)
# Keywords that mean a question is about the KrakenPrime CoC farming bot
BOT_QUESTION_KEYWORDS = [
    k.lower() for k in config.get(
        "bot_question_keywords",
        [
            "krakenprime", "kraken prime", "kraken bot", "kraken",
            "farming bot", "farm bot", "auto farm", "autofarm",
            "coc bot", "clash bot", "the bot",
        ],
    )
]
# Keywords that mean the user is describing a problem/issue
ISSUE_KEYWORDS = [
    k.lower() for k in config.get(
        "issue_keywords",
        [
            "not working", "doesn't work", "dont work", "doesnt work",
            "won't", "wont ", "error", "crash", "stuck", "broken",
            "problem", "issue", "fail", "bug", "can't", "cant ",
            "help me", "need help",
        ],
    )
]

intents = discord.Intents.default()
intents.members = True          # required to receive on_member_join
intents.message_content = True  # required to read message text for question detection
intents.presences = True        # required to see whether the owner is online

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


def is_about_krakenprime(message_content: str) -> bool:
    """
    Detect whether a message is talking about the KrakenPrime CoC farming bot,
    based on the configured keyword list.
    """
    text = message_content.lower()
    return any(keyword in text for keyword in BOT_QUESTION_KEYWORDS)


def mentions_issue(message_content: str) -> bool:
    """
    Detect whether a message sounds like the user is experiencing a problem
    (errors, crashes, something not working), based on the configured list.
    """
    text = message_content.lower()
    return any(keyword in text for keyword in ISSUE_KEYWORDS)


def channel_mention(guild: discord.Guild, name: str) -> str:
    """
    Return a clickable channel mention (e.g. <#123>) for the channel with the
    given name, falling back to a plain "#name" if no such channel exists.
    """
    channel = discord.utils.find(lambda c: c.name == name, guild.text_channels)
    return channel.mention if channel else f"#{name}"


def owner_is_online(guild: discord.Guild) -> bool:
    """
    Check whether the configured owner (Poseidon) appears online in this guild.
    Counts online/idle/dnd as online; offline and invisible count as offline.
    Requires the Presence Intent to be enabled in the Discord Developer Portal,
    otherwise every member always appears offline.
    """
    if not OWNER_ID:
        log.warning("owner_id is not set in config.json - treating owner as offline")
        return False
    member = guild.get_member(OWNER_ID)
    if member is None:
        return False
    return member.status != discord.Status.offline


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
        rules_channel=channel_mention(guild, RULES_CHANNEL_NAME),
    )

    # Build an embed so we can show the new member's profile picture.
    embed = discord.Embed(description=message, color=discord.Color.blue())
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Member #{guild.member_count}")

    try:
        # Mention in content so the new member actually gets pinged
        # (mentions inside an embed do not trigger a notification).
        await welcome_channel.send(content=member.mention, embed=embed)
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

    if message.guild is None or not is_question(message.content):
        return

    # Only act on questions about the KrakenPrime bot or users reporting
    # an issue - other chatter is left alone (applies to all channels).
    if not (is_about_krakenprime(message.content) or mentions_issue(message.content)):
        return

    # --- #general: always redirect questions to the support channel, ---
    # --- whether the owner is online or not                          ---
    if message.channel.name in GENERAL_CHANNEL_NAMES:
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
            general_channel=message.channel.mention,
        )

        try:
            await message.reply(reply_text, mention_author=False)
            log.info("Redirected %s to #%s", message.author, SUPPORT_CHANNEL_NAME)
        except discord.Forbidden:
            log.error("Missing permission to send messages in #%s", message.channel.name)
        return

    # --- Every other channel: if the owner is offline, point the asker ---
    # --- to #ask-poseidon (no point telling them to ask there if they  ---
    # --- already are)                                                  ---
    if message.channel.name == ASK_POSEIDON_CHANNEL_NAME:
        return

    if owner_is_online(message.guild):
        return

    now = time.time()
    last = _last_redirect_at.get(message.author.id, 0)
    if now - last < REDIRECT_COOLDOWN_SECONDS:
        return
    _last_redirect_at[message.author.id] = now

    ask_channel = discord.utils.find(
        lambda c: c.name == ASK_POSEIDON_CHANNEL_NAME, message.guild.text_channels
    )
    ask_mention = ask_channel.mention if ask_channel else f"#{ASK_POSEIDON_CHANNEL_NAME}"

    reply_text = OFFLINE_REDIRECT_MESSAGE_TEMPLATE.format(
        user=message.author.mention,
        ask_poseidon_channel=ask_mention,
    )

    try:
        await message.reply(reply_text, mention_author=False)
        log.info(
            "Owner offline - pointed %s to #%s from #%s",
            message.author, ASK_POSEIDON_CHANNEL_NAME, message.channel.name,
        )
    except discord.Forbidden:
        log.error("Missing permission to send messages in #%s", message.channel.name)


# ---------------------------------------------------------------------------
# Optional: simple admin/utility command to confirm the bot is alive
# ---------------------------------------------------------------------------

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send(f"Pong! {round(bot.latency * 1000)}ms")


web_app = FastAPI(title="Sharky Health Check")

@web_app.head("/health")
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
