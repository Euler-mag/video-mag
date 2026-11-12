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
from .recording import Recording

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
    result = command(["scrcpy", "-s", serial, "--list-camera-sizes"], timeout=15)
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
    sizes = {}
    current = None
    for line in raw.splitlines():
        match = re.search(r"--camera-id=([^\s]+)\s+\((.*)\)", line)
        if match:
            current = match.group(1)
            sizes[current] = []
        elif "High speed capture" in line:
            current = None
        elif current:
            size = re.match(r"\s*-\s+(\d+x\d+)\s*$", line)
            if size:
                sizes[current].append(size.group(1))
    for camera in found:
        camera["sizes"] = sizes.get(camera["id"], [])
        rates = re.search(r"fps=\{([^}]+)\}", camera["description"])
        camera["fps"] = [int(value) for value in re.findall(r"\d+", rates.group(1))] if rates else []
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
        self.filter_revision = 0
        self.state, self.error, self.source = "idle", None, None
        self.fps, self.count, self.clipping = 0., 0, 0.
        self.recording = None
        self.recording_stopping = False
        self.last_recording = None
        self.device = "cpu"
        self.device_message = ""
        self.processing_ms = 0.0
        self.preview_ms = 0.0

    def status(self):
        with self.lock:
            return {"state": self.state, "error": self.error, "source": self.source,
                    "fps": round(self.fps, 1), "frames": self.count,
                    "clipping": round(self.clipping * 100, 2), "config": dict(self.config),
                    "device": self.device, "device_message": self.device_message,
                    "recording": self.recording is not None, "last_recording": self.last_recording,
                    "processing_ms": round(self.processing_ms, 1), "preview_ms": round(self.preview_ms, 1),
                    "logs": list(self.logs), "timing": "local frame arrival; not sensor timestamps"}

    def configure(self, values):
        Magnifier(**{key: value for key, value in values.items() if key not in ("device", "method")})
        with self.lock:
            if self.state in ("starting", "running") and values["fps"] != self.config["fps"]:
                raise ValueError("Stop capture before changing FPS")
            if any(values.get(key) != self.config.get(key) for key in ("mode", "low", "high", "fps", "levels", "order", "device", "method")):
                self.filter_revision += 1
            self.config = dict(values)
            self.revision += 1
        return self.status()

    def start(self, source, serial=None, camera_id=None, sink=None, camera_size="1280x720", camera_torch=False):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError("Stop the current capture first")
            if source == "phone":
                for executable in ("adb", "scrcpy", "ffmpeg"):
                    if not shutil.which(executable):
                        raise ValueError(f"Missing {executable}")
                if serial not in [d["serial"] for d in devices() if d["state"] == "device"]:
                    raise ValueError("No authorized USB phone. Connect it and accept USB debugging.")
            if not re.fullmatch(r"\d{2,4}x\d{2,4}", camera_size):
                raise ValueError("Invalid camera size")
            width, height = map(int, camera_size.split("x"))
            if not (64 <= width <= 4096 and 64 <= height <= 4096):
                raise ValueError("Camera size must be between 64 and 4096 pixels per side")
            self.camera_size = camera_size
            self.camera_torch = camera_torch
            self.stop_event.clear()
            self.state, self.error, self.source = "starting", None, source
            self.frames = {"original": None, "amplified": None}
            self.count, self.fps = 0, 0
            self.logs.clear()
            self.worker = threading.Thread(target=self._run, args=(source, serial, camera_id, sink), daemon=True)
            self.worker.start()
        return self.status()

    def start_recording(self):
        with self.lock:
            if self.state != "running":
                raise ValueError("Start camera capture before recording")
            if self.recording is not None or self.recording_stopping:
                raise ValueError("A recording is already in progress")
            self.last_recording = None
            self.recording = Recording(self.config["fps"])
        return self.status()

    def stop_recording(self):
        with self.lock:
            recording = self.recording
            if recording is None:
                raise ValueError("No recording is in progress")
            self.recording = None
            self.recording_stopping = True
        try:
            result = recording.stop()
            with self.lock:
                self.last_recording = result
            return result
        finally:
            with self.lock:
                self.recording_stopping = False

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
                "--no-video-playback", "--capture-orientation=0", f"--camera-size={self.camera_size}", f"--camera-fps={int(self.config['fps'])}",
                "--video-codec=h264", "--video-bit-rate=20M", f"--record={fifo}", "--record-format=mkv"]
        args.append(f"--camera-id={camera_id}" if camera_id else "--camera-facing=back")
        if self.camera_torch:
            args.append("--camera-torch")
        producer = self._spawn(args)
        threading.Thread(target=self._log, args=(producer.stdout,), daemon=True).start()
        reader = self._spawn(["ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "2",
                              "-i", fifo,
                              "-an", "-threads", "2", "-filter_threads", "2", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"])
        fd = reader.stdout.fileno()
        os.set_blocking(fd, False)
        buffer = bytearray()
        width, height = map(int, self.camera_size.split("x"))
        size = width * height * 3
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
                frame = np.frombuffer(bytes(buffer[:size]), np.uint8).reshape(height, width, 3)
                del buffer[:size]
                last_frame = time.monotonic()
                yield frame

    def _demo_frames(self):
        start, index = time.monotonic(), 0
        while not self.stop_event.is_set():
            yield demo_frame(index / self.config["fps"])
            index += 1
            self.stop_event.wait(max(0, start + index / self.config["fps"] - time.monotonic()))

    def _run(self, source, serial, camera_id, sink):
        try:
            revision, processor, previous = -1, None, None
            preview_queue = deque(maxlen=1)
            preview_event = threading.Event()
            preview_done = threading.Event()

            def encode_preview():
                while not preview_done.is_set():
                    if not preview_event.wait(.2):
                        continue
                    preview_event.clear()
                    with self.lock:
                        pair = preview_queue.pop() if preview_queue else None
                    if pair is None:
                        continue
                    started = time.monotonic()
                    encoded = {}
                    for key, image in pair:
                        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        if not ok:
                            continue
                        encoded[key] = jpeg.tobytes()
                    if len(encoded) == 2:
                        with self.lock:
                            self.frames = encoded
                            self.preview_ms = (time.monotonic() - started) * 1000

            preview_worker = threading.Thread(target=encode_preview, daemon=True)
            preview_worker.start()
            times = deque(maxlen=60)
            frames = self._demo_frames() if source == "demo" else self._phone_frames(serial, camera_id, sink)
            for frame in frames:
                if self.stop_event.is_set():
                    break
                now = time.monotonic()
                with self.lock:
                    config, current_revision = dict(self.config), self.filter_revision
                if revision != current_revision or (previous is not None and now - previous > .25):
                    choice = config.pop("device", "auto")
                    method = config.pop("method", "linear")
                    self.device_message = ""
                    if choice != "cpu":
                        try:
                            if method == "riesz" and config["mode"] == "motion":
                                from .riesz_cuda import RieszMagnifier
                                processor = RieszMagnifier(**config)
                            else:
                                from .evm_cuda import CudaMagnifier
                                processor = CudaMagnifier(**config)
                            self.device = "cuda"
                        except (ImportError, RuntimeError, ValueError) as exc:
                            if choice == "cuda":
                                raise ValueError(f"CUDA initialization failed: {exc}") from exc
                            self.device_message = f"CUDA unavailable, using CPU: {exc}"
                            if method == "riesz" and config["mode"] == "motion":
                                raise ValueError("Riesz phase processing requires CUDA") from exc
                            processor = Magnifier(**config)
                            self.device = "cpu"
                    else:
                        if method == "riesz" and config["mode"] == "motion":
                            raise ValueError("Riesz phase processing requires CUDA")
                        processor = Magnifier(**config)
                        self.device = "cpu"
                    revision = current_revision
                else:
                    processor.gain = config["gain"]
                    processor.min_detail = config.get("min_detail", 0)
                previous = now
                processing_start = time.monotonic()
                output = processor.process(frame)
                processing_ms = (time.monotonic() - processing_start) * 1000
                with self.lock:
                    if self.recording is not None:
                        self.recording.put(frame, output)
                    preview_queue.append((("original", frame), ("amplified", output)))
                preview_event.set()
                times.append(now)
                with self.lock:
                    self.state = "running"
                    self.count += 1
                    self.clipping = processor.clipped
                    self.processing_ms = processing_ms
                    if len(times) > 1:
                        self.fps = (len(times) - 1) / (times[-1] - times[0])
        except Exception as exc:
            with self.lock:
                self.state, self.error = "error", str(exc)
        finally:
            with self.lock:
                recording_active = self.recording is not None
            if recording_active:
                try:
                    self.stop_recording()
                except ValueError as exc:
                    with self.lock:
                        self.error = f"Recording failed: {exc}"
            preview_done.set()
            preview_event.set()
            preview_worker.join(timeout=3)
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
        with self.lock:
            if self.recording is not None or self.recording_stopping:
                raise ValueError("Stop recording before stopping capture")
        self.stop_event.set()
        worker = self.worker
        if worker:
            worker.join(timeout=12)
            if worker.is_alive():
                raise ValueError("Capture is still stopping; try again shortly")
        return self.status()
