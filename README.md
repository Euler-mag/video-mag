# Video Amplification

Local Fedora KDE prototype with two paths: **Process a video** uploads a local
clip to the localhost app, lets you mark a wrist crop, stabilizes that crop, and
compares classical Eulerian color or motion amplification at up to three gains. **Live camera** uses
Galaxy S24 rear camera → scrcpy over USB/ADB → V4L2 loopback → FFmpeg → causal
Eulerian magnification. A synthetic source is available on the live camera path.

## Run

Dependencies are installed locally for this workspace. Start both servers:

```bash
./run.sh
```

Open http://127.0.0.1:5173. Stop with Ctrl+C. Logs are in `.runtime/`.
The servers bind to localhost only.

For a fresh checkout (Python 3.12 or newer, Node compatible with Vite 8):

```bash
python -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
cd frontend
npm ci
cd ..
./run.sh
```

The phone source also requires `adb`, `scrcpy` with camera/V4L2 support,
`ffmpeg`, `v4l2-ctl`, `fuser`, and an existing V4L2 loopback device. Fedora
packages include `android-tools`, `scrcpy`, `v4l-utils`, and `psmisc`.
V4L2 loopback installation/loading depends on the host kernel; the app does not
install, unload or reconfigure kernel modules. On the development laptop,
`/dev/video0` is an existing OBS Virtual Camera loopback. It must be unused;
the app refuses to take a device with existing users.

## Connect the S24

1. Enable Developer options and USB debugging on the phone.
2. Connect a USB data cable, unlock the phone, accept its debugging prompt.
3. Close any phone app already using the camera.
4. Choose **S24 · USB**, click **Refresh**, then **Find lenses**.
5. Select the main rear wide camera from Android's listed camera IDs. Automatic
   rear selection is available, but does not guarantee the specific main lens.
6. Select the unused virtual camera and click **Start capture**.
7. Choose **Color amp** or **Motion amp**. Change gain/band and click
   **Apply settings**. Mode changes apply immediately and reset filter state.

Capture requests 30 FPS, H.264 at 20 Mbps and a camera size up to 1280 pixels.
Processing uses 640×360 with aspect-preserving letterboxing. Startup failure,
USB disconnection and decoder failure appear in the interface and capture log.
Stop terminates only the scrcpy/FFmpeg children started by this app.

## Process a local wrist video

1. Select **Process a video**, then choose an MP4, MOV, MKV, AVI, or M4V file.
2. On the video's first frame, click four points clockwise around the pulse
   area. Drag points to refine the selection. Keep a little surrounding skin
   texture inside the margin.
3. Set the temporal band in Hz. The default is 0.7–3 Hz; the backend converts
   it using the source video's measured FPS.
4. Start processing. The backend uploads the file to local `.runtime/video-jobs`,
   optionally registers the crop to its first frame, then runs EVM at the selected gains.
5. Compare the stabilized original crop with the three amplified crops using
   the shared Play/Pause and seek controls. Download each amplified result.

Only the selected, margin-expanded rectangular crop is stabilized and sent to
the EVM algorithm. The rest of the original frame is excluded from stabilization and
amplification and does not appear in the outputs.
The original crop and each model result retain the input frame rate. Completed
job files remain under `.runtime/video-jobs` until **Clear results** is used.
After successful crop preparation, the uploaded full video copy is removed.
Both processing paths use classical EVM. The UI exposes amplification mode,
gain, frequency band, pyramid depth, and Butterworth filter order. Recorded
video settings also include the number of outputs, crop margin, stabilization
toggle, and maximum stabilization shift. No AI runtime or weights are needed.

## Algorithms and limits

- **Color:** Gaussian-pyramid reduction, causal second-order Butterworth
  band-pass, upsampled band signal added to the original BGR channels.
- **Motion:** Laplacian-pyramid Eulerian baseline; band-pass intermediate
  spatial bands with gain attenuated at finer scales. Finest detail and coarse
  residual remain unchanged. This is **not phase-based motion magnification**
  and does not reproduce all scale/gain rules of the original EVM implementation.
- Gain is the **added filtered-signal gain**, not a guaranteed physical motion
  multiplier. Gain zero returns the original exactly before preview encoding.
- Each spatial filter has persistent causal state; state resets on settings
  changes and arrival gaps over 250 ms. Allow several seconds to settle.
- Frequency settings require `0 < low < high < 15 Hz` for the 30 FPS target.
- The previews are JPEG/MJPEG representations of processed frames. No recording
  export, live ROI editor, physiological measurements or RAW capture is implemented.
- V4L2/FFmpeg ingestion currently uses local frame-arrival timing rather than
  preserved sensor timestamps. The filter assumes uniform 30 FPS. Low delivery
  rates, duplicated frames and camera processing can invalidate frequency
  interpretation. The UI reports processing rate and clipping; this is a
  qualitative preview, not a validated scientific instrument.
- Mount the phone steadily, use stable illumination and keep gain conservative.
  scrcpy does not expose manual exposure/focus/white-balance locking here.
- Memory is bounded by current frames, pyramid/filter state and OS pipe buffers;
  frames are not accumulated for an entire recording.

Research foundation: [Wu et al., Eulerian Video Magnification, 2012](https://people.csail.mit.edu/mrub/evm/).
Capture reference: [scrcpy camera](https://github.com/Genymobile/scrcpy/blob/master/doc/camera.md)
and [V4L2 output](https://github.com/Genymobile/scrcpy/blob/master/doc/v4l2.md).

## Verify

```bash
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
cd ../frontend
npm run build
npm run lint
```

Tests cover zero-gain identity, static-scene stability, passband/stopband
response, known color amplification, subpixel-motion response, settings
validation, and demo lifecycle. These checks do not establish actual S24 camera
quality, sensor timing, or measurement accuracy.

## Development verification (10 October 2026)

11 automated tests passed; frontend build and lint passed. Browser checks
confirmed both live previews and switching between color and motion modes.
A generated video exercised the actual V4L2 → FFmpeg → EVM → JPEG path at
approximately 30 FPS, with owned processes cleaned up after Stop. HTTP checks
confirmed invalid-band rejection, missing-phone errors and MJPEG closing on Stop.
The S24 was not connected during these checks; its camera capture remains to be
verified on the actual device.
