#!/usr/bin/env python3
"""BlunderBus Discord bot — MVP daemon.

Connects to Discord using DISCORD_BOT_TOKEN + DISCORD_GUILD_ID from vault.
Provides slash commands for interacting with the BlunderBus memory system from
inside Discord:

    /health        — bot + system status snapshot
    /brief [date]  — post today's (or specified) brief into the current channel
    /concerns      — list active agent concerns
    /tasks         — list open items from TASKS.md (Active + Ops sections)

The bot is read-mostly in this MVP. Phase 2 adds:
  - question-thread workflow (agent enqueues → user replies in thread → registry updated)
  - /resolve <concern-id>, /snooze <target> <duration>
  - per-channel agent routing (#infra-alerts pushes from infra agent, etc.)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from collections import defaultdict, deque
from datetime import date as date_cls, datetime
from pathlib import Path

import discord
from discord import app_commands

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from runtime import configure_utf8_stdio, resolve_claude_command

configure_utf8_stdio()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("blunderbus.discord")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID_STR = os.environ.get("DISCORD_GUILD_ID", "")
GUILD = discord.Object(id=int(GUILD_ID_STR)) if GUILD_ID_STR else None

if not TOKEN or not GUILD:
    log.error("DISCORD_BOT_TOKEN and DISCORD_GUILD_ID are required (set via vault.py)")
    sys.exit(1)


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


# ── Slash command: /health ───────────────────────────────────────────────────


@tree.command(name="health", description="BlunderBus system status snapshot", guild=GUILD)
async def cmd_health(interaction: discord.Interaction) -> None:
    """Quick system pulse — bot uptime, last brief, active concerns count."""
    await interaction.response.defer(thinking=True)
    try:
        brief_dir = ROOT / "data" / "briefs"
        latest_brief = "(none)"
        if brief_dir.exists():
            briefs = sorted(brief_dir.glob("*.json"), reverse=True)
            if briefs:
                latest_brief = briefs[0].stem

        # Count active concerns (best effort)
        concerns_count = "?"
        try:
            from blunderbus_memory.concerns import PostgresConcerns
            with PostgresConcerns() as store:
                concerns_count = str(len(store.list_active()))
        except Exception as exc:
            concerns_count = f"(unavailable: {type(exc).__name__})"

        msg = (
            f"**🟢 BlunderBus online**\n"
            f"• Latest brief: `{latest_brief}`\n"
            f"• Active concerns: {concerns_count}\n"
            f"• Bot uptime: since {client.start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"• Guild: `{GUILD_ID_STR}`"
        )
        await interaction.followup.send(msg)
    except Exception as exc:
        log.exception("/health failed")
        await interaction.followup.send(f"❌ /health error: {exc}")


# ── Slash command: /brief ────────────────────────────────────────────────────


@tree.command(name="brief", description="Post today's brief (or a past date)", guild=GUILD)
@app_commands.describe(date="Optional date YYYY-MM-DD (defaults to today)")
async def cmd_brief(interaction: discord.Interaction, date: str = "") -> None:
    await interaction.response.defer(thinking=True)
    try:
        target = date_cls.fromisoformat(date) if date else date_cls.today()
        brief_file = ROOT / "data" / "briefs" / f"{target.isoformat()}.json"
        if not brief_file.exists():
            await interaction.followup.send(f"❌ No brief found for {target.isoformat()}")
            return
        payload = json.loads(brief_file.read_text(encoding="utf-8"))
        briefing = payload.get("briefing") or payload.get("brief") or ""
        if not briefing:
            await interaction.followup.send(f"⚠️ Brief for {target.isoformat()} exists but is empty.")
            return
        # Discord caps messages at 2000 chars — chunk if needed
        header = f"📋 **Brief — {target.isoformat()}**\n"
        chunks = _chunk_message(header + briefing, limit=1900)
        for i, chunk in enumerate(chunks):
            if i == 0:
                await interaction.followup.send(chunk)
            else:
                await interaction.followup.send(chunk)
    except ValueError:
        await interaction.followup.send("❌ Invalid date — use YYYY-MM-DD")
    except Exception as exc:
        log.exception("/brief failed")
        await interaction.followup.send(f"❌ /brief error: {exc}")


# ── Slash command: /concerns ─────────────────────────────────────────────────


@tree.command(name="concerns", description="List active agent concerns", guild=GUILD)
async def cmd_concerns(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    try:
        from blunderbus_memory.concerns import PostgresConcerns
        with PostgresConcerns() as store:
            active = store.list_active()
        if not active:
            await interaction.followup.send("✅ No active concerns.")
            return
        lines = ["**🔔 Active concerns:**"]
        for c in active[:25]:
            sev_emoji = {"critical":"🔴","high":"🔴","medium":"🟡","low":"🟢","info":"🔵"}.get(c.severity.value, "⚪")
            target = f" · `{c.target}`" if c.target else ""
            age = c.days_seen
            age_str = f"{age}d" if age >= 1 else "today"
            lines.append(f"{sev_emoji} [{c.agent}]{target} — {c.summary[:120]} _{age_str}_")
        msg = "\n".join(lines)
        for chunk in _chunk_message(msg, limit=1900):
            await interaction.followup.send(chunk)
    except Exception as exc:
        log.exception("/concerns failed")
        await interaction.followup.send(f"❌ /concerns error: {exc}")


# ── Slash command: /tasks ────────────────────────────────────────────────────


@tree.command(name="tasks", description="Show open items from TASKS.md (Active + Ops)", guild=GUILD)
async def cmd_tasks(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    try:
        from note_template import read_active_tasks
        tasks = read_active_tasks()
        if not tasks:
            await interaction.followup.send("✅ No open items in TASKS.md `## Active` or `## Ops — Needs Attention`.")
            return
        lines = ["**📋 Active tasks (TASKS.md):**"]
        for t in tasks:
            lines.append(f"• {t[:200]}")
        await interaction.followup.send("\n".join(lines))
    except Exception as exc:
        log.exception("/tasks failed")
        await interaction.followup.send(f"❌ /tasks error: {exc}")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _chunk_message(text: str, limit: int = 1900) -> list[str]:
    """Split a message at line boundaries so it fits under Discord's 2000-char cap."""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out


# ── Conversational chat (on_message) ────────────────────────────────────────
#
# Triggers: bot is @-mentioned OR message is a DM. Everything else is ignored
# so #general chatter doesn't summon the agent. Replies stream back into the
# same channel (or DM) with a typing indicator while Claude is thinking.

# Per-channel rolling window of recent messages, for follow-up coherence.
# Survives only within the bot process — restart wipes it. Phase 2.5 persists
# to Postgres so it survives restarts.
CONVERSATION_TURNS = 8
_history: dict[int, deque[tuple[str, str]]] = defaultdict(lambda: deque(maxlen=CONVERSATION_TURNS))


CHAT_PROMPT = """You are BlunderBus — Brian's home AI ops platform.

You're chatting with Brian over Discord. Reply naturally and concisely (1–4
short paragraphs unless he asks for depth). Don't repeat structured data
verbatim if he can just glance at /concerns or /tasks — synthesize. Don't
apologize, don't preface with "great question," just answer. If you don't
know something, say so plainly and tell him which file or system would have
the answer.

You have READ-ONLY access to the snapshots below. These were grabbed the
moment Brian sent his message; treat them as ground truth for "right now."

═══ ACTIVE CONCERNS (Postgres agent_concerns, truth source for ages) ═══
{concerns}

═══ TASKS.md (full file) ════════════════════════════════════════════════
{tasks}

═══ TODAY'S BRIEF ═══════════════════════════════════════════════════════
{brief_today}

═══ YESTERDAY'S BRIEF ═══════════════════════════════════════════════════
{brief_yesterday}

═══ DECISIONS JOURNAL — last 3 days (what was decided + why) ═══════════
{decisions}

═══ REGISTRY CONTEXT (people, projects, project blockers) ══════════════
{registry}

═══ PER-AGENT MEMORY (learnings + baselines + recurring patterns) ══════
{agent_memory}

═══ RECENT CONVERSATION IN THIS CHANNEL (most recent last) ═════════════
{conversation}

═══ BRIAN'S NEW MESSAGE ═════════════════════════════════════════════════
{message}

Reply now. No markdown headers. No "Hello Brian." Just the answer."""


def _safe_read(path: Path, max_chars: int = 4000) -> str:
    """Best-effort file read with size cap. Empty string if unavailable."""
    try:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n\n…[truncated, {len(text)-max_chars} more chars]"
        return text
    except Exception:
        return ""


def _gather_chat_context(channel_id: int) -> dict[str, str]:
    """Pull live snapshots of the memory system to inject into the chat prompt."""
    ctx: dict[str, str] = {
        "concerns":        "(unavailable)",
        "tasks":           "(unavailable)",
        "brief_today":     "(unavailable)",
        "brief_yesterday": "(unavailable)",
        "decisions":       "(unavailable)",
        "registry":        "(unavailable)",
        "agent_memory":    "(unavailable)",
        "conversation":    "(no prior turns in this channel)",
    }

    # ─── Active concerns from Postgres ───────────────────────────────────────
    try:
        from blunderbus_memory.concerns import PostgresConcerns
        with PostgresConcerns() as store:
            active = store.list_active()
        if active:
            lines = []
            for c in active:
                tgt = f" [{c.target}]" if c.target else ""
                age = f"{c.days_seen}d" if c.days_seen >= 1 else "today"
                first = c.first_seen.date().isoformat() if c.first_seen else "?"
                lines.append(f"- [{c.agent}/{c.severity.value}]{tgt} {c.summary[:160]} (since {first}, age={age})")
            ctx["concerns"] = "\n".join(lines)
        else:
            ctx["concerns"] = "(none active)"
    except Exception as exc:
        ctx["concerns"] = f"(error: {exc})"

    # ─── Full TASKS.md ───────────────────────────────────────────────────────
    tasks_text = _safe_read(ROOT / "TASKS.md", max_chars=4000)
    ctx["tasks"] = tasks_text or "(TASKS.md not found)"

    # ─── Today's + yesterday's brief ────────────────────────────────────────
    try:
        from datetime import date as _date, timedelta as _td
        for label, offset in (("brief_today", 0), ("brief_yesterday", 1)):
            d = _date.today() - _td(days=offset)
            brief_file = ROOT / "data" / "briefs" / f"{d.isoformat()}.json"
            if brief_file.exists():
                payload = json.loads(brief_file.read_text(encoding="utf-8"))
                briefing = (payload.get("briefing") or payload.get("brief") or "")[:2000]
                ctx[label] = briefing or f"({d.isoformat()} brief has no body)"
            else:
                ctx[label] = f"(no brief for {d.isoformat()})"
    except Exception as exc:
        ctx["brief_today"] = f"(error: {exc})"

    # ─── Decisions journal — last 3 days ────────────────────────────────────
    try:
        from datetime import date as _date, timedelta as _td
        chunks = []
        for offset in range(3):
            d = _date.today() - _td(days=offset)
            f = ROOT / "decisions" / f"{d.isoformat()}.md"
            content = _safe_read(f, max_chars=3500)
            if content:
                chunks.append(f"--- {d.isoformat()} ---\n{content}")
        ctx["decisions"] = "\n\n".join(chunks) if chunks else "(no decisions logged in last 3 days)"
    except Exception as exc:
        ctx["decisions"] = f"(error: {exc})"

    # ─── Registry context (reuse daily_brief builder) ───────────────────────
    try:
        from daily_brief import _build_registry_context
        ctx["registry"] = _build_registry_context()[:3000]
    except Exception as exc:
        ctx["registry"] = f"(error: {exc})"

    # ─── Per-agent memory: learnings + baselines + recurring per domain ─────
    mem_chunks = []
    for agent in ("infra", "finance", "workspace"):
        for fname in ("learnings.md", "baselines.md", "recurring.md"):
            content = _safe_read(ROOT / "memory" / agent / fname, max_chars=2500)
            if content:
                mem_chunks.append(f"--- memory/{agent}/{fname} ---\n{content}")
    ctx["agent_memory"] = "\n\n".join(mem_chunks) if mem_chunks else "(no per-agent memory files found)"

    # ─── Conversation history in this channel ───────────────────────────────
    convo = _history.get(channel_id)
    if convo:
        ctx["conversation"] = "\n".join(f"{speaker}: {text}" for speaker, text in list(convo))

    return ctx


def _run_claude_sync(prompt: str, timeout: int = 90) -> str:
    """Blocking claude CLI call. Wrapped in to_thread for async use."""
    claude_cmd = resolve_claude_command()
    if not claude_cmd:
        return "(Claude CLI not on PATH — can't reply right now.)"
    try:
        result = subprocess.run(
            [claude_cmd, "--print", "--output-format", "text"],
            input=prompt,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8",
            cwd=os.path.expanduser("~"),
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip() or f"exit {result.returncode}"
            return f"(Claude CLI error: {err[:300]})"
        return result.stdout.strip() or "(empty response)"
    except subprocess.TimeoutExpired:
        return "(Claude CLI timed out after 90s — try again or shorten the question.)"
    except Exception as exc:
        return f"(Claude CLI exception: {exc})"


@client.event
async def on_message(message: discord.Message) -> None:
    """Routes incoming messages to:
      1. Question-thread reply handler (if in a tracked question thread)
      2. Conversational chat (if @mention or DM)
      3. Ignored otherwise
    """
    if message.author.bot or message.author == client.user:
        return

    # ── Path C: question thread reply? ──────────────────────────────────────
    if isinstance(message.channel, discord.Thread):
        handled = await _maybe_handle_question_reply(message)
        if handled:
            return

    is_dm = isinstance(message.channel, discord.DMChannel)
    mentioned = client.user in message.mentions if client.user else False
    log.info(
        f"on_message received: author={message.author} "
        f"channel={getattr(message.channel,'name','DM')} "
        f"is_dm={is_dm} mentioned={mentioned} "
        f"content_len={len(message.content)} "
        f"content_preview={message.content[:60]!r}"
    )
    if not (is_dm or mentioned):
        return

    # Strip @bot mention from the start of the message text
    content = message.content
    if client.user:
        content = content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    if not content:
        await message.reply("👋 — ask me something. Try `what's broken right now?` or `tldr the brief`")
        return

    ch_id = message.channel.id
    async with message.channel.typing():
        try:
            ctx = await asyncio.to_thread(_gather_chat_context, ch_id)
            prompt = CHAT_PROMPT.format(message=content, **ctx)
            reply_text = await asyncio.to_thread(_run_claude_sync, prompt)
            # Update conversation memory
            _history[ch_id].append(("Brian", content))
            _history[ch_id].append(("BlunderBus", reply_text))
            # Send (chunked if long)
            for chunk in _chunk_message(reply_text, limit=1900):
                await message.channel.send(chunk)
        except Exception as exc:
            log.exception("on_message failed")
            await message.channel.send(f"❌ Sorry, something broke: `{exc}`")


# ── Path C: Question threads ─────────────────────────────────────────────────
#
# Background loop polls Postgres for `status='open'` questions, creates a
# thread per question in QUESTIONS_CHANNEL, posts the prompt + answer format,
# and transitions the row to `status='posted'`. From then on, the on_message
# handler watches that thread for the operator's reply.

QUESTIONS_CHANNEL_NAME = os.environ.get("BBM_QUESTIONS_CHANNEL", "inbox")
QUESTIONS_POLL_SECONDS = int(os.environ.get("BBM_QUESTIONS_POLL_SECONDS", "60"))


def _thread_title(q) -> str:
    """Compact thread name. Discord caps at 100 chars."""
    qt = q.question_type.replace("-", " ")
    base = f"{qt} · {q.target_id}"
    return base[:96] + ("…" if len(base) > 96 else "")


def _thread_opening_message(q) -> str:
    """Body of the first message in the thread. Tells operator what to reply."""
    sf = q.suggested_format or "free text"
    return (
        f"❓ **{q.agent} · {q.question_type} · `{q.target_id}`**\n\n"
        f"{q.prompt}\n\n"
        f"_Reply in this thread to answer. Format hint:_ {sf}\n\n"
        f"`question id: {q.id}`"
    )


async def _create_question_threads_once() -> None:
    """Find open questions, post each in QUESTIONS_CHANNEL as a thread."""
    try:
        from blunderbus_memory.questions import PostgresQuestions
    except Exception as exc:
        log.warning(f"questions backend unavailable, skipping: {exc}")
        return

    guild = client.get_guild(int(GUILD_ID_STR)) if GUILD_ID_STR else None
    if not guild:
        log.warning("guild not resolved, can't create threads yet")
        return
    channel = discord.utils.get(guild.text_channels, name=QUESTIONS_CHANNEL_NAME)
    if not channel:
        log.warning(f"channel '#{QUESTIONS_CHANNEL_NAME}' not found in guild — skipping question threads")
        return

    try:
        with PostgresQuestions() as store:
            open_qs = store.list_open()
            if not open_qs:
                return
            log.info(f"questions: {len(open_qs)} open, creating threads in #{QUESTIONS_CHANNEL_NAME}")
            for q in open_qs:
                try:
                    thread = await channel.create_thread(
                        name=_thread_title(q),
                        type=discord.ChannelType.public_thread,
                        auto_archive_duration=1440,  # 24 hours of inactivity → archive
                        reason=f"BlunderBus question {q.id}",
                    )
                    await thread.send(_thread_opening_message(q))
                    store.mark_posted(q.id, thread.id)
                    log.info(f"  posted question {q.id} → thread {thread.id}")
                except Exception as exc:
                    log.exception(f"  failed to post question {q.id}: {exc}")
    except Exception as exc:
        log.exception(f"question polling loop failed: {exc}")


async def _question_poller_loop() -> None:
    """Forever-loop: poll for open questions every QUESTIONS_POLL_SECONDS."""
    await client.wait_until_ready()
    log.info(f"question poller starting — every {QUESTIONS_POLL_SECONDS}s, channel #{QUESTIONS_CHANNEL_NAME}")
    while not client.is_closed():
        try:
            await _create_question_threads_once()
        except Exception as exc:
            log.exception(f"question poller iteration crashed: {exc}")
        await asyncio.sleep(QUESTIONS_POLL_SECONDS)


# ── Path C: Thread reply → AI-parse → propose-and-confirm ──────────────────


PARSE_PROMPT = """You are extracting a structured answer from a chat reply.

The agent posted this question:
  type: {question_type}
  prompt: {prompt}
  suggested_format: {suggested_format}
  target: {target_kind}/{target_id} (field: {target_field})

The operator (Brian) replied:
  "{reply}"

Extract the answer as a JSON object with these keys:
  value      — the canonical value to write to the registry field. Use registry
               person ids (e.g. "brian-hodgerson", "jamie-hodgerson", "evangeline-hodgerson")
               for owner questions. For "joint" answers, format as
               "joint:brian-hodgerson+jamie-hodgerson".
               Known registry people: brian-hodgerson, jamie-hodgerson,
               evangeline-hodgerson, nathaniel-hodgerson, chris (Chris Puzio CFP),
               sheila-streeter, mike-hess, rusty, vanessa-franco.
  confidence — "high", "medium", or "low"
  rationale  — one sentence explaining how you derived the value
  needs_clarification — true if the reply is ambiguous and you can't propose
                         a value with confidence; in that case, fill `value`
                         with a clarifying question to ask back.

Respond with ONLY the JSON object — no markdown, no preamble. Example:
{{"value": "brian-hodgerson", "confidence": "high", "rationale": "Operator said 'I am the owner' which maps to brian-hodgerson.", "needs_clarification": false}}
"""


# Cache of (propose_message_id → question_id) so the reaction handler can find
# the right question. Survives only within bot process; on restart we rebuild
# from DB on demand.
_propose_msg_to_qid: dict[int, str] = {}


async def _maybe_handle_question_reply(message: discord.Message) -> bool:
    """If `message` is in a thread that maps to an open Question, AI-parse the
    reply and post a proposal. Returns True if handled (caller skips chat path)."""
    try:
        from blunderbus_memory.questions import PostgresQuestions, QuestionStatus
    except Exception:
        return False

    try:
        with PostgresQuestions() as store:
            q = store.get_by_thread(message.channel.id)
    except Exception as exc:
        log.warning(f"thread reply: couldn't query questions store: {exc}")
        return False
    if not q:
        return False
    if q.status not in (QuestionStatus.POSTED, QuestionStatus.PROPOSED):
        # Already applied or abandoned — let the operator chat freely in this thread
        return False

    reply = message.content.strip()
    if not reply:
        return False
    log.info(f"thread reply matched question {q.id}: {reply[:80]!r}")

    async with message.channel.typing():
        prompt = PARSE_PROMPT.format(
            question_type=q.question_type,
            prompt=q.prompt,
            suggested_format=q.suggested_format or "(none)",
            target_kind=q.target_kind.value if hasattr(q.target_kind, "value") else q.target_kind,
            target_id=q.target_id,
            target_field=q.target_field or "(unset)",
            reply=reply,
        )
        raw = await asyncio.to_thread(_run_claude_sync, prompt)
        try:
            parsed = json.loads(_extract_json(raw))
        except Exception as exc:
            await message.channel.send(
                f"⚠️ I couldn't parse the AI response as JSON ({exc}).\n"
                f"Raw output:\n```\n{raw[:500]}\n```\n"
                f"Please reply again — maybe more explicit?"
            )
            return True

        value = parsed.get("value", "")
        confidence = parsed.get("confidence", "low")
        rationale = parsed.get("rationale", "")
        needs_clar = bool(parsed.get("needs_clarification"))

        if needs_clar:
            await message.channel.send(
                f"🤔 I'm not sure I got that right. {value}\n"
                f"_(confidence: {confidence})_"
            )
            return True

        # Post proposal — operator reacts 👍 to confirm
        proposal = (
            f"I'll set:\n```\n"
            f"  memory/registry/{_kind_to_folder(q.target_kind)}/{q.target_id}.md\n"
            f"    {q.target_field or q.question_type}: {value}\n"
            f"```\n"
            f"React 👍 to confirm · ❌ to cancel.\n"
            f"_Reasoning_: {rationale}  _(confidence: {confidence})_"
        )
        propose_msg = await message.channel.send(proposal)
        await propose_msg.add_reaction("👍")
        await propose_msg.add_reaction("❌")

        try:
            with PostgresQuestions() as store:
                store.mark_proposed(q.id, value, propose_msg.id,
                                    answered_by=str(message.author.id))
            _propose_msg_to_qid[propose_msg.id] = q.id
        except Exception as exc:
            log.exception(f"failed to mark question proposed: {exc}")
            await message.channel.send(f"⚠️ proposed but couldn't persist: `{exc}`")
    return True


def _extract_json(raw: str) -> str:
    """Strip surrounding markdown fences / preamble if claude included any."""
    raw = raw.strip()
    if raw.startswith("```"):
        # Walk past the first fence line
        first_nl = raw.find("\n")
        if first_nl >= 0:
            raw = raw[first_nl + 1:]
        if raw.endswith("```"):
            raw = raw[:-3]
    # Find the first { and matching close to be robust
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start:end+1]
    return raw


def _kind_to_folder(kind) -> str:
    val = kind.value if hasattr(kind, "value") else str(kind)
    return {"account": "accounts", "person": "people",
            "project": "projects", "inventory": "inventory"}.get(val, val)


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    """Apply or abandon a proposed question based on 👍 / ❌ reaction."""
    if payload.user_id == (client.user.id if client.user else 0):
        return
    emoji = str(payload.emoji)
    if emoji not in ("👍", "❌"):
        return

    # Find the question for this propose message
    qid = _propose_msg_to_qid.get(payload.message_id)
    if not qid:
        # Cache miss — fall back to DB lookup
        try:
            from blunderbus_memory.questions import PostgresQuestions
            with PostgresQuestions() as store:
                # No direct index on propose_msg_id; query manually
                conn = store.connect()
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM agent_questions WHERE discord_propose_message_id=%s",
                        (payload.message_id,)
                    )
                    row = cur.fetchone()
                    qid = row[0] if row else None
        except Exception:
            return
        if not qid:
            return
        _propose_msg_to_qid[payload.message_id] = qid

    try:
        from blunderbus_memory.questions import PostgresQuestions, QuestionStatus
        from blunderbus_memory.registry_writer import apply_question, RegistryWriteError
        from blunderbus_memory.journal import write_decision
    except Exception as exc:
        log.exception(f"reaction handler imports failed: {exc}")
        return

    channel = client.get_channel(payload.channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(payload.channel_id)
        except Exception:
            log.warning(f"can't fetch channel {payload.channel_id}")
            return

    try:
        with PostgresQuestions() as store:
            q = store.get(qid)
            if not q or q.status != QuestionStatus.PROPOSED:
                return
            if emoji == "❌":
                store.mark_abandoned(qid)
                await channel.send(f"❌ Got it — left `{q.target_id}` as-is.")
                _propose_msg_to_qid.pop(payload.message_id, None)
                return
            # 👍 → apply
            try:
                diff = apply_question(q, q.proposed_value or "")
                store.mark_applied(qid, q.proposed_value or "")
                write_decision(
                    agent=q.agent,
                    target=q.target_id,
                    decision="applied (discord)",
                    reasoning=(
                        f"Question {q.id} answered via Discord thread. "
                        f"{diff['field']}: {diff['before']} → {diff['after']}"
                    ),
                    related=[f"agent_questions:{q.id}", diff["path"]],
                )
                await channel.send(
                    f"✅ Recorded.\n```diff\n"
                    f"  {diff['path']}\n"
                    f"-   {diff['field']}: {diff['before']}\n"
                    f"+   {diff['field']}: {diff['after']}\n"
                    f"```"
                    f"Logged to `decisions/{datetime.now().strftime('%Y-%m-%d')}.md`. "
                    f"Brief won't re-ask."
                )
                _propose_msg_to_qid.pop(payload.message_id, None)
            except RegistryWriteError as exc:
                await channel.send(f"❌ Couldn't write registry: `{exc}`")
            except Exception as exc:
                log.exception(f"apply failed: {exc}")
                await channel.send(f"❌ Apply failed: `{exc}` — question left as proposed.")
    except Exception as exc:
        log.exception(f"reaction handler failed: {exc}")


# ── Lifecycle ────────────────────────────────────────────────────────────────


@client.event
async def on_ready() -> None:
    client.start_time = datetime.now()  # type: ignore[attr-defined]
    log.info(f"Logged in as {client.user} — id {client.user.id if client.user else '?'}")
    # Sync slash commands to the guild (fast — instant within the guild)
    try:
        synced = await tree.sync(guild=GUILD)
        log.info(f"Synced {len(synced)} slash command(s) to guild {GUILD_ID_STR}")
    except Exception as exc:
        log.exception(f"Slash command sync failed: {exc}")
    # Kick off the question-thread poller exactly once
    if not getattr(client, "_question_poller_started", False):
        client._question_poller_started = True  # type: ignore[attr-defined]
        asyncio.create_task(_question_poller_loop())


def main() -> None:
    log.info("Starting BlunderBus Discord bot...")
    client.run(TOKEN, log_handler=None)  # Use our own logging config


if __name__ == "__main__":
    main()
