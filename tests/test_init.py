"""Tests for the init module (CLI init command and template loader)."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from db_inspector_mcp.init import (
    MCP_JSON_SERVER_ENTRY,
    _ENV_STARTER_BLOCK,
    _env_has_db_mcp_vars,
    _register_global_mcp,
    _register_in_config,
    _write_env_file,
    is_globally_registered,
    load_env_example,
    run_init,
)


class TestLoadEnvExample:
    """Tests for the .env.example template loader."""

    def test_loads_from_repo_root(self):
        """Template is found via the repo root path (editable install)."""
        content = load_env_example()
        assert "DB_MCP_DATABASE" in content
        assert "DB_MCP_CONNECTION_STRING" in content

    def test_content_matches_root_file(self):
        """Loaded content matches the actual .env.example at the repo root."""
        repo_root = Path(__file__).resolve().parent.parent
        expected = (repo_root / ".env.example").read_text(encoding="utf-8")
        assert load_env_example() == expected

    def test_raises_when_not_found(self, tmp_path, monkeypatch):
        """FileNotFoundError is raised when the template cannot be found."""
        # Place a fake init.py deep inside tmp_path so walking up never
        # reaches the real repo root.
        fake_pkg = tmp_path / "a" / "b" / "c"
        fake_pkg.mkdir(parents=True)
        fake_init = fake_pkg / "init.py"
        fake_init.write_text("")

        import db_inspector_mcp.init as init_mod
        original_file = init_mod.__file__
        try:
            init_mod.__file__ = str(fake_init)
            with patch("importlib.resources.files", side_effect=FileNotFoundError):
                with pytest.raises(FileNotFoundError, match="Could not find .env.example"):
                    load_env_example()
        finally:
            init_mod.__file__ = original_file


class TestWriteEnvFile:
    """Tests for _write_env_file."""

    def test_creates_env_file(self, tmp_path):
        """Creates .env from template in the target directory."""
        env_path = _write_env_file(tmp_path, force=False)
        assert env_path == tmp_path / ".env"
        assert env_path.exists()
        content = env_path.read_text(encoding="utf-8")
        assert "DB_MCP_DATABASE" in content

    def test_fails_if_exists(self, tmp_path):
        """Exits with error when .env already exists and --force is not set."""
        (tmp_path / ".env").write_text("existing content")
        with pytest.raises(SystemExit):
            _write_env_file(tmp_path, force=False)

    def test_force_overwrites(self, tmp_path):
        """Overwrites existing .env when --force is set."""
        (tmp_path / ".env").write_text("old content")
        env_path = _write_env_file(tmp_path, force=True)
        content = env_path.read_text(encoding="utf-8")
        assert "DB_MCP_DATABASE" in content
        assert "old content" not in content


class TestRegisterInConfig:
    """Tests for _register_in_config (single-file registration)."""

    def test_creates_new_file(self, tmp_path):
        """Creates config file if it doesn't exist."""
        mcp_json = tmp_path / ".cursor" / "mcp.json"

        result = _register_in_config(mcp_json, quiet=True)
        assert result == mcp_json
        assert mcp_json.exists()

        data = json.loads(mcp_json.read_text())
        assert "db-inspector-mcp" in data["mcpServers"]
        assert data["mcpServers"]["db-inspector-mcp"] == MCP_JSON_SERVER_ENTRY

    def test_adds_to_existing_file(self, tmp_path):
        """Adds db-inspector-mcp entry to existing config without clobbering."""
        mcp_json = tmp_path / ".cursor" / "mcp.json"
        mcp_json.parent.mkdir(parents=True)
        existing = {
            "mcpServers": {
                "other-server": {"command": "other-cmd"}
            }
        }
        mcp_json.write_text(json.dumps(existing))

        _register_in_config(mcp_json, quiet=True)
        data = json.loads(mcp_json.read_text())
        assert "other-server" in data["mcpServers"]
        assert "db-inspector-mcp" in data["mcpServers"]

    def test_skips_if_already_registered(self, tmp_path):
        """Does not modify config if db-inspector-mcp is already registered."""
        mcp_json = tmp_path / ".cursor" / "mcp.json"
        mcp_json.parent.mkdir(parents=True)
        existing = {
            "mcpServers": {
                "db-inspector-mcp": {"command": "db-inspector-mcp", "custom": True}
            }
        }
        mcp_json.write_text(json.dumps(existing))

        _register_in_config(mcp_json, quiet=True)
        data = json.loads(mcp_json.read_text())
        assert data["mcpServers"]["db-inspector-mcp"]["custom"] is True

    def test_handles_corrupt_json(self, tmp_path):
        """Handles corrupt/empty config gracefully."""
        mcp_json = tmp_path / ".cursor" / "mcp.json"
        mcp_json.parent.mkdir(parents=True)
        mcp_json.write_text("not valid json {{{")

        _register_in_config(mcp_json, quiet=True)
        data = json.loads(mcp_json.read_text())
        assert "db-inspector-mcp" in data["mcpServers"]

    def test_no_env_overrides_in_entry(self, tmp_path):
        """Config entry must NOT contain env overrides."""
        mcp_json = tmp_path / "mcp.json"

        _register_in_config(mcp_json, quiet=True)
        data = json.loads(mcp_json.read_text())
        entry = data["mcpServers"]["db-inspector-mcp"]
        assert "env" not in entry


class TestRegisterGlobalMcp:
    """Tests for _register_global_mcp (registers in all known clients)."""

    def test_registers_in_both_clients(self, tmp_path, monkeypatch):
        """Registers in both Cursor and Claude Code config files."""
        cursor_json = tmp_path / ".cursor" / "mcp.json"
        claude_json = tmp_path / ".claude.json"
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json), ("Claude Code", claude_json)],
        )

        _register_global_mcp(quiet=True)

        for path in [cursor_json, claude_json]:
            assert path.exists()
            data = json.loads(path.read_text())
            assert "db-inspector-mcp" in data["mcpServers"]


class TestIsGloballyRegistered:
    """Tests for is_globally_registered."""

    def test_true_when_registered_in_cursor(self, tmp_path, monkeypatch):
        """Returns True when db-inspector-mcp is in Cursor config."""
        cursor_json = tmp_path / ".cursor" / "mcp.json"
        cursor_json.parent.mkdir(parents=True)
        cursor_json.write_text(json.dumps({
            "mcpServers": {"db-inspector-mcp": {"command": "db-inspector-mcp"}}
        }))
        claude_json = tmp_path / ".claude.json"
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json), ("Claude Code", claude_json)],
        )
        assert is_globally_registered() is True

    def test_true_when_registered_in_claude(self, tmp_path, monkeypatch):
        """Returns True when db-inspector-mcp is in Claude Code config."""
        cursor_json = tmp_path / ".cursor" / "mcp.json"
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {"db-inspector-mcp": {"command": "db-inspector-mcp"}}
        }))
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json), ("Claude Code", claude_json)],
        )
        assert is_globally_registered() is True

    def test_false_when_not_registered(self, tmp_path, monkeypatch):
        """Returns False when db-inspector-mcp is not in any config."""
        cursor_json = tmp_path / ".cursor" / "mcp.json"
        cursor_json.parent.mkdir(parents=True)
        cursor_json.write_text(json.dumps({
            "mcpServers": {"other-server": {"command": "other-cmd"}}
        }))
        claude_json = tmp_path / ".claude.json"
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json), ("Claude Code", claude_json)],
        )
        assert is_globally_registered() is False

    def test_false_when_files_missing(self, tmp_path, monkeypatch):
        """Returns False when no config files exist."""
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [
                ("Cursor", tmp_path / ".cursor" / "mcp.json"),
                ("Claude Code", tmp_path / ".claude.json"),
            ],
        )
        assert is_globally_registered() is False

    def test_false_when_corrupt_json(self, tmp_path, monkeypatch):
        """Returns False when config contains invalid JSON."""
        cursor_json = tmp_path / ".cursor" / "mcp.json"
        cursor_json.parent.mkdir(parents=True)
        cursor_json.write_text("not valid json {{{")
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json)],
        )
        assert is_globally_registered() is False


class TestEnvHasDbMcpVars:
    """Tests for _env_has_db_mcp_vars detection."""

    def test_detects_active_var(self, tmp_path):
        """Returns True for uncommented DB_MCP_ variable."""
        env = tmp_path / ".env"
        env.write_text("DB_MCP_DATABASE=sqlserver\n")
        assert _env_has_db_mcp_vars(env) is True

    def test_detects_commented_var(self, tmp_path):
        """Returns True for commented-out DB_MCP_ variable."""
        env = tmp_path / ".env"
        env.write_text("# DB_MCP_DATABASE=sqlserver\n")
        assert _env_has_db_mcp_vars(env) is True

    def test_false_without_db_mcp(self, tmp_path):
        """Returns False when no DB_MCP_ references exist."""
        env = tmp_path / ".env"
        env.write_text("SECRET_KEY=abc\nDEBUG=true\n")
        assert _env_has_db_mcp_vars(env) is False

    def test_false_for_empty_file(self, tmp_path):
        """Returns False for an empty .env file."""
        env = tmp_path / ".env"
        env.write_text("")
        assert _env_has_db_mcp_vars(env) is False


class TestRunInit:
    """Tests for the full run_init CLI command."""

    def test_full_init(self, tmp_path, monkeypatch):
        """Full init creates .env and registers in all client configs."""
        cursor_json = tmp_path / "cursor_home" / ".cursor" / "mcp.json"
        claude_json = tmp_path / "cursor_home" / ".claude.json"
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json), ("Claude Code", claude_json)],
        )

        run_init(["--dir", str(tmp_path)])

        assert (tmp_path / ".env").exists()
        assert cursor_json.exists()
        assert claude_json.exists()

        env_content = (tmp_path / ".env").read_text()
        assert "DB_MCP_DATABASE" in env_content

        for path in [cursor_json, claude_json]:
            data = json.loads(path.read_text())
            assert "db-inspector-mcp" in data["mcpServers"]

    def test_init_skips_env_if_db_mcp_vars_present(self, tmp_path, monkeypatch):
        """Init leaves .env unchanged when it already contains DB_MCP_ config."""
        original = "SECRET_KEY=abc\nDB_MCP_DATABASE=sqlserver\n"
        (tmp_path / ".env").write_text(original)
        cursor_json = tmp_path / "cursor_home" / ".cursor" / "mcp.json"
        claude_json = tmp_path / "cursor_home" / ".claude.json"
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json), ("Claude Code", claude_json)],
        )

        run_init(["--dir", str(tmp_path)])

        assert (tmp_path / ".env").read_text() == original
        assert cursor_json.exists()
        assert claude_json.exists()

    def test_init_skips_when_db_mcp_commented(self, tmp_path, monkeypatch):
        """Init treats commented DB_MCP_ references as already configured."""
        original = "SECRET_KEY=abc\n#DB_MCP_DATABASE=sqlserver\n"
        (tmp_path / ".env").write_text(original)
        cursor_json = tmp_path / "cursor_home" / ".cursor" / "mcp.json"
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json)],
        )

        run_init(["--dir", str(tmp_path)])

        assert (tmp_path / ".env").read_text() == original

    def test_init_appends_when_no_db_mcp_vars(self, tmp_path, monkeypatch):
        """Init appends starter block when .env exists without DB_MCP_ vars."""
        original = "SECRET_KEY=abc\nDEBUG=true\n"
        (tmp_path / ".env").write_text(original)
        cursor_json = tmp_path / "cursor_home" / ".cursor" / "mcp.json"
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json)],
        )

        run_init(["--dir", str(tmp_path)])

        content = (tmp_path / ".env").read_text()
        assert content.startswith(original)
        assert "DB_MCP_DATABASE" in content
        assert "DB_MCP_CONNECTION_STRING" in content
        assert "db-inspector-mcp" in content
        assert cursor_json.exists()

    def test_init_force_overwrites(self, tmp_path, monkeypatch):
        """Init with --force overwrites existing .env."""
        (tmp_path / ".env").write_text("old")
        cursor_json = tmp_path / "cursor_home" / ".cursor" / "mcp.json"
        monkeypatch.setattr(
            "db_inspector_mcp.init._MCP_CLIENT_CONFIGS",
            [("Cursor", cursor_json)],
        )

        run_init(["--dir", str(tmp_path), "--force"])

        content = (tmp_path / ".env").read_text()
        assert "DB_MCP_DATABASE" in content

    def test_init_bad_dir(self, tmp_path):
        """Init exits with error for nonexistent directory."""
        with pytest.raises(SystemExit):
            run_init(["--dir", str(tmp_path / "nonexistent")])
