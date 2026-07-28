"""Tests for the main application module."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bender import __version__
from bender.app import BenderApp, create_app, start
from bender.config import Settings


class TestCreateApp:
    """Tests for the create_app factory function."""

    @patch("bender.app.AsyncSocketModeHandler")
    def test_returns_bender_app(self, mock_handler_cls, settings: Settings) -> None:
        """create_app returns a BenderApp instance."""
        app = create_app(settings)
        assert isinstance(app, BenderApp)

    @patch("bender.app.AsyncSocketModeHandler")
    def test_fastapi_app_configured(self, mock_handler_cls, settings: Settings) -> None:
        """FastAPI app has correct title and version."""
        app = create_app(settings)
        assert app.fastapi_app.title == "Bender API"
        assert app.fastapi_app.version == __version__

    @patch("bender.app.AsyncSocketModeHandler")
    def test_settings_stored(self, mock_handler_cls, settings: Settings) -> None:
        """Settings are stored in the BenderApp."""
        app = create_app(settings)
        assert app.settings is settings

    @patch("bender.app.AsyncSocketModeHandler")
    def test_health_endpoint_registered(self, mock_handler_cls, settings: Settings) -> None:
        """Health endpoint is registered on the FastAPI app."""
        app = create_app(settings)
        routes = [r.path for r in app.fastapi_app.routes]
        assert "/health" in routes

    @patch("bender.app.AsyncSocketModeHandler")
    def test_invoke_endpoint_registered(self, mock_handler_cls, settings: Settings) -> None:
        """Invoke endpoint is registered on the FastAPI app."""
        app = create_app(settings)
        routes = [r.path for r in app.fastapi_app.routes]
        assert "/api/invoke" in routes

    @patch("bender.app.AsyncSocketModeHandler")
    def test_socket_handler_created_with_app_token(
        self, mock_handler_cls, settings: Settings
    ) -> None:
        """AsyncSocketModeHandler is created with the correct app token."""
        create_app(settings)
        mock_handler_cls.assert_called_once()
        call_args = mock_handler_cls.call_args
        assert call_args[0][1] == settings.slack_app_token

    async def test_start_binds_fastapi_to_configured_host(self, settings: Settings) -> None:
        """The HTTP API honors the configured loopback bind address."""
        settings.bender_api_host = "127.0.0.2"
        app = SimpleNamespace(
            fastapi_app=MagicMock(),
            socket_handler=SimpleNamespace(
                start_async=AsyncMock(return_value=None),
            ),
        )
        mock_server = MagicMock()
        mock_server.serve = AsyncMock(return_value=None)

        with (
            patch("bender.app.uvicorn.Config") as mock_config,
            patch("bender.app.uvicorn.Server", return_value=mock_server),
        ):
            await start(app, settings)

        assert mock_config.call_args.kwargs["host"] == "127.0.0.2"
