"""Real-audio end-to-end test for scribe.transcribe.

Runs the *actual* faster-whisper pipeline on a tiny committed clip (~12s, two
speakers, Portuguese) sliced from a real meeting -- so it exercises model load,
per-track inference, language auto-detection, and the timestamp merge for real.

It's slow and needs the faster-whisper dependency + a one-time model download,
so it is OPT-IN. The default suite (`python -m unittest` / `mise run test`)
skips it. Run it explicitly:

    mise run test-e2e
    # or: SCRIBE_E2E=1 python -m unittest tests.test_transcribe_e2e -v

It uses the `tiny` model on purpose: this checks the *plumbing*, not accuracy,
so the smallest/fastest model is enough.
"""
import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from scribe import transcribe as tr

FIXTURE = Path(__file__).parent / "fixtures" / "meeting"
_HAS_FASTER_WHISPER = importlib.util.find_spec("faster_whisper") is not None
_E2E_ENABLED = os.environ.get("SCRIBE_E2E") == "1"
_LINE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] (You|Them): .+")


@unittest.skipUnless(_E2E_ENABLED, "set SCRIBE_E2E=1 to run the real-audio e2e")
@unittest.skipUnless(_HAS_FASTER_WHISPER, "faster-whisper is not installed")
class TranscribeE2ETests(unittest.TestCase):
    def setUp(self):
        # transcribe() writes outputs into the meeting dir; copy the fixture to a
        # tmp dir so the committed clip stays pristine.
        self._tmp = tempfile.TemporaryDirectory()
        self.meeting = Path(self._tmp.name) / "meeting"
        shutil.copytree(FIXTURE, self.meeting)

    def tearDown(self):
        self._tmp.cleanup()

    def test_real_audio_produces_merged_two_speaker_transcript(self):
        out = tr.transcribe(self.meeting, model="tiny")
        self.assertEqual(out, self.meeting / tr.TRANSCRIPT_TXT)

        # per-track JSON: parses, has a detected language and at least one segment
        for name in ("mic.json", "remote.json"):
            data = json.loads((self.meeting / name).read_text(encoding="utf-8"))
            self.assertTrue(data["language"], f"{name} has no detected language")
            self.assertGreater(len(data["segments"]), 0, f"{name} has no segments")

        # merged transcript: non-empty, both speakers present, time-ordered
        merged = json.loads(
            (self.meeting / tr.TRANSCRIPT_JSON).read_text(encoding="utf-8")
        )["segments"]
        self.assertEqual({s["label"] for s in merged}, {"You", "Them"})
        starts = [s["start"] for s in merged]
        self.assertEqual(starts, sorted(starts), "segments not ordered by start time")

        # every transcript line matches `[hh:mm:ss] <Label>: <text>`
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertRegex(line, _LINE)


if __name__ == "__main__":
    unittest.main()
