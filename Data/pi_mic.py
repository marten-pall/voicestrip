"""Server-side capture from an INMP441 I2S microphone on a Raspberry Pi.

Plain audio code (no Flask). sounddevice / numpy / scipy are imported lazily,
so the collector still runs on a PC where they are not installed.

The I2S overlay exposes a 2-channel 48 kHz int32 capture device; the INMP441
delivers on one channel (left when its L/R pin is tied to GND). Audio is kept
at 48 kHz while recording and resampled to 16 kHz once at the end, which avoids
filter artifacts at every chunk boundary.

Environment overrides:
    PI_MIC=1 / PI_MIC=0      force detection on / off (default: only on a Raspberry Pi)
    PI_MIC_DEVICE=<n|name>   use this input device index or name substring
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
import wave
from pathlib import Path

log = logging.getLogger(__name__)

CAPTURE_RATE = 48000
CAPTURE_CHANNELS = 2
TARGET_RATE = 16000
INT32_FULL_SCALE = 2 ** 31
DEVICE_PATTERNS = ("googlevoicehat", "snd_rpi", "i2s", "adau7002", "mems")
MONITOR_IDLE_SEC = 30
MIN_DURATION_SEC = 0.1
BLOCK_FRAMES = 960  # 20 ms per callback: small blocks keep the live level meter responsive


class PiMicError(RuntimeError):
    """User-facing problem (no mic, already recording, unknown recording id, ...)."""


def is_raspberry_pi() -> bool:
    try:
        return "raspberry pi" in Path("/proc/device-tree/model").read_text(errors="ignore").lower()
    except OSError:
        return False


def find_device() -> int | None:
    """Index of the I2S input device (PI_MIC_DEVICE overrides the name patterns)."""
    import sounddevice as sd

    override = os.environ.get("PI_MIC_DEVICE", "").strip().lower()
    for index, device in enumerate(sd.query_devices()):
        if device.get("max_input_channels", 0) < 1:
            continue
        name = str(device.get("name", "")).lower()
        if override:
            if (override.isdigit() and int(override) == index) or (not override.isdigit() and override in name):
                return index
        elif any(pattern in name for pattern in DEVICE_PATTERNS):
            return index
    return None


def detect_pi_mic() -> bool:
    """True if an INMP441-style I2S input device is present on this host."""
    forced = os.environ.get("PI_MIC")
    if forced == "0":
        return False
    if forced != "1" and not is_raspberry_pi():
        return False  # PC: some laptop codecs also have "I2S" in their name
    try:
        return find_device() is not None
    except (ImportError, OSError) as exc:
        log.warning("Pi mic detection failed: %s", exc)
        return False


def input_devices() -> list[str]:
    """Human-readable list of input devices, for the mic test page."""
    try:
        import sounddevice as sd
        return [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d.get("max_input_channels", 0) > 0]
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return [f"(sounddevice unavailable: {exc})"]


def to_pcm16(audio) -> bytes:
    """float32 [-1, 1] -> 16-bit little-endian PCM bytes."""
    import numpy as np
    return (np.clip(audio, -1.0, 32767 / 32768) * 32768).round().astype("<i2").tobytes()


def wav_bytes(audio) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(to_pcm16(audio))
    return buf.getvalue()


class _Capture:
    """One open InputStream. Tracks the peak level; optionally keeps the audio."""

    def __init__(self, device: int, keep_audio: bool) -> None:
        import numpy as np
        import sounddevice as sd

        self.keep_audio = keep_audio
        self.chunks: list = []
        self.frames = 0
        self.peak_since_read = 0.0
        self.overflows = 0
        self.error: str | None = None
        self.started = time.monotonic()
        self.lock = threading.Lock()

        def callback(indata, frames, time_info, status):  # runs in PortAudio's thread
            try:
                if status and status.input_overflow:
                    self.overflows += 1
                left = indata[:, 0].astype(np.float32) / INT32_FULL_SCALE
                peak = float(np.abs(left).max()) if frames else 0.0
                with self.lock:
                    self.frames += frames
                    self.peak_since_read = max(self.peak_since_read, peak)
                    if self.keep_audio:
                        self.chunks.append(left)
            except Exception as exc:  # noqa: BLE001 - must not die silently in the audio thread
                self.error = f"{type(exc).__name__}: {exc}"
                raise sd.CallbackAbort from exc

        # latency="low": PortAudio's default for input is "high", which on some ALSA/I2S
        # drivers delivers audio in large, late chunks.
        self.stream = sd.InputStream(device=device, samplerate=CAPTURE_RATE, channels=CAPTURE_CHANNELS,
                                     dtype="int32", callback=callback, blocksize=BLOCK_FRAMES, latency="low")
        self.stream.start()

    @property
    def alive(self) -> bool:
        return self.error is None and bool(self.stream.active)

    @property
    def duration(self) -> float:
        return self.frames / CAPTURE_RATE

    @property
    def lag_ms(self) -> float:
        """How far the delivered audio trails wall-clock time (driver/PortAudio buffering)."""
        return max(0.0, (time.monotonic() - self.started - self.duration) * 1000)

    def read_peak(self) -> float:
        with self.lock:
            peak, self.peak_since_read = self.peak_since_read, 0.0
        return peak

    def close(self) -> None:
        try:
            self.stream.stop()
            self.stream.close()
        except Exception as exc:  # noqa: BLE001 - closing a broken stream may fail too
            log.debug("closing stream: %s", exc)

    def audio_16k(self):
        import numpy as np
        from scipy.signal import resample_poly

        with self.lock:
            audio = np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.float32)
        return resample_poly(audio, 1, CAPTURE_RATE // TARGET_RATE).astype(np.float32)


class PiMic:
    """At most one stream on the device at a time: a recording, a 3 s test, or the idle level monitor."""

    def __init__(self, available: bool) -> None:
        self.available = available
        self.device: int | None = None
        self.lock = threading.Lock()
        self.recording: dict | None = None  # {id, name, default_label, started_at, capture}
        self.monitor: _Capture | None = None
        self.monitor_used = 0.0
        if available:
            try:
                self.device = find_device()
            except Exception as exc:  # noqa: BLE001
                self._disable(f"device lookup failed: {exc}")

    def _disable(self, reason: str) -> None:
        self.available = False
        log.warning("Pi I2S mic disabled: %s", reason)

    def self_test(self) -> None:
        """Startup check: detection found a device, but can a stream really be opened?"""
        if not self.available:
            return
        try:
            capture = _Capture(self.device, keep_audio=False)
            time.sleep(0.2)
            capture.close()
        except Exception as exc:  # noqa: BLE001
            self._disable(f"opening the input stream failed ({exc})")

    def _require(self) -> None:
        if not self.available:
            raise PiMicError("Pi I2S mikrofoni pole saadaval")

    def _close_monitor(self) -> None:
        if self.monitor is not None:
            self.monitor.close()
            self.monitor = None

    def active_recording_id(self) -> str | None:
        rec = self.recording
        return rec["id"] if rec else None

    # ------------------------------------------------------------------ recording

    def start(self, recording_id: str, name: str, default_label: str) -> str:
        self._require()
        with self.lock:
            if self.recording is not None:
                raise PiMicError(f"Salvestus {self.recording['id']} juba käib")
            self._close_monitor()
            capture = _Capture(self.device, keep_audio=True)
            started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            self.recording = {"id": recording_id, "name": name, "default_label": default_label,
                              "started_at": started_at, "capture": capture}
        return started_at

    def status(self) -> dict:
        rec = self.recording
        if rec is None:
            return {"active": False}
        capture: _Capture = rec["capture"]
        failed = not capture.alive
        return {
            "active": True,
            "recording_id": rec["id"],
            "duration_so_far": round(capture.duration, 2),
            "buffer_lag_ms": round(capture.lag_ms),
            "peak_amplitude": round(capture.read_peak(), 4),
            "failed": failed,
            "error": (capture.error or "Mikrofoni voog katkes (ühendus või I2S seadistus?)") if failed else None,
        }

    def stop(self, recording_id: str):
        """Stop and return (audio float32 @ 16 kHz, recording meta). Works for failed recordings too,
        so whatever was captured before the failure can still be saved."""
        with self.lock:
            rec = self.recording
            if rec is None or rec["id"] != recording_id:
                raise PiMicError(f"Pole aktiivset salvestust {recording_id}")
            self.recording = None
        capture: _Capture = rec["capture"]
        failed = not capture.alive
        error, overflows = capture.error, capture.overflows
        capture.close()
        audio = capture.audio_16k()
        if overflows:
            log.warning("Pi recording %s: %d input overflows (audio may have small gaps)", recording_id, overflows)
        meta = {k: rec[k] for k in ("id", "name", "default_label", "started_at")}
        meta["warning"] = (f"Salvestus katkes enneaegselt: {error or 'mikrofoni voog peatus'}"
                           if failed else None)
        if len(audio) < MIN_DURATION_SEC * TARGET_RATE:
            raise PiMicError("Salvestus on tühi või liiga lühike")
        return audio, meta

    # ------------------------------------------------------------- test & monitor

    def current_peak(self) -> float:
        """Peak since the previous call. Uses the active recording if there is one,
        otherwise a lazily opened monitor stream that closes after 30 s without requests."""
        self._require()
        rec = self.recording
        if rec is not None:
            return rec["capture"].read_peak()
        with self.lock:
            if self.monitor is not None and not self.monitor.alive:
                self._close_monitor()
            if self.monitor is None:
                self.monitor = _Capture(self.device, keep_audio=False)
                threading.Thread(target=self._close_when_idle, daemon=True).start()
            self.monitor_used = time.monotonic()
            return self.monitor.read_peak()

    def current_lag_ms(self) -> float | None:
        rec, monitor = self.recording, self.monitor
        capture = rec["capture"] if rec else monitor
        return round(capture.lag_ms) if capture is not None else None

    def _close_when_idle(self) -> None:
        while True:
            time.sleep(1.0)
            with self.lock:
                if self.monitor is None:
                    return
                if time.monotonic() - self.monitor_used > MONITOR_IDLE_SEC:
                    self._close_monitor()
                    return

    def test_record(self, seconds: float):
        """Record `seconds` of audio (not saved anywhere); returns float32 @ 16 kHz."""
        self._require()
        with self.lock:
            if self.recording is not None:
                raise PiMicError("Salvestus käib - testi ei saa praegu teha")
            self._close_monitor()
            capture = _Capture(self.device, keep_audio=True)
            try:
                time.sleep(seconds)
            finally:
                capture.close()
        if capture.error:
            raise PiMicError(f"Testsalvestus ebaõnnestus: {capture.error}")
        return capture.audio_16k()
