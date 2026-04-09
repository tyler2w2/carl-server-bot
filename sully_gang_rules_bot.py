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

# Role allowed to use /rules
RULES_ROLE_ID = int(os.getenv("RULES_ROLE_ID", "1467182548894351505"))

# Role users receive after reacting to the bot's verification message
VERIFY_ROLE_ID = int(os.getenv("VERIFY_ROLE_ID", "1491590761689649282"))
VERIFY_EMOJI = "👍"

# Channel that is ONLY for stream ideas
STREAM_IDEAS_CHANNEL_ID = int(os.getenv("STREAM_IDEAS_CHANNEL_ID", "1483866348131188757"))

# Mentioned user in rule 5
STREAMER_USER_ID = int(os.getenv("STREAMER_USER_ID", "831542616188256347"))

# Channel where the bot posts the reaction-role message on startup.
REACTION_ROLE_CHANNEL_ID = int(os.getenv("REACTION_ROLE_CHANNEL_ID", "0"))

# Optional: if you already have a reaction-role message, put its ID here.
REACTION_ROLE_MESSAGE_ID = int(os.getenv("REACTION_ROLE_MESSAGE_ID", "0"))

# Optional: put your server ID here for faster slash-command sync.
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# Timeout durations
ONE_DAY = timedelta(days=1)
ONE_WEEK = timedelta(weeks=1)

# Detection windows
SPAM_WINDOW_SECONDS = 10
SPAM_REPEAT_COUNT = 5
MOD_BEG_WINDOW_SECONDS = 600  # 10 minutes
MOD_BEG_REPEAT_COUNT = 2

# "Fairly strict" keyword sets. Adjust these to fit your server.
TOS_TERMS = {
    "tos",
}

# Keep this reasonably small so it is not too strict.
HATE_SPEECH_TERMS = {
    "faggot",
    "nigger",
    "nigga",
    "retard",
    "kike",
    "spic",
    "chink",
    "tranny",
}

MOD_BEG_PATTERNS = [
    re.compile(r"\bmake me mod\b", re.IGNORECASE),
    re.compile(r"\bgive me mod\b", re.IGNORECASE),
    re.compile(r"\bcan i have mod\b", re.IGNORECASE),
    re.compile(r"\bi want mod\b", re.IGNORECASE),
    re.compile(r"\bmod me\b", re.IGNORECASE),
    re.compile(r"\bcan i be mod\b", re.IGNORECASE),
]

ARGUMENT_PATTERNS = [
    re.compile(r"\bfuck you\b", re.IGNORECASE),
    re.compile(r"\bstfu\b", re.IGNORECASE),
    re.compile(r"\bshut up\b", re.IGNORECASE),
    re.compile(r"\byou are dumb\b", re.IGNORECASE),
    re.compile(r"\byoure dumb\b", re.IGNORECASE),
    re.compile(r"\bkill yourself\b", re.IGNORECASE),
    re.compile(r"\bkys\b", re.IGNORECASE),
]

# In-memory tracking
recent_messages = defaultdict(deque)
recent_mod_begs = defaultdict(deque)
recent_word_usage = defaultdict(lambda: defaultdict(deque))
reaction_role_message_id = REACTION_ROLE_MESSAGE_ID


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


async def send_reaction_role_message():
    global reaction_role_message_id

    if not REACTION_ROLE_CHANNEL_ID:
        return

    channel = bot.get_channel(REACTION_ROLE_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(REACTION_ROLE_CHANNEL_ID)
        except Exception:
            return

    embed = discord.Embed(
        title="Sully Gang Access",
        description=(
            f"React with {VERIFY_EMOJI} below to get access to the server role.\n\n"
            "By reacting, you confirm that you agree to follow the server rules."
        ),
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Sully Gang")

    if reaction_role_message_id:
        try:
            existing = await channel.fetch_message(reaction_role_message_id)
            try:
                await existing.add_reaction(VERIFY_EMOJI)
            except discord.HTTPException:
                pass
            return
        except Exception:
            reaction_role_message_id = 0

    try:
        sent = await channel.send(embed=embed)
        await sent.add_reaction(VERIFY_EMOJI)
        reaction_role_message_id = sent.id
        print(f"Reaction-role message created: {reaction_role_message_id}")
    except Exception as exc:
        print(f"Failed to send reaction-role message: {exc}")


async def handle_exact_spam(message: discord.Message) -> bool:
    now = discord.utils.utcnow()
    user_queue = recent_messages[message.author.id]
    normalized = normalize_text(message.content)

    user_queue.append((normalized, now))

    while user_queue and (now - user_queue[0][1]).total_seconds() > SPAM_WINDOW_SECONDS:
        user_queue.popleft()

    same_count = sum(1 for content, _ in user_queue if content == normalized and content)
    if same_count >= SPAM_REPEAT_COUNT:
        await timeout_member(message.author, ONE_DAY, "Spam: repeated same message 5 times in 10 seconds")
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
            await timeout_member(message.author, ONE_DAY, f"Spam: repeated word '{token}' 5 times in 10 seconds")
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
        await timeout_member(message.author, ONE_DAY, "Asked for mod twice within 10 minutes")
        return True

    return False


async def handle_stream_ideas_channel_rule(message: discord.Message) -> bool:
    # Disabled moderation for this channel as requested
    return False

    # Any message not clearly a stream idea gets timed out.
    # This includes replies in that channel.
    if message.reference is not None:
        await timeout_member(message.author, ONE_DAY, "Replying in the stream ideas channel is not allowed")
        return True

    text = normalize_text(message.content)
    if not text:
        await timeout_member(message.author, ONE_DAY, "Only stream ideas are allowed in the stream ideas channel")
        return True

    idea_markers = [
        "stream idea",
        "you should stream",
        "stream",
        "play",
        "react to",
        "do a stream",
        "idea:",
        "you should do",
    ]

    if not any(marker in text for marker in idea_markers):
        await timeout_member(message.author, ONE_DAY, "Only stream ideas are allowed in the stream ideas channel")
        return True

    return False


async def handle_severe_content(message: discord.Message) -> bool:
    text = message.content

    if contains_banned_terms(text, TOS_TERMS) or contains_banned_terms(text, HATE_SPEECH_TERMS):
        await safe_delete(message)
        await timeout_member(message.author, ONE_WEEK, "Used prohibited language (TOS / slurs / hate speech)")
        return True

    return False


async def handle_arguments_and_threats(message: discord.Message) -> bool:
    if matches_any_pattern(message.content, ARGUMENT_PATTERNS):
        await timeout_member(message.author, ONE_DAY, "Threats / hostile arguing / harassment")
        return True

    return False


def build_rules_embed(guild: discord.Guild | None) -> discord.Embed:
    streamer_mention = f"<@{STREAMER_USER_ID}>"
    stream_ideas_channel = f"<#{STREAM_IDEAS_CHANNEL_ID}>"
    verify_role_mention = f"<@&{VERIFY_ROLE_ID}>"

    description = (
        f"React with {VERIFY_EMOJI} on the verification message to get {verify_role_mention}.\n\n"
        "Please read and follow the rules below."
    )

    rules_text = (
        "**1.** You cannot at any circumstance say **tos** in any chat.\n"
        f"**2.** Do not use {stream_ideas_channel} for anything other than stream ideas. If you even reply to a message in there, you will be timed out. It is specifically for stream ideas, and mods are tired of having to clear up chats from people just yapping.\n"
        "**3.** Do not ask for mod. You will be auto timed out.\n"
        "**4.** Do not spam the same message 5 times. You will be timed out.\n"
        f"**5.** No arguing. This is a community and we are all here for the love of {streamer_mention} and his dumb streams, so just be friendly.\n"
        "**6.** Respect everyone fairly and equally.\n"
        "**7.** No form of hate speech, tos, or slurs will be tolerated. You will be instantly timed out for a week.\n"
        "**8.** There is no making fun of or threatening people in any of the chats. Just be friends, guys. If you do not like each other, do not start problems."
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


# =========================
# EVENTS
# =========================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await send_reaction_role_message()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # Rule 7 first: delete severe content and timeout for a week.
    if await handle_severe_content(message):
        return

    # Rule 2: stream ideas channel enforcement.
    if await handle_stream_ideas_channel_rule(message):
        return

    # Rule 3: asking for mod.
    if await handle_mod_begging(message):
        return

    # Rule 4: same message spam.
    if await handle_exact_spam(message):
        return

    # Extra: same word 5 times in 10 seconds.
    if await handle_word_spam(message):
        return

    # Rules 5 and 8: arguing / threats / harassment.
    if await handle_arguments_and_threats(message):
        return

    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    if not reaction_role_message_id or payload.message_id != reaction_role_message_id:
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
        await member.add_roles(role, reason="Reaction role verification")
    except discord.Forbidden:
        print("Missing permission to assign verification role.")
    except discord.HTTPException:
        pass


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if not reaction_role_message_id or payload.message_id != reaction_role_message_id:
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
        await member.remove_roles(role, reason="Reaction role removed")
    except discord.Forbidden:
        print("Missing permission to remove verification role.")
    except discord.HTTPException:
        pass


# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="rules", description="Send the Sully Gang rules embed")
async def rules_command(interaction: discord.Interaction):
    global reaction_role_message_id

    member = interaction.user

    if not isinstance(member, discord.Member):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    allowed = any(role.id == RULES_ROLE_ID for role in member.roles)
    if not allowed:
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    embed = build_rules_embed(interaction.guild)
    await interaction.response.send_message("Rules embed sent.", ephemeral=True)
    sent = await interaction.channel.send(embed=embed)

    try:
        await sent.add_reaction(VERIFY_EMOJI)
        reaction_role_message_id = sent.id
    except discord.HTTPException:
        pass


# Optional: better slash-command permission display in Discord clients
@rules_command.error
async def rules_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if interaction.response.is_done():
        await interaction.followup.send(f"Error: {error}", ephemeral=True)
    else:
        await interaction.response.send_message(f"Error: {error}", ephemeral=True)


# =========================
# ENTRY POINT
# =========================
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Missing DISCORD_BOT_TOKEN environment variable.")

    bot.run(TOKEN)
