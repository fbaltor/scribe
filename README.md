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
| Transcribe | `scribe transcribe` | 🟡 planned (whisper.cpp `small.en`) |
| Summarize | `scribe summarize` | 🟡 planned (local 3B LLM or Claude) |

## Capture

```bash
python -m scribe capture            # -> ~/meetings/<timestamp>/{remote,mic}.wav
python -m scribe capture ~/mtg/foo  # custom output dir
# Ctrl-C to stop.
```

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

## Roadmap → "our own bot"

1. **transcribe** — `whisper.cpp` (`small.en` is the CPU-only sweet spot,
   ~real-time on a 15W chip). Per-track, then merge into a labeled transcript.
2. **summarize** — feed the transcript to a local 3B LLM (Ollama) for full
   privacy, or to Claude for a faster/better summary (only de-identifiable text
   leaves, never audio).
3. **glue** — calendar trigger, auto start/stop on meeting-app audio, notes
   written to the knowledge base.

## Hardware reality (this box: i7-8565U, no GPU)

All inference is CPU. `small.en` keeps pace for transcription; local summaries
with a 3B/7B model run in minutes (batch, after the meeting). Hybrid path
(local transcript → cloud summary) is the pragmatic default.

## Requirements

- Linux + PipeWire (`pw-record`, `pw-dump` — ship with PipeWire).
- Python 3.9+ (stdlib only for capture).
- Later: `whisper-cpp` (in nixpkgs), optionally Ollama.
