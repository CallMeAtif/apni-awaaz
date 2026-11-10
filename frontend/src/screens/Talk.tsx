import { Settings, Volume2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { SettingsSheet } from '../components/SettingsSheet'
import {
  ListenButton,
  PhraseBoard,
  Suggestions,
  Thread,
  TypeBox,
  type ListenPhase,
  type SpeakStage,
  type Turn,
  type TypeStage,
} from '../components/talk'
import { Footer, Signature, Wordmark } from '../components/ui'
import { api } from '../lib/api'
import { ApiError, friendlyError, type FriendlyError } from '../lib/errors'
import { playBlob, stopAudio, unlockAudio, usePlayingId } from '../lib/player'
import type { StoredVoice } from '../lib/storage'
import type { HistoryItem, PhraseCategory, Recording, Suggestion } from '../lib/types'
import { useRecorder } from '../lib/useRecorder'
import { STANDARD_VOICE_NAME } from './Welcome'

const LISTEN_MAX_S = 25
const WARMUP_EVERY_MS = 4 * 60_000
const HISTORY_TURNS = 6
/** Shorter than this is almost certainly an accidental double tap; don't spend STT credit on it. */
const LISTEN_MIN_S = 0.8

interface Props {
  voice: StoredVoice
  onChangeVoice: (v: StoredVoice) => void
  onVoiceDeleted: (voiceId: string) => void
  onRebank: () => void
  notify: (kind: 'error' | 'info', msg: FriendlyError) => void
}

let turnSeq = 0
const newId = () => `t${++turnSeq}`
const isMostlyDevanagari = (s: string) => {
  const letters = s.replace(/[\s\d\p{P}]/gu, '')
  const dev = letters.match(/[ऀ-ॿ]/g)?.length ?? 0
  return letters.length > 0 && dev / letters.length > 0.6
}

export function Talk({ voice, onChangeVoice, onVoiceDeleted, onRebank, notify }: Props) {
  const [turns, setTurns] = useState<Turn[]>([])
  const turnsRef = useRef(turns)
  useEffect(() => {
    turnsRef.current = turns
  }, [turns])

  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [lastTranscript, setLastTranscript] = useState('')
  const [spoken, setSpoken] = useState<Set<string>>(new Set())
  const [understanding, setUnderstanding] = useState(false)
  const [moreLoading, setMoreLoading] = useState(false)
  const [phrases, setPhrases] = useState<PhraseCategory[] | null>(null)
  const [phrasesFailed, setPhrasesFailed] = useState(false)
  const [typeStage, setTypeStage] = useState<TypeStage>('idle')
  const [typePreview, setTypePreview] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)

  // What is being spoken right now: the tapped element's key + the thread turn it becomes.
  const [speaking, setSpeaking] = useState<{ key: string; turnId?: string } | null>(null)
  const speakAbort = useRef<AbortController | null>(null)
  const playingId = usePlayingId()
  const stage: SpeakStage | null = speaking
    ? speaking.turnId && playingId === speaking.turnId
      ? 'playing'
      : 'fetching'
    : null

  const peaks = voice.peaks
  const history = (): HistoryItem[] =>
    turnsRef.current.slice(-HISTORY_TURNS).map(({ role, text }) => ({ role, text }))

  // ---------- keep the backend's upstream connections warm ----------
  // A cold NVIDIA/Gnani connection made the first suggestion take up to 6 s. Warm on
  // mount, every few minutes while the tab is visible, and when the tab becomes visible.
  useEffect(() => {
    let last = 0
    const warm = () => {
      if (document.visibilityState !== 'visible') return
      last = Date.now()
      api.warmup().catch(() => {})
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() - last > WARMUP_EVERY_MS / 2) warm()
    }
    warm()
    const id = window.setInterval(warm, WARMUP_EVERY_MS)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  // ---------- startup: health + phrases ----------

  const loadPhrases = useCallback(() => {
    api
      .phrases()
      .then((p) => {
        setPhrases(p)
        setPhrasesFailed(false)
      })
      .catch(() => setPhrasesFailed(true))
  }, [])

  useEffect(() => {
    loadPhrases()
    api
      .health()
      .then((h) => {
        if (!h.gnani_key || !h.llm_key) {
          notify('error', {
            hi: 'सर्वर पर API key नहीं मिली।',
            en: `Server is missing ${!h.gnani_key ? 'the Gnani' : 'the LLM'} API key.`,
          })
        }
      })
      .catch((e) => notify('error', friendlyError(e)))
  }, [loadPhrases, notify])

  // ---------- speaking ----------

  /** Speak `text` in the chosen voice. Starts playback as soon as audio arrives, then adds it to the thread. */
  const speak = useCallback(
    async (text: string, key: string): Promise<boolean> => {
      unlockAudio()
      speakAbort.current?.abort()
      stopAudio()
      const ctrl = new AbortController()
      speakAbort.current = ctrl
      setSpeaking({ key })
      try {
        const blob = await api.speak(voice.voice_id, text, ctrl.signal)
        if (ctrl.signal.aborted) return false
        const turn: Turn = { id: newId(), role: 'user', text, blob }
        setSpeaking({ key, turnId: turn.id })
        const playing = playBlob(blob, turn.id)
        setTurns((ts) => [...ts, turn])
        await playing
        return true
      } catch (e) {
        if (ctrl.signal.aborted) return false
        notify('error', friendlyError(e, 'speak'))
        return false
      } finally {
        if (speakAbort.current === ctrl) {
          speakAbort.current = null
          setSpeaking(null)
        }
      }
    },
    [voice.voice_id, notify],
  )

  const replay = (t: Turn) => {
    if (!t.blob) return
    unlockAudio()
    if (playingId === t.id) return stopAudio()
    speakAbort.current?.abort()
    playBlob(t.blob, t.id).catch((e) => notify('error', friendlyError(e)))
  }

  const pickSuggestion = async (s: Suggestion, key: string) => {
    const ok = await speak(s.text, key)
    if (ok) setSpoken((prev) => new Set(prev).add(key))
  }

  // ---------- listening ----------

  const onHeard = async (rec: Recording) => {
    if (rec.durationS < LISTEN_MIN_S) {
      notify('info', friendlyError(new ApiError('AUDIO_TOO_SHORT', 'client-side'), 'listen'))
      return
    }
    setUnderstanding(true)
    try {
      const r = await api.listen(rec, history())
      const transcript = r.transcript.trim()
      if (!transcript) {
        notify('info', {
          hi: 'कुछ सुनाई नहीं दिया।',
          en: "Didn't catch that. Try again, a little closer to the microphone.",
        })
        return
      }
      setTurns((ts) => [...ts, { id: newId(), role: 'family', text: transcript }])
      setLastTranscript(transcript)
      setSuggestions(r.suggestions.slice(0, 3))
      setSpoken(new Set())
      if (r.suggestions.length === 0) {
        notify('info', { hi: 'अभी सुझाव नहीं मिले।', en: 'No suggestions this time. Use a phrase or type a reply.' })
      }
    } catch (e) {
      notify('error', friendlyError(e, 'listen'))
    } finally {
      setUnderstanding(false)
    }
  }

  const recorder = useRecorder({
    maxSeconds: LISTEN_MAX_S,
    onComplete: (rec) => void onHeard(rec),
    onError: (e) => notify('error', friendlyError(e, 'listen')),
  })

  const listenPhase: ListenPhase = understanding ? 'understanding' : recorder.status

  const pressListen = () => {
    if (listenPhase === 'recording') return recorder.stop()
    if (listenPhase !== 'idle') return
    unlockAudio()
    // Don't record our own voice coming out of the speakers.
    speakAbort.current?.abort()
    stopAudio()
    void recorder.start()
  }

  const moreOptions = async () => {
    if (!lastTranscript) return
    setMoreLoading(true)
    try {
      // Same shape as /api/listen: the turns *before* the transcript being answered.
      const all = turnsRef.current
      let cut = all.length
      for (let i = all.length - 1; i >= 0; i--) {
        if (all[i].role === 'family' && all[i].text === lastTranscript) {
          cut = i
          break
        }
      }
      const h = all.slice(Math.max(0, cut - HISTORY_TURNS), cut).map(({ role, text }) => ({ role, text }))
      const s = await api.suggest(lastTranscript, h)
      if (s.length) {
        setSuggestions(s.slice(0, 3))
        setSpoken(new Set())
      } else {
        notify('info', { hi: 'और सुझाव नहीं मिले।', en: 'No more suggestions right now.' })
      }
    } catch (e) {
      notify('error', friendlyError(e))
    } finally {
      setMoreLoading(false)
    }
  }

  // ---------- typing ----------

  const speakTyped = async (raw: string): Promise<boolean> => {
    const text = raw.trim()
    if (!text) return false
    unlockAudio()
    setTypePreview(null)
    let dev = text
    if (!isMostlyDevanagari(text)) {
      setTypeStage('composing')
      try {
        dev = (await api.compose(text)).trim()
      } catch (e) {
        notify('error', friendlyError(e, 'compose'))
        setTypeStage('idle')
        return false
      }
      if (!dev) {
        notify('error', { hi: 'इसे समझ नहीं पाए।', en: "Couldn't convert that. Try writing it another way." })
        setTypeStage('idle')
        return false
      }
    }
    setTypePreview(dev)
    setTypeStage('speaking')
    const ok = await speak(dev, 'type')
    setTypeStage('idle')
    return ok
  }

  // ---------- render ----------

  const isStandard = voice.voice_id === null
  const voicePlaying = stage === 'playing' || (playingId !== null && turns.some((t) => t.id === playingId))

  return (
    <div className="flex min-h-dvh flex-col lg:grid lg:h-dvh lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      {/* Their side: what the family says, and the control that listens to them. */}
      <section aria-label="What they say" className="flex min-w-0 flex-col bg-neel-900 text-white lg:min-h-0">
        <header className="flex h-[72px] shrink-0 items-center px-5 sm:px-8">
          <Wordmark tone="light" />
        </header>
        <div className="scrollbar-dark max-h-[46vh] min-h-[13rem] flex-1 overflow-y-auto px-5 pt-2 sm:px-8 lg:max-h-none">
          <Thread turns={turns} playingId={playingId} peaks={peaks} onReplay={replay} />
        </div>
        <div className="shrink-0 border-t border-white/10 px-5 pb-4 pt-5 sm:px-8">
          <ListenButton
            phase={listenPhase}
            elapsed={recorder.elapsed}
            maxSeconds={LISTEN_MAX_S}
            history={recorder.history}
            onPress={pressListen}
          />
          <Footer className="mt-4 text-neel-300" />
        </div>
      </section>

      {/* Your side: everything here is spoken in your own voice. */}
      <section aria-label="What you say" className="flex min-w-0 flex-1 flex-col bg-chalk lg:min-h-0">
        <header className="flex h-[72px] shrink-0 items-center justify-end gap-2 px-5 sm:px-8">
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="flex min-h-14 min-w-0 items-center gap-3 rounded-full py-1 pl-2 pr-4 text-left active:bg-white"
            aria-label="Current voice, open voice settings"
          >
            {isStandard ? (
              <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-neel-100">
                <Volume2 className="h-5 w-5 text-neel-800" />
              </span>
            ) : (
              <span className="grid h-11 w-14 shrink-0 place-items-center rounded-full bg-haldi-100 px-2">
                <Signature peaks={peaks} bars={9} live={voicePlaying} className="h-6 w-full" barClassName="bg-haldi-600" />
              </span>
            )}
            <span className="min-w-0">
              <span lang="hi" className="block truncate font-voice text-[1.25rem] leading-tight text-neel-950">
                {isStandard ? STANDARD_VOICE_NAME : `${voice.name} की आवाज़`}
              </span>
              <span className="block truncate text-[0.8rem] leading-tight text-ink-soft">
                {isStandard ? 'Standard voice' : 'Your own voice'}
              </span>
            </span>
          </button>
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            aria-label="Voice settings"
            className="grid h-14 w-14 shrink-0 place-items-center rounded-full text-neel-800 active:bg-white"
          >
            <Settings className="h-6 w-6" />
          </button>
        </header>

        <div className="scrollbar-thin flex flex-1 flex-col gap-4 px-5 pb-4 sm:px-8 lg:min-h-0 lg:overflow-y-auto">
          <Suggestions
            suggestions={suggestions}
            loading={understanding}
            activeKey={speaking?.key ?? null}
            stage={stage}
            spoken={spoken}
            peaks={peaks}
            canMore={!!lastTranscript}
            moreLoading={moreLoading}
            onPick={pickSuggestion}
            onMore={moreOptions}
          />
          <PhraseBoard
            categories={phrases}
            failed={phrasesFailed}
            activeKey={speaking?.key ?? null}
            stage={stage}
            peaks={peaks}
            onSpeak={(text, key) => void speak(text, key)}
            onRetry={loadPhrases}
          />
        </div>

        <div className="shrink-0 px-5 pb-5 sm:px-8">
          <TypeBox
            stage={typeStage}
            preview={typePreview}
            peaks={peaks}
            playing={stage === 'playing' && speaking?.key === 'type'}
            onSubmit={speakTyped}
          />
        </div>
      </section>

      {settingsOpen && (
        <SettingsSheet
          open
          current={voice}
          standardName={STANDARD_VOICE_NAME}
          onClose={() => setSettingsOpen(false)}
          onChoose={(v) => {
            onChangeVoice(v)
            setSettingsOpen(false)
          }}
          onDeleted={(id) => {
            if (id === voice.voice_id) setSettingsOpen(false)
            onVoiceDeleted(id)
          }}
          onRebank={() => {
            setSettingsOpen(false)
            stopAudio()
            onRebank()
          }}
          notify={notify}
        />
      )}
    </div>
  )
}
