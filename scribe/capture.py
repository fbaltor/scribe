"""Audio capture for meetings.

Records two 16kHz mono WAVs for later Whisper/LLM processing:

  remote.wav  -> monitor of the default output sink (everyone else)
  mic.wav     -> default input source (you)

The streams have no fixed target: they follow the *current default* device,
so connecting/disconnecting Bluetooth mid-meeting moves the capture to the new
device automatically (single continuous file, no restart). No real-time
transcription. Ctrl-C to stop.
"""
import datetime
import json
import os
import signal
import subprocess
import sys


def pw_dump():
    out = subprocess.run(["pw-dump"], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _val_name(value):
    # metadata value may already be a dict, or a JSON string
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return value.get("name") if isinstance(value, dict) else None


def default_devices(dump):
    """Return (sink_name, source_name) from PipeWire default-device metadata."""
    sink = source = None
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Metadata":
            continue
        for m in o.get("metadata", []) or []:
            if m.get("key") == "default.audio.sink":
                sink = _val_name(m.get("value"))
            elif m.get("key") == "default.audio.source":
                source = _val_name(m.get("value"))
    return sink, source


def first_of_class(dump, media_class):
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        props = (o.get("info", {}) or {}).get("props", {}) or {}
        if props.get("media.class") == media_class:
            return props.get("node.name")
    return None


def describe(dump, node_name):
    for o in dump:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        props = (o.get("info", {}) or {}).get("props", {}) or {}
        if props.get("node.name") == node_name:
            return props.get("node.description") or node_name
    return node_name


def recordings_dir():
    """Base dir for recordings: env override, else <repo>/recordings.

    SCRIBE_RECORDINGS_DIR is set by mise to {{config_root}}/recordings; the
    fallback keeps output inside the repo even when run as plain python.
    """
    env = os.environ.get("SCRIBE_RECORDINGS_DIR")
    if env:
        return os.path.expanduser(env)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, "recordings")


def default_out_dir():
    return os.path.join(
        recordings_dir(), datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    )


def capture(out_dir=None):
    out_dir = os.path.expanduser(out_dir) if out_dir else default_out_dir()
    os.makedirs(out_dir, exist_ok=True)

    dump = pw_dump()
    sink, source = default_devices(dump)
    sink = sink or first_of_class(dump, "Audio/Sink")
    source = source or first_of_class(dump, "Audio/Source")

    if not sink:
        sys.exit("No output sink found - is audio configured?")

    # No fixed --target: streams follow the default device live, so connecting
    # or disconnecting Bluetooth mid-meeting moves the capture automatically.
    print(f"Recording -> {out_dir}   (follows device changes live)")
    print(f"  remote.wav  <- default output monitor   (now: {describe(dump, sink)})")
    print(f"  mic.wav     <- default input source      (now: {describe(dump, source) if source else 'none yet'})")
    print("Connect/disconnect Bluetooth anytime. Press Ctrl-C to stop.\n")

    common = ["--rate", "16000", "--channels", "1", "--format", "s16"]
    procs = [
        # remote = monitor of whatever sink is default (stream.capture.sink, no target)
        subprocess.Popen(["pw-record", "-P", "stream.capture.sink=true",
                          *common, os.path.join(out_dir, "remote.wav")]),
        # mic = whatever source is default (no target -> session manager follows default)
        subprocess.Popen(["pw-record", *common, os.path.join(out_dir, "mic.wav")]),
    ]

    def stop(*_):
        for p in procs:
            if p.poll() is None:
                p.terminate()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    for p in procs:
        p.wait()

    print("\nStopped. Saved:")
    subprocess.run(["ls", "-lh", out_dir])
    return out_dir
