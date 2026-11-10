import { AlertCircle, Info, X } from 'lucide-react'
import { useEffect } from 'react'

export interface Notice {
  id: number
  kind: 'error' | 'info'
  hi: string
  en: string
}

/** One friendly message at a time, above the footer. Never hover-only; 56 px close target. */
export function Toast({ notice, onClose }: { notice: Notice | null; onClose: () => void }) {
  useEffect(() => {
    if (!notice) return
    const t = window.setTimeout(onClose, notice.kind === 'error' ? 9000 : 6000)
    return () => window.clearTimeout(t)
  }, [notice, onClose])

  if (!notice) return null
  const isErr = notice.kind === 'error'
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex justify-center px-4">
      <div
        key={notice.id}
        role={isErr ? 'alert' : 'status'}
        className={`pointer-events-auto flex w-full max-w-xl items-center gap-3 rounded-2xl border-2 py-2 pl-4 pr-2 shadow-lg ${
          isErr ? 'border-brick/30 bg-brick-50 text-brick' : 'border-neel-200 bg-white text-neel-900'
        }`}
      >
        {isErr ? <AlertCircle className="h-6 w-6 shrink-0" /> : <Info className="h-6 w-6 shrink-0" />}
        <div className="min-w-0 flex-1 py-1">
          <p lang="hi" className="font-hindi text-[1.05rem] font-semibold leading-snug">
            {notice.hi}
          </p>
          <p className="text-[0.9rem] leading-snug text-neel-900/80">{notice.en}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close message"
          className="grid h-14 w-14 shrink-0 place-items-center rounded-xl text-neel-900/70 active:bg-black/5"
        >
          <X className="h-6 w-6" />
        </button>
      </div>
    </div>
  )
}
