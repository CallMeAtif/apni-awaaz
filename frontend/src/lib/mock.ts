// DEV-ONLY mock of the backend contract, enabled with VITE_MOCK=1.
// Canned, contract-shaped data with realistic delays. Never used by default,
// and the UI shows a MOCK badge whenever it is active.

import { ApiError } from './errors'
import type { Api } from './api'
import type { CreatedVoice, HistoryItem, PhraseCategory, Suggestion, Voice } from './types'

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(t)
      reject(new DOMException('Aborted', 'AbortError'))
    })
  })

const STT_MS = 600
const LLM_MS = 1000
const SPEAK_MS = 2000

// Persist mock voices for the tab session so reloads behave like a real server.
const VOICES_KEY = 'apni-awaaz.mock-voices'
const voices: Voice[] = (() => {
  try {
    return JSON.parse(sessionStorage.getItem(VOICES_KEY) ?? '[]') as Voice[]
  } catch {
    return []
  }
})()
const persist = () => {
  try {
    sessionStorage.setItem(VOICES_KEY, JSON.stringify(voices))
  } catch {
    /* ignore */
  }
}

const PASSAGE =
  'नमस्ते, मेरा नाम मेरी पहचान है। सुबह की चाय, बच्चों की हँसी और शाम की ठंडी हवा मुझे बहुत अच्छी लगती है। ' +
  'मैं चाहता हूँ कि मेरे अपने लोग मेरी बात हमेशा मेरी ही आवाज़ में सुनें। ' +
  'आज मौसम साफ़ है, बाज़ार में ताज़ी सब्ज़ियाँ आई हैं, और घर में सब ख़ुश हैं।'

const FAMILY_LINES: { transcript: string; suggestions: Suggestion[] }[] = [
  {
    transcript: 'पापा, आज खाने में क्या बनाऊँ? दाल चावल चलेगा?',
    suggestions: [
      { text: 'हाँ बेटा, दाल चावल बढ़िया रहेगा।', intent: 'agree' },
      { text: 'आज खिचड़ी बना दो, हल्का खाना चाहिए।', intent: 'alternative' },
      { text: 'जो तुम्हें ठीक लगे, बेटा।', intent: 'defer' },
    ],
  },
  {
    transcript: 'Papa, दवाई ली आपने?',
    suggestions: [
      { text: 'हाँ, सुबह वाली ले ली।', intent: 'confirm' },
      { text: 'नहीं, अभी दे दो।', intent: 'request' },
      { text: 'याद नहीं, एक बार देख लो।', intent: 'unsure' },
    ],
  },
]
let lineIdx = 0

const MORE: Suggestion[] = [
  { text: 'थोड़ी देर बाद खाऊँगा।', intent: 'later' },
  { text: 'साथ में दही भी हो तो अच्छा।', intent: 'add' },
  { text: 'शुक्रिया बेटा।', intent: 'thanks' },
]

const COMPOSE: Record<string, string> = {
  'thoda paani de do': 'थोड़ा पानी दे दो',
  'mujhe neend aa rahi hai': 'मुझे नींद आ रही है',
  'dard ho raha hai': 'दर्द हो रहा है',
}

const PHRASES: PhraseCategory[] = [
  {
    category: 'ज़रूरतें',
    category_en: 'Needs',
    phrases: [
      { text: 'पानी चाहिए', en: 'I need water', emoji: '💧' },
      { text: 'भूख लगी है', en: "I'm hungry", emoji: '🍲' },
      { text: 'बाथरूम जाना है', en: 'I need the bathroom', emoji: '🚻' },
      { text: 'चाय पीनी है', en: "I'd like tea", emoji: '☕' },
      { text: 'आराम करना है', en: 'I want to rest', emoji: '🛏️' },
      { text: 'चश्मा दे दो', en: 'Pass my glasses', emoji: '👓' },
    ],
  },
  {
    category: 'तबीयत',
    category_en: 'Health',
    phrases: [
      { text: 'दर्द हो रहा है', en: "I'm in pain", emoji: '🤕' },
      { text: 'साँस लेने में तकलीफ़ है', en: "It's hard to breathe", emoji: '🫁' },
      { text: 'दवाई का समय हो गया', en: "It's medicine time", emoji: '💊' },
      { text: 'डॉक्टर को बुलाओ', en: 'Call the doctor', emoji: '🩺' },
    ],
  },
  {
    category: 'भावनाएँ',
    category_en: 'Feelings',
    phrases: [
      { text: 'मैं ठीक हूँ', en: "I'm okay", emoji: '🙂' },
      { text: 'मुझे तुम पर गर्व है', en: "I'm proud of you", emoji: '❤️' },
      { text: 'थोड़ा अकेला लग रहा है', en: 'I feel a little lonely', emoji: '🫂' },
    ],
  },
  {
    category: 'जवाब',
    category_en: 'Replies',
    phrases: [
      { text: 'हाँ', en: 'Yes', emoji: '👍' },
      { text: 'नहीं', en: 'No', emoji: '✋' },
      { text: 'शुक्रिया', en: 'Thank you', emoji: '🙏' },
      { text: 'एक मिनट रुको', en: 'Wait a minute', emoji: '⏳' },
    ],
  },
]

export const mockApi: Api = {
  async health() {
    await sleep(80)
    return { ok: true, gnani_key: true, llm_key: true }
  },

  async passage() {
    await sleep(150)
    return PASSAGE
  },

  async createVoice(rec, name): Promise<CreatedVoice> {
    await sleep(1100)
    if (rec.durationS < 5) throw new ApiError('AUDIO_TOO_SHORT', 'Need at least 5 s of speech', 400)
    const v: CreatedVoice = {
      voice_id: `mock-${Math.random().toString(36).slice(2, 10)}`,
      name,
      duration_s: Math.min(30, Math.round(rec.durationS * 10) / 10),
      created_at: new Date().toISOString(),
    }
    voices.push({ voice_id: v.voice_id, name: v.name, created_at: v.created_at })
    persist()
    return v
  },

  async listVoices() {
    await sleep(120)
    return [...voices]
  },

  async deleteVoice(voiceId) {
    await sleep(150)
    const i = voices.findIndex((v) => v.voice_id === voiceId)
    if (i >= 0) voices.splice(i, 1)
    persist()
  },

  async listen(rec, _history: HistoryItem[]) {
    await sleep(STT_MS)
    if (rec.durationS < 0.6) throw new ApiError('AUDIO_TOO_SHORT', 'Audio too short', 400)
    const line = FAMILY_LINES[lineIdx++ % FAMILY_LINES.length]
    await sleep(LLM_MS)
    return { ...line, timings: { stt_ms: STT_MS, llm_ms: LLM_MS } }
  },

  async suggest() {
    await sleep(LLM_MS)
    return MORE
  },

  async compose(text) {
    await sleep(LLM_MS)
    return COMPOSE[text.trim().toLowerCase()] ?? `[MOCK] ${text}`
  },

  async speak(_voiceId, _text, signal) {
    await sleep(SPEAK_MS, signal)
    const res = await fetch('/mock/reply.wav', { signal })
    return res.blob()
  },

  async phrases() {
    await sleep(200)
    return PHRASES
  },

  async warmup() {
    await sleep(50)
  },
}
