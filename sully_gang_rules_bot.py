import os
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Only this role can use /rules
RULES_ROLE_ID = int(os.getenv("RULES_ROLE_ID", "1467182548894351505"))

# Role users get from reacting to the rules message
VERIFY_ROLE_ID = int(os.getenv("VERIFY_ROLE_ID", "1491590761689649282"))
VERIFY_EMOJI = "👍"

# Mentioned user in the rules text
STREAMER_USER_ID = int(os.getenv("STREAMER_USER_ID", "831542616188256347"))

# Channel where the bot posts the rules embed on startup
RULES_CHANNEL_ID = int(os.getenv("RULES_CHANNEL_ID", "0"))

# Channel where moderation logs are sent
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "1494837485984022658"))

# Optional: existing rules message ID to reuse after restart
RULES_MESSAGE_ID = int(os.getenv("RULES_MESSAGE_ID", "0"))

# Optional: faster slash-command sync for one server
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# Timeout durations
ONE_HOUR = timedelta(hours=1)
ONE_DAY = timedelta(days=1)
ONE_WEEK = timedelta(weeks=1)

# Detection windows
SPAM_WINDOW_SECONDS = 10
SPAM_REPEAT_COUNT = 5
SPAM_FLOOD_WINDOW_SECONDS = 8
SPAM_FLOOD_COUNT = 5
MOD_BEG_WINDOW_SECONDS = 600
MOD_BEG_REPEAT_COUNT = 2
MAX_LOG_CONTENT_LENGTH = 1000
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_FIELD_LENGTH = 1024

# =========================
# MODERATION SETTINGS
# =========================

def parse_env_terms(name: str) -> set[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return set()
    return {term.strip().lower() for term in raw.split(",") if term.strip()}


# Optional extra TOS terms from env, comma-separated
TOS_TERMS = parse_env_terms("TOS_TERMS")

# Major-only slurs / hate speech
HATE_SPEECH_TERMS = {
    "faggot",
    "nigger",
    "nigga",
    "kike",
    "spic",
    "chink",
    "tranny",
}

# Asking for mod
MOD_BEG_PATTERNS = [
    re.compile(r"\bmake me mod\b", re.IGNORECASE),
    re.compile(r"\bgive me mod\b", re.IGNORECASE),
    re.compile(r"\bcan i have mod\b", re.IGNORECASE),
    re.compile(r"\bi want mod\b", re.IGNORECASE),
    re.compile(r"\bmod me\b", re.IGNORECASE),
    re.compile(r"\bcan i be mod\b", re.IGNORECASE),
]

# Major threat / severe harassment phrases only
SEVERE_THREAT_PATTERNS = [
    re.compile(r"\bkill yourself\b", re.IGNORECASE),
    re.compile(r"\bkys\b", re.IGNORECASE),
    re.compile(r"\bhang yourself\b", re.IGNORECASE),
    re.compile(r"\bgo die\b", re.IGNORECASE),
    re.compile(r"\bi will kill you\b", re.IGNORECASE),
    re.compile(r"\bi'm going to kill you\b", re.IGNORECASE),
    re.compile(r"\bim going to kill you\b", re.IGNORECASE),
    re.compile(r"\bi will find you\b", re.IGNORECASE),
    re.compile(r"\bi'm going to find you\b", re.IGNORECASE),
    re.compile(r"\bim going to find you\b", re.IGNORECASE),
]

COMMON_CHAT_WORDS = {
    "a", "and", "are", "bro", "but", "for", "hello", "hey", "hi", "i", "im", "is",
    "it", "lol", "lmao", "me", "my", "need", "no", "not", "please", "the", "to",
    "u", "ur", "want", "we", "wish", "ya", "yo", "you"
}

# =========================
# IN-MEMORY TRACKING
# =========================
recent_messages = defaultdict(deque)
recent_mod_begs = defaultdict(deque)
recent_activity = defaultdict(deque)
user_message_history = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))
rules_message_id = RULES_MESSAGE_ID

# =========================
# BOT SETUP
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.reactions = True


class SullyGangBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


bot = SullyGangBot()


# =========================
# HELPERS
# =========================
def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def contains_banned_terms(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    for term in terms:
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            return True
    return False


def matches_any_pattern(text: str, patterns: list[re.Pattern]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def truncate_text(text: str, limit: int = MAX_LOG_CONTENT_LENGTH) -> str:
    if not text:
        return "(no text)"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z']+", text.lower())


def looks_like_gibberish(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False

    if normalized.startswith(("http://", "https://")):
        return False

    letters_only = re.sub(r"[^a-z]", "", normalized)
    if len(letters_only) < 6:
        return False

    tokens = tokenize(normalized)
    if not tokens:
        return False

    known_words = sum(1 for tok in tokens if tok in COMMON_CHAT_WORDS)
    weird_chars = re.findall(r"[^\w\s]", text)
    vowel_ratio = sum(1 for ch in letters_only if ch in "aeiou") / max(len(letters_only), 1)
    unique_chars = len(set(letters_only)) / max(len(letters_only), 1)
    consonant_runs = re.findall(r"[bcdfghjklmnpqrstvwxyz]{4,}", letters_only)

    single_token_message = len(tokens) == 1 and len(tokens[0]) >= 7
    mostly_single_blob = len(tokens) <= 2 and max(len(tok) for tok in tokens) >= 7

    if known_words >= 2:
        return False

    if consonant_runs:
        return True

    if len(weird_chars) >= 1 and mostly_single_blob and known_words == 0:
        return True

    if single_token_message and known_words == 0 and unique_chars >= 0.5:
        return True

    if mostly_single_blob and known_words == 0 and unique_chars >= 0.6 and 0.2 <= vowel_ratio <= 0.55:
        return True

    return False


def record_user_message(message: discord.Message):
    attachment_note = ""
    if message.attachments:
        attachment_note = f" [attachments: {len(message.attachments)}]"

    content = message.content.strip() or "(no text)"
    user_message_history[message.author.id].append({
        "timestamp": message.created_at,
        "content": f"{content}{attachment_note}",
    })


def build_recent_history_text(user_id: int) -> str:
    history = user_message_history.get(user_id)
    if not history:
        return "No recent history."

    lines = []
    total_length = 0

    for entry in history:
        timestamp = entry["timestamp"]
        if isinstance(timestamp, datetime):
            timestamp_text = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            timestamp_text = str(timestamp)

        content = truncate_text(entry["content"], 140)
        line = f"[{timestamp_text}] {content}"
        projected = total_length + len(line) + 1

        if projected > MAX_HISTORY_FIELD_LENGTH:
            remaining = MAX_HISTORY_FIELD_LENGTH - total_length - 4
            if remaining > 0:
                lines.append(line[:remaining] + "...")
            break

        lines.append(line)
        total_length = projected

    return "\n".join(lines) if lines else "No recent history."


async def get_mod_log_channel(guild: discord.Guild | None):
    if guild is None or not MOD_LOG_CHANNEL_ID:
        return None

    channel = guild.get_channel(MOD_LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(MOD_LOG_CHANNEL_ID)
        except Exception as exc:
            print(f"Could not fetch mod log channel: {exc}")
            return None
    return channel


async def build_image_files(message: discord.Message) -> list[discord.File]:
    files: list[discord.File] = []

    for index, attachment in enumerate(message.attachments[:3], start=1):
        content_type = (attachment.content_type or "").lower()
        filename = (attachment.filename or f"attachment_{index}").lower()
        is_image = content_type.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        if not is_image:
            continue

        try:
            data = await attachment.read(use_cached=True)
        except Exception as exc:
            print(f"Failed to read attachment for logging: {exc}")
            continue

        files.append(discord.File(BytesIO(data), filename=attachment.filename or f"evidence_{index}"))

    return files


async def log_moderation_action(
    *,
    message: discord.Message,
    reason: str,
    duration: timedelta,
    deleted: bool = False,
):
    channel = await get_mod_log_channel(message.guild)
    if channel is None:
        return

    embed = discord.Embed(
        title="Moderation Log",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="User", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Timeout Length", value=str(duration), inline=True)
    embed.add_field(name="Deleted Message", value="Yes" if deleted else "No", inline=True)
    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
    embed.add_field(name="When", value=f"<t:{int(discord.utils.utcnow().timestamp())}:F>", inline=False)
    embed.add_field(name="Message Content", value=truncate_text(message.content), inline=False)
    embed.add_field(
        name="Last 10 Messages",
        value=build_recent_history_text(message.author.id),
        inline=False,
    )

    if message.attachments:
        attachment_lines = [attachment.url for attachment in message.attachments[:5]]
        embed.add_field(name="Attachments", value="\n".join(attachment_lines), inline=False)

    jump_url = getattr(message, "jump_url", None)
    if jump_url:
        embed.add_field(name="Message Link", value=jump_url, inline=False)

    files = await build_image_files(message)
    if files:
        embed.set_image(url=f"attachment://{files[0].filename}")

    try:
        await channel.send(embed=embed, files=files)
    except discord.Forbidden:
        print("Missing permission to send moderation logs.")
    except discord.HTTPException as exc:
        print(f"Failed to send moderation log: {exc}")


async def timeout_and_log(
    message: discord.Message,
    duration: timedelta,
    reason: str,
    *,
    delete_message: bool = False,
) -> bool:
    deleted = False
    if delete_message:
        deleted = await safe_delete(message)

    until = discord.utils.utcnow() + duration
    try:
        await message.author.timeout(until, reason=reason)
    except discord.Forbidden:
        print(f"Missing permission to timeout {message.author}.")
        return False
    except discord.HTTPException as exc:
        print(f"Failed to timeout {message.author}: {exc}")
        return False

    await log_moderation_action(
        message=message,
        reason=reason,
        duration=duration,
        deleted=deleted,
    )
    return True


async def safe_delete(message: discord.Message) -> bool:
    try:
        await message.delete()
        return True
    except discord.Forbidden:
        print("Missing permission to delete messages.")
    except discord.HTTPException:
        pass
    return False


def build_rules_embed(guild: discord.Guild | None) -> discord.Embed:
    streamer_mention = f"<@{STREAMER_USER_ID}>"
    verify_role_mention = f"<@&{VERIFY_ROLE_ID}>"

    description = (
        f"React with {VERIFY_EMOJI} on this message to get {verify_role_mention}.\n\n"
        "Please read and follow the rules below."
    )

    rules_text = (
        "**1.** Do not spam or start problems in chat. Obvious spam flood is an auto 1 hour timeout.\n"
        "**2.** Do not use the stream ideas channel for anything other than stream ideas.\n"
        "**3.** Do not ask for mod twice. You will be auto timed out.\n"
        "**4.** Real spam gets timed out. Normal conversation with different messages does not.\n"
        "**5.** Arguing will be dealt with manually. Keep it friendly.\n"
        f"**6.** Respect everyone fairly and equally. We are all here for the love of {streamer_mention} and his dumb streams.\n"
        "**7.** No hate speech, slurs, TOS-breaking content, or severe phrases like kys. That is an instant one week timeout.\n"
        "**8.** No serious threats toward people in chat.\n"
    )

    embed = discord.Embed(
        title="Sully Gang Rules",
        description=description,
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Rules", value=rules_text, inline=False)
    embed.set_footer(text="Sully Gang")

    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    return embed


async def send_or_attach_rules_message():
    global rules_message_id

    if not RULES_CHANNEL_ID:
        return

    channel = bot.get_channel(RULES_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(RULES_CHANNEL_ID)
        except Exception as exc:
            print(f"Could not fetch rules channel: {exc}")
            return

    if rules_message_id:
        try:
            existing = await channel.fetch_message(rules_message_id)
            try:
                await existing.add_reaction(VERIFY_EMOJI)
            except discord.HTTPException as exc:
                print(f"Could not add reaction to existing rules message: {exc}")
            return
        except Exception:
            rules_message_id = 0

    embed = build_rules_embed(getattr(channel, "guild", None))

    try:
        sent = await channel.send(embed=embed)
        await sent.add_reaction(VERIFY_EMOJI)
        rules_message_id = sent.id
        print(f"Rules message created: {rules_message_id}")
    except Exception as exc:
        print(f"Failed to send rules message: {exc}")


async def handle_exact_spam(message: discord.Message) -> bool:
    now = discord.utils.utcnow()
    normalized = normalize_text(message.content)

    if not normalized:
        return False

    user_queue = recent_messages[message.author.id]
    user_queue.append((normalized, now))

    while user_queue and (now - user_queue[0][1]).total_seconds() > SPAM_WINDOW_SECONDS:
        user_queue.popleft()

    same_count = sum(1 for content, _ in user_queue if content == normalized)
    if same_count >= SPAM_REPEAT_COUNT:
        return await timeout_and_log(
            message,
            ONE_HOUR,
            "Spam: repeated same message 5 times in 10 seconds",
            delete_message=False,
        )

    return False


async def handle_gibberish_flood(message: discord.Message) -> bool:
    now = discord.utils.utcnow()
    normalized = normalize_text(message.content)

    if not normalized:
        return False

    dq = recent_activity[message.author.id]
    dq.append((normalized, now, looks_like_gibberish(message.content)))

    while dq and (now - dq[0][1]).total_seconds() > SPAM_FLOOD_WINDOW_SECONDS:
        dq.popleft()

    if len(dq) < SPAM_FLOOD_COUNT:
        return False

    gibberish_count = sum(1 for _, _, flagged in dq if flagged)
    distinct_count = len({content for content, _, _ in dq})

    suspicious_single_blob_count = sum(
        1
        for content, _, _ in dq
        if len(tokenize(content)) <= 2 and len(re.sub(r"[^a-z]", "", content)) >= 6
    )

    if gibberish_count >= 4 and distinct_count >= 4 and suspicious_single_blob_count >= 4:
        return await timeout_and_log(
            message,
            ONE_HOUR,
            "Spam: obvious gibberish flood across 5 messages",
            delete_message=False,
        )

    return False


async def handle_mod_begging(message: discord.Message) -> bool:
    if not matches_any_pattern(message.content, MOD_BEG_PATTERNS):
        return False

    now = discord.utils.utcnow()
    dq = recent_mod_begs[message.author.id]
    dq.append(now)

    while dq and (now - dq[0]).total_seconds() > MOD_BEG_WINDOW_SECONDS:
        dq.popleft()

    if len(dq) >= MOD_BEG_REPEAT_COUNT:
        return await timeout_and_log(
            message,
            ONE_DAY,
            "Asked for mod twice within 10 minutes",
            delete_message=False,
        )

    return False


async def handle_severe_content(message: discord.Message) -> bool:
    text = message.content

    if contains_banned_terms(text, HATE_SPEECH_TERMS):
        return await timeout_and_log(
            message,
            ONE_WEEK,
            "Used slurs / hate speech",
            delete_message=True,
        )

    if TOS_TERMS and contains_banned_terms(text, TOS_TERMS):
        return await timeout_and_log(
            message,
            ONE_WEEK,
            "Used TOS-breaking term",
            delete_message=True,
        )

    return False


async def handle_severe_threats(message: discord.Message) -> bool:
    if matches_any_pattern(message.content, SEVERE_THREAT_PATTERNS):
        return await timeout_and_log(
            message,
            ONE_WEEK,
            "Severe threats / harassment",
            delete_message=True,
        )

    return False


# =========================
# EVENTS
# =========================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await send_or_attach_rules_message()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    record_user_message(message)

    if await handle_severe_content(message):
        return

    if await handle_severe_threats(message):
        return

    if await handle_mod_begging(message):
        return

    if await handle_exact_spam(message):
        return

    if await handle_gibberish_flood(message):
        return

    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    global rules_message_id

    if payload.user_id == bot.user.id:
        return

    if payload.guild_id is None:
        return

    if not rules_message_id or payload.message_id != rules_message_id:
        return

    if str(payload.emoji) != VERIFY_EMOJI:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        try:
            guild = await bot.fetch_guild(payload.guild_id)
        except Exception:
            return

    member = guild.get_member(payload.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return

    role = guild.get_role(VERIFY_ROLE_ID)
    if role is None:
        print("Verify role not found.")
        return

    try:
        await member.add_roles(role, reason="Rules reaction verification")
    except discord.Forbidden:
        print("Missing permission to assign verification role.")
    except discord.HTTPException as exc:
        print(f"Failed to assign verification role: {exc}")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    global rules_message_id

    if payload.guild_id is None:
        return

    if not rules_message_id or payload.message_id != rules_message_id:
        return

    if str(payload.emoji) != VERIFY_EMOJI:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        try:
            guild = await bot.fetch_guild(payload.guild_id)
        except Exception:
            return

    member = guild.get_member(payload.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return

    role = guild.get_role(VERIFY_ROLE_ID)
    if role is None:
        return

    try:
        await member.remove_roles(role, reason="Rules reaction removed")
    except discord.Forbidden:
        print("Missing permission to remove verification role.")
    except discord.HTTPException as exc:
        print(f"Failed to remove verification role: {exc}")


# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="rules", description="Send the Sully Gang rules embed")
async def rules_command(interaction: discord.Interaction):
    global rules_message_id

    await interaction.response.defer(ephemeral=True)

    member = interaction.user

    if not isinstance(member, discord.Member):
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    if not any(role.id == RULES_ROLE_ID for role in member.roles):
        await interaction.followup.send("You do not have permission to use this command.", ephemeral=True)
        return

    try:
        embed = build_rules_embed(interaction.guild)
        sent = await interaction.channel.send(embed=embed)
        await sent.add_reaction(VERIFY_EMOJI)
        rules_message_id = sent.id
        await interaction.followup.send("Rules sent.", ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(f"Failed to send rules: {exc}", ephemeral=True)


@rules_command.error
async def rules_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(f"Error: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)
    except Exception:
        print(f"Slash command error: {error}")


# =========================
# ENTRY POINT
# =========================
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Missing DISCORD_BOT_TOKEN environment variable.")

    bot.run(TOKEN)
