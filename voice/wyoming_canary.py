#!/usr/bin/env python3
"""Wyoming ASR shim for the warm Canary-Qwen STT server.

Exposes the local canary_server (HTTP POST /transcribe, file-path based) as a
Wyoming-protocol ASR service so Home Assistant's Assist pipeline can use the
same warm GPU model that powers the desk Stream Deck PTT.

MUST run on the same host as canary-stt.service (the Canary API takes a local
file path, not audio bytes).

Usage:
    python wyoming_canary.py --uri tcp://0.0.0.0:10300 \
        --canary-url http://127.0.0.1:8765

Protocol flow per session:
    describe -> Info(asr=[canary-qwen])
    transcribe (optional) -> noted, single-model server so ignored
    audio-start -> open buffer
    audio-chunk -> append PCM
    audio-stop -> write temp WAV, POST path to Canary, reply Transcript
"""

import argparse
import asyncio
import io
import json
import logging
import os
import tempfile
import urllib.request
import wave

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info
from wyoming.server import AsyncEventHandler, AsyncServer

_LOGGER = logging.getLogger("wyoming_canary")

ATTRIBUTION = Attribution(
    name="NVIDIA", url="https://huggingface.co/nvidia/canary-qwen-2.5b"
)

INFO = Info(
    asr=[
        AsrProgram(
            name="canary-qwen",
            description="NVIDIA Canary-Qwen 2.5B (warm local GPU server)",
            attribution=ATTRIBUTION,
            installed=True,
            version="1.0",
            models=[
                AsrModel(
                    name="canary-qwen-2.5b",
                    description="Canary-Qwen 2.5B via local canary-stt service",
                    attribution=ATTRIBUTION,
                    installed=True,
                    version="2.5b",
                    languages=["en"],
                )
            ],
        )
    ]
)


def transcribe_wav(canary_url: str, wav_path: str, timeout: float = 60.0) -> str:
    """POST the WAV file path to the Canary server; return transcript text."""
    payload = json.dumps({"audio": wav_path, "max_new_tokens": 256}).encode()
    req = urllib.request.Request(
        f"{canary_url}/transcribe",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
    # canary_server returns {"text": "..."} (fall back to common alternatives)
    for key in ("text", "transcript", "result"):
        if isinstance(body.get(key), str):
            return body[key].strip()
    _LOGGER.warning("Unexpected Canary response keys: %s", list(body))
    return ""


class CanaryEventHandler(AsyncEventHandler):
    def __init__(self, *args, canary_url: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.canary_url = canary_url
        self._wav_buffer: io.BytesIO | None = None
        self._wav_writer: wave.Wave_write | None = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(INFO.event())
            return True

        if Transcribe.is_type(event.type):
            # Single-model server; nothing to select.
            return True

        if AudioStart.is_type(event.type):
            start = AudioStart.from_event(event)
            self._wav_buffer = io.BytesIO()
            self._wav_writer = wave.open(self._wav_buffer, "wb")
            self._wav_writer.setnchannels(start.channels)
            self._wav_writer.setsampwidth(start.width)
            self._wav_writer.setframerate(start.rate)
            return True

        if AudioChunk.is_type(event.type):
            if self._wav_writer is None:
                # Some clients skip audio-start; default to Wyoming norms.
                chunk = AudioChunk.from_event(event)
                self._wav_buffer = io.BytesIO()
                self._wav_writer = wave.open(self._wav_buffer, "wb")
                self._wav_writer.setnchannels(chunk.channels)
                self._wav_writer.setsampwidth(chunk.width)
                self._wav_writer.setframerate(chunk.rate)
            chunk = AudioChunk.from_event(event)
            self._wav_writer.writeframes(chunk.audio)
            return True

        if AudioStop.is_type(event.type):
            text = ""
            if self._wav_writer is not None:
                self._wav_writer.close()
                wav_bytes = self._wav_buffer.getvalue()
                self._wav_writer = None
                self._wav_buffer = None
                text = await asyncio.get_running_loop().run_in_executor(
                    None, self._transcribe_bytes, wav_bytes
                )
            _LOGGER.info("Transcript: %s", text)
            await self.write_event(Transcript(text=text).event())
            return True

        return True

    def _transcribe_bytes(self, wav_bytes: bytes) -> str:
        fd, path = tempfile.mkstemp(prefix="wyoming-canary-", suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav_bytes)
            return transcribe_wav(self.canary_url, path)
        except Exception:
            _LOGGER.exception("Canary transcription failed")
            return ""
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", default="tcp://0.0.0.0:10300")
    parser.add_argument("--canary-url", default="http://127.0.0.1:8765")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("Wyoming Canary shim on %s -> %s", args.uri, args.canary_url)
    await server.run(
        lambda *a, **kw: CanaryEventHandler(*a, canary_url=args.canary_url, **kw)
    )


if __name__ == "__main__":
    asyncio.run(main())
