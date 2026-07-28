"""Tests for the HTTP API endpoints module."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from slack_sdk.errors import SlackApiError

from bender.api import InvokeRequest, InvokeResponse, create_api
from bender.claude_code import ClaudeCodeError, ClaudeResponse
from bender.config import Settings
from bender.session_manager import SessionManager


@pytest.fixture
def settings_with_api_key(settings: Settings) -> Settings:
    """Create Settings with an API key configured."""
    settings.bender_api_key = "test-api-key"
    return settings


@pytest.fixture
def api_app(
    settings_with_api_key: Settings,
    session_manager: SessionManager,
    mock_slack_client: AsyncMock,
):
    """Create a FastAPI app with API routes registered."""
    app = FastAPI()
    create_api(app, mock_slack_client, settings_with_api_key, session_manager)
    return app


@pytest.fixture
def client(api_app: FastAPI) -> TestClient:
    """Create a sync test client for the API."""
    return TestClient(api_app)


@pytest.fixture
async def async_client(api_app: FastAPI):
    """Create an async test client for the API."""
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


AUTH_HEADERS = {"Authorization": "Bearer test-api-key"}


class TestHealthEndpoint:
    """Tests for the GET /health endpoint."""

    def test_health_returns_ok(self, client: TestClient) -> None:
        """Health endpoint returns status ok."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestInvokeAuthentication:
    """Tests for the /api/invoke authentication."""

    def test_missing_auth_header_returns_unauthorized(self, client: TestClient) -> None:
        """Returns 401 when Authorization header is missing."""
        response = client.post("/api/invoke", json={"channel": "C123", "message": "Test"})
        assert response.status_code == 401

    def test_invalid_api_key_returns_401(self, client: TestClient) -> None:
        """Returns 401 when API key is invalid."""
        response = client.post(
            "/api/invoke",
            json={"channel": "C123", "message": "Test"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 401

    def test_no_api_key_configured_returns_503(
        self,
        settings: Settings,
        session_manager: SessionManager,
        mock_slack_client: AsyncMock,
    ) -> None:
        """Returns 503 when server has no API key configured."""
        # Settings without bender_api_key (defaults to None)
        app = FastAPI()
        create_api(app, mock_slack_client, settings, session_manager)
        client = TestClient(app)

        response = client.post(
            "/api/invoke",
            json={"channel": "C123", "message": "Test"},
            headers={"Authorization": "Bearer any-key"},
        )
        assert response.status_code == 503


class TestInvokeEndpoint:
    """Tests for the POST /api/invoke endpoint."""

    async def test_invoke_success(
        self,
        async_client: AsyncClient,
        session_manager: SessionManager,
    ) -> None:
        """Successful invocation creates thread, calls Claude, posts response."""
        mock_claude_response = ClaudeResponse(result="Claude says hello", session_id="session-abc")
        with patch(
            "bender.api.invoke_claude",
            new_callable=AsyncMock,
            return_value=mock_claude_response,
        ) as mock_invoke:
            response = await async_client.post(
                "/api/invoke",
                json={"channel": "C123", "message": "Hello Claude"},
                headers=AUTH_HEADERS,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["thread_ts"] == "1234567890.123456"
        assert data["response"] == "Claude says hello"
        assert mock_invoke.await_args.kwargs["timeout"] == 900

    async def test_invoke_creates_session(
        self,
        async_client: AsyncClient,
        session_manager: SessionManager,
    ) -> None:
        """Invocation creates a session for the thread."""
        mock_claude_response = ClaudeResponse(result="ok", session_id="s1")
        with patch(
            "bender.api.invoke_claude",
            new_callable=AsyncMock,
            return_value=mock_claude_response,
        ):
            await async_client.post(
                "/api/invoke",
                json={"channel": "C123", "message": "Test"},
                headers=AUTH_HEADERS,
            )

        session_id = await session_manager.get_session("1234567890.123456")
        assert session_id is not None

    async def test_invoke_slack_failure_returns_502(
        self,
        api_app: FastAPI,
        mock_slack_client: AsyncMock,
    ) -> None:
        """Returns 502 when Slack post fails."""
        mock_slack_client.chat_postMessage = AsyncMock(
            side_effect=SlackApiError(message="Slack down", response=AsyncMock())
        )

        transport = ASGITransport(app=api_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/invoke",
                json={"channel": "C123", "message": "Test"},
                headers=AUTH_HEADERS,
            )

        assert response.status_code == 502

    async def test_invoke_claude_failure_returns_500(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Returns 500 when Claude Code invocation fails."""
        with patch(
            "bender.api.invoke_claude",
            new_callable=AsyncMock,
            side_effect=ClaudeCodeError("Claude crashed"),
        ):
            response = await async_client.post(
                "/api/invoke",
                json={"channel": "C123", "message": "Test"},
                headers=AUTH_HEADERS,
            )

        assert response.status_code == 500

    def test_invoke_missing_channel_returns_422(self, client: TestClient) -> None:
        """Returns 422 when 'channel' field is missing."""
        response = client.post(
            "/api/invoke",
            json={"message": "Test"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422

    def test_invoke_missing_message_returns_422(self, client: TestClient) -> None:
        """Returns 422 when 'message' field is missing."""
        response = client.post(
            "/api/invoke",
            json={"channel": "C123"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422

    def test_invoke_empty_body_returns_422(self, client: TestClient) -> None:
        """Returns 422 when request body is empty."""
        response = client.post(
            "/api/invoke",
            json={},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422


class TestInvokeRequestModel:
    """Tests for the InvokeRequest Pydantic model."""

    def test_valid_request(self) -> None:
        """Valid request parses correctly."""
        req = InvokeRequest(channel="C123", message="hello")
        assert req.channel == "C123"
        assert req.message == "hello"


class TestInvokeResponseModel:
    """Tests for the InvokeResponse Pydantic model."""

    def test_valid_response(self) -> None:
        """Valid response serializes correctly."""
        resp = InvokeResponse(thread_ts="1234.5678", session_id="s1", response="hello")
        assert resp.thread_ts == "1234.5678"
        assert resp.session_id == "s1"
        assert resp.response == "hello"
