"""Shared helpers for running long-lived LLM subprocesses with progress logs."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class LlmCommandResult:
    returncode: int
    tail: str


def run_llm_command_with_progress(command: list[str], *, cwd: Path, log_path: Path, label: str) -> LlmCommandResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"{label}: started. Log: {log_path}", file=sys.stderr)
    tail_lines: list[str] = []
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            bufsize=1,
        )
        assert process.stdout is not None
        output_queue: queue.Queue[str] = queue.Queue()
        reader = threading.Thread(target=_enqueue_process_output, args=(process.stdout, output_queue), daemon=True)
        reader.start()
        last_progress = time.monotonic()
        while process.poll() is None or not output_queue.empty():
            try:
                line = output_queue.get(timeout=1)
            except queue.Empty:
                now = time.monotonic()
                if now - last_progress > 30:
                    print(f"{label}: still running. Log: {log_path}", file=sys.stderr)
                    last_progress = now
                continue
            log.write(line)
            log.flush()
            stripped = line.strip()
            if stripped:
                tail_lines.append(stripped)
                tail_lines = tail_lines[-20:]
                if is_progress_line(stripped):
                    print(f"{label}: {stripped[:180]}", file=sys.stderr)
                    last_progress = time.monotonic()
        returncode = process.wait()
        reader.join(timeout=1)
    print(
        f"{label}: {'completed' if returncode == 0 else f'failed with exit {returncode}'}. Log: {log_path}",
        file=sys.stderr,
    )
    return LlmCommandResult(returncode=returncode, tail="\n".join(tail_lines[-10:]))


def _enqueue_process_output(stream: TextIO, output_queue: queue.Queue[str]) -> None:
    try:
        for line in stream:
            output_queue.put(line)
    finally:
        stream.close()


def is_progress_line(line: str) -> bool:
    lowered = line.lower()
    markers = (
        "thinking",
        "analy",
        "read",
        "edit",
        "write",
        "created",
        "updated",
        "generated",
        "validation",
        "running",
        "completed",
        "error",
    )
    return any(marker in lowered for marker in markers)
