# Sharky - Discord Bot for The Trench

A lightweight bot that:
1. **Greets new members** - posts a welcome message in a designated channel (e.g. `#welcome`) when someone joins.
2. **Redirects questions** - if someone asks a question in `#general`, Sharky replies pointing them to your support channel (e.g. `#clash-of-clans-support`), since general isn't monitored for support.

No paid APIs, no AI calls - pure event-driven logic, so there's no ongoing cost and behavior is fully predictable.

---

## 1. Create the bot in Discord

1. Go to https://discord.com/developers/applications -> your **Sharky** application.
2. Go to the **Bot** tab:
   - Click **Reset Token** / **Copy** to get your bot token. Keep this secret - it's shown only once.
   - Under **Privileged Gateway Intents**, enable:
     - `SERVER MEMBERS INTENT` (required for welcome messages)
     - `MESSAGE CONTENT INTENT` (required to read messages for question detection)
3. Go to **OAuth2 -> URL Generator**:
   - Scopes: `bot`
   - Bot permissions: `Read Messages/View Channels`, `Send Messages`, `Read Message History`
4. Open the generated URL, pick The Trench, and authorize it.

## 2. Install dependencies

```bash
pip install -r requirements.txt
```

(On Linux, e.g. on your `fmt-server` box, you may want a venv:)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Configure

```bash
cp config.example.json config.json
```

Edit `config.json`:
- `welcome_channel_name`: channel name (no `#`) where new-member greetings get posted. Must already exist.
- `general_channel_names`: channels Sharky watches for questions (currently just `general`).
- `support_channel_name`: the channel name to redirect people to (e.g. `clash-of-clans-support`).
- `redirect_message_template`: supports `{member}` (mention) and `{support_channel}` (clickable mention if the channel exists, else plain text).
- `welcome_message_template`: supports `{user}` (mention), `{member_name}`, `{server}`, `{member_count}`.
- `token`: paste your bot token here, **or** leave blank and set the `DISCORD_BOT_TOKEN` environment variable instead (recommended if this lives in a git repo - never commit a real token).

## 4. Run it

```bash
export DISCORD_BOT_TOKEN="your-token-here"   # if not using config.json for the token
python bot.py
```

You should see:
```
Logged in as Sharky#1234 (id: ...)
```

Test with `!ping` in any channel Sharky can see, and try posting something like "how do I install the bot?" in `#general` - Sharky should reply redirecting you to your support channel.

## How question detection works

Sharky treats a message as a question if it contains a `?`, or starts with words like "how", "what", "can I", "does", "anyone know", etc. - no AI, just a quick text check.

**Per-user cooldown**: `redirect_cooldown_seconds` (default 15s) stops Sharky from replying to the same person repeatedly if they send several question-shaped messages in a row.

## Running it long-term

Since you already run `fmt-server` for other background services (Voicebox TTS), this is a good candidate to run there too:

```bash
nohup python3 bot.py > bot.log 2>&1 &
```

Or set it up as a systemd service for auto-restart on crash/reboot - happy to write that unit file if you want to go that route.