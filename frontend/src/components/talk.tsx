import { Check, Ear, Keyboard, Mic, Play, RefreshCw, Square, Volume2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { PhraseCategory, Suggestion } from '../lib/types'
import { Signature, Spinner, Thinking } from './ui'

export interface Turn {
  id: string
  role: 'family' | 'user'
  text: string
  blob?: Blob
}

/** Which tappable thing is currently being spoken, and how far along it is. */
export type SpeakStage = 'fetching' | 'playing'

// ---------------------------------------------------------------- Thread (family side, dark)

export function Thread({
  turns,
  playingId,
  peaks,
  onReplay,
}: {
  turns: Turn[]
  playingId: string | null
  peaks?: number[]
  onReplay: (t: Turn) => void
}) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    end.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'end' })
  }, [turns.length])

  if (turns.length === 0) {
    return (
      <div className="flex h-full min-h-[11rem] flex-col items-start justify-end gap-4 pb-2">
        <Ear className="h-16 w-16 text-neel-500" strokeWidth={1.5} aria-hidden="true" />
        <p lang="hi" className="max-w-[22ch] text-[1.6rem] font-semibold leading-snug text-white">
          कोई आपसे बात करे, तो सुनिए दबाइए
        </p>
        <p className="-mt-3 text-[0.95rem] text-neel-300">When someone speaks to you, tap Listen.</p>
      </div>
    )
  }

  const lastFamily = [...turns].reverse().find((t) => t.role === 'family')?.id

  return (
    <ol className="flex flex-col gap-5 pb-2" aria-live="polite">
      {turns.map((t) =>
        t.role === 'family' ? (
          <li key={t.id} className="flex items-start gap-3">
            <Ear className="mt-1.5 h-5 w-5 shrink-0 text-neel-300" aria-label="They said" />
            <p
              lang="hi"
              className={
                t.id === lastFamily
                  ? 'text-[1.9rem] font-semibold leading-snug text-white'
                  : 'text-[1.3rem] leading-snug text-neel-200'
              }
            >
              {t.text}
            </p>
          </li>
        ) : (
          <li key={t.id} className="flex items-center gap-3 pl-8">
            <button
              type="button"
              onClick={() => onReplay(t)}
              disabled={!t.blob}
              aria-label={playingId === t.id ? 'Stop' : 'Play again'}
              className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-haldi-500 text-neel-950 active:bg-haldi-300 disabled:opacity-40"
            >
              {playingId === t.id ? (
                <Signature peaks={peaks} bars={5} live className="h-5 w-6" barClassName="bg-neel-950" />
              ) : (
                <Play className="h-5 w-5 translate-x-[1px]" fill="currentColor" />
              )}
            </button>
            <p lang="hi" className="font-voice text-[1.45rem] leading-snug text-haldi-300">
              {t.text}
            </p>
          </li>
        ),
      )}
      <div ref={end} />
    </ol>
  )
}

// ---------------------------------------------------------------- Listen (family side, dark)

export type ListenPhase = 'idle' | 'starting' | 'recording' | 'understanding'

export function ListenButton({
  phase,
  elapsed,
  maxSeconds,
  history,
  onPress,
}: {
  phase: ListenPhase
  elapsed: number
  maxSeconds: number
  history: number[]
  onPress: () => void
}) {
  const recording = phase === 'recording' || phase === 'starting'
  const busy = phase === 'understanding'
  return (
    <button
      type="button"
      onClick={onPress}
      disabled={busy}
      aria-label={recording ? 'Stop listening' : busy ? 'Understanding' : 'Listen'}
      className="group flex w-full items-center gap-5 text-left text-white disabled:cursor-wait"
    >
      <span
        className={`grid h-24 w-24 shrink-0 place-items-center rounded-full transition-colors ${
          recording ? 'rec-halo bg-brick text-white' : busy ? 'bg-neel-700 text-white' : 'bg-white text-neel-900 group-active:bg-neel-100'
        }`}
      >
        {recording ? <Square className="h-9 w-9" fill="currentColor" /> : busy ? <Spinner className="h-9 w-9" /> : <Mic className="h-11 w-11" />}
      </span>

      <span className="min-w-0 flex-1">
        <span lang="hi" className="block text-[2rem] font-bold leading-none">
          {phase === 'starting' ? 'माइक चालू…' : recording ? 'सुन रहे हैं' : busy ? 'समझ रहे हैं' : 'सुनिए'}
        </span>
        <span className="mt-1.5 block text-[0.95rem] text-neel-300">
          {phase === 'starting' ? 'Starting the microphone' : recording ? 'Tap to stop' : busy ? 'Working out replies' : 'Listen'}
        </span>
      </span>

      {recording && (
        <span className="flex shrink-0 flex-col items-end gap-2">
          <span className="flex h-9 items-center gap-[3px]" aria-hidden="true">
            {history.slice(-16).map((v, i) => (
              <span key={i} className="block w-[4px] rounded-full bg-white/70" style={{ height: `${Math.max(12, v * 100)}%` }} />
            ))}
          </span>
          <span className="tabular text-lg font-semibold">
            0:{String(Math.floor(elapsed)).padStart(2, '0')}
            <span className="text-sm font-normal text-neel-300"> / 0:{maxSeconds}</span>
          </span>
        </span>
      )}
    </button>
  )
}

// ---------------------------------------------------------------- Suggestions (your side)

export function Suggestions({
  suggestions,
  loading,
  activeKey,
  stage,
  spoken,
  peaks,
  canMore,
  moreLoading,
  onPick,
  onMore,
}: {
  suggestions: Suggestion[]
  loading: boolean
  activeKey: string | null
  stage: SpeakStage | null
  spoken: Set<string>
  peaks?: number[]
  canMore: boolean
  moreLoading: boolean
  onPick: (s: Suggestion, key: string) => void
  onMore: () => void
}) {
  const busy = loading || moreLoading
  return (
    <div>
      <div className="min-h-[13.25rem]">
        {busy ? (
          <div className="flex h-[13.25rem] items-center gap-4 px-2" aria-label="Preparing replies">
            <Thinking />
            <span lang="hi" className="font-voice text-[1.4rem] text-haldi-700">
              आपके जवाब तैयार हो रहे हैं
            </span>
          </div>
        ) : suggestions.length === 0 ? (
          <div className="flex h-[13.25rem] items-center gap-5 px-2">
            <Signature peaks={peaks} bars={9} className="h-12 w-20 shrink-0 opacity-40" />
            <p lang="hi" className="max-w-[26ch] font-voice text-[1.4rem] leading-snug text-ink-soft">
              उनकी बात सुनते ही, आपके तीन जवाब यहाँ आएँगे
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {suggestions.map((s, i) => {
              const key = `sug-${i}-${s.text}`
              const active = activeKey === key
              return (
                <li key={key}>
                  <button
                    type="button"
                    data-suggestion
                    onClick={() => onPick(s, key)}
                    className={`flex min-h-16 w-full items-center gap-4 rounded-2xl border-2 py-1.5 pl-2 pr-4 text-left transition-colors ${
                      active ? 'border-haldi-500 bg-haldi-50' : 'border-transparent bg-white active:bg-haldi-50'
                    }`}
                  >
                    <span
                      className={`grid h-12 w-12 shrink-0 place-items-center rounded-full ${active ? 'bg-haldi-500' : 'bg-haldi-100'}`}
                    >
                      {active && stage === 'fetching' ? (
                        <Spinner className="h-5 w-5 text-neel-950" />
                      ) : (
                        <Signature
                          peaks={peaks}
                          bars={5}
                          live={active && stage === 'playing'}
                          className="h-5 w-6"
                          barClassName={active ? 'bg-neel-950' : 'bg-haldi-600'}
                        />
                      )}
                    </span>
                    <span lang="hi" className="flex-1 font-voice text-[1.5rem] leading-snug text-neel-950">
                      {s.text}
                    </span>
                    {spoken.has(key) && !active && (
                      <Check className="h-6 w-6 shrink-0 text-leaf" aria-label="Spoken" strokeWidth={3} />
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
      <div className={`mt-1 flex justify-end ${canMore && !busy && suggestions.length > 0 ? '' : 'invisible'}`}>
        <button
          type="button"
          onClick={onMore}
          disabled={!canMore || busy}
          className="flex min-h-11 items-center gap-2 rounded-xl px-3 text-[1rem] font-semibold text-neel-800 active:bg-white disabled:opacity-40"
        >
          <RefreshCw className="h-[1.05rem] w-[1.05rem]" />
          <span lang="hi">और जवाब</span>
          <span className="text-sm font-normal text-ink-soft">More options</span>
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- Phrase board (your side)

type AacKey = 'needs' | 'feelings' | 'answers' | 'people' | 'social'
const AAC_ORDER: AacKey[] = ['needs', 'feelings', 'answers', 'people', 'social']
const AAC_COLOR: Record<AacKey, string> = {
  needs: 'var(--color-aac-needs)',
  feelings: 'var(--color-aac-feelings)',
  answers: 'var(--color-aac-answers)',
  people: 'var(--color-aac-people)',
  social: 'var(--color-aac-social)',
}
const AAC_GLYPH: Record<AacKey, string> = { needs: '💧', feelings: '🤕', answers: '👍', people: '👪', social: '🙏' }

/** Map a category to an AAC board colour (Fitzgerald-style key), by its English name. */
function aacKey(c: PhraseCategory, i: number): AacKey {
  const en = (c.category_en ?? '').toLowerCase()
  if (en.includes('need')) return 'needs'
  if (en.includes('pain') || en.includes('feel')) return 'feelings'
  if (en.includes('answer')) return 'answers'
  if (en.includes('family') || en.includes('people')) return 'people'
  if (en.includes('social')) return 'social'
  return AAC_ORDER[i % AAC_ORDER.length]
}

export function PhraseBoard({
  categories,
  failed,
  activeKey,
  stage,
  peaks,
  onSpeak,
  onRetry,
}: {
  categories: PhraseCategory[] | null
  failed: boolean
  activeKey: string | null
  stage: SpeakStage | null
  peaks?: number[]
  onSpeak: (text: string, key: string) => void
  onRetry: () => void
}) {
  const [tab, setTab] = useState(0)
  const cats = categories ?? []
  const idx = Math.min(tab, Math.max(0, cats.length - 1))
  const current = cats[idx]
  const color = current ? AAC_COLOR[aacKey(current, idx)] : AAC_COLOR.needs

  if (categories === null) {
    return failed ? (
      <button
        type="button"
        onClick={onRetry}
        className="min-h-14 rounded-xl border-2 border-neel-200 bg-white px-5 font-semibold text-neel-900 active:bg-neel-50"
      >
        <span lang="hi">वाक्य फिर से लोड करें</span> <span className="font-normal text-ink-soft">Reload phrases</span>
      </button>
    ) : (
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4" aria-label="Loading phrases">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl bg-white" />
        ))}
      </div>
    )
  }

  return (
    <div>
      <div role="tablist" aria-label="Phrase categories" className="flex flex-wrap gap-2">
        {cats.map((c, i) => {
          const k = aacKey(c, i)
          const selected = i === idx
          return (
            <button
              key={c.category + i}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setTab(i)}
              className={`flex min-h-12 items-center gap-2 rounded-full border-2 pl-2.5 pr-4 text-[1.05rem] font-semibold ${
                selected ? 'text-white' : 'bg-white text-neel-900 active:bg-neel-50'
              }`}
              style={selected ? { backgroundColor: AAC_COLOR[k], borderColor: AAC_COLOR[k] } : { borderColor: 'transparent' }}
            >
              <span aria-hidden="true" className="text-[1.2rem]">
                {AAC_GLYPH[k]}
              </span>
              <span lang="hi">{c.category}</span>
              <span className="sr-only">{c.category_en}</span>
            </button>
          )
        })}
      </div>

      <div role="tabpanel" className="mt-3 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        {current?.phrases.map((p, i) => {
          const key = `phr-${current.category}-${i}`
          const active = activeKey === key
          return (
            <button
              key={key}
              type="button"
              onClick={() => onSpeak(p.text, key)}
              className={`relative flex min-h-[5.5rem] flex-col justify-between overflow-hidden rounded-xl border-2 bg-white px-3 pb-2.5 pt-3.5 text-left transition-colors active:bg-haldi-50 ${
                active ? 'border-haldi-500 bg-haldi-50' : 'border-transparent'
              }`}
            >
              <span aria-hidden="true" className="absolute inset-x-0 top-0 h-[5px]" style={{ backgroundColor: color }} />
              <span className="flex items-start justify-between gap-2">
                <span className="text-[1.7rem] leading-none" aria-hidden="true">
                  {p.emoji ?? '💬'}
                </span>
                {active &&
                  (stage === 'fetching' ? (
                    <Spinner className="h-5 w-5 text-haldi-600" />
                  ) : (
                    <Signature peaks={peaks} bars={5} live className="h-5 w-6" barClassName="bg-haldi-600" />
                  ))}
              </span>
              <span className="mt-1.5">
                <span lang="hi" className="block text-[1.12rem] font-semibold leading-tight text-neel-950">
                  {p.text}
                </span>
                {p.en && <span className="mt-0.5 block text-[0.8rem] leading-tight text-ink-soft">{p.en}</span>}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- Type box (your side)

export type TypeStage = 'idle' | 'composing' | 'speaking'

export function TypeBox({
  stage,
  preview,
  peaks,
  playing,
  onSubmit,
}: {
  stage: TypeStage
  preview: string | null
  peaks?: number[]
  playing: boolean
  onSubmit: (text: string) => Promise<boolean>
}) {
  const [text, setText] = useState('')
  const busy = stage !== 'idle'

  const submit = async () => {
    if (!text.trim() || busy) return
    const ok = await onSubmit(text)
    if (ok) setText('')
  }

  return (
    <div>
      {preview && (
        <p lang="hi" className="mb-2 flex items-center gap-2 px-1 font-voice text-[1.35rem] text-neel-950">
          <Signature peaks={peaks} bars={5} live={playing} className="h-4 w-5 shrink-0" />
          {preview}
        </p>
      )}
      <form
        className="flex items-center gap-2 rounded-2xl border-2 border-neel-100 bg-white p-1.5 focus-within:border-neel-700"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Keyboard className="ml-2.5 h-6 w-6 shrink-0 text-neel-500" aria-hidden="true" />
        <input
          id="type-box"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="जैसे: thoda paani de do"
          aria-label="Type to speak, in Hindi or Hinglish"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          className="h-14 min-w-0 flex-1 bg-transparent px-1 text-[1.2rem] text-neel-950 placeholder:text-neel-300 focus:outline-none"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          aria-busy={busy}
          className="flex h-14 shrink-0 items-center gap-2 rounded-xl bg-haldi-500 px-5 text-[1.1rem] font-bold text-neel-950 active:bg-haldi-300 disabled:bg-neel-50 disabled:text-neel-300"
        >
          {stage === 'composing' || (stage === 'speaking' && !playing) ? (
            <Spinner className="h-5 w-5" />
          ) : stage === 'speaking' ? (
            <Signature peaks={peaks} bars={5} live className="h-5 w-6" barClassName="bg-neel-950" />
          ) : (
            <Volume2 className="h-6 w-6" />
          )}
          <span lang="hi">{stage === 'composing' ? 'समझ रहे हैं' : stage === 'speaking' ? 'बोल रहे हैं' : 'बोलिए'}</span>
        </button>
      </form>
    </div>
  )
}
