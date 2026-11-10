import { useCallback, useEffect, useState } from 'react'
import { Toast, type Notice } from './components/Toast'
import { MockBadge } from './components/ui'
import { api } from './lib/api'
import type { FriendlyError } from './lib/errors'
import { clearVoice, deletePeaks, loadVoice, saveVoice, type StoredVoice } from './lib/storage'
import { STANDARD_VOICE_NAME, Welcome } from './screens/Welcome'
import { Talk } from './screens/Talk'

type Screen = 'welcome' | 'talk'

/** `?screen=welcome|talk` forces a screen (handy for screenshots and demos). */
function forcedScreen(): Screen | null {
  try {
    const s = new URLSearchParams(window.location.search).get('screen')
    if (s === 'welcome' || s === 'talk') return s
  } catch {
    /* ignore */
  }
  return null
}

function initialScreen(hasVoice: boolean): Screen {
  return forcedScreen() ?? (hasVoice ? 'talk' : 'welcome')
}

export default function App() {
  const [voice, setVoice] = useState<StoredVoice | null>(() => loadVoice())
  const [screen, setScreen] = useState<Screen>(() => initialScreen(loadVoice() !== null))
  const [notice, setNotice] = useState<Notice | null>(null)

  const notify = useCallback((kind: 'error' | 'info', msg: FriendlyError) => {
    setNotice({ id: Date.now(), kind, ...msg })
  }, [])
  const closeNotice = useCallback(() => setNotice(null), [])

  // A voice remembered by this browser may no longer exist on the backend (deleted test
  // voices, a wiped data dir). Forget it so the app opens on Welcome instead of failing on
  // the first tap. Only a successful voice list can clear it; network errors change nothing.
  useEffect(() => {
    const stored = loadVoice()
    if (!stored?.voice_id) return
    const id = stored.voice_id
    api
      .listVoices()
      .then((list) => {
        if (list.some((v) => v.voice_id === id)) return
        clearVoice()
        deletePeaks(id)
        setVoice(null)
        if (!forcedScreen()) setScreen('welcome')
      })
      .catch(() => {})
  }, [])

  const chooseVoice = useCallback((v: StoredVoice) => {
    saveVoice(v)
    setVoice(v)
  }, [])

  const onVoiceDeleted = useCallback(
    (id: string) => {
      deletePeaks(id)
      if (voice?.voice_id === id) {
        clearVoice()
        setVoice(null)
        setScreen('welcome')
        notify('info', { hi: 'आवाज़ मिटा दी गई।', en: 'Voice deleted. You can bank a new one any time.' })
      }
    },
    [voice, notify],
  )

  const activeVoice: StoredVoice = voice ?? { voice_id: null, name: STANDARD_VOICE_NAME }

  return (
    <>
      <MockBadge />
      {screen === 'welcome' ? (
        <Welcome
          canGoBack={voice !== null}
          onBack={() => setScreen('talk')}
          onVoiceReady={chooseVoice}
          onStart={() => setScreen('talk')}
          notify={notify}
        />
      ) : (
        <Talk
          voice={activeVoice}
          onChangeVoice={chooseVoice}
          onVoiceDeleted={onVoiceDeleted}
          onRebank={() => setScreen('welcome')}
          notify={notify}
        />
      )}
      <Toast notice={notice} onClose={closeNotice} />
    </>
  )
}
