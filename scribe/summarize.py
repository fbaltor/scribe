"""Summarization for meetings.

Turns a transcribed meeting dir (``transcript.txt``) into a short, iwe-ready
Markdown note (``summary.md``) sitting next to the transcript.

Provider-agnostic by design. The default ``claude-code`` provider shells out to
the Claude Code CLI (``claude -p``) -- zero config, no API key, no extra
dependency, using your existing login. Adding another backend (Anthropic SDK,
Ollama, ...) is just writing a ``Summarizer`` and registering it in ``PROVIDERS``.

Privacy note: only the *text* transcript is sent to the provider; the audio never
leaves the box.

Writes into the meeting dir:

  summary.md   titled, dated, concise prose summary (copy into your notes)
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .capture import recordings_dir

# --- summarization parameters ----------------------------------------------
DEFAULT_PROVIDER = "claude-code"
TRANSCRIPT_TXT = "transcript.txt"   # produced by `scribe transcribe`
SUMMARY_MD = "summary.md"
TIMEOUT = 300                       # seconds to wait for the provider

DEFAULT_PROMPT = (
    "You are summarizing a meeting transcript. Lines are labeled by speaker -- "
    '"You" is the person who recorded the meeting, "Them" is everyone else -- '
    "and timestamped [hh:mm:ss].\n\n"
    "Write a concise summary as a few tight Markdown paragraphs: what was "
    "discussed, any decisions reached, and any action items or follow-ups (note "
    "who owns each when it's clear). Use prose, not headers or bullet lists, "
    "unless a short list genuinely aids clarity. Write in the transcript's "
    "primary language. Output only the summary body -- no title, no preamble "
    'like "Here is the summary".'
)


class SummarizeError(RuntimeError):
    """Summarization cannot proceed (e.g. no transcript, or the provider fails)."""


class Summarizer(Protocol):
    """A pluggable summarization backend.

    Implement ``summarize`` and register the class in ``PROVIDERS`` to add a new
    backend -- nothing else in this module changes. A ``Protocol`` (rather than
    the plain functions used elsewhere) is the idiomatic way to express the
    "agnostic, swappable provider" contract the design calls for.
    """

    def summarize(self, transcript: str, *, prompt: str, model: str | None) -> str:
        """Return the summary body (Markdown prose) for ``transcript``."""
        ...


class ClaudeCodeSummarizer:
    """Summarize via the Claude Code CLI (``claude -p``), reading from stdin.

    Uses your existing Claude Code install + login -- no API key, no dependency.
    The transcript is piped via stdin so the instruction stays in ``-p`` and a
    long transcript never approaches an argv size limit.
    """

    def summarize(self, transcript: str, *, prompt: str, model: str | None) -> str:
        cmd = ["claude", "-p", prompt]
        if model:
            cmd += ["--model", model]
        try:
            proc = subprocess.run(
                cmd, input=transcript, capture_output=True, text=True, timeout=TIMEOUT
            )
        except FileNotFoundError as e:
            raise SummarizeError(
                "claude CLI not found - install Claude Code, or pick another backend "
                "via --provider / SCRIBE_SUMMARY_PROVIDER."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise SummarizeError(
                f"claude timed out after {TIMEOUT}s summarizing the transcript."
            ) from e
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise SummarizeError(f"claude exited {proc.returncode}: {detail}")
        body = proc.stdout.strip()
        if not body:
            raise SummarizeError("claude returned an empty summary.")
        return body


# Provider registry. Add a backend by implementing ``Summarizer`` and adding it
# here, e.g. "anthropic": AnthropicSummarizer or "ollama": OllamaSummarizer.
PROVIDERS: dict[str, Callable[[], Summarizer]] = {
    "claude-code": ClaudeCodeSummarizer,
}


def _resolve_provider(cli: str | None) -> str:
    """Provider precedence: CLI flag > ``SCRIBE_SUMMARY_PROVIDER`` env > default."""
    return cli or os.environ.get("SCRIBE_SUMMARY_PROVIDER") or DEFAULT_PROVIDER


def _resolve_model(cli: str | None) -> str | None:
    """Model precedence: CLI flag > ``SCRIBE_SUMMARY_MODEL`` env > None (provider default)."""
    return cli or os.environ.get("SCRIBE_SUMMARY_MODEL") or None


def _resolve_prompt(cli: str | None) -> str:
    """Prompt precedence: CLI flag (literal text) > ``SCRIBE_SUMMARY_PROMPT`` (file) > default."""
    if cli:
        return cli
    path = os.environ.get("SCRIBE_SUMMARY_PROMPT")
    if path:
        try:
            return Path(path).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            raise SummarizeError(
                f"Could not read SCRIBE_SUMMARY_PROMPT file {path!r}: {e}"
            ) from e
    return DEFAULT_PROMPT


def _get_provider(name: str) -> Summarizer:
    """Instantiate the named provider, or raise listing the available ones."""
    factory = PROVIDERS.get(name)
    if factory is None:
        avail = ", ".join(sorted(PROVIDERS))
        raise SummarizeError(f"Unknown summary provider {name!r}. Available: {avail}.")
    return factory()


def _latest_meeting() -> Path:
    """Most recent meeting dir under ``recordings_dir()`` (timestamps sort lexically)."""
    base = recordings_dir()
    dirs = [p for p in base.iterdir() if p.is_dir()] if base.is_dir() else []
    if not dirs:
        raise SummarizeError(
            f"No recordings found in {base} - run `scribe capture` first?"
        )
    return max(dirs, key=lambda p: p.name)


def _note_title(mdir: Path) -> str:
    """H1 title from the meeting dir's ``YYYY-MM-DD_HHMMSS`` name; raw name on miss."""
    try:
        dt = datetime.datetime.strptime(mdir.name, "%Y-%m-%d_%H%M%S")
    except ValueError:
        return f"# Meeting — {mdir.name}"
    return f"# Meeting — {dt:%Y-%m-%d %H:%M}"


def _render_note(mdir: Path, provider_label: str, body: str) -> str:
    """Wrap the summary body in an iwe-ready note: H1 title + metadata + prose."""
    today = datetime.date.today().isoformat()
    meta = f"_{today} · generated by scribe ({provider_label})_"
    return f"{_note_title(mdir)}\n\n{meta}\n\n{body}\n"


def summarize(
    meeting_dir: str | os.PathLike[str] | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
) -> Path:
    """Summarize a meeting's transcript into ``summary.md``; return its path.

    ``meeting_dir`` defaults to the most recent recording. Reads ``transcript.txt``
    (produced by `scribe transcribe`), sends only that text to the resolved
    provider (default ``claude-code``), and writes a titled, dated prose note.
    """
    mdir = Path(meeting_dir).expanduser() if meeting_dir else _latest_meeting()
    if not mdir.is_dir():
        raise SummarizeError(f"Meeting dir not found: {mdir}")

    transcript_path = mdir / TRANSCRIPT_TXT
    try:
        transcript = transcript_path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise SummarizeError(
            f"No {TRANSCRIPT_TXT} in {mdir} - run `scribe transcribe` first."
        ) from e
    if not transcript.strip():
        raise SummarizeError(f"{transcript_path} is empty - nothing to summarize.")

    provider_name = _resolve_provider(provider)
    model_name = _resolve_model(model)
    prompt_text = _resolve_prompt(prompt)
    summarizer = _get_provider(provider_name)

    label = provider_name + (f"/{model_name}" if model_name else "")
    print(f"Summarizing {transcript_path.name} via {label}...", file=sys.stderr)
    body = summarizer.summarize(transcript, prompt=prompt_text, model=model_name)

    out = mdir / SUMMARY_MD
    out.write_text(_render_note(mdir, label, body), encoding="utf-8")
    print(f"\nSummary -> {out}", file=sys.stderr)
    return out
