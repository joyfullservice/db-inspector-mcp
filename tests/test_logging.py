"""Tests for usage logging lifecycle and hot-reload interactions."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import db_inspector_mcp.usage_logging as logging_module
from db_inspector_mcp.usage_logging import (
    _initialize_logging,
    reset_logging,
)


@pytest.fixture(autouse=True)
def _clean_logging_state():
    """Reset module-level logging state before and after every test."""
    logging_module._logging_enabled = None
    logging_module._log_handler = None
    logging_module._log_file = None
    yield
    logging_module._logging_enabled = None
    logging_module._log_handler = None
    logging_module._log_file = None


class TestInitializeLogging:
    """Tests for _initialize_logging() caching behaviour."""

    def test_disabled_not_cached(self):
        """When logging is disabled, _logging_enabled stays None so the
        check re-runs on the next call (allows lazy .env loading to
        enable it later).
        """
        with patch.dict(os.environ, {"DB_MCP_ENABLE_LOGGING": "false"}, clear=False):
            result = _initialize_logging()
            assert result is False
            assert logging_module._logging_enabled is None

    def test_enabled_cached(self, tmp_path):
        """When logging is enabled and initialisation succeeds, the True
        state is cached so subsequent calls skip re-init.
        """
        log_dir = tmp_path / "logs"
        with patch.dict(
            os.environ,
            {"DB_MCP_ENABLE_LOGGING": "true", "DB_MCP_LOG_DIR": str(log_dir)},
            clear=False,
        ):
            result = _initialize_logging()
            assert result is True
            assert logging_module._logging_enabled is True

            second = _initialize_logging()
            assert second is True

    def test_works_after_lazy_env_load(self, tmp_path):
        """Simulates the lazy-init scenario: first call with no env var
        returns False, then the env var is set, and the next call
        initialises successfully.
        """
        log_dir = tmp_path / "logs"

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DB_MCP_ENABLE_LOGGING", None)
            os.environ.pop("DB_MCP_LOG_DIR", None)

            assert _initialize_logging() is False
            assert logging_module._logging_enabled is None

        with patch.dict(
            os.environ,
            {"DB_MCP_ENABLE_LOGGING": "true", "DB_MCP_LOG_DIR": str(log_dir)},
            clear=False,
        ):
            assert _initialize_logging() is True
            assert logging_module._logging_enabled is True

    def test_failure_cached_to_avoid_retry_spam(self, tmp_path):
        """When init fails (e.g. can't create log dir), False is cached
        so we don't retry and spam stderr on every tool call.
        """
        bad_dir = tmp_path / "nonexistent" / "deeply" / "nested"

        with patch.dict(
            os.environ,
            {"DB_MCP_ENABLE_LOGGING": "true", "DB_MCP_LOG_DIR": str(bad_dir)},
            clear=False,
        ), patch.object(
            logging_module, "_ensure_log_dir", return_value=False
        ):
            result = _initialize_logging()
            assert result is False
            assert logging_module._logging_enabled is False


class TestResetLogging:
    """Tests for reset_logging() state cleanup."""

    def test_clears_all_state(self, tmp_path):
        """reset_logging() sets all module globals back to None."""
        log_dir = tmp_path / "logs"
        with patch.dict(
            os.environ,
            {"DB_MCP_ENABLE_LOGGING": "true", "DB_MCP_LOG_DIR": str(log_dir)},
            clear=False,
        ):
            _initialize_logging()
            assert logging_module._logging_enabled is True
            assert logging_module._log_handler is not None
            assert logging_module._log_file is not None

        reset_logging()
        assert logging_module._logging_enabled is None
        assert logging_module._log_handler is None
        assert logging_module._log_file is None

    def test_closes_handler(self):
        """reset_logging() calls close() on the existing file handler."""
        mock_handler = MagicMock()
        logging_module._logging_enabled = True
        logging_module._log_handler = mock_handler
        logging_module._log_file = Path("/fake/log.jsonl")

        reset_logging()
        mock_handler.close.assert_called_once()

    def test_safe_when_never_initialized(self):
        """reset_logging() does not raise when logging was never init'd."""
        assert logging_module._log_handler is None
        reset_logging()
        assert logging_module._logging_enabled is None

    def test_reinitializes_after_reset(self, tmp_path):
        """After reset, the next _initialize_logging() call picks up
        fresh env vars and re-creates the handler.
        """
        log_dir = tmp_path / "logs"
        with patch.dict(
            os.environ,
            {"DB_MCP_ENABLE_LOGGING": "true", "DB_MCP_LOG_DIR": str(log_dir)},
            clear=False,
        ):
            _initialize_logging()
            old_handler = logging_module._log_handler

        reset_logging()

        with patch.dict(
            os.environ,
            {"DB_MCP_ENABLE_LOGGING": "true", "DB_MCP_LOG_DIR": str(log_dir)},
            clear=False,
        ):
            _initialize_logging()
            assert logging_module._log_handler is not None
            assert logging_module._log_handler is not old_handler
