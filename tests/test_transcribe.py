"""Behavior tests for scribe.transcribe.

faster-whisper is never imported for real: the module imports it lazily inside
``_load_model``, so the pure logic (merge/format/resolution) is tested directly,
and orchestration is tested with ``_load_model`` patched to a fake model (or with
a fake ``faster_whisper`` module injected into ``sys.modules``). No network, no
weights, no audio.
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scribe import transcribe as tr


def _seg(start, end, text):
    """A stand-in for a faster-whisper Segment (attribute access)."""
    return types.SimpleNamespace(start=start, end=end, text=text)


class _FakeModel:
    """Stand-in for a loaded WhisperModel; maps wav basename -> (segments, lang)."""

    def __init__(self, per_file):
        self.per_file = per_file
        self.calls = []  # (basename, language) per transcribe() call

    def transcribe(self, audio, beam_size=None, vad_filter=None, language=None):
        name = Path(audio).name
        self.calls.append((name, language))
        segments, lang = self.per_file[name]
        return iter(segments), types.SimpleNamespace(language=lang)


class MergeTests(unittest.TestCase):
    def test_interleaves_by_start(self):
        merged = tr._merge([
            ("You", [{"start": 0.0, "end": 2.0, "text": "first"}]),
            ("Them", [{"start": 1.0, "end": 3.0, "text": "second"}]),
        ])
        self.assertEqual([m["text"] for m in merged], ["first", "second"])
        self.assertEqual([m["label"] for m in merged], ["You", "Them"])

    def test_equal_start_tiebreak_by_label(self):
        merged = tr._merge([
            ("You", [{"start": 1.0, "end": 2.0, "text": "you"}]),
            ("Them", [{"start": 1.0, "end": 2.0, "text": "them"}]),
        ])
        # "Them" < "You" lexically -> deterministic order
        self.assertEqual([m["label"] for m in merged], ["Them", "You"])

    def test_single_track_passthrough(self):
        merged = tr._merge([
            ("You", [{"start": 2.0, "end": 3.0, "text": "b"},
                     {"start": 0.0, "end": 1.0, "text": "a"}]),
            ("Them", []),
        ])
        self.assertEqual([m["text"] for m in merged], ["a", "b"])


class FmtTsTests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(tr._fmt_ts(0), "00:00:00")

    def test_minutes_seconds(self):
        self.assertEqual(tr._fmt_ts(65), "00:01:05")

    def test_hours(self):
        self.assertEqual(tr._fmt_ts(3725), "01:02:05")

    def test_truncates_fractional_seconds(self):
        self.assertEqual(tr._fmt_ts(5.9), "00:00:05")


class FormatTranscriptTests(unittest.TestCase):
    def test_line_format(self):
        text = tr._format_transcript([
            {"start": 3.0, "end": 4.0, "label": "You", "text": "hi"},
            {"start": 5.0, "end": 6.0, "label": "Them", "text": "yo"},
        ])
        self.assertEqual(text, "[00:00:03] You: hi\n[00:00:05] Them: yo\n")

    def test_empty_is_empty_string(self):
        self.assertEqual(tr._format_transcript([]), "")


class ResolveModelTests(unittest.TestCase):
    def test_cli_wins(self):
        with mock.patch.dict("os.environ", {"SCRIBE_WHISPER_MODEL": "medium"}):
            self.assertEqual(tr._resolve_model("base"), "base")

    def test_env_used_when_no_cli(self):
        with mock.patch.dict("os.environ", {"SCRIBE_WHISPER_MODEL": "medium"}):
            self.assertEqual(tr._resolve_model(None), "medium")

    def test_default_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(tr._resolve_model(None), tr.DEFAULT_MODEL)


class ResolveLanguageTests(unittest.TestCase):
    def test_cli_wins(self):
        with mock.patch.dict("os.environ", {"SCRIBE_WHISPER_LANG": "en"}):
            self.assertEqual(tr._resolve_language("pt"), "pt")

    def test_env_used_when_no_cli(self):
        with mock.patch.dict("os.environ", {"SCRIBE_WHISPER_LANG": "en"}):
            self.assertEqual(tr._resolve_language(None), "en")

    def test_none_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(tr._resolve_language(None))


class ModelsDirTests(unittest.TestCase):
    def test_env_override_is_expanded(self):
        with mock.patch.dict("os.environ", {"SCRIBE_MODELS_DIR": "~/m"}):
            self.assertEqual(tr.models_dir(), Path.home() / "m")

    def test_repo_fallback_when_env_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            d = tr.models_dir()
        self.assertEqual(d.name, "models")
        self.assertEqual(d.parent, Path(tr.__file__).resolve().parent.parent)


class LatestMeetingTests(unittest.TestCase):
    def test_picks_newest_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "2026-01-01_000000").mkdir()
            (Path(tmp) / "2026-02-02_120000").mkdir()
            with mock.patch.dict("os.environ", {"SCRIBE_RECORDINGS_DIR": tmp}):
                self.assertEqual(tr._latest_meeting().name, "2026-02-02_120000")

    def test_raises_when_none(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict("os.environ", {"SCRIBE_RECORDINGS_DIR": tmp}), \
             self.assertRaises(tr.TranscribeError):
            tr._latest_meeting()


class LoadModelTests(unittest.TestCase):
    def test_import_error_raises_transcribe_error(self):
        # sys.modules[name] = None makes `import faster_whisper` raise ImportError
        with mock.patch.dict(sys.modules, {"faster_whisper": None}), \
             self.assertRaises(tr.TranscribeError) as cm:
            tr._load_model("small")
        self.assertIn("mise run dev", str(cm.exception))

    def test_constructs_whisper_model_with_expected_args(self):
        wm = mock.MagicMock(return_value="MODEL")
        fake_mod = types.SimpleNamespace(WhisperModel=wm)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(sys.modules, {"faster_whisper": fake_mod}), \
             mock.patch.dict("os.environ", {"SCRIBE_MODELS_DIR": tmp}):
            result = tr._load_model("small")
        self.assertEqual(result, "MODEL")
        wm.assert_called_once_with(
            "small", device=tr.DEVICE, compute_type=tr.COMPUTE_TYPE, download_root=tmp
        )

    def test_load_failure_raises_transcribe_error(self):
        wm = mock.MagicMock(side_effect=RuntimeError("boom"))
        fake_mod = types.SimpleNamespace(WhisperModel=wm)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(sys.modules, {"faster_whisper": fake_mod}), \
             mock.patch.dict("os.environ", {"SCRIBE_MODELS_DIR": tmp}), \
             self.assertRaises(tr.TranscribeError):
            tr._load_model("small")


def _meeting_with_tracks(tmp):
    mdir = Path(tmp) / "meeting"
    mdir.mkdir()
    (mdir / "mic.wav").touch()
    (mdir / "remote.wav").touch()
    return mdir


class TranscribeOrchestrationTests(unittest.TestCase):
    def test_missing_mic_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "m"
            mdir.mkdir()
            (mdir / "remote.wav").touch()
            with self.assertRaises(tr.TranscribeError) as cm:
                tr.transcribe(mdir)
            self.assertIn("mic.wav", str(cm.exception))

    def test_missing_remote_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "m"
            mdir.mkdir()
            (mdir / "mic.wav").touch()
            with self.assertRaises(tr.TranscribeError) as cm:
                tr.transcribe(mdir)
            self.assertIn("remote.wav", str(cm.exception))

    def test_writes_outputs_and_returns_transcript_path(self):
        fake = _FakeModel({
            "mic.wav": ([_seg(0.0, 2.0, "hi there ")], "en"),
            "remote.wav": ([_seg(1.0, 3.0, "hello back")], "en"),
        })
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _meeting_with_tracks(tmp)
            with mock.patch.object(tr, "_load_model", return_value=fake):
                out = tr.transcribe(mdir)

            self.assertEqual(out, mdir / tr.TRANSCRIPT_TXT)
            for name in ("mic.json", "remote.json",
                         tr.TRANSCRIPT_TXT, tr.TRANSCRIPT_JSON):
                self.assertTrue((mdir / name).exists(), name)

            text = out.read_text(encoding="utf-8")
            self.assertIn("You: hi there", text)        # text is stripped
            self.assertIn("Them: hello back", text)
            self.assertLess(text.index("You:"), text.index("Them:"))  # 0.0 before 1.0

            mic = json.loads((mdir / "mic.json").read_text(encoding="utf-8"))
            self.assertEqual(mic["language"], "en")
            self.assertEqual(mic["segments"][0]["text"], "hi there")

    def test_language_is_passed_through(self):
        fake = _FakeModel({
            "mic.wav": ([_seg(0.0, 1.0, "a")], "pt"),
            "remote.wav": ([_seg(0.0, 1.0, "b")], "pt"),
        })
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _meeting_with_tracks(tmp)
            with mock.patch.object(tr, "_load_model", return_value=fake), \
                 mock.patch.dict("os.environ", {}, clear=True):
                tr.transcribe(mdir, language="pt")
        self.assertEqual([lang for _, lang in fake.calls], ["pt", "pt"])

    def test_defaults_to_latest_meeting(self):
        fake = _FakeModel({
            "mic.wav": ([_seg(0.0, 1.0, "a")], "en"),
            "remote.wav": ([_seg(0.0, 1.0, "b")], "en"),
        })
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _meeting_with_tracks(tmp)
            with mock.patch.object(tr, "_load_model", return_value=fake), \
                 mock.patch.object(tr, "_latest_meeting", return_value=mdir):
                out = tr.transcribe(None)
        self.assertEqual(out, mdir / tr.TRANSCRIPT_TXT)


if __name__ == "__main__":
    unittest.main()
