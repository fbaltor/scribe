"""Transcription for meetings.

Turns a captured meeting dir (``mic.wav`` + ``remote.wav``) into a single
chronological, speaker-labeled transcript using faster-whisper (CTranslate2).

The two tracks already separate the speakers, so each is transcribed on its own
(``mic.wav`` -> "You", ``remote.wav`` -> "Them") and the segments are merged by
start time -- no diarization engine needed. Writes:

  mic.json / remote.json   per-track segments + detected language (re-mergeable)
  transcript.txt           merged, labeled, timestamped (consumed by `summarize`)
  transcript.json          structured mirror of transcript.txt

The model is multilingual by default (``small``); the language is auto-detected
per track, so a mixed-language call is handled correctly. CPU-only, int8.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .capture import recordings_dir

# --- transcription parameters ----------------------------------------------
DEFAULT_MODEL = "small"   # multilingual; language auto-detected. small.en = English-only.
DEVICE = "cpu"
COMPUTE_TYPE = "int8"     # CPU fast path
VAD_FILTER = True         # Silero VAD: skip silence, fewer hallucinations
BEAM_SIZE = 5

# meeting-dir filename -> speaker label, in transcription order
TRACKS = {"mic.wav": "You", "remote.wav": "Them"}

TRANSCRIPT_TXT = "transcript.txt"
TRANSCRIPT_JSON = "transcript.json"


class TranscribeError(RuntimeError):
    """Transcription cannot proceed (e.g. a track is missing or the model fails)."""


def models_dir() -> Path:
    """Where CT2 weights are cached: env override, else ``<repo>/models``.

    ``SCRIBE_MODELS_DIR`` lets you point at a shared cache; the fallback keeps
    weights inside the repo (gitignored), mirroring ``recordings_dir()``.
    """
    env = os.environ.get("SCRIBE_MODELS_DIR")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent / "models"


def _resolve_model(cli: str | None) -> str:
    """Model name precedence: CLI flag > ``SCRIBE_WHISPER_MODEL`` env > default."""
    return cli or os.environ.get("SCRIBE_WHISPER_MODEL") or DEFAULT_MODEL


def _resolve_language(cli: str | None) -> str | None:
    """Language precedence: CLI flag > ``SCRIBE_WHISPER_LANG`` env > None (auto)."""
    return cli or os.environ.get("SCRIBE_WHISPER_LANG") or None


def _latest_meeting() -> Path:
    """Most recent meeting dir under ``recordings_dir()`` (timestamps sort lexically)."""
    base = recordings_dir()
    dirs = [p for p in base.iterdir() if p.is_dir()] if base.is_dir() else []
    if not dirs:
        raise TranscribeError(
            f"No recordings found in {base} - run `scribe capture` first?"
        )
    return max(dirs, key=lambda p: p.name)


def _load_model(model: str):
    """Construct a faster-whisper ``WhisperModel`` (CPU, int8), or raise TranscribeError."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise TranscribeError(
            "faster-whisper is not installed - run `mise run dev` "
            "(installs it into the project .venv via uv)."
        ) from e
    cache = models_dir()
    cache.mkdir(parents=True, exist_ok=True)
    try:
        return WhisperModel(
            model, device=DEVICE, compute_type=COMPUTE_TYPE, download_root=str(cache)
        )
    except Exception as e:  # noqa: BLE001 - surface any load/download failure as TranscribeError
        raise TranscribeError(f"Could not load model {model!r}: {e}") from e


def _transcribe_track(model, wav: Path, language: str | None) -> tuple[list[dict], str]:
    """Run the model on one WAV; return ``(segments, detected_language)``.

    ``segments`` is a list of ``{start, end, text}`` dicts. This is the only place
    that touches faster-whisper objects -- everything downstream is plain dicts.
    """
    segments, info = model.transcribe(
        str(wav), beam_size=BEAM_SIZE, vad_filter=VAD_FILTER, language=language
    )
    segs = [
        {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
        for s in segments
    ]
    return segs, info.language


def _merge(tracks: list[tuple[str, list[dict]]]) -> list[dict]:
    """Flatten labeled per-track segments into one list ordered by start time.

    Equal start times are tie-broken by label so the output is deterministic.
    """
    items: list[dict] = []
    for label, segs in tracks:
        for s in segs:
            items.append(
                {"start": s["start"], "end": s["end"], "label": label,
                 "text": s["text"].strip()}
            )
    items.sort(key=lambda x: (x["start"], x["label"]))
    return items


def _fmt_ts(seconds: float) -> str:
    """Seconds -> ``hh:mm:ss``."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _format_transcript(merged: list[dict]) -> str:
    """Render merged segments as ``[hh:mm:ss] Label: text`` lines."""
    lines = [f"[{_fmt_ts(seg['start'])}] {seg['label']}: {seg['text']}" for seg in merged]
    return "\n".join(lines) + ("\n" if lines else "")


def _write_json(path: Path, language: str, segments: list[dict]) -> None:
    payload = {"language": language, "segments": segments}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def transcribe(
    meeting_dir: str | os.PathLike[str] | None = None,
    *,
    model: str | None = None,
    language: str | None = None,
) -> Path:
    """Transcribe a meeting dir into a labeled transcript; return its path.

    ``meeting_dir`` defaults to the most recent recording. Each of ``mic.wav`` and
    ``remote.wav`` is transcribed (both must exist), per-track JSON is written,
    then the segments are merged into ``transcript.txt`` (+ ``transcript.json``).
    """
    mdir = Path(meeting_dir).expanduser() if meeting_dir else _latest_meeting()
    if not mdir.is_dir():
        raise TranscribeError(f"Meeting dir not found: {mdir}")

    wavs: dict[str, Path] = {}
    for fname in TRACKS:
        wav = mdir / fname
        if not wav.exists():
            raise TranscribeError(
                f"Missing {fname} in {mdir} - is this a `scribe capture` dir?"
            )
        wavs[fname] = wav

    model_name = _resolve_model(model)
    lang = _resolve_language(language)
    print(f"Loading model {model_name!r} (device={DEVICE}, compute={COMPUTE_TYPE})...",
          file=sys.stderr)
    m = _load_model(model_name)

    tracks: list[tuple[str, list[dict]]] = []
    for fname, label in TRACKS.items():
        print(f"Transcribing {fname} ({label})...", file=sys.stderr)
        segs, detected = _transcribe_track(m, wavs[fname], lang)
        _write_json(mdir / f"{Path(fname).stem}.json", detected, segs)
        print(f"  -> {len(segs)} segments, language={detected}", file=sys.stderr)
        tracks.append((label, segs))

    merged = _merge(tracks)
    out = mdir / TRANSCRIPT_TXT
    out.write_text(_format_transcript(merged), encoding="utf-8")
    (mdir / TRANSCRIPT_JSON).write_text(
        json.dumps({"segments": merged}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nTranscript -> {out}", file=sys.stderr)
    return out
