import { useEffect, useMemo, useRef, useState } from 'react'
import './VideoWorkflow.css'
import FilterSettings, { Slider } from './FilterSettings.jsx'

const API = '/api/video-jobs'

async function request(path, options) {
  const response = await fetch(path, options)
  const value = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(value.detail || 'Video processing request failed.')
  return value
}

export default function VideoWorkflow() {
  const [file, setFile] = useState(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [points, setPoints] = useState([])
  const [dragIndex, setDragIndex] = useState(-1)
  const [band, setBand] = useState({ low: 0.7, high: 3 })
  const [settings, setSettings] = useState({
    mode: 'motion', method: 'linear', zero_phase: false, gains: [5, 10, 20], levels: 4, order: 2,
    stabilize: true, margin: 20, max_shift: 18,
  })
  const updateSettings = patch => setSettings(previous => ({ ...previous, ...patch }))
  const [job, setJob] = useState(null)
  const [busy, setBusy] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [error, setError] = useState('')
  const [sourceAspect, setSourceAspect] = useState("16 / 9")
  const [position, setPosition] = useState(0)
  const stageRef = useRef(null)
  const resultRefs = useRef([])
  const hasResults = job?.completed_gains?.length > 0
  const processing = ['uploading', 'queued', 'preparing', 'magnifying'].includes(job?.state)

  useEffect(() => {
    const saved = localStorage.getItem('video-mag-job')
    if (!saved) return
    request(`${API}/${encodeURIComponent(saved)}`).then(setJob).catch(() => localStorage.removeItem('video-mag-job'))
  }, [])

  useEffect(() => {
    if (!job?.id || ['completed', 'error'].includes(job.state)) return undefined
    const timer = setInterval(() => {
      request(`${API}/${encodeURIComponent(job.id)}`).then(setJob).catch(err => setError(err.message))
    }, 1000)
    return () => clearInterval(timer)
  }, [job?.id, job?.state])

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl) }, [previewUrl])

  const resultFiles = useMemo(() => {
    if (!job?.id) return []
    const base = `${API}/${encodeURIComponent(job.id)}/files`
    return [
      { name: job.settings?.stabilize ? 'Stabilized original crop' : 'Original crop', file: 'crop_original.mp4', gain: null },
      ...(job.completed_gains ?? [])
        .map(gain => ({ name: `${job.settings?.method === "phase" ? "Phase" : "EVM"} · ${gain}×`, file: `evm_${gain}x.mp4`, gain })),
    ].filter(item => item.file === 'crop_original.mp4' ? job.files?.includes(item.file) : true)
      .map(item => ({ ...item, url: `${base}/${encodeURIComponent(item.file)}` }))
  }, [job])

  function chooseFile(next) {
    if (!next) return
    setDuration(0)
    setSourceAspect("16 / 9")
    setPosition(0)
    setPlaying(false)
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setFile(next)
    setPreviewUrl(URL.createObjectURL(next))
    setPoints([])
    setJob(null)
    setError('')
    localStorage.removeItem('video-mag-job')
  }

  function normalizedPoint(event) {
    const rect = stageRef.current.getBoundingClientRect()
    return [Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height))]
  }

  function placePoint(event) {
    if (dragIndex >= 0) {
      event.preventDefault()
      const point = normalizedPoint(event)
      setPoints(current => current.map((value, index) => index === dragIndex ? point : value))
    }
  }

  function addPoint(event) {
    if (points.length >= 4 || dragIndex >= 0) return
    setPoints(current => [...current, normalizedPoint(event)])
  }

  async function startProcessing() {
    setBusy(true)
    setError('')
    try {
      const query = new URLSearchParams({ filename: file.name, points: JSON.stringify(points),
        low: String(band.low), high: String(band.high), settings: JSON.stringify(settings) })
      const next = await request(`${API}?${query}`, { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file })
      localStorage.setItem('video-mag-job', next.id)
      setJob(next)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function clearResults() {
    if (job?.id && ['completed', 'error'].includes(job.state)) {
      try { await request(`${API}/${encodeURIComponent(job.id)}`, { method: 'DELETE' }) } catch { /* result may already have expired */ }
    }
    localStorage.removeItem('video-mag-job')
    setJob(null)
  }

  async function togglePlayback() {
    const videos = resultRefs.current.filter(Boolean)
    if (!videos.length) return
    if (playing) {
      videos.forEach(video => video.pause())
      setPlaying(false)
      return
    }
    const position = videos[0].currentTime
    videos.forEach(video => { video.currentTime = position })
    await Promise.all(videos.map(video => video.play().catch(() => {})))
    setPlaying(true)
  }

  function syncTime(index) {
    const source = resultRefs.current[index]
    if (!source) return
    setPosition(source.currentTime)
    resultRefs.current.forEach((video, other) => {
      if (video && other !== index && Math.abs(video.currentTime - source.currentTime) > 0.1) {
        video.currentTime = source.currentTime
      }
    })
  }

  function seekAll(event) {
    const time = Number(event.target.value)
    setPosition(time)
    resultRefs.current.forEach(video => { if (video) video.currentTime = time })
  }

  const ready = file && points.length === 4 && !busy && !['uploading', 'queued', 'preparing', 'magnifying'].includes(job?.state)

  return (
    <section className="video-workflow">
      <div className="workflow-heading">
        <div><p className="eyebrow">LOCAL VIDEO · WRIST MOTION</p><h2>Magnify a selected wrist crop</h2>
          <p>Choose a video, mark the wrist on its first frame, and compare algorithm results at your chosen gains.</p></div>
        <label className="file-button">{file ? 'Choose another video' : 'Choose a video'}
          <input type="file" disabled={processing || busy} accept="video/mp4,video/quicktime,video/x-matroska,video/x-msvideo,.m4v" onChange={event => chooseFile(event.target.files?.[0])} />
        </label>
      </div>

      {error && <div role="alert" className="error-message">{error}</div>}
      {job?.error && <div role="alert" className="error-message">{job.error}</div>}

      {file && !hasResults && <div className="selection-layout">
        <div className="selection-panel">
          <div className="workflow-panel-title"><strong>Wrist selection</strong><span>{file.name}</span></div>
          <div ref={stageRef} className="selection-stage"
            style={{ aspectRatio: sourceAspect }}
            onPointerMove={placePoint} onPointerUp={() => setDragIndex(-1)} onPointerCancel={() => setDragIndex(-1)}>
            <video src={previewUrl} preload="auto" onLoadedMetadata={event => {
              const { videoWidth, videoHeight } = event.currentTarget
              setSourceAspect(`${videoWidth} / ${videoHeight}`)
            }} />
            <svg className="selection-overlay" viewBox="0 0 1000 1000" preserveAspectRatio="none"
              onClick={addPoint} onPointerMove={placePoint} onPointerUp={() => setDragIndex(-1)}>
              {points.length < 4 && <rect className="selection-catcher" width="1000" height="1000" />}
              {points.length > 1 && <polyline points={points.map(point => `${point[0] * 1000},${point[1] * 1000}`).join(' ') + (points.length === 4 ? ` ${points[0][0] * 1000},${points[0][1] * 1000}` : '')} />}
              {points.map((point, index) => <g key={index} onClick={event => event.stopPropagation()} onPointerDown={event => { event.stopPropagation(); event.currentTarget.ownerSVGElement.setPointerCapture(event.pointerId); setDragIndex(index) }}>
                <circle className="selection-hit" cx={point[0] * 1000} cy={point[1] * 1000} r="38" />
                <circle className="selection-point" cx={point[0] * 1000} cy={point[1] * 1000} r="17" />
                <text x={point[0] * 1000} y={point[1] * 1000 + 6}>{index + 1}</text>
              </g>)}
            </svg>
          </div>
          <div className="selection-footer"><span>{points.length}/4 points · click clockwise around the pulse area, then drag to adjust</span>
            <button className="secondary" disabled={!points.length || busy} onClick={() => setPoints([])}>Clear points</button></div>
        </div>

        <aside className="processing-panel">
          <strong>Processing settings</strong>
          <p>Choose how to process the selected crop.</p>
          <fieldset className="processing-fields" disabled={processing || busy}>
            <label htmlFor="video-mode">Amplification mode</label>
            <select id="video-mode" value={settings.mode} onChange={event => updateSettings({ mode: event.target.value, method: 'linear', zero_phase: false })}>
              <option value="motion">Motion</option>
              <option value="color">Color</option>
            </select>
            {settings.mode === 'motion' && <>
              <label htmlFor="video-method">Motion method</label>
              <select id="video-method" value={settings.method || 'linear'} onChange={event => updateSettings({ method: event.target.value, zero_phase: false })}>
                <option value="linear">Linear EVM</option>
                <option value="phase">Steerable pyramid phase</option>
              </select>
            </>}
            {settings.mode === 'motion' && settings.method === 'phase' && <label htmlFor="zero-phase">Zero-phase filter
              <input id="zero-phase" type="checkbox" checked={settings.zero_phase}
                onChange={event => updateSettings({ zero_phase: event.target.checked })} />
            </label>}
            <label htmlFor="gain-count">Comparison outputs</label>
            <select id="gain-count" value={settings.gains.length} onChange={event => {
              const count = Number(event.target.value)
              updateSettings({ gains: [5, 10, 20].map((gain, index) => settings.gains[index] ?? gain).slice(0, count) })
            }}>
              {[1, 2, 3].map(count => <option key={count} value={count}>{count}</option>)}
            </select>
            {settings.gains.map((gain, index) => <Slider key={index} id={`video-gain-${index}`}
              label={`Added gain ${index + 1}`} value={gain} min={0} max={100} suffix="×"
              onChange={value => updateSettings({ gains: settings.gains.map((entry, i) => i === index ? value : entry) })} />)}
          <div className="band-inputs">
            <div><label htmlFor="video-low">Low · Hz</label><input id="video-low" type="number" min="0.1" max="119.9" step="0.1" value={band.low} onChange={event => setBand({ ...band, low: Number(event.target.value) })} /></div>
            <div><label htmlFor="video-high">High · Hz</label><input id="video-high" type="number" min="0.2" max="119.9" step="0.1" value={band.high} onChange={event => setBand({ ...band, high: Number(event.target.value) })} /></div>
          </div>
          <p className="workflow-note">Default band: 0.7–3 Hz. The app converts Hz to the selected video's frame rate.</p>
          <details>
            <summary>Crop and filter settings</summary>
            <Slider id="crop-margin" label="Crop margin per side" value={settings.margin} min={0} max={50}
              suffix="%" onChange={margin => updateSettings({ margin })} />
            <label htmlFor="stabilize">Stabilize crop
              <input id="stabilize" type="checkbox" checked={settings.stabilize}
                onChange={event => updateSettings({ stabilize: event.target.checked })} />
            </label>
            {settings.stabilize && <Slider id="crop-shift" label="Maximum stabilization shift"
              value={settings.max_shift} min={1} max={30} suffix="%" onChange={max_shift => updateSettings({ max_shift })} />}
            <FilterSettings prefix="video" settings={settings} update={updateSettings} />
          </details>
          </fieldset>
          <button className="primary" disabled={!ready} onClick={startProcessing}>{busy ? 'Uploading video…' : 'Start crop processing'}</button>
          {job && <div className="job-progress"><div><span>{job.state === 'magnifying' ? `EVM · ${job.current_gain}×` : job.state === 'preparing' ? 'Stabilizing crop' : job.state}</span><span>{job.progress}%</span></div><progress max="100" value={job.progress} /></div>}
          <p className="workflow-note">Run time depends on crop size, video length, and the number of outputs.</p>
        </aside>
      </div>}

      {!file && !hasResults && <div className="video-empty"><span>▱</span><strong>{processing ? 'Processing your selected wrist crop' : 'Select a local video to begin'}</strong>
        <p>{processing ? `${job.state === 'magnifying' ? `EVM · ${job.current_gain}×` : 'Stabilizing crop'} · ${job.progress}% complete` : 'Your video is uploaded to this local app for processing.'}</p>
        {processing && <progress max="100" value={job.progress} />}</div>}

      {hasResults && <div className="comparison-section">
        <div className="comparison-heading"><div><strong>Crop comparison</strong><p>All clips share the same crop, frame rate, and playback time.</p>
          {processing && <p className="workflow-running">{job.state === 'magnifying' ? `Processing ${job.current_gain}× · ${job.progress}%` : `Preparing stabilized crop · ${job.progress}%`} · finished gains: {job.completed_gains?.join('×, ') || 'none yet'}</p>}</div>
          <div className="comparison-actions"><button className="secondary" onClick={togglePlayback}>{playing ? 'Pause all' : 'Play all'}</button><button className="secondary" disabled={processing} onClick={clearResults}>Clear results</button></div></div>
        <div className="comparison-grid">
          {resultFiles.map((item, index) => <article className="comparison-card" key={item.file}>
            <div className="workflow-panel-title"><strong>{item.name}</strong>{item.gain !== null && <a href={item.url} download={item.file}>Download</a>}</div>
            <video ref={node => { resultRefs.current[index] = node }} src={item.url} muted playsInline preload="metadata"
              onLoadedMetadata={event => { if (index === 0) setDuration(event.currentTarget.duration || 0) }}
              onTimeUpdate={index === 0 ? () => syncTime(0) : undefined} onPlay={() => setPlaying(true)} onPause={() => { if (resultRefs.current.every(video => !video || video.paused)) setPlaying(false) }} />
          </article>)}
        </div>
        <div className="shared-timeline"><button className="secondary" onClick={togglePlayback}>{playing ? 'Pause' : 'Play'}</button>
          <input aria-label="Synchronized playback position" type="range" min="0" max={duration} step="0.01" value={Math.min(position, duration)} onChange={seekAll} />
          <button className="secondary" disabled={processing} onClick={clearResults}>Choose another video</button></div>
      </div>}

      {job?.state === 'error' && <div className="workflow-error-actions"><button className="secondary" onClick={clearResults}>Dismiss job</button></div>}
      <p className="workflow-footnote">Compare the original crop and amplified outputs with shared playback. Select whichever gain shows useful motion without obvious edge wobble.</p>
    </section>
  )
}
