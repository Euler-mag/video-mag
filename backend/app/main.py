import asyncio
import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .capture import Session, cameras, devices, loopbacks
from . import video_jobs

session = Session()


@asynccontextmanager
async def lifespan(app):
    yield
    session.stop()


app = FastAPI(title="Video Amplification", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])


class Settings(BaseModel):
    mode: Literal["color", "motion"] = "color"
    gain: float = Field(default=20, ge=0, le=100, allow_inf_nan=False)
    low: float = Field(default=.7, gt=0, allow_inf_nan=False)
    high: float = Field(default=3, gt=0, allow_inf_nan=False)
    levels: int = Field(default=4, ge=2, le=6)
    order: int = Field(default=2, ge=1, le=4)
    fps: Literal[30] = 30

    @model_validator(mode="after")
    def valid_band(self):
        if not self.low < self.high < self.fps / 2:
            raise ValueError("Use 0 < low < high < 15 Hz")
        return self


class VideoSettings(BaseModel):
    mode: Literal["color", "motion"] = "motion"
    gains: list[int] = Field(default_factory=lambda: [5, 10, 20], min_length=1, max_length=3)
    levels: int = Field(default=4, ge=2, le=6)
    order: int = Field(default=2, ge=1, le=4)
    stabilize: bool = True
    margin: int = Field(default=20, ge=0, le=50)
    max_shift: int = Field(default=18, ge=1, le=30)

    @model_validator(mode="after")
    def valid_gains(self):
        if len(set(self.gains)) != len(self.gains) or any(not 0 <= gain <= 100 for gain in self.gains):
            raise ValueError("Choose distinct gains between 0 and 100")
        return self


class Start(BaseModel):
    source: Literal["phone", "demo"] = "phone"
    serial: str | None = None
    camera_id: str | None = None
    sink: str | None = None


def checked(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/devices")
def get_devices():
    return {"devices": checked(devices), "loopbacks": loopbacks()}


@app.get("/api/cameras")
def get_cameras(serial: str):
    return checked(cameras, serial)


@app.get("/api/status")
def status():
    return session.status()


@app.put("/api/settings")
def settings(values: Settings):
    return checked(session.configure, values.model_dump())


@app.post("/api/start")
def start(values: Start):
    return checked(session.start, **values.model_dump())


@app.post("/api/stop")
def stop():
    return checked(session.stop)


@app.post("/api/video-jobs")
async def upload_video(request: Request):
    filename = request.query_params.get("filename", "")
    points_raw = request.query_params.get("points", "")
    low_raw, high_raw = request.query_params.get("low", ""), request.query_params.get("high", "")
    try:
        points = json.loads(points_raw)
        low, high = float(low_raw), float(high_raw)
        options = VideoSettings.model_validate_json(request.query_params.get("settings", "{}"))
        job = video_jobs.create_job(filename, points, low, high, options.model_dump())
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc

    job_id = job["id"]
    folder = video_jobs.job_path(job_id) / "source"
    folder.mkdir()
    destination = folder / job["filename"]
    written = 0
    try:
        with destination.open("wb") as output:
            async for chunk in request.stream():
                written += len(chunk)
                if written > 2 * 1024 * 1024 * 1024:
                    raise HTTPException(413, "Video exceeds the 2 GB upload limit")
                output.write(chunk)
        if written == 0:
            raise HTTPException(400, "The selected video is empty")
        video_jobs.set_uploaded(job_id)
        video_jobs.start_job(job_id)
    except BaseException:
        try:
            video_jobs.abort_upload(job_id)
        except ValueError:
            pass
        raise
    return video_jobs.status(job_id)


@app.get("/api/video-jobs/{job_id}")
def video_job_status(job_id: str):
    return checked(video_jobs.status, job_id)


@app.get("/api/video-jobs/{job_id}/files/{name}")
def video_job_file(job_id: str, name: str):
    path = checked(video_jobs.file_path, job_id, name)
    return FileResponse(path, media_type="video/mp4", filename=name,
                        content_disposition_type="inline")


@app.delete("/api/video-jobs/{job_id}")
def delete_video_job(job_id: str):
    checked(video_jobs.delete_job, job_id)
    return {"deleted": True}


@app.get("/api/stream/{kind}")
def stream(kind: Literal["original", "amplified"]):
    async def frames():
        previous = None
        while True:
            with session.lock:
                frame = session.frames[kind]
                active = session.state in ("starting", "running")
            if frame is not None and frame is not previous:
                previous = frame
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
            if not active:
                return
            await asyncio.sleep(1 / 30)
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})
