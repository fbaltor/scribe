"""scribe CLI.

  scribe capture [out_dir]   record meeting audio (works today)
  scribe transcribe <dir>    [planned] Whisper transcription
  scribe summarize  <dir>    [planned] LLM summary
"""
import argparse
import sys

from . import capture as capture_mod


def cmd_capture(args):
    capture_mod.capture(args.out)


def cmd_transcribe(args):
    sys.exit(
        "transcribe: not implemented yet.\n"
        "Roadmap: whisper.cpp small.en (CPU-only ~real-time) over remote.wav + mic.wav.\n"
        "Manual for now:  nix-shell -p whisper-cpp --run "
        "'whisper-cli -m <model> -f remote.wav -otxt'"
    )


def cmd_summarize(args):
    sys.exit(
        "summarize: not implemented yet.\n"
        "Roadmap: feed the merged transcript to a local 3B LLM (Ollama) or to Claude.\n"
        "On this CPU-only box, hybrid (local transcript -> cloud summary) is the fast path."
    )


def main():
    p = argparse.ArgumentParser(
        prog="scribe",
        description="Local, bot-free meeting note-taker. No bot joins the call; "
                    "audio is captured on-device for later transcription + summary.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="record meeting audio (follows default device live)")
    c.add_argument("out", nargs="?", default=None,
                   help="output dir (default: ~/meetings/<timestamp>)")
    c.set_defaults(func=cmd_capture)

    t = sub.add_parser("transcribe", help="[planned] Whisper transcription")
    t.add_argument("dir", nargs="?", help="meeting dir with remote.wav/mic.wav")
    t.set_defaults(func=cmd_transcribe)

    s = sub.add_parser("summarize", help="[planned] LLM summary")
    s.add_argument("dir", nargs="?", help="meeting dir with transcript")
    s.set_defaults(func=cmd_summarize)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
