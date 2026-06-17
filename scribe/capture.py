"""Audio capture for meetings.

Records two 16kHz mono WAVs for later Whisper/LLM processing:

  remote.wav  -> monitor of the default output sink (everyone else)
  mic.wav     -> default input source (you)

The streams have no fixed target: they follow the *current default* device,
so connecting/disconnecting Bluetooth mid-meeting moves the capture to the new
device automatically (single continuous file, no restart). No real-time
transcription. Ctrl-C to stop.
"""
from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
from pathlib import Path

# --- capture parameters -----------------------------------------------------
RATE = 16000  # Whisper-native sample rate
CHANNELS = 1
SAMPLE_FORMAT = "s16"
# pw-record args shared by both tracks
PW_RECORD_COMMON = [
    "--rate", str(RATE),
    "--channels", str(CHANNELS),
    "--format", SAMPLE_FORMAT,
]
TERMINATE_TIMEOUT = 5  # seconds to wait for a clean WAV-header flush before kill

# PipeWire object types / media classes / metadata keys
NODE_TYPE = "PipeWire:Interface:Node"
METADATA_TYPE = "PipeWire:Interface:Metadata"
SINK_CLASS = "Audio/Sink"
SOURCE_CLASS = "Audio/Source"
DEFAULT_SINK_KEY = "default.audio.sink"
DEFAULT_SOURCE_KEY = "default.audio.source"


class CaptureError(RuntimeError):
    """Capture cannot proceed (e.g. no audio sink available)."""


def pw_dump() -> list[dict]:
    """Parsed ``pw-dump`` output: the full PipeWire object graph."""
    out = subprocess.run(
        ["pw-dump"], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def _val_name(value: object) -> str | None:
    """Pull a ``name`` out of a metadata value (dict, JSON string, or plain str)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return value.get("name") if isinstance(value, dict) else None


def _nodes_by_name(dump: list[dict]) -> dict[str, dict]:
    """Index ``node.name -> props`` for every node, in a single pass.

    Insertion order follows the dump, so callers can still ask for the *first*
    node of a given class by iterating the result.
    """
    index: dict[str, dict] = {}
    for obj in dump:
        if obj.get("type") != NODE_TYPE:
            continue
        props = (obj.get("info", {}) or {}).get("props", {}) or {}
        name = props.get("node.name")
        if name and name not in index:
            index[name] = props
    return index


def default_devices(dump: list[dict]) -> tuple[str | None, str | None]:
    """Return ``(sink_name, source_name)`` from PipeWire default-device metadata."""
    sink = source = None
    for obj in dump:
        if obj.get("type") != METADATA_TYPE:
            continue
        for m in obj.get("metadata", []) or []:
            key = m.get("key")
            if key == DEFAULT_SINK_KEY:
                sink = _val_name(m.get("value"))
            elif key == DEFAULT_SOURCE_KEY:
                source = _val_name(m.get("value"))
    return sink, source


def first_of_class(nodes: dict[str, dict], media_class: str) -> str | None:
    """Name of the first node whose ``media.class`` matches, else ``None``."""
    return next(
        (name for name, props in nodes.items()
         if props.get("media.class") == media_class),
        None,
    )


def describe(nodes: dict[str, dict], node_name: str) -> str:
    """Human-readable description for a node name (falls back to the name)."""
    props = nodes.get(node_name)
    return (props.get("node.description") if props else None) or node_name


def recordings_dir() -> Path:
    """Base dir for recordings: env override, else ``<repo>/recordings``.

    ``SCRIBE_RECORDINGS_DIR`` is set by mise to ``{{config_root}}/recordings``;
    the fallback keeps output inside the repo even when run as plain python.
    """
    env = os.environ.get("SCRIBE_RECORDINGS_DIR")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent / "recordings"


def default_out_dir() -> Path:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return recordings_dir() / stamp


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"  # unreachable; satisfies type-checkers


def _print_listing(directory: Path) -> None:
    for entry in sorted(directory.iterdir()):
        print(f"  {_human_size(entry.stat().st_size):>9}  {entry.name}")


def capture(out_dir: str | os.PathLike[str] | None = None) -> Path:
    out = Path(out_dir).expanduser() if out_dir else default_out_dir()
    out.mkdir(parents=True, exist_ok=True)

    dump = pw_dump()
    nodes = _nodes_by_name(dump)
    sink, source = default_devices(dump)
    sink = sink or first_of_class(nodes, SINK_CLASS)
    source = source or first_of_class(nodes, SOURCE_CLASS)

    if not sink:
        raise CaptureError("No output sink found - is audio configured?")

    # No fixed --target: streams follow the default device live, so connecting
    # or disconnecting Bluetooth mid-meeting moves the capture automatically.
    here = describe(nodes, source) if source else "none yet"
    print(f"Recording -> {out}   (follows device changes live)")
    print(f"  remote.wav  <- default output monitor   (now: {describe(nodes, sink)})")
    print(f"  mic.wav     <- default input source      (now: {here})")
    print("Connect/disconnect Bluetooth anytime. Press Ctrl-C to stop.\n")

    procs: list[subprocess.Popen] = []

    def stop(*_: object) -> None:
        for p in procs:
            if p.poll() is None:
                p.terminate()

    old_int = signal.signal(signal.SIGINT, stop)
    old_term = signal.signal(signal.SIGTERM, stop)
    try:
        # remote = monitor of whatever sink is default (no target)
        procs.append(subprocess.Popen(
            ["pw-record", "-P", "stream.capture.sink=true",
             *PW_RECORD_COMMON, str(out / "remote.wav")]))
        # mic = whatever source is default (no target -> session manager follows)
        procs.append(subprocess.Popen(
            ["pw-record", *PW_RECORD_COMMON, str(out / "mic.wav")]))
        for p in procs:
            p.wait()
    finally:
        # Terminate anything still alive (e.g. one Popen failed, or we unwound
        # for a reason other than the signal handler), then reap so pw-record
        # gets to flush the final RIFF size into the WAV header.
        stop()
        for p in procs:
            try:
                p.wait(timeout=TERMINATE_TIMEOUT)
            except subprocess.TimeoutExpired:
                p.kill()
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)

    print("\nStopped. Saved:")
    _print_listing(out)
    return out
