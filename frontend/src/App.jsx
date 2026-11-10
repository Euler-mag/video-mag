import { useEffect, useState } from 'react'
import './App.css'
import FilterSettings from './FilterSettings.jsx'
import VideoWorkflow from './VideoWorkflow.jsx'

async function api(path, method = 'GET', body) {
  const response = await fetch(`/api/${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the frequency band and gain settings.')
  return data
}

export default function App() {
  const [workspaceMode, setWorkspaceMode] = useState('video')
  const [status, setStatus] = useState({ state: 'idle', frames: 0, fps: 0, clipping: 0 })
  const [inventory, setInventory] = useState({ devices: [], loopbacks: [] })
  const [source, setSource] = useState('phone')
  const [serial, setSerial] = useState('')
  const [sink, setSink] = useState('')
  const [cameras, setCameras] = useState([])
  const [cameraId, setCameraId] = useState('')
  const [cameraDetails, setCameraDetails] = useState('')
  const [settings, setSettings] = useState({ mode: 'color', gain: 20, low: 0.7, high: 3, fps: 30 })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [streamKey, setStreamKey] = useState(0)
  const active = ['starting', 'running'].includes(status.state)

  async function refresh() {
    const data = await api('devices')
    setInventory(data)
    const nextSerial = data.devices.some(device => device.serial === serial) ? serial : data.devices.find(device => device.state === 'device')?.serial || ''
    if (nextSerial !== serial) { setCameras([]); setCameraId(''); setCameraDetails('') }
    setSerial(nextSerial)
    setSink(previous => data.loopbacks.some(device => device.path === previous) ? previous : data.loopbacks[0]?.path || '')
  }

  useEffect(() => {
    let mounted = true
    let initialized = false
    async function poll() {
      try {
        const data = await api('status')
        if (mounted) {
          setStatus(data)
          if (!initialized) {
            setSettings(data.config)
            if (data.source) setSource(data.source)
            initialized = true
          }
          setError(previous => previous === 'Backend unavailable. Start the local server.' ? '' : previous)
        }
      } catch {
        if (mounted) setError('Backend unavailable. Start the local server.')
      }
    }
    poll()
    api('devices').then(data => {
      if (!mounted) return
      setInventory(data)
      setSerial(data.devices.find(device => device.state === 'device')?.serial || '')
      setSink(data.loopbacks[0]?.path || '')
    }).catch(err => { if (mounted) setError(err.message) })
    const timer = setInterval(poll, 1000)
    return () => { mounted = false; clearInterval(timer) }
  }, [])

  async function action(task) {
    setBusy(true); setError('')
    try { await task() } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  async function start() {
    const applied = await api('settings', 'PUT', settings)
    setSettings(applied.config)
    const data = await api('start', 'POST', { source, serial, sink, camera_id: cameraId || null })
    setStatus(data); setStreamKey(key => key + 1)
  }

  async function chooseMode(mode) {
    const values = { ...settings, mode, gain: mode === 'motion' ? 10 : 20 }
    const data = await api('settings', 'PUT', values)
    setSettings(data.config)
  }

  const selectedPhone = inventory.devices.find(device => device.serial === serial)
  const canStart = source === 'demo' || (selectedPhone?.state === 'device' && sink)
  return (
    <main className="app-shell">
      <header>
        <div><p className="eyebrow">VIDEO MAGNIFICATION / LOCAL LAB</p><h1>Make the subtle visible.</h1></div>
        <div className="workspace-switch"><button className={workspaceMode === 'video' ? 'selected' : ''} onClick={() => setWorkspaceMode('video')}>Process a video</button>
          <button className={workspaceMode === 'camera' ? 'selected' : ''} onClick={() => setWorkspaceMode('camera')}>Live camera</button>
          {workspaceMode === 'camera' && <span className={`status ${status.state}`}><i />{status.state === 'running' ? 'Live' : status.state}</span>}</div>
      </header>
      {workspaceMode === 'video' ? <VideoWorkflow /> : <div className="workspace">
        <aside className="controls">
          <section>
            <h2>Camera source</h2>
            <div className="segmented source-toggle">
              <button disabled={active || busy} className={source === 'phone' ? 'selected' : ''} onClick={() => setSource('phone')}>S24 · USB</button>
              <button disabled={active || busy} className={source === 'demo' ? 'selected' : ''} onClick={() => setSource('demo')}>Synthetic demo</button>
            </div>
            {source === 'phone' ? <>
              <label htmlFor="phone">USB device</label>
              <select id="phone" value={serial} disabled={active} onChange={event => { setSerial(event.target.value); setCameras([]); setCameraId(''); setCameraDetails('') }}>
                {!inventory.devices.length && <option value="">No phone connected</option>}
                {inventory.devices.map(device => <option key={device.serial} value={device.serial}>{device.name} · {device.state}</option>)}
              </select>
              <div className="button-row">
                <button className="secondary" disabled={active || busy} onClick={() => action(refresh)}>Refresh</button>
                <button className="secondary" disabled={!serial || active || busy} onClick={() => action(async () => {
                  const data = await api(`cameras?serial=${encodeURIComponent(serial)}`)
                  setCameras(data.cameras); setCameraDetails(data.details)
                })}>Find lenses</button>
              </div>
              <label htmlFor="lens">Camera lens</label>
              <select id="lens" value={cameraId} disabled={active} onChange={event => setCameraId(event.target.value)}>
                <option value="">Automatic rear camera</option>
                {cameras.map(camera => <option key={camera.id} value={camera.id}>{camera.id} · {camera.description}</option>)}
              </select>
              {cameraDetails && <details><summary>Camera capabilities</summary><pre>{cameraDetails}</pre></details>}
              <label htmlFor="sink">Virtual camera</label>
              <select id="sink" value={sink} disabled={active} onChange={event => setSink(event.target.value)}>
                {!inventory.loopbacks.length && <option value="">No V4L2 loopback available</option>}
                {inventory.loopbacks.map(device => <option key={device.path} value={device.path}>{device.path} · {device.name}</option>)}
              </select>
              {!serial && <p className="notice">Connect your phone, enable USB debugging and accept the authorization prompt. Then refresh.</p>}
            </> : <p className="notice">A 1.2 Hz color signal on the left and a subtly moving stripe on the right. Generated locally; this is not a phone feed.</p>}
            <button className="primary" disabled={busy || (!active && !canStart)} onClick={() => action(active ? async () => setStatus(await api('stop', 'POST')) : start)}>{busy ? 'Please wait…' : active ? 'Stop capture' : 'Start capture'}</button>
          </section>
          <section>
            <h2>Processing settings</h2>
            <div className="segmented modes">
              <button className={settings.mode === 'color' ? 'selected' : ''} disabled={busy} onClick={() => action(() => chooseMode('color'))}>Color amp</button>
              <button className={settings.mode === 'motion' ? 'selected' : ''} disabled={busy} onClick={() => action(() => chooseMode('motion'))}>Motion amp</button>
            </div>
            <label htmlFor="gain">Added gain <strong>{settings.gain}×</strong></label>
            <input id="gain" type="range" min="0" max="100" value={settings.gain} onChange={event => setSettings({ ...settings, gain: Number(event.target.value) })} />
            <div className="band-inputs">
              <div><label htmlFor="low">Low · Hz</label><input id="low" type="number" min="0.05" max="14.9" step="0.1" value={settings.low} onChange={event => setSettings({ ...settings, low: Number(event.target.value) })} /></div>
              <div><label htmlFor="high">High · Hz</label><input id="high" type="number" min="0.1" max="14.9" step="0.1" value={settings.high} onChange={event => setSettings({ ...settings, high: Number(event.target.value) })} /></div>
            </div>
            <details>
              <summary>Advanced filter settings</summary>
              <FilterSettings prefix="live" settings={settings}
                update={patch => setSettings(previous => ({ ...previous, ...patch }))} />
            </details>
            <button className="secondary wide" disabled={busy} onClick={() => action(async () => { const data = await api('settings', 'PUT', settings); setSettings(data.config) })}>Apply settings</button>
            <p className="notice">{settings.mode === 'color' ? 'Eulerian color magnification.' : 'Laplacian Eulerian motion magnification.'} Allow a few seconds for the causal filter to settle after changing settings.</p>
          </section>
        </aside>
        <div className="view-area">
          {(error || status.error) && <div role="alert" className="error-message">{error || status.error}</div>}
          <div className="previews">
            {['original', 'amplified'].map(kind => <section className="preview" key={kind}>
              <div className="preview-title"><h2>{kind === 'original' ? 'Original' : 'Amplified'}</h2><span>{kind === 'original' ? 'INPUT' : settings.mode.toUpperCase()}</span></div>
              <div className="video-surface">
                {status.frames > 0 ? <img key={streamKey} src={`/api/stream/${kind}?session=${streamKey}`} alt={`${kind} live video`} /> : <div className="empty-preview"><span>◉</span><p>{status.state === 'starting' ? 'Opening camera…' : 'Ready for capture'}</p></div>}
              </div>
            </section>)}
          </div>
          <div className="metrics">
            <div><span>Processing rate</span><strong>{status.fps} <small>FPS</small></strong></div>
            <div><span>Frames processed</span><strong>{status.frames}</strong></div>
            <div><span>Output clipping</span><strong>{status.clipping}<small>%</small></strong></div>
            <div><span>Processing size</span><strong>640 <small>×</small> 360</strong></div>
          </div>
          {status.state === 'running' && status.frames > 90 && status.fps < 27 && <p className="warning">Frame delivery is below the configured 30 FPS. Frequency filtering may be inaccurate; this feed is a qualitative preview.</p>}
          {status.clipping > 1 && <p className="warning">Output is clipping. Reduce gain for a cleaner result.</p>}
          <p className="footnote">30 FPS capture target · local processing · frame-arrival timing · preview only, not a validated measurement</p>
          {!!status.logs?.length && <details className="capture-log"><summary>Capture log</summary><pre>{status.logs.join('\n')}</pre></details>}
        </div>
      </div>}
    </main>
  )
}
