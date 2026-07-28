"""Tests for the Slack event handlers module."""

from unittest.mock import AsyncMock, patch

import pytest

from bender.claude_code import ClaudeCodeError, ClaudeResponse
from bender.config import Settings
from bender.session_manager import SessionManager
from bender.slack_handler import _strip_mention, register_handlers


class TestStripMention:
    """Tests for the _strip_mention helper."""

    def test_removes_single_mention(self) -> None:
        """Removes a single <@UXXXX> mention."""
        assert _strip_mention("<@U12345ABC> hello world") == "hello world"

    def test_removes_multiple_mentions(self) -> None:
        """Removes multiple mentions."""
        assert _strip_mention("<@U111> <@U222> do something") == "do something"

    def test_no_mention_returns_text(self) -> None:
        """Returns text unchanged when no mention is present."""
        assert _strip_mention("no mention here") == "no mention here"

    def test_empty_string(self) -> None:
        """Handles empty string gracefully."""
        assert _strip_mention("") == ""

    def test_only_mention_returns_empty(self) -> None:
        """Returns empty string when text is just a mention."""
        assert _strip_mention("<@U12345ABC>") == ""

    def test_removes_bot_mention(self) -> None:
        """Removes <@BXXXX> bot mentions."""
        assert _strip_mention("<@B12345ABC> hello") == "hello"

    def test_removes_workspace_mention(self) -> None:
        """Removes <@WXXXX> workspace mentions."""
        assert _strip_mention("<@W12345ABC> hello") == "hello"


class TestHandleMention:
    """Tests for the app_mention event handler."""

    @pytest.fixture
    def setup_handler(self, settings: Settings, session_manager: SessionManager):
        """Set up a mock bolt app and register handlers."""
        mock_app = AsyncMock()
        handlers = {}

        def capture_event(event_type):
            def decorator(func):
                handlers[event_type] = func
                return func

            return decorator

        mock_app.event = capture_event
        register_handlers(mock_app, settings, session_manager)
        return handlers

    async def test_mention_creates_session_and_invokes(
        self, setup_handler, session_manager: SessionManager, mock_say: AsyncMock
    ) -> None:
        """New mention creates session and invokes Claude Code."""
        handler = setup_handler["app_mention"]
        event = {
            "text": "<@U12345> check the logs",
            "ts": "1234567890.000001",
            "channel": "C123",
            "user": "U12345",
        }

        mock_response = ClaudeResponse(result="Logs look fine", session_id="s1")
        with patch(
            "bender.slack_handler.invoke_claude", new_callable=AsyncMock, return_value=mock_response
        ):
            await handler(event=event, say=mock_say)

        # Session should be created
        session_id = await session_manager.get_session("1234567890.000001")
        assert session_id is not None

        # Work is acknowledged immediately, then the response is posted.
        assert mock_say.await_args_list[0].kwargs == {
            "text": "Accepted — I’m working on this now.",
            "thread_ts": "1234567890.000001",
        }
        assert mock_say.await_args_list[1].kwargs == {
            "text": "Logs look fine",
            "thread_ts": "1234567890.000001",
        }

    async def test_mention_empty_text_responds_help(
        self, setup_handler, mock_say: AsyncMock
    ) -> None:
        """Empty mention text gets a help response."""
        handler = setup_handler["app_mention"]
        event = {
            "text": "<@U12345>",
            "ts": "1234567890.000001",
            "channel": "C123",
            "user": "U12345",
        }

        await handler(event=event, say=mock_say)
        mock_say.assert_called_once_with(text="How can I help?", thread_ts="1234567890.000001")

    async def test_mention_claude_error_posts_error(
        self, setup_handler, mock_say: AsyncMock
    ) -> None:
        """Posts error message when Claude Code invocation fails."""
        handler = setup_handler["app_mention"]
        event = {
            "text": "<@U12345> do something",
            "ts": "1234567890.000001",
            "channel": "C123",
            "user": "U12345",
        }

        with patch(
            "bender.slack_handler.invoke_claude",
            new_callable=AsyncMock,
            side_effect=ClaudeCodeError("CLI crashed"),
        ):
            await handler(event=event, say=mock_say)

        assert mock_say.await_count == 2
        call_kwargs = mock_say.await_args_list[1].kwargs
        assert "Sorry, something went wrong" in call_kwargs["text"]
        assert "CLI crashed" in call_kwargs["text"]

    async def test_unauthorized_mention_is_ignored(
        self,
        setup_handler,
        session_manager: SessionManager,
        mock_say: AsyncMock,
    ) -> None:
        """A Slack user outside the explicit allowlist cannot invoke Claude."""
        handler = setup_handler["app_mention"]
        event = {
            "text": "<@UBENDER> inspect the data",
            "ts": "1234567890.000001",
            "channel": "C123",
            "user": "U99999",
        }

        with patch("bender.slack_handler.invoke_claude", new_callable=AsyncMock) as mock_invoke:
            await handler(event=event, say=mock_say)

        mock_invoke.assert_not_awaited()
        mock_say.assert_not_awaited()
        assert await session_manager.get_session(event["ts"]) is None

    async def test_mention_passes_configured_timeout(
        self, setup_handler, mock_say: AsyncMock
    ) -> None:
        """Slack work uses the operator-configured Claude deadline."""
        handler = setup_handler["app_mention"]
        event = {
            "text": "<@UBENDER> inspect the data",
            "ts": "1234567890.000001",
            "channel": "C123",
            "user": "U12345",
        }
        mock_response = ClaudeResponse(result="Done", session_id="s1")

        with patch(
            "bender.slack_handler.invoke_claude",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_invoke:
            await handler(event=event, say=mock_say)

        assert mock_invoke.await_args.kwargs["timeout"] == 900


class TestHandleMessage:
    """Tests for the message event handler (thread replies)."""

    @pytest.fixture
    def setup_handler(self, settings: Settings, session_manager: SessionManager):
        """Set up a mock bolt app and register handlers."""
        mock_app = AsyncMock()
        handlers = {}

        def capture_event(event_type):
            def decorator(func):
                handlers[event_type] = func
                return func

            return decorator

        mock_app.event = capture_event
        register_handlers(mock_app, settings, session_manager)
        return handlers

    async def test_thread_reply_resumes_session(
        self, setup_handler, session_manager: SessionManager, mock_say: AsyncMock
    ) -> None:
        """Thread reply resumes existing Claude Code session."""
        handler = setup_handler["message"]
        thread_ts = "1234567890.000001"
        await session_manager.create_session(thread_ts)

        event = {
            "text": "yes, go ahead",
            "thread_ts": thread_ts,
            "channel": "C123",
            "user": "U12345",
        }

        mock_response = ClaudeResponse(result="Done!", session_id="s1")
        with patch(
            "bender.slack_handler.invoke_claude",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_invoke:
            await handler(event=event, say=mock_say)

        # Should invoke with resume=True
        mock_invoke.assert_called_once()
        assert mock_invoke.call_args[1]["resume"] is True
        assert mock_invoke.call_args[1]["timeout"] == 900
        assert mock_say.await_args_list[0].kwargs["text"] == "Accepted — I’m working on this now."
        assert mock_say.await_args_list[1].kwargs == {
            "text": "Done!",
            "thread_ts": thread_ts,
        }

    async def test_thread_reply_ignores_bot_messages(
        self, setup_handler, session_manager: SessionManager, mock_say: AsyncMock
    ) -> None:
        """Bot messages are ignored to prevent loops."""
        handler = setup_handler["message"]
        thread_ts = "1234567890.000001"
        await session_manager.create_session(thread_ts)

        event = {
            "text": "bot response",
            "thread_ts": thread_ts,
            "bot_id": "B12345",
            "channel": "C123",
            "user": "U12345",
        }

        await handler(event=event, say=mock_say)
        mock_say.assert_not_called()

    async def test_thread_reply_ignores_subtype_messages(
        self, setup_handler, session_manager: SessionManager, mock_say: AsyncMock
    ) -> None:
        """Messages with subtype (e.g., channel_join) are ignored."""
        handler = setup_handler["message"]
        thread_ts = "1234567890.000001"
        await session_manager.create_session(thread_ts)

        event = {
            "text": "joined the channel",
            "thread_ts": thread_ts,
            "subtype": "channel_join",
            "channel": "C123",
            "user": "U12345",
        }

        await handler(event=event, say=mock_say)
        mock_say.assert_not_called()

    async def test_non_thread_message_ignored(self, setup_handler, mock_say: AsyncMock) -> None:
        """Non-thread messages are ignored."""
        handler = setup_handler["message"]
        event = {"text": "hello", "channel": "C123", "user": "U12345"}

        await handler(event=event, say=mock_say)
        mock_say.assert_not_called()

    async def test_untracked_thread_ignored(self, setup_handler, mock_say: AsyncMock) -> None:
        """Thread replies in untracked threads are ignored."""
        handler = setup_handler["message"]
        event = {
            "text": "hello",
            "thread_ts": "9999999999.999999",
            "channel": "C123",
            "user": "U12345",
        }

        await handler(event=event, say=mock_say)
        mock_say.assert_not_called()

    async def test_thread_reply_empty_text_ignored(
        self, setup_handler, session_manager: SessionManager, mock_say: AsyncMock
    ) -> None:
        """Empty thread replies are ignored."""
        handler = setup_handler["message"]
        thread_ts = "1234567890.000001"
        await session_manager.create_session(thread_ts)

        event = {
            "text": "<@U12345>",
            "thread_ts": thread_ts,
            "channel": "C123",
            "user": "U12345",
        }

        await handler(event=event, say=mock_say)
        mock_say.assert_not_called()

    async def test_thread_reply_claude_error(
        self, setup_handler, session_manager: SessionManager, mock_say: AsyncMock
    ) -> None:
        """Posts error message on Claude Code failure in thread replies."""
        handler = setup_handler["message"]
        thread_ts = "1234567890.000001"
        await session_manager.create_session(thread_ts)

        event = {
            "text": "do something",
            "thread_ts": thread_ts,
            "channel": "C123",
            "user": "U12345",
        }

        with patch(
            "bender.slack_handler.invoke_claude",
            new_callable=AsyncMock,
            side_effect=ClaudeCodeError("timeout"),
        ):
            await handler(event=event, say=mock_say)

        assert mock_say.await_count == 2
        assert "Sorry, something went wrong" in mock_say.await_args_list[1].kwargs["text"]

    async def test_unauthorized_thread_reply_is_ignored(
        self,
        setup_handler,
        session_manager: SessionManager,
        mock_say: AsyncMock,
    ) -> None:
        """An allowlisted session cannot be resumed by a different Slack user."""
        handler = setup_handler["message"]
        thread_ts = "1234567890.000001"
        await session_manager.create_session(thread_ts)
        event = {
            "text": "show me the table",
            "thread_ts": thread_ts,
            "channel": "C123",
            "user": "U99999",
        }

        with patch("bender.slack_handler.invoke_claude", new_callable=AsyncMock) as mock_invoke:
            await handler(event=event, say=mock_say)

        mock_invoke.assert_not_awaited()
        mock_say.assert_not_awaited()
