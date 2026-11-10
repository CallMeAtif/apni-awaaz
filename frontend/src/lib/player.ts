// One shared <audio> element for the whole app, so replies never overlap.

import { useSyncExternalStore } from 'react'
import { ApiError } from './errors'

let audio: HTMLAudioElement | null = null
let currentUrl: string | null = null
let playingId: string | null = null
let settle: (() => void) | null = null
let unlocked = false
const listeners = new Set<() => void>()

function el(): HTMLAudioElement {
  if (!audio) {
    audio = new Audio()
    audio.preload = 'auto'
  }
  return audio
}

function setPlaying(id: string | null) {
  playingId = id
  listeners.forEach((l) => l())
}

function silentWav(): string {
  // 0.05 s of 8 kHz mono silence; enough to satisfy Safari's gesture unlock.
  const samples = 400
  const buf = new ArrayBuffer(44 + samples * 2)
  const v = new DataView(buf)
  const w = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)))
  w(0, 'RIFF')
  v.setUint32(4, 36 + samples * 2, true)
  w(8, 'WAVE')
  w(12, 'fmt ')
  v.setUint32(16, 16, true)
  v.setUint16(20, 1, true)
  v.setUint16(22, 1, true)
  v.setUint32(24, 8000, true)
  v.setUint32(28, 16000, true)
  v.setUint16(32, 2, true)
  v.setUint16(34, 16, true)
  w(36, 'data')
  v.setUint32(40, samples * 2, true)
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }))
}

/**
 * Call synchronously inside a tap handler, before any await. Safari only lets
 * an element play later (after a fetch) if it was started during a gesture.
 */
export function unlockAudio(): void {
  if (unlocked) return
  unlocked = true
  const a = el()
  if (playingId) return
  a.src = silentWav()
  a.play().catch(() => {})
}

/** Stop whatever is playing. A pending playBlob() resolves (not rejects). */
export function stopAudio(): void {
  const a = el()
  a.pause()
  a.onended = null
  a.onerror = null
  if (currentUrl) {
    URL.revokeObjectURL(currentUrl)
    currentUrl = null
  }
  const s = settle
  settle = null
  setPlaying(null)
  s?.()
}

/** Play a blob on the shared element. Resolves when it ends or is interrupted. */
export function playBlob(blob: Blob, id: string): Promise<void> {
  stopAudio()
  const a = el()
  const url = URL.createObjectURL(blob)
  currentUrl = url
  a.src = url
  setPlaying(id)
  return new Promise<void>((resolve, reject) => {
    const finish = () => {
      if (settle !== finish) return
      settle = null
      a.onended = null
      a.onerror = null
      if (currentUrl === url) {
        URL.revokeObjectURL(url)
        currentUrl = null
      }
      setPlaying(null)
      resolve()
    }
    settle = finish
    a.onended = finish
    a.onerror = () => {
      if (settle !== finish) return
      settle = null
      setPlaying(null)
      reject(new ApiError('PLAYBACK', 'Audio element error'))
    }
    a.play().catch((err: unknown) => {
      if (settle !== finish) return // interrupted by a newer play/stop
      if (err instanceof DOMException && err.name === 'AbortError') return finish()
      settle = null
      setPlaying(null)
      reject(new ApiError('PLAYBACK', String(err)))
    })
  })
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

/** The id passed to the playBlob() call that is currently audible, or null. */
export function usePlayingId(): string | null {
  return useSyncExternalStore(subscribe, () => playingId)
}
