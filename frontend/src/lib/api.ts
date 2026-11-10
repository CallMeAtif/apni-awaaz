// HTTP client for the frozen contract. The real backend is the default path;
// VITE_MOCK=1 swaps in canned, contract-shaped responses for offline UI work.

import { ApiError, errorFromResponse } from './errors'
import type {
  CreatedVoice,
  Health,
  HistoryItem,
  ListenResult,
  PhraseCategory,
  Recording,
  Suggestion,
  Voice,
} from './types'

export const IS_MOCK = import.meta.env.VITE_MOCK === '1'

export interface Api {
  health(): Promise<Health>
  passage(): Promise<string>
  createVoice(rec: Recording, name: string): Promise<CreatedVoice>
  listVoices(): Promise<Voice[]>
  deleteVoice(voiceId: string): Promise<void>
  listen(rec: Recording, history: HistoryItem[]): Promise<ListenResult>
  suggest(transcript: string, history: HistoryItem[]): Promise<Suggestion[]>
  compose(text: string): Promise<string>
  speak(voiceId: string | null, text: string, signal?: AbortSignal): Promise<Blob>
  phrases(): Promise<PhraseCategory[]>
  /** Opens the backend's NVIDIA + Gnani connections (no billable call). Best effort. */
  warmup(): Promise<void>
}

// ---------- defensive normalisers (tolerate small contract drift) ----------

export function normaliseSuggestions(raw: unknown): Suggestion[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((s): Suggestion | null => {
      if (typeof s === 'string') return { text: s }
      if (s && typeof s === 'object' && typeof (s as Suggestion).text === 'string') {
        return { text: (s as Suggestion).text, intent: (s as Suggestion).intent }
      }
      return null
    })
    .filter((s): s is Suggestion => !!s && s.text.trim().length > 0)
}

function normalisePhrases(raw: unknown): PhraseCategory[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((c) => c && typeof c === 'object' && Array.isArray((c as PhraseCategory).phrases))
    .map((c) => {
      const cat = c as PhraseCategory
      return {
        category: String(cat.category ?? ''),
        category_en: cat.category_en,
        phrases: cat.phrases
          .map((p) => (typeof p === 'string' ? { text: p } : p))
          .filter((p) => p && typeof p.text === 'string' && p.text.trim()),
      }
    })
    .filter((c) => c.phrases.length > 0)
}

// ---------- fetch plumbing ----------

function withTimeout(ms: number, outer?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(ms)
  if (!outer) return timeout
  if (typeof AbortSignal.any === 'function') return AbortSignal.any([outer, timeout])
  return outer
}

async function request(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<Response> {
  const { timeoutMs = 30_000, signal, ...rest } = init
  let res: Response
  try {
    res = await fetch(path, { ...rest, signal: withTimeout(timeoutMs, signal ?? undefined) })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError' && signal?.aborted) throw err
    if (err instanceof DOMException && err.name === 'TimeoutError') {
      throw new ApiError('UPSTREAM_ERROR', 'Request timed out', 0)
    }
    throw new ApiError('NETWORK', String(err), 0)
  }
  if (!res.ok) {
    // The backend always answers with JSON errors. A non-JSON 5xx means the
    // dev proxy couldn't reach it at all.
    const isJson = (res.headers.get('content-type') ?? '').includes('json')
    if (!isJson && res.status >= 500) throw new ApiError('NETWORK', `HTTP ${res.status}`, res.status)
    throw await errorFromResponse(res)
  }
  return res
}

async function json<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const res = await request(path, init)
  return (await res.json()) as T
}

function audioForm(rec: Recording): FormData {
  const fd = new FormData()
  fd.append('audio', rec.blob, `recording.${rec.ext}`)
  return fd
}

// ---------- real implementation ----------

const realApi: Api = {
  health: () => json<Health>('/api/health', { timeoutMs: 5_000 }),

  passage: async () => (await json<{ text: string }>('/api/enroll/passage', { timeoutMs: 10_000 })).text,

  createVoice: (rec, name) => {
    const fd = audioForm(rec)
    fd.append('name', name)
    fd.append('consent', 'true')
    return json<CreatedVoice>('/api/voices', { method: 'POST', body: fd, timeoutMs: 45_000 })
  },

  listVoices: async () => {
    const v = await json<unknown>('/api/voices', { timeoutMs: 10_000 })
    return Array.isArray(v) ? (v as Voice[]) : []
  },

  deleteVoice: async (voiceId) => {
    await request(`/api/voices/${encodeURIComponent(voiceId)}`, { method: 'DELETE', timeoutMs: 10_000 })
  },

  listen: async (rec, history) => {
    const fd = audioForm(rec)
    fd.append('history', JSON.stringify(history))
    const r = await json<ListenResult>('/api/listen', { method: 'POST', body: fd, timeoutMs: 30_000 })
    return {
      transcript: typeof r.transcript === 'string' ? r.transcript : '',
      suggestions: normaliseSuggestions(r.suggestions),
      timings: r.timings,
    }
  },

  suggest: async (transcript, history) => {
    const r = await json<{ suggestions: unknown }>('/api/suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript, history }),
      timeoutMs: 20_000,
    })
    return normaliseSuggestions(r.suggestions)
  },

  compose: async (text) => {
    const r = await json<{ text: string }>('/api/compose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
      timeoutMs: 20_000,
    })
    return typeof r.text === 'string' ? r.text : ''
  },

  speak: async (voiceId, text, signal) => {
    const res = await request('/api/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_id: voiceId, text }),
      signal,
      timeoutMs: 45_000,
    })
    return res.blob()
  },

  phrases: async () => normalisePhrases(await json<unknown>('/api/phrases', { timeoutMs: 10_000 })),

  warmup: async () => {
    // Read the body: an unread fetch body keeps the request open until the timeout signal
    // aborts it (logged as net::ERR_ABORTED even though the warmup succeeded).
    await (await request('/api/warmup', { method: 'POST', timeoutMs: 10_000 })).text()
  },
}

// ---------- selection + a small client-side TTS cache ----------

let impl: Promise<Api> | null = null
function getImpl(): Promise<Api> {
  if (!impl) {
    impl = IS_MOCK ? import('./mock').then((m) => m.mockApi) : Promise.resolve(realApi)
  }
  return impl
}

const speakCache = new Map<string, Blob>()
const SPEAK_CACHE_MAX = 60

/** Same voice + same text → reuse the audio we already have (saves credits and time). */
async function speakCached(voiceId: string | null, text: string, signal?: AbortSignal): Promise<Blob> {
  const key = `${voiceId ?? '∅'}|${text}`
  const hit = speakCache.get(key)
  if (hit) return hit
  const blob = await (await getImpl()).speak(voiceId, text, signal)
  speakCache.set(key, blob)
  if (speakCache.size > SPEAK_CACHE_MAX) {
    const oldest = speakCache.keys().next().value
    if (oldest !== undefined) speakCache.delete(oldest)
  }
  return blob
}

export const api: Api = {
  health: async () => (await getImpl()).health(),
  passage: async () => (await getImpl()).passage(),
  createVoice: async (rec, name) => (await getImpl()).createVoice(rec, name),
  listVoices: async () => (await getImpl()).listVoices(),
  deleteVoice: async (id) => (await getImpl()).deleteVoice(id),
  listen: async (rec, history) => (await getImpl()).listen(rec, history),
  suggest: async (t, h) => (await getImpl()).suggest(t, h),
  compose: async (t) => (await getImpl()).compose(t),
  speak: speakCached,
  phrases: async () => (await getImpl()).phrases(),
  warmup: async () => (await getImpl()).warmup(),
}
