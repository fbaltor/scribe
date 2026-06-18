"""scribe CLI.

  scribe capture [out_dir]   record meeting audio (works today)
  scribe transcribe [dir]    faster-whisper transcription (per-track + merged)
  scribe summarize  [dir]    LLM summary of the transcript (default: Claude Code CLI)
"""
from __future__ import annotations

import argparse
import sys

from . import capture as capture_mod
from . import summarize as summarize_mod
from . import transcribe as transcribe_mod


def cmd_capture(args: argparse.Namespace) -> None:
    try:
        capture_mod.capture(args.out)
    except capture_mod.CaptureError as e:
        sys.exit(str(e))


def cmd_transcribe(args: argparse.Namespace) -> None:
    try:
        out = transcribe_mod.transcribe(args.dir, model=args.model, language=args.language)
        print(out)
    except transcribe_mod.TranscribeError as e:
        sys.exit(str(e))


def cmd_summarize(args: argparse.Namespace) -> None:
    try:
        out = summarize_mod.summarize(
            args.dir, provider=args.provider, model=args.model, prompt=args.prompt
        )
        print(out)
    except summarize_mod.SummarizeError as e:
        sys.exit(str(e))


def main() -> None:
    p = argparse.ArgumentParser(
        prog="scribe",
        description="Local, bot-free meeting note-taker. No bot joins the call; "
                    "audio is captured on-device for later transcription + summary.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="record meeting audio (follows default device live)")
    c.add_argument("out", nargs="?", default=None,
                   help="output dir (default: recordings/<timestamp> inside the repo)")
    c.set_defaults(func=cmd_capture)

    t = sub.add_parser("transcribe",
                       help="transcribe a meeting (faster-whisper, per-track + merged)")
    t.add_argument("dir", nargs="?", default=None,
                   help="meeting dir with mic.wav/remote.wav (default: latest recording)")
    t.add_argument("--model", default=None,
                   help="whisper model (default: small; env SCRIBE_WHISPER_MODEL)")
    t.add_argument("--language", default=None,
                   help="force language e.g. pt/en (default: auto-detect; env SCRIBE_WHISPER_LANG)")
    t.set_defaults(func=cmd_transcribe)

    s = sub.add_parser("summarize",
                       help="summarize a transcript into summary.md (default: Claude Code CLI)")
    s.add_argument("dir", nargs="?", default=None,
                   help="meeting dir with transcript.txt (default: latest recording)")
    s.add_argument("--provider", default=None,
                   help="summary backend (default: claude-code; env SCRIBE_SUMMARY_PROVIDER)")
    s.add_argument("--model", default=None,
                   help="provider model (default: provider's own; env SCRIBE_SUMMARY_MODEL)")
    s.add_argument("--prompt", default=None,
                   help="override prompt text (env SCRIBE_SUMMARY_PROMPT = path to a prompt file)")
    s.set_defaults(func=cmd_summarize)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
