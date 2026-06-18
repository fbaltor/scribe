"""Behavior tests for scribe.summarize.

No network, no real subprocess: ``scribe.summarize.subprocess.run`` is patched in
every test that would otherwise shell out to the ``claude`` CLI. Meeting dirs live
in ``tempfile.TemporaryDirectory``. Env-var precedence tests use
``patch.dict(os.environ, ..., clear=False)`` so they don't leak between tests.
"""
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scribe import summarize as sm


def _run_result(returncode=0, stdout="", stderr=""):
    """Stand-in for a completed ``subprocess.run`` (attribute access)."""
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _meeting_with_transcript(tmp, name="2026-06-17_161401", text="hello world"):
    mdir = Path(tmp) / name
    mdir.mkdir()
    (mdir / sm.TRANSCRIPT_TXT).write_text(text, encoding="utf-8")
    return mdir


class ResolveProviderTests(unittest.TestCase):
    def test_cli_flag_wins_over_env(self):
        with mock.patch.dict(os.environ, {"SCRIBE_SUMMARY_PROVIDER": "envp"}, clear=False):
            self.assertEqual(sm._resolve_provider("flagp"), "flagp")

    def test_env_used_when_no_cli(self):
        with mock.patch.dict(os.environ, {"SCRIBE_SUMMARY_PROVIDER": "envp"}, clear=False):
            self.assertEqual(sm._resolve_provider(None), "envp")

    def test_default_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sm._resolve_provider(None), "claude-code")
        self.assertEqual(sm.DEFAULT_PROVIDER, "claude-code")


class ResolveModelTests(unittest.TestCase):
    def test_cli_flag_wins_over_env(self):
        with mock.patch.dict(os.environ, {"SCRIBE_SUMMARY_MODEL": "envm"}, clear=False):
            self.assertEqual(sm._resolve_model("flagm"), "flagm")

    def test_env_used_when_no_cli(self):
        with mock.patch.dict(os.environ, {"SCRIBE_SUMMARY_MODEL": "envm"}, clear=False):
            self.assertEqual(sm._resolve_model(None), "envm")

    def test_none_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(sm._resolve_model(None))


class ResolvePromptTests(unittest.TestCase):
    def test_cli_literal_text_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            pf = Path(tmp) / "prompt.txt"
            pf.write_text("from file", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SCRIBE_SUMMARY_PROMPT": str(pf)}, clear=False):
                self.assertEqual(sm._resolve_prompt("literal flag"), "literal flag")

    def test_env_file_contents_used_when_no_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            pf = Path(tmp) / "prompt.txt"
            pf.write_text("summarize concisely", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SCRIBE_SUMMARY_PROMPT": str(pf)}, clear=False):
                self.assertEqual(sm._resolve_prompt(None), "summarize concisely")

    def test_default_prompt_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sm._resolve_prompt(None), sm.DEFAULT_PROMPT)
        self.assertIsInstance(sm.DEFAULT_PROMPT, str)
        self.assertTrue(sm.DEFAULT_PROMPT)


class GetProviderTests(unittest.TestCase):
    def test_known_provider_returns_instance(self):
        prov = sm._get_provider("claude-code")
        self.assertIsNotNone(prov)
        self.assertIn("claude-code", sm.PROVIDERS)

    def test_unknown_provider_lists_available(self):
        with self.assertRaises(sm.SummarizeError) as cm:
            sm._get_provider("nope")
        msg = str(cm.exception)
        for name in sm.PROVIDERS:
            self.assertIn(name, msg)


class ClaudeCodeSummarizerTests(unittest.TestCase):
    def test_success_with_model_builds_argv_and_returns_stripped(self):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return _run_result(returncode=0, stdout="  the body  \n", stderr="")

        with mock.patch.object(sm.subprocess, "run", side_effect=fake_run):
            out = sm.ClaudeCodeSummarizer().summarize(
                "the transcript", prompt="PROMPT", model="x"
            )

        self.assertEqual(captured["argv"], ["claude", "-p", "PROMPT", "--model", "x"])
        self.assertEqual(captured["kwargs"]["input"], "the transcript")
        self.assertEqual(out, "the body")

    def test_success_without_model_omits_model_flag(self):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return _run_result(returncode=0, stdout="body", stderr="")

        with mock.patch.object(sm.subprocess, "run", side_effect=fake_run):
            sm.ClaudeCodeSummarizer().summarize("t", prompt="PROMPT", model=None)

        self.assertEqual(captured["argv"], ["claude", "-p", "PROMPT"])
        self.assertNotIn("--model", captured["argv"])

    def test_claude_missing_raises(self):
        with mock.patch.object(
            sm.subprocess, "run", side_effect=FileNotFoundError("claude")
        ), self.assertRaises(sm.SummarizeError) as cm:
            sm.ClaudeCodeSummarizer().summarize("t", prompt="P", model=None)
        self.assertIn("claude", str(cm.exception))

    def test_nonzero_exit_surfaces_stderr(self):
        with mock.patch.object(
            sm.subprocess, "run",
            return_value=_run_result(returncode=2, stdout="", stderr="boom"),
        ), self.assertRaises(sm.SummarizeError) as cm:
            sm.ClaudeCodeSummarizer().summarize("t", prompt="P", model=None)
        self.assertIn("boom", str(cm.exception))

    def test_timeout_raises(self):
        import subprocess

        with mock.patch.object(
            sm.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=1),
        ), self.assertRaises(sm.SummarizeError):
            sm.ClaudeCodeSummarizer().summarize("t", prompt="P", model=None)

    def test_empty_output_raises(self):
        with mock.patch.object(
            sm.subprocess, "run",
            return_value=_run_result(returncode=0, stdout="   ", stderr=""),
        ), self.assertRaises(sm.SummarizeError):
            sm.ClaudeCodeSummarizer().summarize("t", prompt="P", model=None)


class SummarizeOrchestrationTests(unittest.TestCase):
    def test_missing_transcript_raises_mentions_transcribe(self):
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "2026-06-17_161401"
            mdir.mkdir()
            with mock.patch.object(
                sm.subprocess, "run", return_value=_run_result(stdout="b")
            ), self.assertRaises(sm.SummarizeError) as cm:
                sm.summarize(mdir)
        self.assertIn("transcribe", str(cm.exception).lower())

    def test_unknown_provider_lists_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _meeting_with_transcript(tmp)
            # Patch run defensively; it should not be reached.
            with mock.patch.object(
                sm.subprocess, "run", return_value=_run_result(stdout="b")
            ), self.assertRaises(sm.SummarizeError) as cm:
                sm.summarize(mdir, provider="nope")
        msg = str(cm.exception)
        for name in sm.PROVIDERS:
            self.assertIn(name, msg)

    def test_writes_summary_note_with_h1_and_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _meeting_with_transcript(tmp)
            with mock.patch.object(
                sm.subprocess, "run",
                return_value=_run_result(returncode=0, stdout="THE BODY", stderr=""),
            ):
                out = sm.summarize(mdir)

            self.assertEqual(out, mdir / sm.SUMMARY_MD)
            self.assertTrue(out.exists())
            text = out.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Meeting"), text[:40])
            self.assertIn("THE BODY", text)


class LatestMeetingTests(unittest.TestCase):
    def test_picks_greatest_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "2026-01-01_000000").mkdir()
            (Path(tmp) / "2026-02-02_120000").mkdir()
            with mock.patch.dict(os.environ, {"SCRIBE_RECORDINGS_DIR": tmp}, clear=False):
                self.assertEqual(sm._latest_meeting().name, "2026-02-02_120000")

    def test_raises_when_none(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"SCRIBE_RECORDINGS_DIR": tmp}, clear=False), \
             self.assertRaises(sm.SummarizeError):
            sm._latest_meeting()

    def test_summarize_none_uses_latest_meeting(self):
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _meeting_with_transcript(tmp)
            with mock.patch.dict(os.environ, {"SCRIBE_RECORDINGS_DIR": tmp}, clear=False), \
                 mock.patch.object(
                     sm.subprocess, "run",
                     return_value=_run_result(returncode=0, stdout="BODY", stderr=""),
                 ):
                out = sm.summarize(None)
            self.assertEqual(out, mdir / sm.SUMMARY_MD)
            self.assertTrue(out.exists())


class NoteTitleTests(unittest.TestCase):
    def test_valid_timestamp_dir(self):
        title = sm._note_title(Path("/x/2026-06-17_161401"))
        self.assertEqual(title, "# Meeting — 2026-06-17 16:14")

    def test_non_matching_name_falls_back_to_dir_name(self):
        title = sm._note_title(Path("/x/random-name"))
        self.assertEqual(title, "# Meeting — random-name")


if __name__ == "__main__":
    unittest.main()
