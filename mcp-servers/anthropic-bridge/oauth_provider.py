"""
SQLite-backed OAuth 2.1 provider for the BlunderBus MCP server.

Subclasses fastmcp's InMemoryOAuthProvider, keeping all the OAuth 2.1
flow logic (DCR, PKCE, code exchange, refresh) and adding restart-safe
persistence for:

  - registered clients (so claude.ai's DCR registration survives restarts)
  - access tokens (so an open claude.ai session doesn't get logged out)
  - refresh tokens (so token rotation works after restart)

Authorization codes (5-minute ephemeral) are intentionally NOT persisted.

Schema is created on first run; no migrations.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from mcp.server.auth.provider import AccessToken, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider


SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id        TEXT PRIMARY KEY,
    client_info_json TEXT NOT NULL,
    created_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS access_tokens (
    token       TEXT PRIMARY KEY,
    token_json  TEXT NOT NULL,
    expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token       TEXT PRIMARY KEY,
    token_json  TEXT NOT NULL,
    expires_at  INTEGER
);

CREATE TABLE IF NOT EXISTS token_pairs (
    access_token  TEXT PRIMARY KEY,
    refresh_token TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_access_expiry  ON access_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_refresh_expiry ON refresh_tokens(expires_at);
"""


class SqliteOAuthProvider(InMemoryOAuthProvider):
    """
    OAuth provider with SQLite-backed durability.

    All in-memory dicts inherited from InMemoryOAuthProvider remain the
    hot path. Mutations are mirrored to SQLite so a process restart can
    rehydrate state from disk in __init__.
    """

    def __init__(self, db_path: Path | str, **kwargs):
        super().__init__(**kwargs)
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._rehydrate()

    # ─── rehydrate on boot ──────────────────────────────────────────────────

    def _rehydrate(self) -> None:
        with self._lock:
            now = int(time.time())

            for row in self._conn.execute("SELECT client_info_json FROM clients"):
                try:
                    info = OAuthClientInformationFull.model_validate_json(row[0])
                except Exception:
                    continue
                if info.client_id:
                    self.clients[info.client_id] = info

            self._conn.execute("DELETE FROM access_tokens WHERE expires_at < ?", (now,))
            for row in self._conn.execute("SELECT token_json FROM access_tokens"):
                try:
                    tok = AccessToken.model_validate_json(row[0])
                except Exception:
                    continue
                self.access_tokens[tok.token] = tok

            self._conn.execute(
                "DELETE FROM refresh_tokens WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            for row in self._conn.execute("SELECT token_json FROM refresh_tokens"):
                try:
                    tok = RefreshToken.model_validate_json(row[0])
                except Exception:
                    continue
                self.refresh_tokens[tok.token] = tok

            for access_t, refresh_t in self._conn.execute(
                "SELECT access_token, refresh_token FROM token_pairs"
            ):
                if access_t in self.access_tokens and refresh_t in self.refresh_tokens:
                    self._access_to_refresh_map[access_t] = refresh_t
                    self._refresh_to_access_map[refresh_t] = access_t
                else:
                    self._conn.execute(
                        "DELETE FROM token_pairs WHERE access_token = ?", (access_t,)
                    )

            self._conn.commit()

    # ─── persistence helpers ────────────────────────────────────────────────

    def _persist_client(self, info: OAuthClientInformationFull) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO clients (client_id, client_info_json, created_at) "
                "VALUES (?, ?, ?)",
                (info.client_id, info.model_dump_json(), int(time.time())),
            )
            self._conn.commit()

    def _persist_access_token(self, tok: AccessToken) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO access_tokens (token, token_json, expires_at) "
                "VALUES (?, ?, ?)",
                (tok.token, tok.model_dump_json(), int(tok.expires_at or 0)),
            )
            self._conn.commit()

    def _persist_refresh_token(self, tok: RefreshToken) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO refresh_tokens (token, token_json, expires_at) "
                "VALUES (?, ?, ?)",
                (tok.token, tok.model_dump_json(), tok.expires_at),
            )
            self._conn.commit()

    def _persist_pair(self, access_t: str, refresh_t: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO token_pairs (access_token, refresh_token) "
                "VALUES (?, ?)",
                (access_t, refresh_t),
            )
            self._conn.commit()

    def _delete_access_token(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM access_tokens WHERE token = ?", (token,))
            self._conn.execute("DELETE FROM token_pairs WHERE access_token = ?", (token,))
            self._conn.commit()

    def _delete_refresh_token(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (token,))
            self._conn.execute("DELETE FROM token_pairs WHERE refresh_token = ?", (token,))
            self._conn.commit()

    # ─── override mutating methods ──────────────────────────────────────────

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await super().register_client(client_info)
        if client_info.client_id and client_info.client_id in self.clients:
            self._persist_client(self.clients[client_info.client_id])

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code
    ) -> OAuthToken:
        token = await super().exchange_authorization_code(client, authorization_code)
        access_t = token.access_token
        refresh_t = token.refresh_token
        if access_t in self.access_tokens:
            self._persist_access_token(self.access_tokens[access_t])
        if refresh_t and refresh_t in self.refresh_tokens:
            self._persist_refresh_token(self.refresh_tokens[refresh_t])
        if access_t and refresh_t:
            self._persist_pair(access_t, refresh_t)
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        token = await super().exchange_refresh_token(client, refresh_token, scopes)
        access_t = token.access_token
        new_refresh_t = token.refresh_token
        if access_t in self.access_tokens:
            self._persist_access_token(self.access_tokens[access_t])
        if new_refresh_t and new_refresh_t in self.refresh_tokens:
            self._persist_refresh_token(self.refresh_tokens[new_refresh_t])
        if access_t and new_refresh_t:
            self._persist_pair(access_t, new_refresh_t)
        return token

    def _revoke_internal(
        self,
        access_token_str: str | None = None,
        refresh_token_str: str | None = None,
    ):
        paired_refresh = None
        paired_access = None
        if access_token_str:
            paired_refresh = self._access_to_refresh_map.get(access_token_str)
        if refresh_token_str:
            paired_access = self._refresh_to_access_map.get(refresh_token_str)

        super()._revoke_internal(
            access_token_str=access_token_str, refresh_token_str=refresh_token_str
        )

        if access_token_str:
            self._delete_access_token(access_token_str)
        if paired_refresh:
            self._delete_refresh_token(paired_refresh)
        if refresh_token_str:
            self._delete_refresh_token(refresh_token_str)
        if paired_access:
            self._delete_access_token(paired_access)
