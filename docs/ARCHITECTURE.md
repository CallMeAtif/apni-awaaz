# Apni Awaaz: architecture

## 1. Components

```
┌──────────────────────────── Browser (React 19 + Vite + TS + Tailwind, :5173) ───────────────────────────┐
│ Welcome screen: bank your voice (passage, consent, record, "hear yourself")                             │
│ Talk screen:    Listen (सुनिए) → transcript → 3 suggestions → tap = speak · phrase board · type box     │
│ MediaRecorder (webm/opus or mp4) · audio playback · voice_id kept in localStorage                       │
└───────────────────────────────────────┬─────────────────────────────────────────────────────────────────┘
                                        │ /api/*  (Vite dev proxy → :8000; JSON + multipart; no keys in browser)
┌───────────────────────────────────────▼────────────── FastAPI backend (Python 3.13, uv, :8000) ─────────┐
│ main.py    routes, error envelope {"error":{"code","message"}}, timings                                │
│ audio.py   ffmpeg: browser audio → mono WAV (16 kHz for STT, 44.1 kHz for enrolment), duration checks   │
│ gnani.py   httpx client for api.vachana.ai: STT, embedding, VC TTS, stock TTS; 429 retry; usage.jsonl  │
│ llm.py     NVIDIA Nemotron: suggest_replies(), to_devanagari()                                          │
│ store.py   data/voices/{voice_id}.json (+ sample), TTS cache data/cache/tts/{voice}/{sha256}.wav       │
│ phrases.py phrase board data · passage.py voice-banking passage                                        │
└──────────────┬──────────────────────────────────────────────────────────────┬──────────────────────────┘
               │ X-API-Key-ID, User-Agent: ApniAwaaz/0.1                      │ Bearer NVIDIA_API_KEY
       ┌───────▼──────────────────────────┐                     ┌─────────────▼───────────────────────────────┐
       │ Gnani (api.vachana.ai)           │                     │ NVIDIA API (integrate.api.nvidia.com)       │
       │ Prisma v3.0 STT                  │                     │ nvidia/nemotron-3-nano-omni-30b-a3b-        │
       │ Vachana voice-clone embeddings   │                     │ reasoning, thinking OFF                     │
       │ vachana-vc-v1 TTS · Timbre v3.0  │                     └─────────────────────────────────────────────┘
       └──────────────────────────────────┘
```

Secrets are loaded from the project-root `.env` by the backend only (python-dotenv). The frontend never sees a key.

## 2. Request flows

### Bank a voice: `POST /api/voices` (multipart `audio`, `name`, `consent=true`)
1. Reject the request without consent; save the upload.
2. ffmpeg converts it to 44.1 kHz mono WAV. Under 5 s is rejected (`AUDIO_TOO_SHORT`); over 30 s is trimmed to 30 s.
3. Gnani `voice-clone/embeddings` returns `{embedding (base64 bf16), shape [1,768], dtype}`.
4. The embedding and sample are stored as `data/voices/{voice_id}.json`, and the response is `201 {voice_id, name, duration_s, created_at}`.
5. The frontend immediately calls `/api/speak` with a short greeting, so the user hears their clone ("Hear yourself").

### Listen and suggest: `POST /api/listen` (multipart `audio`, optional `history`)
1. ffmpeg converts the audio to 16 kHz mono WAV (Gnani STT detects the format by file extension and does not accept webm).
2. Prisma v3: `model=prisma-v3.0`, `language_code=hi-IN`, `format=codemix` returns, for example, "पापा, आज खाने में क्या बनाऊँ? दाल चावल चलेगा?". An empty transcript returns `suggestions: []` with no LLM call.
3. `llm.suggest_replies(transcript, history)` returns 3 replies `{text, intent}`. The result is deterministic: rehearsing a line and then saying it again on camera gives the same suggestions.
4. The response is `{transcript, suggestions, timings: {stt_ms, llm_ms}}`.

`POST /api/suggest` (the "More options" button) calls `llm.suggest_replies(..., variety=True)`, which samples at a higher temperature to get different suggestions.

### Warm up: `POST /api/warmup` → `{llm: bool, gnani: bool, ms}`
This opens the pooled NVIDIA connection with one 1-token completion. It opens the Gnani connection with an **unauthenticated** `GET /` (empty key header, returns 401), which is never billed and not written to `usage.jsonl`. It runs at backend startup (`APNI_WARMUP_ON_STARTUP`, switched off in tests), when the Talk screen mounts, and every 4 minutes while the tab is visible. It takes about 0.3 s. It exists because one cold first call took 6 s during the build.

### Speak: `POST /api/speak` (`{voice_id|null, text ≤ 300 chars}`)
1. Normalise the text (whitespace and Unicode NFC, so either encoding of "ड़" gives the same key), then look up the cache key `sha256(model|voice_id|text)`. A **HIT** returns the WAV immediately (`X-Cache: HIT`). `scripts/prewarm_demo.sh <voice_id>` fills the cache for the demo lines.
2. On a MISS, text longer than about 180 characters is split on sentence boundaries. Each chunk is sent to `vachana-vc-v1` with the stored `speaker_embedding`, or to Timbre v3.0 *Prakash* when `voice_id` is null. The WAVs are concatenated, cached and returned (`X-Cache: MISS`).

### Type: `POST /api/compose` (`{text}`)
`llm.to_devanagari(text)` turns "thoda paani de do" into "थोड़ा पानी दे दो।". The frontend then calls `/api/speak` with the result.

## 3. Latency budget (measured during the build)

The browser timings come from `scripts/e2e_mic.mjs`: headless Chrome, a fake mic, and the real APIs.

| Step | Target | Measured |
|---|---|---|
| Tap Listen → mic live | — | 0.2–0.3 s |
| Stop → transcript + 3 suggestions (browser) | < 3.5 s | 1.8 s at 1280 px; 1.8–2.7 s overall. Server side: convert ≈ 0.13 s, Prisma STT ≈ 0.23–0.33 s, LLM 1.3–2.2 s |
| Transcript → suggestions (Nemotron, thinking off) | < 2 s | p50 ≈ 1.4 s, p95 ≈ 1.9 s (`scripts/eval_llm.py`, 14 cases) |
| Tap a suggestion or phrase → audio starts | < 3 s | Cache HIT 33–86 ms; MISS 1.7–2.4 s (Gnani VC TTS ≈ 1.7–2.9 s) |
| Type Hinglish → Speak → audio starts | — | ≈ 0.5–0.6 s with the clip cached (≈ 0.45 s is the transliteration); ≈ 3.9 s uncached |
| Bank voice: Save → "Voice saved" | — | ≈ 1.0–1.1 s (embedding ≈ 0.8–0.9 s) |

Every wait has a visible state in the UI ("सुन रहे हैं…", "समझ रहे हैं…", "बोल रहे हैं…").

## 4. LLM design

**Model:** `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, called with httpx over the OpenAI-compatible endpoint. One pooled `AsyncClient` per event loop keeps connections alive for 300 s; httpx's default is 5 s, after which every pause would cost a new TLS handshake. `llm.warmup()` opens the connection (see `/api/warmup`).

**Thinking off.** With reasoning on, the replies were good but took about 5.8 s. With `chat_template_kwargs: {"enable_thinking": false}` the call takes about 1 s. We verified that a `/no_think` prompt prefix *alone* does not work on this endpoint: `content` comes back null and the answer is in `reasoning_content`. The client therefore uses the kwarg, and the parser falls back to `reasoning_content` anyway.

**Quality through the prompt, not reasoning.** Without thinking, early outputs had two kinds of failure. Some were nonsense ("बस इतना ही कहना है।"); others took the speaker's role, as when the daughter offers to cook and Papa answers "मैं बना लूँगा". The prompt fixes most of this:
- *Lightweight structured notes first.* The model writes `speaker` (child/spouse/doctor/other), `gist` (a ≤12-word English summary) and `offer` (what the speaker offered to do) before the replies. These few English tokens act as a tiny chain of thought for about 0.1 s.
- *A speaker hint computed in code.* Address cues decide who is speaking more reliably than the model does with thinking off: पापा/papa means a child, सुनिए/सुनो जी means a spouse. Code detects them in the utterance or earlier family turns and adds a line such as `Speaker: spouse (they said "सुनिए"). They only mention the doctor; reply to them, not to the doctor.` This fixed a live miss where a wife relaying the doctor's message was answered "ठीक है डॉक्टर साहब".
- *Explicit role rules.* Replies are first-person, said by the user to the speaker, and about the user's own wishes and body. An offer stays the speaker's job: "…दूँ?" is answered with "हाँ, कर दो", never "कर दूँगा". "बेटा" is used only with a child, never with a spouse.
- *Three distinct intents in a fixed order:* `yes` (accept), `no` (decline or a simple alternative), then `ask` or `feeling`.
- *Five few-shot examples* written as Prisma-style codemix input with compact JSON answers. Prompt length dominates latency: about 0.45 ms per prompt token, and Devanagari is token-heavy. Going from 1,840 to 1,060 prompt tokens cut p50 from about 1.6 s to about 1.1 s, so the examples are few and chosen for coverage: a child's offer, a spouse with English words, a self-report question, an announcement, and an offer with an alternative.

**Robust parsing.** The parser strips `<think>` blocks and code fences, then scans every decodable JSON value and takes the last valid one. If the outer structure is broken it recovers the individual `{intent, text}` objects; we saw the model close the list with `}}` instead of `]}`. Each reply must be mostly Devanagari and at most 120 characters, and gets a terminal danda if it has none.

**Devanagari for TTS.** Codemix transcripts tempt the model to copy English words in Latin ("online", "TV"). A small word map converts common words locally (ऑनलाइन, टीवी). Any Latin left over triggers one parallel transliteration call, within the remaining time budget.

**Two candidates, best reply per slot.** Each call sends two requests in parallel:
- `/api/listen`: temperatures 0.0 and 0.5. If the temperature-0 candidate is fully clean (all slots filled, nothing dropped, no unknown words), it is returned as soon as it arrives and the other request is cancelled. The usual latency is therefore one call, and the output is deterministic.
- Otherwise the second candidate is awaited and the two are merged slot by slot (yes / no / third). Each slot takes the reply with the fewest unrecognised words, with ties going to temperature 0.
- A reply with two or more unrecognised words is treated as garbled and replaced by a generic one.
- "Words" are checked against `app/lexicon.py`: about 3,000 normalised everyday Hindi forms, plus the words of the transcript, the history and the few-shot examples. It catches invented words like "कड़ी पत्ता चिकन", "कड़ी क़ीद्दारी" and "कड़ीची".
- `variety=True` ("More options") uses temperatures 0.6 and 0.8.

**Safety net (roles and gender).** A rule-based check drops a reply that:
- uses the wrong grammatical gender in the first person (e.g. "खाऊँगी" for a male user), or
- after the speaker offered something ("…दूँ?", "…बनाऊँ?", "मैं … चलूँगी"), has the user take it over:
  - a first-person form of दे: "मैं बना दूँ?", "भर दूँगा", "पेमेंट कर दूँगा";
  - "ले आऊँगा";
  - "X कर लूँगा", unless X is the user's own business (आराम, इंतज़ार, बात…);
  - or the offered verb in the first person: "बनाऊँगा", "बनाऊँ", "बनाता हूँ".

A dropped reply's slot is filled from the other candidate, or with a generic reply.

**Failure behaviour.** Neither function ever raises.
- An 8 s total budget covers both attempts. 429, 5xx and connection errors are retried once; other errors are not.
- If the key is missing, the request times out, an HTTP error occurs or the output cannot be parsed, the user gets generic replies ("हाँ, ठीक है।" / "नहीं, अभी नहीं।" / "एक बार फिर से बताओ?").
- `to_devanagari` returns its input on failure. Input that is already mostly Devanagari short-circuits with no LLM call.

**Grammatical gender:** set by `APNI_USER_GENDER` (`male` by default, the demo persona; or `female`).

## 5. Credits and cost control

Gnani credits are scarce: 5,000 for the whole contest. TTS is billed per character and STT per audio second.

- **TTS disk cache** keyed by `sha256(model|voice_id|NFC text)`. A phrase tile costs credits once per voice. Deleting a voice purges its clips.
- **Warmup is free.** It uses the unauthenticated `GET /`; no TTS or STT call is made.
- **Usage log:** every live Gnani attempt appends a JSON line to `backend/data/usage.jsonl` (ts, endpoint, chars or audio seconds, status, ms).
- **Tests never call Gnani or NVIDIA.** respx mocks every request, and an unmocked request fails the test.
- The phrase board totals about 540 characters. Warm only the tiles you need rather than the whole board.

## 6. Error model

All errors use `{"error": {"code", "message"}}`:

| Status | Codes |
|---|---|
| 400 | `BAD_REQUEST`, `AUDIO_TOO_SHORT`, `BAD_AUDIO` |
| 402 | `OUT_OF_CREDITS` |
| 404 | `NOT_FOUND` |
| 413 | `AUDIO_TOO_LONG` |
| 502 / 503 / 504 | `UPSTREAM_ERROR` |

LLM problems never surface as errors; they degrade to generic suggestions.

## 7. Testing

- `backend/tests/`: pytest + pytest-asyncio + respx, run with `cd backend && uv run pytest -q`.
  - `test_llm.py` covers:
    - JSON parsing (think blocks, fences, broken outer JSON, bare lists) and Latin handling;
    - the role/gender safety net and the per-slot merge of two candidates, including the early return on a clean temperature-0 answer;
    - speaker hints and the lexicon (including nukta normalisation);
    - fallback on timeout/500/401/garbage/connection errors, retry on 429, deterministic listen vs. variety;
    - the Devanagari short-circuit.
  - `test_api.py` covers `/api/warmup`: no billable call, an empty key header, and it never fails.
- `scripts/eval_llm.py` is a live NVIDIA-only quality and latency eval: 11 synthetic family utterances, 3 "More options" cases and 5 transliteration cases.
- `scripts/smoke_e2e.sh` is a live API smoke test against a running backend (about 3 Gnani calls).
- `scripts/e2e_mic.mjs` drives headless Chrome with a fake microphone through banking, "hear yourself", Listen → suggestions → tap → phrase → type. It runs at 1280×800 and 390×844, saves screenshots to `docs/qa/screenshots/`, and records client-side timings.
  - It needs `--disable-features=AudioServiceOutOfProcess`: Chrome's sandboxed audio service on macOS cannot read the fake-capture file and silently records silence.

## 8. Stretch (not in the MVP)

`WS /api/live`: live captions through Gnani's streaming STT (`wss://api.vachana.ai/stt/v3/stream`). Browsers cannot send the auth header, so the backend would proxy the socket. It is deliberately left out until the MVP is integrated, because reliability matters more than features.
