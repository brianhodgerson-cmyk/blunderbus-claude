#!/usr/bin/env python3
"""Chat history persistence for the Telegram bot."""

from __future__ import annotations

import json
import logging
from collections import deque

from runtime import env_first


logger = logging.getLogger(__name__)


class MemoryHistoryStore:
    def __init__(self, max_entries: int):
        self.max_entries = max_entries
        self._history: dict[int, deque] = {}

    def get(self, chat_id: int) -> deque:
        if chat_id not in self._history:
            self._history[chat_id] = deque(maxlen=self.max_entries)
        return self._history[chat_id]

    def append(self, chat_id: int, role: str, text: str) -> None:
        self.get(chat_id).append((role, text))

    def clear(self, chat_id: int) -> None:
        self._history.pop(chat_id, None)


class RedisHistoryStore:
    def __init__(self, client, prefix: str, max_entries: int, ttl_seconds: int):
        self.client = client
        self.prefix = prefix
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds

    def _key(self, chat_id: int) -> str:
        return f"{self.prefix}:{chat_id}"

    def get(self, chat_id: int) -> deque:
        raw = self.client.lrange(self._key(chat_id), 0, -1)
        items = deque(maxlen=self.max_entries)
        for entry in raw:
            try:
                role, text = json.loads(entry)
            except Exception:
                continue
            items.append((role, text))
        return items

    def append(self, chat_id: int, role: str, text: str) -> None:
        key = self._key(chat_id)
        payload = json.dumps([role, text], ensure_ascii=True)
        pipe = self.client.pipeline()
        pipe.rpush(key, payload)
        pipe.ltrim(key, -self.max_entries, -1)
        pipe.expire(key, self.ttl_seconds)
        pipe.execute()

    def clear(self, chat_id: int) -> None:
        self.client.delete(self._key(chat_id))


def create_history_store(max_turns: int):
    max_entries = max_turns * 2
    backend = (env_first("BLUNDERBUS_HISTORY_BACKEND", default="memory") or "memory").strip().lower()
    redis_url = env_first("REDIS_URL", "BLUNDERBUS_REDIS_URL")

    if backend == "redis" or redis_url:
        try:
            import redis

            client = redis.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            logger.info("Telegram history backend: redis")
            return RedisHistoryStore(
                client,
                prefix=env_first("BLUNDERBUS_HISTORY_PREFIX", default="blunderbus:telegram:history") or "blunderbus:telegram:history",
                max_entries=max_entries,
                ttl_seconds=int(env_first("BLUNDERBUS_HISTORY_TTL_SECONDS", default="1209600") or "1209600"),
            )
        except Exception as exc:
            logger.warning("Redis history unavailable, falling back to memory: %s", exc)

    logger.info("Telegram history backend: memory")
    return MemoryHistoryStore(max_entries=max_entries)
