import type { ReactNode } from 'react'
import { IS_MOCK } from '../lib/api'

/** Hindi first, English small. Used sparingly: only where a mixed audience needs the gloss. */
export function Bi({
  hi,
  en,
  className = '',
  enClassName = '',
  stacked = true,
}: {
  hi: ReactNode
  en: ReactNode
  className?: string
  enClassName?: string
  stacked?: boolean
}) {
  return (
    <span className={`${stacked ? 'flex flex-col' : 'inline-flex items-baseline gap-2'} ${className}`}>
      <span lang="hi" className="leading-snug">
        {hi}
      </span>
      <span lang="en" className={`text-[0.72em] font-normal leading-tight opacity-75 ${enClassName}`}>
        {en}
      </span>
    </span>
  )
}

/** Your voiceprint: bars drawn from the input levels recorded while banking the voice. */
export function Signature({
  peaks,
  live = false,
  className = '',
  barClassName = 'bg-haldi-500',
  bars,
}: {
  peaks?: number[]
  live?: boolean
  className?: string
  barClassName?: string
  /** Optionally resample to fewer bars for small sizes. */
  bars?: number
}) {
  const src = peaks && peaks.length ? peaks : FLAT
  const data = bars ? resample(src, bars) : src
  return (
    <span aria-hidden="true" className={`flex items-center gap-[2px] ${className}`}>
      {data.map((p, i) => (
        <span
          key={i}
          className={`block min-w-[2px] flex-1 rounded-full ${barClassName} ${live ? 'sig-bar-live' : ''}`}
          style={{
            height: `${Math.max(12, Math.min(100, p * 100))}%`,
            animationDelay: live ? `${(i % 9) * 0.07}s` : undefined,
          }}
        />
      ))}
    </span>
  )
}

const FLAT = [0.3, 0.5, 0.7, 0.45, 0.85, 0.6, 0.35, 0.7, 0.5, 0.3, 0.55, 0.4]

function resample(src: number[], n: number): number[] {
  if (src.length <= n) return src
  return Array.from({ length: n }, (_, i) => {
    const a = Math.floor((i * src.length) / n)
    const b = Math.floor(((i + 1) * src.length) / n)
    return Math.max(...src.slice(a, Math.max(b, a + 1)))
  })
}

/** Wordmark: a five-bar voiceprint and the name set in the "your voice" face. */
export function Wordmark({ tone = 'dark', compact = false }: { tone?: 'dark' | 'light'; compact?: boolean }) {
  const ink = tone === 'light' ? 'text-white' : 'text-neel-900'
  return (
    <span className="flex items-center gap-2.5">
      <Signature peaks={[0.4, 0.75, 1, 0.6, 0.35]} className="h-7 w-8 shrink-0" />
      {compact ? (
        <span className="sr-only">Apni Awaaz</span>
      ) : (
        <span lang="hi" className={`font-voice text-[1.55rem] leading-none ${ink}`}>
          अपनी आवाज़
        </span>
      )}
    </span>
  )
}

export function MockBadge() {
  if (!IS_MOCK) return null
  return (
    <div
      role="status"
      aria-label="Mock mode: responses are canned, not live"
      className="pointer-events-none fixed bottom-2 left-2 z-[100] rounded-md border-2 border-white bg-brick px-3 py-1 text-xs font-bold text-white shadow-md"
    >
      MOCK: canned data, not live
    </div>
  )
}

export function Footer({ className = '' }: { className?: string }) {
  return (
    <footer className={`text-[0.8rem] leading-snug text-ink-soft ${className}`}>
      <span lang="hi">संवाद सहायक है, चिकित्सा उपकरण नहीं।</span> Communication aid, not a medical device. Built on
      Gnani AI.
    </footer>
  )
}

export function Spinner({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block animate-spin rounded-full border-[3px] border-current border-r-transparent ${className}`}
    />
  )
}

/** Three bouncing dots: the app is working on something you'll say. */
export function Thinking({ className = '' }: { className?: string }) {
  return (
    <span aria-hidden="true" className={`inline-flex items-end gap-1.5 ${className}`}>
      {[0, 1, 2].map((i) => (
        <span key={i} className="think-dot block h-2.5 w-2.5 rounded-full bg-haldi-500" style={{ animationDelay: `${i * 0.15}s` }} />
      ))}
    </span>
  )
}
