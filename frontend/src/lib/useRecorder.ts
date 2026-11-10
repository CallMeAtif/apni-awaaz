import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './errors'
import type { Recording } from './types'

const MIME_CANDIDATES: [string, string][] = [
  ['audio/webm;codecs=opus', 'webm'],
  ['audio/webm', 'webm'],
  ['audio/mp4;codecs=mp4a.40.2', 'mp4'], // Safari
  ['audio/mp4', 'mp4'],
  ['audio/ogg;codecs=opus', 'ogg'],
]

function pickMime(): { mimeType?: string; ext: string } {
  if (typeof MediaRecorder === 'undefined') return { ext: 'webm' }
  for (const [mimeType, ext] of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(mimeType)) return { mimeType, ext }
  }
  return { ext: 'webm' }
}

function extFromMime(mime: string, fallback: string): string {
  if (mime.includes('webm')) return 'webm'
  if (mime.includes('mp4') || mime.includes('aac')) return 'mp4'
  if (mime.includes('ogg')) return 'ogg'
  if (mime.includes('wav')) return 'wav'
  return fallback
}

/** Downsample the level history into a fixed number of bars (max per bucket). */
function toPeaks(levels: number[], bars = 48): number[] {
  if (levels.length === 0) return Array.from({ length: bars }, () => 0.08)
  const out: number[] = []
  for (let i = 0; i < bars; i++) {
    const a = Math.floor((i * levels.length) / bars)
    const b = Math.max(a + 1, Math.floor(((i + 1) * levels.length) / bars))
    let m = 0
    for (let j = a; j < b && j < levels.length; j++) m = Math.max(m, levels[j])
    out.push(Math.round(Math.max(0.08, m) * 100) / 100)
  }
  return out
}

export type RecorderStatus = 'idle' | 'starting' | 'recording'

interface Options {
  maxSeconds: number
  /** Delivered after every stop, manual or automatic. */
  onComplete: (rec: Recording) => void
  onError?: (err: ApiError) => void
  /** Turn browser echo cancellation off for cleaner voice-banking samples. */
  raw?: boolean
}

const TICK_MS = 50
const SAMPLE_EVERY = 2 // keep one level sample per 100 ms for the signature

export function useRecorder({ maxSeconds, onComplete, onError, raw = false }: Options) {
  const [status, setStatus] = useState<RecorderStatus>('idle')
  const [elapsed, setElapsed] = useState(0)
  const [level, setLevel] = useState(0)
  const [history, setHistory] = useState<number[]>([])

  const cbRef = useRef({ onComplete, onError })
  useEffect(() => {
    cbRef.current = { onComplete, onError }
  }, [onComplete, onError])

  const live = useRef<{
    stream: MediaStream
    recorder: MediaRecorder
    ctx: AudioContext | null
    timer: number
    t0: number
    levels: number[]
    discard: boolean
  } | null>(null)
  /** True between start() and the mic actually opening. A tap in that window cancels. */
  const starting = useRef(false)
  const abortStart = useRef(false)

  const release = useCallback(() => {
    const l = live.current
    if (!l) return
    window.clearInterval(l.timer)
    l.stream.getTracks().forEach((t) => t.stop()) // release the mic every time
    l.ctx?.close().catch(() => {})
    live.current = null
  }, [])

  const stop = useCallback(() => {
    if (starting.current) {
      abortStart.current = true
      return
    }
    const l = live.current
    if (!l || l.recorder.state === 'inactive') return
    l.recorder.stop()
  }, [])

  const cancel = useCallback(() => {
    if (starting.current) abortStart.current = true
    const l = live.current
    if (!l) return
    l.discard = true
    if (l.recorder.state !== 'inactive') l.recorder.stop()
    else release()
    setStatus('idle')
  }, [release])

  const start = useCallback(async () => {
    if (live.current || starting.current) return
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      cbRef.current.onError?.(new ApiError('MIC_UNAVAILABLE', 'MediaRecorder not supported'))
      return
    }
    starting.current = true
    abortStart.current = false
    setStatus('starting')
    setElapsed(0)
    setLevel(0)
    setHistory([])

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: !raw,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
    } catch (err) {
      starting.current = false
      setStatus('idle')
      const name = err instanceof DOMException ? err.name : ''
      const code = name === 'NotAllowedError' || name === 'SecurityError' ? 'MIC_DENIED' : 'MIC_UNAVAILABLE'
      cbRef.current.onError?.(new ApiError(code, String(err)))
      return
    }

    const picked = pickMime()
    let recorder: MediaRecorder
    try {
      recorder = new MediaRecorder(stream, picked.mimeType ? { mimeType: picked.mimeType } : undefined)
    } catch {
      recorder = new MediaRecorder(stream)
    }

    // Live input level via an analyser (best effort; recording works without it).
    let ctx: AudioContext | null = null
    let analyser: AnalyserNode | null = null
    try {
      ctx = new AudioContext()
      const src = ctx.createMediaStreamSource(stream)
      analyser = ctx.createAnalyser()
      analyser.fftSize = 1024
      src.connect(analyser)
    } catch {
      ctx = null
    }
    const buf = new Float32Array(1024)

    const chunks: Blob[] = []
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data)
    }

    const t0 = performance.now()
    let tick = 0
    let smooth = 0
    const timer = window.setInterval(() => {
      const secs = (performance.now() - t0) / 1000
      setElapsed(secs)
      if (analyser) {
        analyser.getFloatTimeDomainData(buf)
        let sum = 0
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i]
        const rms = Math.sqrt(sum / buf.length)
        const v = Math.min(1, Math.sqrt(rms) * 1.9)
        smooth = Math.max(v, smooth * 0.82)
        setLevel(smooth)
        if (tick++ % SAMPLE_EVERY === 0 && live.current) {
          live.current.levels.push(smooth)
          setHistory((h) => (h.length > 400 ? [...h.slice(-300), smooth] : [...h, smooth]))
        }
      }
      if (secs >= maxSeconds) stop()
    }, TICK_MS)

    live.current = { stream, recorder, ctx, timer, t0, levels: [], discard: false }

    recorder.onstop = () => {
      const l = live.current
      const durationS = l ? (performance.now() - l.t0) / 1000 : 0
      const levels = l?.levels ?? []
      const discard = l?.discard ?? false
      release()
      setStatus('idle')
      setLevel(0)
      if (discard) return
      const mime = recorder.mimeType || picked.mimeType || 'audio/webm'
      const blob = new Blob(chunks, { type: mime })
      cbRef.current.onComplete({
        blob,
        ext: extFromMime(mime, picked.ext),
        durationS: Math.min(durationS, maxSeconds),
        peaks: toPeaks(levels),
      })
    }

    starting.current = false
    if (abortStart.current) {
      // The user tapped again before the mic opened: treat it as "never mind".
      live.current.discard = true
      release()
      setStatus('idle')
      return
    }
    recorder.start(250)
    setStatus('recording')
  }, [maxSeconds, raw, release, stop])

  // Never leave the mic open if the screen unmounts mid-recording.
  useEffect(
    () => () => {
      if (live.current) {
        live.current.discard = true
        try {
          if (live.current.recorder.state !== 'inactive') live.current.recorder.stop()
        } catch {
          /* ignore */
        }
        release()
      }
    },
    [release],
  )

  return { status, elapsed, level, history, start, stop, cancel }
}
