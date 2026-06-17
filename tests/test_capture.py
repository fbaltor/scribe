"""Behavior tests for scribe.capture.

Pure parsing/helpers are tested against a realistic pw-dump fixture; the
orchestration in capture() is tested with pw_dump + subprocess.Popen stubbed,
so no audio device or real recording is required.
"""
import json
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scribe import capture as cap


def _node(name, media_class, description=None):
    props = {"node.name": name, "media.class": media_class}
    if description is not None:
        props["node.description"] = description
    return {"type": cap.NODE_TYPE, "info": {"props": props}}


def _metadata(sink=None, source=None, *, as_json_string=False):
    entries = []
    if sink is not None:
        val = {"name": sink}
        entries.append({"key": cap.DEFAULT_SINK_KEY,
                        "value": json.dumps(val) if as_json_string else val})
    if source is not None:
        val = {"name": source}
        entries.append({"key": cap.DEFAULT_SOURCE_KEY,
                        "value": json.dumps(val) if as_json_string else val})
    return {"type": cap.METADATA_TYPE, "metadata": entries}


SAMPLE_DUMP = [
    {"type": "PipeWire:Interface:Client", "info": {}},  # ignored noise
    _node("alsa_output.pci.analog", cap.SINK_CLASS, "Built-in Speakers"),
    _node("bluez.headset", cap.SINK_CLASS, "WH-1000XM4"),
    _node("alsa_input.pci.analog", cap.SOURCE_CLASS, "Built-in Mic"),
    _metadata(sink="bluez.headset", source="alsa_input.pci.analog"),
]


class ValNameTests(unittest.TestCase):
    def test_dict_value_returns_name(self):
        self.assertEqual(cap._val_name({"name": "sink-a"}), "sink-a")

    def test_json_string_value_is_parsed(self):
        self.assertEqual(cap._val_name('{"name": "sink-a"}'), "sink-a")

    def test_non_json_string_returns_string_verbatim(self):
        self.assertEqual(cap._val_name("not-json"), "not-json")

    def test_value_without_name_returns_none(self):
        self.assertIsNone(cap._val_name({"other": 1}))

    def test_non_mapping_value_returns_none(self):
        self.assertIsNone(cap._val_name(42))


class NodesByNameTests(unittest.TestCase):
    def test_indexes_only_nodes(self):
        nodes = cap._nodes_by_name(SAMPLE_DUMP)
        self.assertEqual(
            set(nodes),
            {"alsa_output.pci.analog", "bluez.headset", "alsa_input.pci.analog"},
        )

    def test_preserves_dump_order(self):
        nodes = cap._nodes_by_name(SAMPLE_DUMP)
        self.assertEqual(list(nodes)[0], "alsa_output.pci.analog")

    def test_first_occurrence_wins_on_duplicate(self):
        dump = [_node("dup", cap.SINK_CLASS, "first"),
                _node("dup", cap.SINK_CLASS, "second")]
        self.assertEqual(cap._nodes_by_name(dump)["dup"]["node.description"], "first")


class DefaultDevicesTests(unittest.TestCase):
    def test_extracts_sink_and_source(self):
        self.assertEqual(
            cap.default_devices(SAMPLE_DUMP),
            ("bluez.headset", "alsa_input.pci.analog"),
        )

    def test_handles_value_as_json_string(self):
        dump = [_metadata(sink="s", source="m", as_json_string=True)]
        self.assertEqual(cap.default_devices(dump), ("s", "m"))

    def test_missing_metadata_returns_none_pair(self):
        self.assertEqual(cap.default_devices([_node("n", cap.SINK_CLASS)]),
                         (None, None))


class FirstOfClassTests(unittest.TestCase):
    def test_returns_first_node_of_class(self):
        nodes = cap._nodes_by_name(SAMPLE_DUMP)
        self.assertEqual(cap.first_of_class(nodes, cap.SINK_CLASS),
                         "alsa_output.pci.analog")

    def test_returns_none_when_absent(self):
        nodes = cap._nodes_by_name([_node("only-sink", cap.SINK_CLASS)])
        self.assertIsNone(cap.first_of_class(nodes, cap.SOURCE_CLASS))


class DescribeTests(unittest.TestCase):
    def test_returns_description(self):
        nodes = cap._nodes_by_name(SAMPLE_DUMP)
        self.assertEqual(cap.describe(nodes, "bluez.headset"), "WH-1000XM4")

    def test_falls_back_to_name_for_unknown_node(self):
        self.assertEqual(cap.describe({}, "ghost"), "ghost")

    def test_falls_back_to_name_when_no_description(self):
        nodes = cap._nodes_by_name([_node("bare", cap.SINK_CLASS)])
        self.assertEqual(cap.describe(nodes, "bare"), "bare")


class RecordingsDirTests(unittest.TestCase):
    def test_env_override_is_expanded(self):
        with mock.patch.dict("os.environ", {"SCRIBE_RECORDINGS_DIR": "~/rec"}):
            self.assertEqual(cap.recordings_dir(), Path.home() / "rec")

    def test_repo_fallback_when_env_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            d = cap.recordings_dir()
        self.assertEqual(d.name, "recordings")
        self.assertEqual(d.parent, Path(cap.__file__).resolve().parent.parent)

    def test_default_out_dir_is_under_recordings(self):
        with mock.patch.dict("os.environ", {"SCRIBE_RECORDINGS_DIR": "/tmp/rec"}):
            out = cap.default_out_dir()
        self.assertEqual(out.parent, Path("/tmp/rec"))


class HumanSizeTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(cap._human_size(512), "512 B")

    def test_kib(self):
        self.assertEqual(cap._human_size(2048), "2.0 KiB")

    def test_mib(self):
        self.assertEqual(cap._human_size(5 * 1024 * 1024), "5.0 MiB")


class _FakeProc:
    """Stand-in for a pw-record subprocess that has already finished."""
    instances: list["_FakeProc"] = []

    def __init__(self, args, *a, **k):
        self.args = args
        self.returncode = 0
        _FakeProc.instances.append(self)

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class CaptureTests(unittest.TestCase):
    def setUp(self):
        _FakeProc.instances = []

    def test_raises_capture_error_when_no_sink(self):
        dump = [_node("only-mic", cap.SOURCE_CLASS)]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(cap, "pw_dump", return_value=dump), \
             self.assertRaises(cap.CaptureError):
            cap.capture(Path(tmp) / "nosink")

    def test_spawns_two_tracks_with_expected_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "meeting"
            with mock.patch.object(cap, "pw_dump", return_value=SAMPLE_DUMP), \
                 mock.patch.object(subprocess, "Popen", _FakeProc):
                result = cap.capture(out)

            self.assertEqual(result, out)
            self.assertTrue(out.is_dir())
            self.assertEqual(len(_FakeProc.instances), 2)

            remote_args, mic_args = (p.args for p in _FakeProc.instances)
            # remote track captures the sink monitor
            self.assertIn("stream.capture.sink=true", remote_args)
            self.assertTrue(remote_args[-1].endswith("remote.wav"))
            # mic track has no sink-monitor flag
            self.assertNotIn("stream.capture.sink=true", mic_args)
            self.assertTrue(mic_args[-1].endswith("mic.wav"))
            # both record 16kHz mono s16
            for args in (remote_args, mic_args):
                self.assertEqual(args[args.index("--rate") + 1], str(cap.RATE))

    def test_restores_signal_handlers_after_capture(self):
        before_int = signal.getsignal(signal.SIGINT)
        before_term = signal.getsignal(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(cap, "pw_dump", return_value=SAMPLE_DUMP), \
             mock.patch.object(subprocess, "Popen", _FakeProc):
            cap.capture(Path(tmp) / "m")
        self.assertIs(signal.getsignal(signal.SIGINT), before_int)
        self.assertIs(signal.getsignal(signal.SIGTERM), before_term)


if __name__ == "__main__":
    unittest.main()
