#!/usr/bin/env python3
"""Filesystem-backed smoke test for the ProfX build path."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


def main() -> None:
    today = date(2026, 3, 28)
    yesterday = today - timedelta(days=1)

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["BLUNDERBUS_NOTE_BACKEND"] = "filesystem"
        os.environ["BLUNDERBUS_VAULT_ROOT"] = tmpdir
        os.environ["BLUNDERBUS_SKIP_EVENT_LOG"] = "1"
        os.environ["TELEGRAM_BOT_TOKEN"] = "123:smoke-test"
        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "1"

        note_store = importlib.import_module("note_store")
        morning_prep = importlib.import_module("morning_prep")
        telegram_bot = importlib.import_module("telegram_bot")

        store = note_store.resolve_note_store()
        assert store.backend_name == "filesystem"

        yesterday_note = """---
date: 2026-03-27
type: daily
tags: [daily]
---

# Friday, March 27, 2026

## Tasks

- [ ] Carry this task
- [x] Finished task ✅ 2026-03-27
"""
        store.write_daily(yesterday, yesterday_note)

        note = morning_prep.build_note(
            today,
            [("Carry this task", yesterday, 1)],
            ["- 9:00 AM - Smoke test event"],
            ["Ship the backend", "Verify the pipeline", "Review the note output"],
        )
        store.write_daily(today, note)

        current = store.read_daily(today)
        assert "Carry this task" in current
        assert "Smoke test event" in current
        assert "Ship the backend" in current

        finance_block = "*Smoke test finance block*\n"
        updated = note_store.upsert_section(current, "## Finance", finance_block)
        store.write_daily(today, updated)
        current = store.read_daily(today)
        assert "*Smoke test finance block*" in current

        export_dir = Path(tmpdir) / "samsunghealth_smoke"
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "step_daily_trend.csv").write_text(
            "\n".join(
                [
                    "metadata",
                    "day_time,count,distance,calorie",
                    "2026-03-28,5432,4025,315",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (export_dir / "sleep_combined.csv").write_text(
            "\n".join(
                [
                    "metadata",
                    "sleep_score,sleep_duration,start_time,end_time,mental_recovery,physical_recovery",
                    "82,445,2026-03-27 23:40:00,2026-03-28 07:05:00,79,81",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                "scripts/obsidian-health-push.py",
                "--date",
                today.isoformat(),
                "--export-path",
                str(export_dir),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
            check=True,
        )
        current = store.read_daily(today)
        assert "😴 Sleep" in current
        assert "👟 Steps" in current

        telegram_bot.HISTORY_STORE.clear(1)
        telegram_bot.HISTORY_STORE.append(1, "User", "hello")
        telegram_bot.HISTORY_STORE.append(1, "Assistant", "hi")
        prompt = telegram_bot._build_prompt(telegram_bot.HISTORY_STORE.get(1), "status?")
        assert "status?" in prompt
        assert "User: status?" in prompt

        print("Smoke test passed: note creation, finance injection, health note injection, and Telegram history startup path.")
        print(f"Vault root: {Path(tmpdir)}")


if __name__ == "__main__":
    main()
