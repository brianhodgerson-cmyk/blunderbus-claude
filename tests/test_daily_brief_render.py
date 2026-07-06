from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "agents"))

from base import AgentReport, Concern  # noqa: E402
import daily_brief  # noqa: E402


class MorningCommandBriefRenderTests(unittest.TestCase):
    def _reports(self):
        return [
            AgentReport(
                agent="finance",
                status="ok",
                as_of=datetime(2026, 6, 5, 7, 0),
                headline="NW $307,659 · all baselines normal",
                metrics={"net_worth": 307659},
                questions=["[nfcu-share-savings] Confirm owner of `Share Savings (...8001)`"],
                duration_ms=120,
            ),
            AgentReport(
                agent="infra",
                status="degraded",
                as_of=datetime(2026, 6, 5, 7, 0),
                headline="2/8 hosts · 1 pool · 7 real concerns",
                real_concerns=[
                    Concern(
                        severity="high",
                        summary="Thor: Ollama unreachable",
                        days_seen=1,
                        suggested_action="Check Thor network path before local model work",
                    ),
                    Concern(
                        severity="medium",
                        summary="Loki offline",
                        days_seen=8,
                        suggested_action="Restore Loki if observability work is planned",
                    ),
                ],
                carried_concerns=[
                    Concern(severity="medium", summary="Host fury is down", days_seen=3)
                ],
                duration_ms=220,
            ),
            AgentReport(
                agent="workspace",
                status="degraded",
                as_of=datetime(2026, 6, 5, 7, 0),
                headline="5 events today · 201 unread · 6 tasks open",
                metrics={"unread_email": 201, "events_today": 5, "obsidian_tasks_open": 6},
                raw_data={
                    "events": [
                        {"start": "09:00", "summary": "Nike CSIRT on-call (Primary)"},
                        {"start": "17:30", "summary": "Eva gymnastics 5:30"},
                    ]
                },
                duration_ms=320,
            ),
        ]

    def test_obsidian_block_is_command_brief_without_ai(self):
        block = daily_brief.compose_obsidian(date(2026, 6, 5), self._reports(), "")

        self.assertIn("## 🔥 Read This First", block)
        self.assertIn("## ✅ Action Queue", block)
        self.assertIn("## 🧠 Memory & Freshness", block)
        self.assertIn("## 📅 Today", block)
        self.assertIn("## 🏠 HodgeSpot Ops", block)
        self.assertIn("## 📬 Workspace", block)
        self.assertIn("## 💸 Finance", block)
        self.assertIn("Thor: Ollama unreachable", block)
        self.assertIn("Loki offline", block)
        self.assertIn("day 8", block)
        self.assertIn("201 unread", block)
        self.assertIn("Nike CSIRT on-call", block)

    def test_discord_notification_has_topline_and_actions_without_ai(self):
        msg = daily_brief.compose_discord_notification(date(2026, 6, 5), self._reports(), "")

        self.assertIn("Morning, Brian", msg)
        self.assertIn("Read first", msg)
        self.assertIn("Action queue", msg)
        self.assertIn("Thor: Ollama unreachable", msg)
        self.assertLessEqual(len(msg), 1900)


if __name__ == "__main__":
    unittest.main()
