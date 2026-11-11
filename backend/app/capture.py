import os
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from .evm import Magnifier, demo_frame

cv2.setNumThreads(2)


def command(args, timeout=8):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(str(exc)) from exc


def devices():
    result = command(["adb", "devices", "-l"])
    if result.returncode:
        raise ValueError(result.stderr.strip())
    found = []
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and "usb:" in line:
            model = next((item[6:] for item in fields if item.startswith("model:")), fields[0])
            found.append({"serial": fields[0], "state": fields[1], "name": model.replace("_", " ")})
    return found


def loopbacks():
    return [{"path": f"/dev/{node.name}", "name": (node / "name").read_text().strip()}
            for node in sorted(Path("/sys/class/video4linux").glob("video*"))
            if "/virtual/" in str(node.resolve())]


def cameras(serial):
    if serial not in [d["serial"] for d in devices() if d["state"] == "device"]:
        raise ValueError("Connect and authorize this USB phone first")
    result = command(["scrcpy", "-s", serial, "--list-cameras"], timeout=15)
    raw = result.stdout + result.stderr
    if result.returncode:
        raise ValueError(raw.strip()[-1800:])
    found = []
    for line in raw.splitlines():
        modern = re.search(r"--camera-id=([^\s]+)\s+\((.*)\)", line)
        if modern:
            found.append({"id": modern[1], "description": modern[2]})
            continue
        match = re.match(r"\s*-\s+['\"]?([^\s'\"]+)['\"]?\s+\((.*)\)", line)
        if match:
            found.append({"id": match[1], "description": match[2]})
    return {"cameras": found, "details": raw}


class Session:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None
        self.processes = []
        self.logs = deque(maxlen=20)
        self.frames = {"original": None, "amplified": None}
        self.config = {"mode": "color", "gain": 20., "low": .7, "high": 3., "fps": 30.}
        self.revision = 0
        self.state, self.error, self.source = "idle", None, None
        self.fps, self.count, self.clipping = 0., 0, 0.

    def status(self):
        with self.lock:
            return {"state": self.state, "error": self.error, "source": self.source,
                    "fps": round(self.fps, 1), "frames": self.count,
                    "clipping": round(self.clipping * 100, 2), "config": dict(self.config),
                    "logs": list(self.logs), "timing": "local frame arrival; not sensor timestamps"}

    def configure(self, values):
        Magnifier(**values)
        with self.lock:
            self.config = dict(values)
            self.revision += 1
        return self.status()

    def start(self, source, serial=None, camera_id=None, sink=None):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError("Stop the current capture first")
            if source == "phone":
                for executable in ("adb", "scrcpy", "ffmpeg"):
                    if not shutil.which(executable):
                        raise ValueError(f"Missing {executable}")
                if serial not in [d["serial"] for d in devices() if d["state"] == "device"]:
                    raise ValueError("No authorized USB phone. Connect it and accept USB debugging.")
            self.stop_event.clear()
            self.state, self.error, self.source = "starting", None, source
            self.frames = {"original": None, "amplified": None}
            self.count, self.fps = 0, 0
            self.logs.clear()
            self.worker = threading.Thread(target=self._run, args=(source, serial, camera_id, sink), daemon=True)
            self.worker.start()
        return self.status()

    def _spawn(self, args):
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with self.lock:
            self.processes.append(process)
        threading.Thread(target=self._log, args=(process.stderr,), daemon=True).start()
        return process

    def _log(self, stream):
        for line in iter(stream.readline, b""):
            with self.lock:
                self.logs.append(line.decode(errors="replace").strip())
        stream.close()

    def _phone_frames(self, serial, camera_id, sink):
        with tempfile.TemporaryDirectory(prefix="video-mag-camera-") as temporary:
            fifo = str(Path(temporary) / "camera.mkv")
            os.mkfifo(fifo)
            yield from self._phone_fifo_frames(serial, camera_id, fifo)

    def _phone_fifo_frames(self, serial, camera_id, fifo):
        args = ["scrcpy", "-s", serial, "--video-source=camera", "--no-audio", "--no-control",
                "--no-video-playback", "--max-size=1280", "--camera-fps=30",
                "--video-codec=h264", "--video-bit-rate=20M", f"--record={fifo}", "--record-format=mkv"]
        args.append(f"--camera-id={camera_id}" if camera_id else "--camera-facing=back")
        producer = self._spawn(args)
        threading.Thread(target=self._log, args=(producer.stdout,), daemon=True).start()
        reader = self._spawn(["ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "2",
                              "-i", fifo, "-vf", "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2",
                              "-an", "-threads", "2", "-filter_threads", "2", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"])
        fd = reader.stdout.fileno()
        os.set_blocking(fd, False)
        buffer = bytearray()
        size = 640 * 360 * 3
        last_frame = time.monotonic()
        while not self.stop_event.is_set():
            if producer.poll() is not None or reader.poll() is not None:
                raise ValueError("Camera stream ended. Check USB connection and capture log.")
            if not select.select([fd], [], [], .2)[0]:
                if time.monotonic() - last_frame > 8:
                    raise ValueError("Camera stopped delivering frames")
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                raise ValueError("Camera decoder closed")
            buffer.extend(chunk)
            if len(buffer) >= size:
                frame = np.frombuffer(bytes(buffer[:size]), np.uint8).reshape(360, 640, 3)
                del buffer[:size]
                last_frame = time.monotonic()
                yield frame

    def _demo_frames(self):
        start, index = time.monotonic(), 0
        while not self.stop_event.is_set():
            yield demo_frame(index / 30)
            index += 1
            self.stop_event.wait(max(0, start + index / 30 - time.monotonic()))

    def _run(self, source, serial, camera_id, sink):
        try:
            revision, processor, previous = -1, None, None
            times = deque(maxlen=60)
            frames = self._demo_frames() if source == "demo" else self._phone_frames(serial, camera_id, sink)
            for frame in frames:
                if self.stop_event.is_set():
                    break
                now = time.monotonic()
                with self.lock:
                    config, current_revision = dict(self.config), self.revision
                if revision != current_revision or (previous is not None and now - previous > .25):
                    processor = Magnifier(**config)
                    revision = current_revision
                previous = now
                output = processor.process(frame)
                encoded = {}
                for key, image in (("original", frame), ("amplified", output)):
                    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    if not ok:
                        raise ValueError("Could not encode preview")
                    encoded[key] = jpeg.tobytes()
                times.append(now)
                with self.lock:
                    self.frames, self.state = encoded, "running"
                    self.count += 1
                    self.clipping = processor.clipped
                    if len(times) > 1:
                        self.fps = (len(times) - 1) / (times[-1] - times[0])
        except Exception as exc:
            with self.lock:
                self.state, self.error = "error", str(exc)
        finally:
            self._cleanup()
            with self.lock:
                if self.state != "error":
                    self.state = "idle"

    def _cleanup(self):
        with self.lock:
            processes, self.processes = self.processes, []
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if process.stdout:
                process.stdout.close()

    def stop(self):
        self.stop_event.set()
        worker = self.worker
        if worker:
            worker.join(timeout=12)
            if worker.is_alive():
                raise ValueError("Capture is still stopping; try again shortly")
        return self.status()
