import { ArrowLeft, Check, Mic, Play, RotateCcw, Square, Volume2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Footer, Signature, Spinner, Wordmark } from '../components/ui'
import { api } from '../lib/api'
import { friendlyError, type FriendlyError } from '../lib/errors'
import { playBlob, stopAudio, unlockAudio, usePlayingId } from '../lib/player'
import { savePeaks, type StoredVoice } from '../lib/storage'
import type { CreatedVoice, Recording } from '../lib/types'
import { useRecorder } from '../lib/useRecorder'

const MAX_S = 30
const MIN_S = 5
const BEST_S = 20
/** Rough time to read the passage aloud; drives the teleprompter highlight. */
const READ_S = 24
const HEAR_SAMPLE = 'नमस्ते, यह मेरी अपनी आवाज़ है। अब मेरी बात मेरी ही आवाज़ में होगी।'
export const STANDARD_VOICE_NAME = 'मानक आवाज़'

interface Props {
  canGoBack: boolean
  onBack: () => void
  onVoiceReady: (v: StoredVoice) => void
  onStart: () => void
  notify: (kind: 'error' | 'info', msg: FriendlyError) => void
}

const fmt = (s: number) => `0:${String(Math.floor(s)).padStart(2, '0')}`

export function Welcome({ canGoBack, onBack, onVoiceReady, onStart, notify }: Props) {
  const [name, setName] = useState('')
  const [consent, setConsent] = useState(false)
  const [passage, setPassage] = useState<string | null>(null)
  const [passageFailed, setPassageFailed] = useState(false)
  const [rec, setRec] = useState<Recording | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<{ voice: CreatedVoice; peaks: number[] } | null>(null)
  const [hearLoading, setHearLoading] = useState(false)
  const playingId = usePlayingId()

  const recorder = useRecorder({
    maxSeconds: MAX_S,
    raw: true,
    onComplete: setRec,
    onError: (e) => notify('error', friendlyError(e, 'enroll')),
  })
  const recording = recorder.status !== 'idle'

  const loadPassage = useCallback(() => {
    api
      .passage()
      .then((t) => {
        setPassage(t)
        setPassageFailed(false)
      })
      .catch((e) => {
        setPassageFailed(true)
        notify('error', friendlyError(e))
      })
  }, [notify])

  useEffect(loadPassage, [loadPassage])

  const ready = name.trim().length > 0 && consent
  const readProgress = recording ? Math.min(1, recorder.elapsed / READ_S) : 0
  const tooShort = !!rec && rec.durationS < MIN_S

  const toggleRecord = () => {
    if (recording) return recorder.stop()
    if (!ready) return
    unlockAudio()
    stopAudio()
    setRec(null)
    void recorder.start()
  }

  const playBack = () => {
    if (!rec) return
    if (playingId === 'local-rec') return stopAudio()
    playBlob(rec.blob, 'local-rec').catch((e) => notify('error', friendlyError(e)))
  }

  const save = async () => {
    if (!rec || tooShort || !ready) return
    stopAudio()
    setSaving(true)
    try {
      const voice = await api.createVoice(rec, name.trim())
      savePeaks(voice.voice_id, rec.peaks)
      setSaved({ voice, peaks: rec.peaks })
      onVoiceReady({ voice_id: voice.voice_id, name: voice.name || name.trim(), peaks: rec.peaks })
    } catch (e) {
      notify('error', friendlyError(e, 'enroll'))
    } finally {
      setSaving(false)
    }
  }

  const hearYourself = async () => {
    if (!saved) return
    if (playingId === 'hear') return stopAudio()
    unlockAudio()
    setHearLoading(true)
    try {
      const blob = await api.speak(saved.voice.voice_id, HEAR_SAMPLE)
      setHearLoading(false)
      await playBlob(blob, 'hear')
    } catch (e) {
      notify('error', friendlyError(e, 'speak'))
    } finally {
      setHearLoading(false)
    }
  }

  const skip = () => {
    stopAudio()
    recorder.cancel()
    onVoiceReady({ voice_id: null, name: STANDARD_VOICE_NAME })
    onStart()
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex h-[72px] w-full max-w-6xl items-center justify-between gap-3 px-5 sm:px-8">
        <Wordmark />
        {canGoBack && !saved ? (
          <button
            type="button"
            onClick={onBack}
            className="flex min-h-12 items-center gap-2 rounded-full px-3 font-semibold text-neel-800 active:bg-white"
          >
            <ArrowLeft className="h-5 w-5" />
            <span lang="hi">वापस</span>
            <span className="text-sm font-normal text-ink-soft">Back</span>
          </button>
        ) : !saved ? (
          <SkipLink onClick={skip} disabled={recording || saving} />
        ) : null}
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-5 pb-8 sm:px-8">
        {saved ? (
          <SavedPanel
            name={saved.voice.name || name.trim()}
            seconds={saved.voice.duration_s ?? rec?.durationS ?? 0}
            peaks={saved.peaks}
            hearLoading={hearLoading}
            hearPlaying={playingId === 'hear'}
            onHear={hearYourself}
            onStart={() => {
              stopAudio()
              onStart()
            }}
          />
        ) : (
          <div className="grid gap-8 lg:grid-cols-12 lg:gap-12">
            {/* Who is speaking, and their permission */}
            <section className="lg:col-span-4 lg:pt-8">
              <h1 lang="hi" className="font-voice text-[2.6rem] leading-[1.15] text-neel-950 sm:text-[3.2rem]">
                अपनी आवाज़ बचाइए
              </h1>
              <p lang="hi" className="mt-3 max-w-[34ch] text-[1.15rem] leading-relaxed text-neel-900">
                बीस सेकंड पढ़कर सुनाइए। फिर आपका हर जवाब आपकी अपनी आवाज़ में बोला जाएगा।
              </p>
              <p className="mt-1 max-w-[40ch] text-[0.95rem] leading-relaxed text-ink-soft">
                Read aloud for 20 seconds. Every reply after that is spoken in your own voice.
              </p>

              <div className="mt-8 space-y-3">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  disabled={recording || saving}
                  placeholder="आपका नाम"
                  autoComplete="name"
                  aria-label="Your name"
                  className="h-16 w-full rounded-2xl border-2 border-transparent bg-white px-5 text-[1.3rem] text-neel-950 placeholder:text-neel-300 focus:border-neel-700 focus:outline-none"
                />
                <label className="flex min-h-16 cursor-pointer items-center gap-4 rounded-2xl border-2 border-transparent bg-white px-4 py-3 has-[:checked]:border-leaf">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    disabled={recording || saving}
                    required
                    className="peer sr-only"
                  />
                  <span
                    aria-hidden="true"
                    className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border-2 border-neel-300 text-white peer-checked:border-leaf peer-checked:bg-leaf peer-focus-visible:outline-3 peer-focus-visible:outline-haldi-500"
                  >
                    <Check className="h-5 w-5" strokeWidth={3} />
                  </span>
                  <span>
                    <span lang="hi" className="block text-[1.12rem] font-semibold leading-snug text-neel-950">
                      यह मेरी अपनी आवाज़ है
                    </span>
                    <span className="block text-[0.88rem] leading-snug text-ink-soft">
                      My own voice. I agree to it being cloned, and I can delete it any time.
                    </span>
                  </span>
                </label>
              </div>
            </section>

            {/* The passage to read, and the recorder that captures it */}
            <section className="lg:col-span-8">
              <div className="overflow-hidden rounded-[22px] bg-white">
                <div className="min-h-[13rem] px-6 pb-6 pt-7 sm:px-9">
                  {passage ? (
                    <Teleprompter text={passage} progress={readProgress} done={!!rec && !recording} />
                  ) : passageFailed ? (
                    <button
                      type="button"
                      onClick={loadPassage}
                      className="min-h-14 rounded-xl border-2 border-neel-200 px-5 font-semibold text-neel-900 active:bg-neel-50"
                    >
                      <span lang="hi">फिर से लोड करें</span> <span className="font-normal text-ink-soft">Reload the passage</span>
                    </button>
                  ) : (
                    <div className="space-y-4" aria-label="Loading passage">
                      {[92, 100, 85, 70].map((w) => (
                        <div key={w} className="h-6 animate-pulse rounded-md bg-neel-50" style={{ width: `${w}%` }} />
                      ))}
                    </div>
                  )}
                </div>

                <Recorder
                  ready={ready}
                  recording={recording}
                  starting={recorder.status === 'starting'}
                  elapsed={recorder.elapsed}
                  history={recorder.history}
                  rec={rec}
                  tooShort={tooShort}
                  saving={saving}
                  playing={playingId === 'local-rec'}
                  onToggle={toggleRecord}
                  onPlayBack={playBack}
                  onRerecord={() => {
                    stopAudio()
                    setRec(null)
                  }}
                  onSave={save}
                />
              </div>
            </section>
          </div>
        )}
      </main>
      <Footer className="mx-auto w-full max-w-6xl px-5 pb-4 sm:px-8" />
    </div>
  )
}

function SkipLink({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex min-h-12 flex-col items-end justify-center rounded-xl px-3 text-right text-neel-800 active:bg-white disabled:opacity-40"
    >
      <span lang="hi" className="text-[1rem] font-semibold underline decoration-neel-300 underline-offset-4">
        मानक आवाज़ से शुरू करें
      </span>
      <span className="text-[0.8rem] text-ink-soft">Skip, use a standard voice</span>
    </button>
  )
}

/** The passage, with the words already read turning marigold: they are now in your voice. */
function Teleprompter({ text, progress, done }: { text: string; progress: number; done: boolean }) {
  let cut = done ? text.length : Math.round(text.length * progress)
  if (cut > 0 && cut < text.length) {
    const sp = text.indexOf(' ', cut)
    cut = sp === -1 ? text.length : sp
  }
  return (
    <p lang="hi" className="font-voice text-[1.5rem] leading-[1.85] sm:text-[1.7rem]">
      <span className="text-haldi-700">{text.slice(0, cut)}</span>
      <span className="text-neel-950">{text.slice(cut)}</span>
    </p>
  )
}

function Recorder(props: {
  ready: boolean
  recording: boolean
  starting: boolean
  elapsed: number
  history: number[]
  rec: Recording | null
  tooShort: boolean
  saving: boolean
  playing: boolean
  onToggle: () => void
  onPlayBack: () => void
  onRerecord: () => void
  onSave: () => void
}) {
  const { ready, recording, starting, elapsed, history, rec, tooShort, saving, playing } = props
  const shownSeconds = recording ? elapsed : (rec?.durationS ?? 0)
  const liveBars = history.slice(-72)
  const hint = starting
    ? { hi: 'माइक चालू हो रहा है', en: 'Starting the microphone' }
    : recording
      ? { hi: 'पढ़ते रहिए', en: 'Keep reading. Tap to stop.' }
      : rec
        ? null
        : ready
          ? { hi: 'दबाइए और पढ़ना शुरू कीजिए', en: 'Tap and start reading' }
          : { hi: 'पहले नाम लिखिए और सहमति दीजिए', en: 'Add your name and consent first' }

  return (
    <div className="bg-neel-50 px-6 py-5 sm:px-9">
      <div className="flex items-center gap-5 sm:gap-7">
        {!rec || recording ? (
          <RingMic
            seconds={shownSeconds}
            recording={recording}
            disabled={!ready && !recording}
            onClick={props.onToggle}
          />
        ) : (
          <span className="grid h-[9rem] w-[9rem] shrink-0 place-items-center">
            <span className="grid h-24 w-24 place-items-center rounded-full bg-leaf text-white">
              <Check className="h-11 w-11" strokeWidth={3} />
            </span>
          </span>
        )}

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-3">
            {hint ? (
              <span className="min-w-0">
                <span lang="hi" className="block text-[1.15rem] font-semibold text-neel-950">
                  {hint.hi}
                </span>
                <span className="block text-[0.88rem] text-ink-soft">{hint.en}</span>
              </span>
            ) : (
              <span />
            )}
            <span className="tabular shrink-0 text-[1.6rem] font-bold text-neel-950">
              {fmt(shownSeconds)}
              <span className="text-base font-normal text-ink-soft"> / {fmt(MAX_S)}</span>
            </span>
          </div>

          {/* The live input level, drawn the way it will be remembered: your voiceprint */}
          <div className="mt-3 h-14" aria-hidden="true">
            {recording ? (
              <div className="flex h-full items-center justify-end gap-[3px] overflow-hidden">
                {liveBars.map((v, i) => (
                  <span key={i} className="block w-[4px] shrink-0 rounded-full bg-haldi-500" style={{ height: `${Math.max(8, v * 100)}%` }} />
                ))}
              </div>
            ) : rec ? (
              <Signature peaks={rec.peaks} live={playing} className="h-full" />
            ) : (
              <div className="flex h-full items-center">
                <div className="h-[3px] w-full rounded-full bg-neel-200" />
              </div>
            )}
          </div>
        </div>
      </div>

      {rec && !recording && (
        <div className="mt-5">
          {tooShort && (
            <p role="alert" className="mb-3 rounded-xl bg-brick-50 px-4 py-3 text-brick">
              <span lang="hi" className="font-semibold">
                रिकॉर्डिंग छोटी है, कम से कम 5 सेकंड चाहिए।
              </span>{' '}
              Too short. Record at least 5 seconds.
            </p>
          )}
          <div className="flex flex-wrap gap-2.5">
            <button
              type="button"
              onClick={props.onPlayBack}
              disabled={saving}
              aria-label={playing ? 'Stop playback' : 'Play back'}
              className="flex min-h-14 items-center gap-2 rounded-xl bg-white px-4 font-semibold text-neel-900 active:bg-neel-100 disabled:opacity-50"
            >
              {playing ? <Square className="h-5 w-5" /> : <Play className="h-5 w-5" />}
              <span lang="hi">{playing ? 'रोकिए' : 'सुनकर देखिए'}</span>
            </button>
            <button
              type="button"
              onClick={props.onRerecord}
              disabled={saving}
              aria-label="Re-record"
              className="flex min-h-14 items-center gap-2 rounded-xl bg-white px-4 font-semibold text-neel-900 active:bg-neel-100 disabled:opacity-50"
            >
              <RotateCcw className="h-5 w-5" />
              <span lang="hi">फिर से</span>
            </button>
            <button
              type="button"
              onClick={props.onSave}
              disabled={saving || tooShort || !ready}
              className="flex min-h-14 flex-1 items-center justify-center gap-3 rounded-xl bg-haldi-500 px-6 text-[1.15rem] font-bold text-neel-950 active:bg-haldi-300 disabled:bg-neel-100 disabled:text-neel-500 sm:flex-none"
            >
              {saving ? <Spinner /> : <Check className="h-6 w-6" strokeWidth={3} />}
              <span lang="hi">{saving ? 'आवाज़ बन रही है…' : 'आवाज़ सुरक्षित करें'}</span>
              <span className="text-sm font-normal opacity-80">{saving ? 'Creating your voice' : 'Save my voice'}</span>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/** The record button, ringed by its own timeline: a tick at the 5 s minimum and a marigold tick at the 20 s target. */
function RingMic({
  seconds,
  recording,
  disabled,
  onClick,
}: {
  seconds: number
  recording: boolean
  disabled: boolean
  onClick: () => void
}) {
  const R = 56
  const C = 2 * Math.PI * R
  const pct = Math.min(1, seconds / MAX_S)
  // Angles start at 12 o'clock and run clockwise, like the progress arc.
  const at = (t: number, r: number) => {
    const a = (2 * Math.PI * t) / MAX_S - Math.PI / 2
    return { x: 80 + r * Math.cos(a), y: 80 + r * Math.sin(a) }
  }
  const tick = (t: number, color: string) => {
    const p1 = at(t, 49)
    const p2 = at(t, 63)
    const lp = at(t, 74)
    return (
      <g>
        <line x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} stroke={color} strokeWidth={4} strokeLinecap="round" />
        <text x={lp.x} y={lp.y} fill={color} fontSize={13} fontWeight={700} textAnchor="middle" dominantBaseline="central">
          {t}
        </text>
      </g>
    )
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={recording ? 'Stop recording' : 'Start recording'}
      className="group relative grid h-[9rem] w-[9rem] shrink-0 place-items-center disabled:cursor-not-allowed"
    >
      <svg viewBox="0 0 160 160" className="absolute inset-0" aria-hidden="true">
        <circle cx={80} cy={80} r={R} fill="none" stroke="var(--color-neel-200)" strokeWidth={6} />
        <circle
          transform="rotate(-90 80 80)"
          cx={80}
          cy={80}
          r={R}
          fill="none"
          stroke={seconds >= BEST_S ? 'var(--color-leaf)' : 'var(--color-haldi-500)'}
          strokeWidth={6}
          strokeLinecap="round"
          strokeDasharray={C}
          strokeDashoffset={C * (1 - pct)}
        />
        {tick(MIN_S, 'var(--color-neel-500)')}
        {tick(BEST_S, 'var(--color-leaf)')}
      </svg>
      <span
        className={`grid h-[5.5rem] w-[5.5rem] place-items-center rounded-full text-white transition-colors group-disabled:bg-neel-300 ${
          recording ? 'rec-halo bg-brick' : 'bg-neel-900 group-active:bg-neel-800'
        }`}
      >
        {recording ? <Square className="h-8 w-8" fill="currentColor" /> : <Mic className="h-10 w-10" />}
      </span>
    </button>
  )
}

function SavedPanel(props: {
  name: string
  seconds: number
  peaks: number[]
  hearLoading: boolean
  hearPlaying: boolean
  onHear: () => void
  onStart: () => void
}) {
  const { name, seconds, peaks, hearLoading, hearPlaying } = props
  return (
    <section className="mx-auto mt-6 max-w-3xl text-center">
      <Signature peaks={peaks} live={hearPlaying} className="mx-auto h-36 max-w-2xl sm:h-44" />
      <h1 lang="hi" className="mt-8 font-voice text-[2.6rem] leading-tight text-neel-950 sm:text-[3.2rem]">
        {name} की आवाज़
      </h1>
      <p className="mt-2 text-[1.05rem] text-ink-soft">
        <span lang="hi" className="font-semibold text-leaf">
          आवाज़ सुरक्षित
        </span>{' '}
        Voice saved from {seconds.toFixed(1)} s of speech
      </p>

      <div className="mt-10 flex flex-col items-stretch justify-center gap-3 sm:flex-row">
        <button
          type="button"
          onClick={props.onHear}
          disabled={hearLoading}
          className="flex min-h-16 items-center justify-center gap-3 rounded-2xl bg-haldi-500 px-7 text-[1.2rem] font-bold text-neel-950 active:bg-haldi-300 disabled:opacity-80"
        >
          {hearLoading ? <Spinner className="h-6 w-6" /> : hearPlaying ? <Square className="h-6 w-6" /> : <Volume2 className="h-7 w-7" />}
          <span lang="hi">{hearLoading ? 'बोल रहे हैं…' : hearPlaying ? 'रोकिए' : 'अपनी आवाज़ सुनिए'}</span>
        </button>
        <button
          type="button"
          onClick={props.onStart}
          className="flex min-h-16 items-center justify-center gap-3 rounded-2xl bg-neel-900 px-7 text-[1.2rem] font-bold text-white active:bg-neel-800"
        >
          <span lang="hi">बात शुरू करें</span>
        </button>
      </div>
    </section>
  )
}
