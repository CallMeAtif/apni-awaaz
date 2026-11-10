import { Check, Mic, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { friendlyError, type FriendlyError } from '../lib/errors'
import { loadPeaks, type StoredVoice } from '../lib/storage'
import type { Voice } from '../lib/types'
import { Bi, Signature, Spinner } from './ui'

interface Props {
  open: boolean
  current: StoredVoice
  standardName: string
  onClose: () => void
  onChoose: (v: StoredVoice) => void
  onDeleted: (voiceId: string) => void
  onRebank: () => void
  notify: (kind: 'error' | 'info', msg: FriendlyError) => void
}

/** Voice management: switch, delete (with confirmation), or bank a new voice. Tap-only, no hover. */
export function SettingsSheet({ open, current, standardName, onClose, onChoose, onDeleted, onRebank, notify }: Props) {
  const [voices, setVoices] = useState<Voice[] | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    api
      .listVoices()
      .then(setVoices)
      .catch((e) => {
        setVoices([])
        notify('error', friendlyError(e))
      })
  }, [open, notify])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const remove = async (id: string) => {
    setDeleting(id)
    try {
      await api.deleteVoice(id)
      setVoices((vs) => (vs ?? []).filter((v) => v.voice_id !== id))
      setConfirming(null)
      onDeleted(id)
    } catch (e) {
      notify('error', friendlyError(e))
    } finally {
      setDeleting(null)
    }
  }

  // Include the current voice even if the server list hasn't loaded or doesn't know it.
  const list: Voice[] = voices ?? []
  const rowBase = 'flex min-h-16 flex-1 items-center gap-3 rounded-2xl border-2 px-4 text-left'

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label="Voice settings">
      <button type="button" aria-label="Close settings" className="absolute inset-0 bg-neel-950/40" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-[440px] flex-col overflow-y-auto bg-chalk shadow-2xl">
        <div className="flex items-center justify-between border-b-2 border-neel-100 bg-white px-5 py-3">
          <Bi hi="आवाज़ की सेटिंग" en="Voice settings" className="text-[1.2rem] font-bold text-neel-900" />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="grid h-14 w-14 place-items-center rounded-2xl text-neel-900 active:bg-neel-50"
          >
            <X className="h-7 w-7" />
          </button>
        </div>

        <div className="flex-1 space-y-6 p-5">
          <section>
            <Bi hi="आवाज़ चुनिए" en="Choose a voice" className="mb-3 text-[1.05rem] font-semibold text-neel-900" />
            <ul className="space-y-2">
              {voices === null && (
                <li className="flex min-h-16 items-center gap-3 px-2 text-ink-soft">
                  <Spinner /> Loading voices…
                </li>
              )}
              {list.map((v) => {
                const selected = current.voice_id === v.voice_id
                const peaks = loadPeaks(v.voice_id)
                return (
                  <li key={v.voice_id}>
                    {confirming === v.voice_id ? (
                      <div className="rounded-2xl border-2 border-brick/40 bg-brick-50 p-3">
                        <p className="px-1 text-brick">
                          <span lang="hi" className="font-hindi font-semibold">
                            “{v.name}” की आवाज़ हमेशा के लिए मिटाएँ?
                          </span>
                          <br />
                          Delete this voice permanently?
                        </p>
                        <div className="mt-3 flex gap-2">
                          <button
                            type="button"
                            onClick={() => remove(v.voice_id)}
                            disabled={deleting === v.voice_id}
                            className="flex min-h-14 flex-1 items-center justify-center gap-2 rounded-2xl bg-brick px-4 font-bold text-white disabled:opacity-60"
                          >
                            {deleting === v.voice_id ? <Spinner /> : <Trash2 className="h-5 w-5" />}
                            <Bi hi="हाँ, मिटाएँ" en="Yes, delete" className="text-left" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirming(null)}
                            className="min-h-14 flex-1 rounded-2xl border-2 border-neel-200 bg-white px-4 text-neel-900"
                          >
                            <Bi hi="रहने दें" en="Keep it" />
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => onChoose({ voice_id: v.voice_id, name: v.name, peaks })}
                          aria-pressed={selected}
                          className={`${rowBase} ${selected ? 'border-haldi-500 bg-haldi-50' : 'border-neel-100 bg-white active:bg-neel-50'}`}
                        >
                          <Signature peaks={peaks} bars={12} className="h-6 w-14 shrink-0" />
                          <span className="flex-1">
                            <span lang="hi" className="block font-hindi text-[1.1rem] font-semibold text-neel-900">
                              {v.name} की आवाज़
                            </span>
                            <span className="block font-hindi text-sm text-ink-soft">
                              Banked {formatDate(v.created_at)}
                            </span>
                          </span>
                          {selected && <Check className="h-6 w-6 text-haldi-700" strokeWidth={3} />}
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirming(v.voice_id)}
                          aria-label={`Delete ${v.name}'s voice`}
                          className="grid min-h-16 w-16 shrink-0 place-items-center rounded-2xl border-2 border-neel-100 bg-white text-brick active:bg-brick-50"
                        >
                          <Trash2 className="h-6 w-6" />
                        </button>
                      </div>
                    )}
                  </li>
                )
              })}
              <li>
                <button
                  type="button"
                  onClick={() => onChoose({ voice_id: null, name: standardName })}
                  aria-pressed={current.voice_id === null}
                  className={`${rowBase} w-full ${current.voice_id === null ? 'border-haldi-500 bg-haldi-50' : 'border-neel-100 bg-white active:bg-neel-50'}`}
                >
                  <span className="flex-1">
                    <span lang="hi" className="block font-hindi text-[1.1rem] font-semibold text-neel-900">
                      {standardName}
                    </span>
                    <span className="block text-sm text-ink-soft">Standard voice (not your own)</span>
                  </span>
                  {current.voice_id === null && <Check className="h-6 w-6 text-haldi-700" strokeWidth={3} />}
                </button>
              </li>
            </ul>
          </section>

          <button
            type="button"
            onClick={onRebank}
            className="flex min-h-16 w-full items-center gap-3 rounded-2xl bg-neel-900 px-5 text-left font-bold text-white active:bg-neel-800"
          >
            <Mic className="h-6 w-6" />
            <Bi hi="नई आवाज़ बचाइए" en="Bank a new voice" className="text-[1.1rem]" />
          </button>

          <p className="text-sm leading-relaxed text-ink-soft">
            Deleting a voice removes it from this app's server. Recordings are only ever used to create your voice.
          </p>
        </div>
      </div>
    </div>
  )
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}
