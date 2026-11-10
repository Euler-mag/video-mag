export function Slider({ id, label, value, min, max, onChange, suffix = '' }) {
  return <div>
    <label htmlFor={id}>{label}<strong>{value}{suffix}</strong></label>
    <input id={id} type="range" min={min} max={max} value={value}
      onChange={event => onChange(Number(event.target.value))} />
  </div>
}

export default function FilterSettings({ prefix, settings, update }) {
  return <>
    <Slider id={prefix + '-levels'} label="Pyramid levels" min={2} max={6}
      value={settings.levels ?? 4} onChange={levels => update({ levels })} />
    <Slider id={prefix + '-order'} label="Butterworth filter order" min={1} max={4}
      value={settings.order ?? 2} onChange={order => update({ order })} />
  </>
}
