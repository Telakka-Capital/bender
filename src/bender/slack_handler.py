"""Slack event handlers — @mentions and thread replies via slack-bolt."""

import logging
import re

from slack_bolt.async_app import AsyncApp

from bender.claude_code import ClaudeCodeError, invoke_claude
from bender.config import Settings
from bender.session_manager import SessionManager
from bender.slack_utils import SLACK_MSG_LIMIT, md_to_mrkdwn, split_text

logger = logging.getLogger(__name__)

ACKNOWLEDGEMENT_TEXT = "Accepted — I’m working on this now."


def register_handlers(app: AsyncApp, settings: Settings, sessions: SessionManager) -> None:
    """Register Slack event handlers on the bolt app."""

    @app.event("reaction_added")
    async def handle_reaction_added(event: dict) -> None:
        """Ignore reaction_added events — Bender doesn't need to track reactions."""
        pass

    @app.event("reaction_removed")
    async def handle_reaction_removed(event: dict) -> None:
        """Ignore reaction_removed events — Bender doesn't need to track reactions."""
        pass

    @app.event("app_mention")
    async def handle_mention(event: dict, say) -> None:
        """Handle new @Bender mentions — create session and invoke Claude Code."""
        text = _strip_mention(event.get("text", ""))
        thread_ts = event.get("ts", "")
        channel = event.get("channel", "")

        if not _is_allowed(event, settings):
            return

        if not text.strip():
            await say(text="How can I help?", thread_ts=thread_ts)
            return

        logger.info("New mention in channel=%s thread=%s", channel, thread_ts)

        session_id = await sessions.create_session(thread_ts)
        await say(text=ACKNOWLEDGEMENT_TEXT, thread_ts=thread_ts)

        try:
            response = await invoke_claude(
                prompt=text,
                workspace=settings.bender_workspace,
                session_id=session_id,
                timeout=settings.bender_timeout_seconds,
            )
            await _post_response(say, response.result, thread_ts)
        except ClaudeCodeError as exc:
            logger.error("Claude Code invocation failed: %s", exc)
            await say(text=f"Sorry, something went wrong: {exc}", thread_ts=thread_ts)

    @app.event("message")
    async def handle_message(event: dict, say) -> None:
        """Handle thread replies — resume existing session if one exists."""
        # Ignore bot messages to avoid loops
        if event.get("bot_id") or event.get("subtype"):
            return

        if not _is_allowed(event, settings):
            return

        thread_ts = event.get("thread_ts")
        if not thread_ts:
            # Not a thread reply, ignore
            return

        session_id = await sessions.get_session(thread_ts)
        if not session_id:
            # Thread not tracked by Bender, ignore
            return

        text = _strip_mention(event.get("text", ""))
        if not text.strip():
            return

        channel = event.get("channel", "")
        logger.info("Thread reply in channel=%s thread=%s", channel, thread_ts)
        await say(text=ACKNOWLEDGEMENT_TEXT, thread_ts=thread_ts)

        try:
            response = await invoke_claude(
                prompt=text,
                workspace=settings.bender_workspace,
                session_id=session_id,
                resume=True,
                timeout=settings.bender_timeout_seconds,
            )
            await _post_response(say, response.result, thread_ts)
        except ClaudeCodeError as exc:
            logger.error("Claude Code invocation failed: %s", exc)
            await say(text=f"Sorry, something went wrong: {exc}", thread_ts=thread_ts)


def _is_allowed(event: dict, settings: Settings) -> bool:
    """Return whether the event comes from an explicitly allowed Slack channel."""
    channel_id = event.get("channel", "")
    if channel_id in settings.allowed_channel_ids:
        return True

    logger.warning(
        "Ignoring Slack event from unauthorized channel=%s user=%s",
        channel_id or "<missing>",
        event.get("user", ""),
    )
    return False


def _strip_mention(text: str) -> str:
    """Remove Slack mention tags (<@U...>, <@B...>, <@W...>) from the message text."""
    return re.sub(r"<@[UBW][A-Z0-9]+>", "", text).strip()


async def _post_response(say, text: str, thread_ts: str) -> None:
    """Post a response in the thread, splitting if it exceeds Slack's limit."""
    text = md_to_mrkdwn(text)

    if len(text) <= SLACK_MSG_LIMIT:
        await say(text=text, thread_ts=thread_ts)
        return

    chunks = split_text(text, SLACK_MSG_LIMIT)
    for chunk in chunks:
        await say(text=chunk, thread_ts=thread_ts)
