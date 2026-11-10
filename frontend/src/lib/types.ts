// Shapes from the frozen HTTP contract (docs/TEAM_BRIEF.md §5).

export type ErrorCode =
  | 'AUDIO_TOO_SHORT'
  | 'AUDIO_TOO_LONG'
  | 'BAD_AUDIO'
  | 'OUT_OF_CREDITS'
  | 'UPSTREAM_ERROR'
  | 'NOT_FOUND'
  | 'BAD_REQUEST'
  // Client-side codes (never sent by the backend):
  | 'NETWORK'
  | 'MIC_DENIED'
  | 'MIC_UNAVAILABLE'
  | 'PLAYBACK'
  | 'UNKNOWN'

export interface Health {
  ok: boolean
  gnani_key: boolean
  llm_key: boolean
}

export interface Voice {
  voice_id: string
  name: string
  created_at: string
}

export interface CreatedVoice extends Voice {
  duration_s: number
}

export interface Suggestion {
  text: string
  intent?: string
}

export interface ListenResult {
  transcript: string
  suggestions: Suggestion[]
  timings?: { stt_ms?: number; llm_ms?: number }
}

export interface HistoryItem {
  role: 'family' | 'user'
  text: string
}

export interface Phrase {
  text: string
  en?: string
  emoji?: string
}

export interface PhraseCategory {
  category: string
  category_en?: string
  phrases: Phrase[]
}

export interface Recording {
  blob: Blob
  ext: string
  durationS: number
  /** Normalised 0..1 input levels sampled during recording (the "voice signature"). */
  peaks: number[]
}
