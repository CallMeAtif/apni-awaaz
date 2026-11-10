import type { ErrorCode } from './types'

export class ApiError extends Error {
  readonly code: ErrorCode
  readonly status: number

  constructor(code: ErrorCode, message: string, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

export interface FriendlyError {
  hi: string
  en: string
}

/** Context lets the same code read naturally on different screens. */
export type ErrorContext = 'enroll' | 'listen' | 'speak' | 'compose' | 'general'

export function friendlyError(err: unknown, ctx: ErrorContext = 'general'): FriendlyError {
  const code: ErrorCode = err instanceof ApiError ? err.code : 'UNKNOWN'
  switch (code) {
    case 'AUDIO_TOO_SHORT':
      return ctx === 'enroll'
        ? { hi: 'रिकॉर्डिंग छोटी है। कम से कम 5 सेकंड पढ़िए।', en: 'Too short. Please read for at least 5 seconds.' }
        : { hi: 'बात बहुत छोटी थी। फिर से बोलकर देखिए।', en: 'That was too short to hear. Please try again.' }
    case 'AUDIO_TOO_LONG':
      return { hi: 'रिकॉर्डिंग बहुत लंबी है।', en: 'That recording is too long. Keep it under 30 seconds.' }
    case 'BAD_AUDIO':
      return { hi: 'आवाज़ साफ़ नहीं आई। फिर से रिकॉर्ड कीजिए।', en: "We couldn't read that audio. Please record again." }
    case 'OUT_OF_CREDITS':
      return {
        hi: 'आवाज़ सेवा की सीमा पूरी हो गई है।',
        en: 'The voice service has run out of credits. Phrases spoken before may still work.',
      }
    case 'UPSTREAM_ERROR':
      return { hi: 'सेवा अभी जवाब नहीं दे रही।', en: "The speech service didn't respond. Try again in a moment." }
    case 'NOT_FOUND':
      return ctx === 'speak'
        ? { hi: 'यह आवाज़ नहीं मिली।', en: 'This voice was not found. Choose another from settings or bank it again.' }
        : { hi: 'यह चीज़ नहीं मिली।', en: 'That item was not found.' }
    case 'BAD_REQUEST':
      return ctx === 'enroll'
        ? { hi: 'नाम और सहमति ज़रूरी है।', en: 'Please add your name and tick the consent box.' }
        : { hi: 'यह अनुरोध पूरा नहीं हो सका।', en: "That request couldn't be completed. Please try again." }
    case 'NETWORK':
      return { hi: 'सर्वर से जुड़ नहीं पाए।', en: "Can't reach the app server. Check that it is running." }
    case 'MIC_DENIED':
      return { hi: 'माइक की अनुमति दीजिए।', en: 'Allow microphone access in your browser to record.' }
    case 'MIC_UNAVAILABLE':
      return { hi: 'माइक नहीं मिला।', en: 'No microphone found. Connect one and try again.' }
    case 'PLAYBACK':
      return { hi: 'आवाज़ चल नहीं पाई।', en: "The audio couldn't play. Tap again to retry." }
    default:
      return { hi: 'कुछ गड़बड़ हो गई।', en: 'Something went wrong. Please try again.' }
  }
}

/** Pull a contract-shaped error out of a failed Response, defensively. */
export async function errorFromResponse(res: Response): Promise<ApiError> {
  let code: ErrorCode = res.status === 402 ? 'OUT_OF_CREDITS' : res.status === 404 ? 'NOT_FOUND' : 'UNKNOWN'
  let message = `HTTP ${res.status}`
  try {
    const body: unknown = await res.json()
    const e = (body as { error?: { code?: string; message?: string }; detail?: unknown })?.error
    if (e?.code) code = e.code as ErrorCode
    if (e?.message) message = e.message
    else if ((body as { detail?: unknown })?.detail) {
      // FastAPI default validation errors arrive as {"detail": ...}
      code = res.status === 422 ? 'BAD_REQUEST' : code
      message = JSON.stringify((body as { detail: unknown }).detail)
    }
  } catch {
    if (res.status >= 500 && code === 'UNKNOWN') code = 'UPSTREAM_ERROR'
  }
  if (code === 'UNKNOWN' && res.status >= 500) code = 'UPSTREAM_ERROR'
  if (code === 'UNKNOWN' && res.status >= 400) code = 'BAD_REQUEST'
  return new ApiError(code, message, res.status)
}
