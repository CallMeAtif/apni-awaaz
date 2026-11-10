// localStorage can throw (private mode, blocked storage). Every access is guarded.

const KEY = 'apni-awaaz.voice.v1'

export interface StoredVoice {
  /** null means "use the standard voice" (backend falls back to Prakash). */
  voice_id: string | null
  name: string
  peaks?: number[]
}

export function loadVoice(): StoredVoice | null {
  try {
    const raw = window.localStorage.getItem(KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as StoredVoice
    if (typeof v !== 'object' || v === null || !('voice_id' in v)) return null
    return v
  } catch {
    return null
  }
}

export function saveVoice(v: StoredVoice): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(v))
  } catch {
    /* storage unavailable: the session still works, it just won't be remembered */
  }
}

export function clearVoice(): void {
  try {
    window.localStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}

// Voice signatures (input-level envelopes) per voice_id, so the chip can show
// the right waveform after switching voices.
const PEAKS_KEY = 'apni-awaaz.peaks.v1'

function readPeaksMap(): Record<string, number[]> {
  try {
    const raw = window.localStorage.getItem(PEAKS_KEY)
    return raw ? (JSON.parse(raw) as Record<string, number[]>) : {}
  } catch {
    return {}
  }
}

export function loadPeaks(voiceId: string | null): number[] | undefined {
  if (!voiceId) return undefined
  const p = readPeaksMap()[voiceId]
  return Array.isArray(p) ? p : undefined
}

export function savePeaks(voiceId: string, peaks: number[]): void {
  try {
    const map = readPeaksMap()
    map[voiceId] = peaks
    window.localStorage.setItem(PEAKS_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
}

export function deletePeaks(voiceId: string): void {
  try {
    const map = readPeaksMap()
    delete map[voiceId]
    window.localStorage.setItem(PEAKS_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
}
