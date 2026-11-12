"""Bounded, asynchronous side-by-side live video recording."""
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np

RECORDINGS = Path(__file__).resolve().parents[2] / ".runtime" / "recordings"


class Recording:
    def __init__(self, fps):
        self.fps = fps
        self.id = uuid.uuid4().hex
        self.queue = queue.Queue(maxsize=4)
        self.error = None
        self.frames = 0
        self.dropped = 0
        self.started = time.monotonic()
        self.path = RECORDINGS / f"{self.id}.mp4"
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def put(self, original, amplified):
        try:
            self.queue.put_nowait((original.copy(), amplified.copy()))
        except queue.Full:
            self.dropped += 1

    def _run(self):
        process = None
        try:
            RECORDINGS.mkdir(parents=True, exist_ok=True)
            while True:
                pair = self.queue.get()
                if pair is None:
                    break
                original, amplified = pair
                height, width = original.shape[:2]
                if process is None:
                    process = subprocess.Popen([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width * 2}x{height}",
                        "-r", str(self.fps), "-i", "-", "-an",
                        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(self.path),
                    ], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                process.stdin.write(np.concatenate((original, amplified), axis=1).tobytes())
                self.frames += 1
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        finally:
            if process:
                if process.stdin:
                    process.stdin.close()
                if process.wait() != 0 and not self.error:
                    self.error = "Video encoding failed"
            if not self.frames and not self.error:
                self.error = "No frames were recorded"

    def stop(self):
        if self.worker.is_alive():
            while self.worker.is_alive():
                try:
                    self.queue.put(None, timeout=.2)
                    break
                except queue.Full:
                    continue
        self.worker.join(timeout=30)
        if self.worker.is_alive():
            raise ValueError("Recording is still finishing")
        if self.error:
            raise ValueError(self.error)
        return {"id": self.id, "frames": self.frames, "dropped": self.dropped,
                "elapsed": round(time.monotonic() - self.started, 1), "fps": self.fps}
