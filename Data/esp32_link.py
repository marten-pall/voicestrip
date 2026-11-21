"""Audio streamed over WiFi from an ESP32-S3 + INMP441 (sketch: ../esp32/collector/).

Plain state code (no Flask), like pi_mic.py. The ESP32 is always the HTTP client,
so it works behind any router without the server knowing its address:

  idle       GET  /api/esp32/poll?rssi=..&fw=..          every ~300 ms (also the heartbeat)
  streaming  POST /api/esp32/chunk?stream=<id>&seq=<n>&dropped=<k>&final=<0|1>
             body = raw 16 kHz mono int16 little-endian PCM (~0.25 s per chunk)

Both get one plain-text command line back (no JSON library needed on the ESP32):

  idle           nothing to do (if streaming: stop and throw the buffer away)
  stream <id>    capture and POST chunks for stream <id>; a new <id> restarts from "now"
  stop <id>      send what is still buffered (last chunk with final=1), then go idle

A stream is either a recording (id = recording_id, audio kept) or the mic test page's
monitor (id = m<N>: only the level is kept, plus the audio during a test recording).
"""

from __future__ import annotations

import sys
import threading
import time
from array import array

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
ONLINE_SEC = 3.0          # heartbeat younger than this = device online
START_TIMEOUT_SEC = 5.0   # recording requested, but no audio arrived yet
STALL_SEC = 6.0           # no chunk for this long during a recording = connection lost
STOP_WAIT_SEC = 2.0       # after STOP: wait for the final chunk (extended while chunks keep coming)
STOP_WAIT_MAX_SEC = 15.0
PEAK_HOLD_SEC = 0.6       # chunks arrive every 0.25 s, the meters poll faster: repeat the last peak
MONITOR_IDLE_SEC = 30
MIN_DURATION_SEC = 0.1


class Esp32Error(RuntimeError):
    """User-facing problem (device offline, already recording, unknown recording id, ...)."""


def pcm_peak(pcm: bytes) -> float:
    """Max absolute amplitude of int16 LE PCM, 0..1."""
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    return max(max(samples), -min(samples)) / 32768


class _Stream:
    """Audio arriving for one stream id."""

    def __init__(self, stream_id: str, keep_audio: bool) -> None:
        self.id = stream_id
        self.keep_audio = keep_audio
        self.audio = bytearray()
        self.samples = 0                 # received (counted even when the audio isn't kept)
        self.started = time.monotonic()  # when the command was issued
        self.last_chunk_at: float | None = None
        self.last_seq = -1
        self.last_peak = 0.0
        self.peak_since_read: float | None = None
        self.dropped = 0                 # samples the ESP32 could not buffer (WiFi too slow)
        self.restarts = 0                # seq went back: the ESP32 rebooted or lost the stream
        self.final = False
        self.stop_requested = False

    @property
    def duration(self) -> float:
        return self.samples / SAMPLE_RATE

    @property
    def lag_ms(self) -> float | None:
        """How far the received audio trails wall-clock time (start latency + WiFi buffering)."""
        if self.last_chunk_at is None:
            return None
        return max(0.0, (time.monotonic() - self.started - self.duration) * 1000)

    def add(self, seq: int, dropped: int, final: bool, pcm: bytes) -> None:
        if seq == self.last_seq:
            return  # retry of a chunk that already arrived (only its reply got lost)
        if seq < self.last_seq:
            self.restarts += 1
        self.last_seq = seq
        self.dropped += dropped
        self.samples += len(pcm) // SAMPLE_WIDTH
        if self.keep_audio:
            self.audio += pcm
        if pcm:
            self.last_peak = pcm_peak(pcm)
            self.peak_since_read = max(self.peak_since_read or 0.0, self.last_peak)
        self.last_chunk_at = time.monotonic()
        self.final = self.final or final

    def read_peak(self) -> float:
        if self.peak_since_read is None:  # nothing new since the last read
            fresh = self.last_chunk_at is not None and time.monotonic() - self.last_chunk_at < PEAK_HOLD_SEC
            return self.last_peak if fresh else 0.0
        peak, self.peak_since_read = self.peak_since_read, None
        return peak

    def failure(self) -> str | None:
        now = time.monotonic()
        if self.last_chunk_at is None:
            if now - self.started > START_TIMEOUT_SEC:
                return "ESP32 ei alustanud heli saatmist (ühendus katkes?)"
            return None
        if now - self.last_chunk_at > STALL_SEC:
            return f"ESP32-st pole {now - self.last_chunk_at:.0f} s heli tulnud (WiFi katkes?)"
        return None


class Esp32Link:
    """State shared by the device endpoints (poll/chunk) and the browser endpoints."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.last_seen = 0.0
        self.device: dict = {}
        self.recording: dict | None = None  # {id, name, default_label, started_at, stream}
        self.monitor: _Stream | None = None
        self.monitor_used = 0.0
        self._monitor_count = 0

    @property
    def online(self) -> bool:
        return time.monotonic() - self.last_seen < ONLINE_SEC

    def device_info(self) -> dict:
        ago = time.monotonic() - self.last_seen if self.last_seen else None
        return {**self.device, "online": self.online,
                "last_seen_sec": round(ago, 1) if ago is not None else None,
                "recording_id": self.active_recording_id()}

    def active_recording_id(self) -> str | None:
        rec = self.recording
        return rec["id"] if rec else None

    # ---------------------------------------------------------------- device side

    def _seen(self, info: dict) -> None:
        self.last_seen = time.monotonic()
        self.device.update({key: value for key, value in info.items() if value})

    def _command(self) -> str:
        rec = self.recording
        if rec is not None:
            return f"{'stop' if rec['stream'].stop_requested else 'stream'} {rec['id']}"
        if self.monitor is not None:
            if time.monotonic() - self.monitor_used <= MONITOR_IDLE_SEC:
                return f"stream {self.monitor.id}"
            self.monitor = None  # test page closed
        return "idle"

    def poll(self, info: dict) -> str:
        with self.cond:
            self._seen(info)
            return self._command()

    def push_chunk(self, stream_id: str, seq: int, dropped: int, final: bool, pcm: bytes, info: dict) -> str:
        with self.cond:
            self._seen(info)
            rec = self.recording
            if rec is not None and rec["id"] == stream_id:
                rec["stream"].add(seq, dropped, final, pcm)
            elif self.monitor is not None and self.monitor.id == stream_id:
                self.monitor.add(seq, dropped, final, pcm)
            # else: a stream that has ended - the reply tells the ESP32 what to do instead
            self.cond.notify_all()
            return self._command()

    # --------------------------------------------------------------- recording

    def start(self, recording_id: str, name: str, default_label: str) -> str:
        with self.cond:
            if not self.online:
                raise Esp32Error("ESP32 pole võrgus. Kontrolli toidet, WiFi-t ja serveri aadressi "
                                 "config.h-s (vt /esp32-mic-test).")
            if self.recording is not None:
                raise Esp32Error(f"Salvestus {self.recording['id']} juba käib")
            started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            self.recording = {"id": recording_id, "name": name, "default_label": default_label,
                              "started_at": started_at, "stream": _Stream(recording_id, keep_audio=True)}
        return started_at

    def status(self) -> dict:
        with self.cond:
            rec = self.recording
            if rec is None:
                return {"active": False, "online": self.online}
            stream: _Stream = rec["stream"]
            error = stream.failure()
            lag = stream.lag_ms
            return {
                "active": True,
                "online": self.online,
                "recording_id": rec["id"],
                "duration_so_far": round(stream.duration, 2),
                "buffer_lag_ms": round(lag) if lag is not None else None,
                "peak_amplitude": round(stream.read_peak(), 4),
                "failed": error is not None,
                "error": error,
            }

    def stop(self, recording_id: str) -> tuple[bytes, dict]:
        """Ask the ESP32 to flush, wait for its final chunk, return (pcm, meta).
        Works for failed recordings too: whatever arrived before the failure is kept."""
        with self.cond:
            rec = self.recording
            if rec is None or rec["id"] != recording_id:
                raise Esp32Error(f"Pole aktiivset ESP32 salvestust {recording_id}")
            stream: _Stream = rec["stream"]
            failure = stream.failure()
            stream.stop_requested = True
            if failure is None:
                deadline = time.monotonic() + STOP_WAIT_SEC
                give_up = time.monotonic() + STOP_WAIT_MAX_SEC
                while not stream.final:
                    remaining = min(deadline, give_up) - time.monotonic()
                    if remaining <= 0:
                        break
                    before = stream.samples
                    self.cond.wait(remaining)
                    if stream.samples != before:  # still draining a backlog
                        deadline = time.monotonic() + STOP_WAIT_SEC
            self.recording = None

        warnings = []
        if failure:
            warnings.append(f"Salvestus katkes enneaegselt: {failure}")
        elif not stream.final:
            warnings.append("ESP32 ei saatnud viimast osa (lõpust võib puududa kuni ~0.5 s)")
        if stream.dropped:
            warnings.append(f"ESP32 puhver sai täis: {stream.dropped / SAMPLE_RATE:.1f} s heli jäi vahele (nõrk WiFi?)")
        if stream.restarts:
            warnings.append(f"ESP32 voog algas {stream.restarts}x uuesti (taaskäivitus?) - salvestuses võib olla lünki")
        meta = {key: rec[key] for key in ("id", "name", "default_label", "started_at")}
        meta["warning"] = "; ".join(warnings) or None
        if stream.samples < MIN_DURATION_SEC * SAMPLE_RATE:
            raise Esp32Error("Salvestus on tühi või liiga lühike")
        return bytes(stream.audio), meta

    # ------------------------------------------------------- test page / monitor

    def _new_monitor(self, keep_audio: bool) -> _Stream:
        self._monitor_count += 1
        self.monitor = _Stream(f"m{self._monitor_count}", keep_audio=keep_audio)
        self.monitor_used = time.monotonic()
        return self.monitor

    def current_peak(self) -> float:
        """Peak of the latest audio. Uses the active recording if there is one, otherwise a
        monitor stream that runs while the test page keeps asking (stops after 30 s)."""
        with self.cond:
            rec = self.recording
            if rec is not None:
                return rec["stream"].read_peak()
            if self.monitor is None:
                self._new_monitor(keep_audio=False)
            self.monitor_used = time.monotonic()
            return self.monitor.read_peak()

    def current_lag_ms(self) -> float | None:
        rec, monitor = self.recording, self.monitor
        stream = rec["stream"] if rec else monitor
        lag = stream.lag_ms if stream is not None else None
        return round(lag) if lag is not None else None

    def test_record(self, seconds: float) -> bytes:
        """Record `seconds` of audio (not saved anywhere); returns int16 PCM."""
        need = int(seconds * SAMPLE_RATE) * SAMPLE_WIDTH
        with self.cond:
            if self.recording is not None:
                raise Esp32Error("Salvestus käib - testi ei saa praegu teha")
            if not self.online:
                raise Esp32Error("ESP32 pole võrgus")
            monitor = self._new_monitor(keep_audio=True)  # new id: the ESP32 starts from "now"
            give_up = time.monotonic() + seconds + 5
            while len(monitor.audio) < need and self.monitor is monitor:
                remaining = give_up - time.monotonic()
                if remaining <= 0:
                    break
                self.cond.wait(remaining)
                self.monitor_used = time.monotonic()
            pcm = bytes(monitor.audio[:need])
            monitor.keep_audio = False
            monitor.audio = bytearray()
        if len(pcm) < need:
            raise Esp32Error(f"ESP32 saatis ainult {len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH):.1f} s / "
                             f"{seconds:.0f} s heli (ühendus katkes või on liiga aeglane)")
        return pcm
