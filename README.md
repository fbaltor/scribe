# scribe

Local, **bot-free** meeting note-taker. No bot joins the call — audio is
captured on-device from your own machine, then transcribed and summarized
locally (or with a cloud LLM by choice). Privacy-first; the audio never leaves
the box unless you decide to send the *text* transcript out.

Built for a CPU-only Linux laptop (PipeWire), but the design is portable.

## Status

| Stage | Command | State |
|------|---------|-------|
| Capture | `scribe capture` | ✅ works |
| Transcribe | `scribe transcribe` | ✅ works (faster-whisper, multilingual `small`) |
| Summarize | `scribe summarize` | 🟡 planned (local 3B LLM or Claude) |

## Setup (mise)

[mise](https://mise.jdx.dev) provides the env + task runner. It uses the system
Python (3.13). Once:

```bash
cd ~/scribe
mise trust
```

> On NixOS, runtimes are not pinned via mise (its CPython install fails here);
> for a reproducible pin, add a nix devshell. mise handles tasks + env + any
> cleanly-distributed CLI tools added later.

Then use the tasks:

```bash
mise run dev            # install deps into .venv (needed for transcribe)
mise run capture        # record (Ctrl-C to stop)
mise run transcribe     # transcribe the latest recording
mise run summarize      # [planned]
```

## Capture

```bash
mise run capture                    # -> recordings/<timestamp>/{remote,mic}.wav
python -m scribe capture            # same (plain, no mise)
python -m scribe capture /some/dir  # custom output dir
# Ctrl-C to stop.
```

Recordings are written **inside the repo** at `recordings/<timestamp>/` (path
from `SCRIBE_RECORDINGS_DIR`, set by mise) and are gitignored — never
committed.

Produces **two** 16kHz mono WAVs:

- `remote.wav` — monitor of the default output sink = everyone else.
- `mic.wav` — default input source = you.

Two separate tracks give free speaker separation downstream (you vs them).

### Follows the default device live

The capture streams have no fixed target — they follow the **current default**
sink/source. Connect or disconnect Bluetooth headphones *mid-meeting* and the
capture moves to the new device automatically, in the same continuous file, no
restart. (Implemented via PipeWire `stream.capture.sink=true` for the monitor
and no-target capture for the mic; the session manager re-links on default
changes.)

Caveats: a sub-second glitch is possible at the moment of a switch; while the
default is a silent device that span records silence (correct — it follows the
default).

## Transcribe

```bash
mise run dev                              # once: install deps into .venv
mise run transcribe                       # latest recording
python -m scribe transcribe recordings/<ts>   # a specific meeting
python -m scribe transcribe --model small.en  # English-only (faster/better for EN)
python -m scribe transcribe --language pt      # force a language (default: auto)
```

Uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2),
CPU-only, `int8`. The default model is multilingual **`small`** — the language is
**auto-detected per track**, so a mixed-language call works. Weights download
once to `models/` (gitignored); override with `SCRIBE_WHISPER_MODEL` /
`SCRIBE_WHISPER_LANG` or the flags above.

Because `capture` already produces **two tracks**, speakers are separated for
free — each track is transcribed on its own and the segments are merged by
timestamp. No diarization model needed. Writes into the meeting dir:

- `mic.json` / `remote.json` — per-track segments + detected language (re-mergeable).
- `transcript.txt` — merged, labeled, timestamped (`[hh:mm:ss] You: …` / `Them: …`).
- `transcript.json` — structured mirror for the `summarize` stage.

Model trade-off (CPU): `base` (fast, rougher) · `small` (default, sweet spot) ·
`medium` (slower, better). `.en` variants (`small.en`) are English-only and a
bit faster/sharper on English.

## Roadmap → "our own bot"

1. **summarize** — feed the transcript to a local 3B LLM (Ollama) for full
   privacy, or to Claude for a faster/better summary (only de-identifiable text
   leaves, never audio).
2. **glue** — calendar trigger, auto start/stop on meeting-app audio, notes
   written to the knowledge base.

## Hardware reality (this box: i7-8565U, no GPU)

All inference is CPU. `small` keeps roughly real-time pace for transcription
(batch, after the meeting); local summaries with a 3B/7B model run in minutes.
Hybrid path (local transcript → cloud summary) is the pragmatic default.

## Requirements

- Linux + PipeWire (`pw-record`, `pw-dump` — ship with PipeWire).
- [mise](https://mise.jdx.dev) for tasks + env (uses system Python 3.13).
- `faster-whisper` for transcribe (installed via `mise run dev`); optionally Ollama later.
