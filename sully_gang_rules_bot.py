import os
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

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

# Optional: existing rules message ID to reuse after restart
RULES_MESSAGE_ID = int(os.getenv("RULES_MESSAGE_ID", "0"))

# Optional: faster slash-command sync for one server
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# Timeout durations
ONE_DAY = timedelta(days=1)
ONE_WEEK = timedelta(weeks=1)

# Detection windows
SPAM_WINDOW_SECONDS = 10
SPAM_REPEAT_COUNT = 5
MOD_BEG_WINDOW_SECONDS = 600  # 10 minutes
MOD_BEG_REPEAT_COUNT = 2

# =========================
# MODERATION SETTINGS
# =========================

# User specifically wants "tos" to trigger moderation
TOS_TERMS = {"tos"}

# Keep this focused on major stuff only
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

# Major threat / self-harm harassment phrases only
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

# =========================
# IN-MEMORY TRACKING
# =========================
recent_messages = defaultdict(deque)
recent_mod_begs = defaultdict(deque)
recent_word_usage = defaultdict(lambda: defaultdict(deque))
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


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9']+", text.lower())


def contains_banned_terms(text: str, terms: set[str]) -> bool:
    lowered = text.lower()
    for term in terms:
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            return True
    return False


def matches_any_pattern(text: str, patterns: list[re.Pattern]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


async def timeout_member(member: discord.Member, duration: timedelta, reason: str):
    until = discord.utils.utcnow() + duration
    try:
        await member.timeout(until, reason=reason)
    except discord.Forbidden:
        print(f"Missing permission to timeout {member}.")
    except discord.HTTPException as exc:
        print(f"Failed to timeout {member}: {exc}")


async def safe_delete(message: discord.Message):
    try:
        await message.delete()
    except discord.Forbidden:
        print("Missing permission to delete messages.")
    except discord.HTTPException:
        pass


def build_rules_embed(guild: discord.Guild | None) -> discord.Embed:
    streamer_mention = f"<@{STREAMER_USER_ID}>"
    verify_role_mention = f"<@&{VERIFY_ROLE_ID}>"

    description = (
        f"React with {VERIFY_EMOJI} on this message to get {verify_role_mention}.\n\n"
        "Please read and follow the rules below."
    )

    rules_text = (
        "**1.** You cannot at any circumstance say **tos** in any chat.\n"
        "**2.** Do not use the stream ideas channel for anything other than stream ideas.\n"
        "**3.** Do not ask for mod. You will be auto timed out.\n"
        "**4.** Do not spam the same message 5 times. You will be timed out.\n"
        "**5.** Arguing will be dealt with manually. Keep it friendly.\n"
        f"**6.** Respect everyone fairly and equally. We are all here for the love of {streamer_mention} and his dumb streams.\n"
        "**7.** No hate speech, slurs, or severe phrases like kys. That is an instant one week timeout.\n"
        "**8.** No serious threats toward people in chat."
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
        await timeout_member(
            message.author,
            ONE_DAY,
            "Spam: repeated same message 5 times in 10 seconds",
        )
        return True

    return False


async def handle_word_spam(message: discord.Message) -> bool:
    now = discord.utils.utcnow()
    tokens = tokenize(message.content)

    if not tokens:
        return False

    for token in tokens:
        dq = recent_word_usage[message.author.id][token]
        dq.append(now)

        while dq and (now - dq[0]).total_seconds() > SPAM_WINDOW_SECONDS:
            dq.popleft()

        if len(dq) >= SPAM_REPEAT_COUNT:
            await timeout_member(
                message.author,
                ONE_DAY,
                f"Spam: repeated word '{token}' 5 times in 10 seconds",
            )
            return True

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
        await timeout_member(
            message.author,
            ONE_DAY,
            "Asked for mod twice within 10 minutes",
        )
        return True

    return False


async def handle_severe_content(message: discord.Message) -> bool:
    text = message.content

    if contains_banned_terms(text, TOS_TERMS):
        await safe_delete(message)
        await timeout_member(
            message.author,
            ONE_DAY,
            "Used tos",
        )
        return True

    if contains_banned_terms(text, HATE_SPEECH_TERMS):
        await safe_delete(message)
        await timeout_member(
            message.author,
            ONE_WEEK,
            "Used slurs / hate speech",
        )
        return True

    return False


async def handle_severe_threats(message: discord.Message) -> bool:
    if matches_any_pattern(message.content, SEVERE_THREAT_PATTERNS):
        await safe_delete(message)
        await timeout_member(
            message.author,
            ONE_WEEK,
            "Severe threats / hate speech",
        )
        return True

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

    # Only major stuff first
    if await handle_severe_content(message):
        return

    if await handle_severe_threats(message):
        return

    # Spam + mod begging
    if await handle_mod_begging(message):
        return

    if await handle_exact_spam(message):
        return

    if await handle_word_spam(message):
        return

    # Arguing is manual, so no auto-timeout for that
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
