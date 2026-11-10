# Apni Awaaz: your own voice

**A speech aid that lets people who are losing their voice keep talking to their family in their *own* voice, in everyday Hindi and Hinglish.**

Built for Gnani AI's *Great Indian AI Internship Challenge* on Gnani's speech APIs.

> Ramesh lost his voice to cancer surgery. His family still hears *him*.

---

## Why

Laryngeal (throat) cancer surgery, ALS/MND and stroke can take away a person's ability to speak. India carries a large share of the world's laryngeal cancer burden. People who lose their voice can still communicate through AAC (augmentative and alternative communication) apps, but the Indian ones speak in a generic robotic voice. A father's "हाँ बेटा" no longer sounds like him.

Apni Awaaz does two things:

1. **Bank your voice.** While you can still speak, read a short Hindi passage for about 20 seconds. Gnani's voice cloning turns it into a voice embedding that is stored on your own machine.
2. **Talk in your own voice.**
   - **Listen and reply:** a family member speaks in normal Hinglish. Gnani Prisma v3 transcribes it, and an LLM proposes three short replies in Hindi. Tap one and it is spoken in your cloned voice.
   - **Phrase board:** one-tap tiles such as "पानी चाहिए" and "दर्द हो रहा है".
   - **Type:** type romanised Hinglish ("thoda paani de do"). It is converted to Devanagari and spoken in your voice.

The user always chooses what is said. Nothing is spoken automatically.

## Models and APIs

| Job | Model / API | Notes |
|---|---|---|
| Speech to text | **Gnani Prisma v3.0** (`POST /stt/v3`, `model=prisma-v3.0`, `language_code=hi-IN`, `format=codemix`) | Hindi in Devanagari with English words kept in Latin ("एक cup चाय"). About 0.4–0.6 s for a short clip |
| Voice banking | **Gnani Vachana voice-clone embeddings** (`POST /api/v1/tts/voice-clone/embeddings`) | 5–30 s of speech produces a 768-dimension speaker embedding, stored locally |
| Speaking in the user's voice | **Gnani `vachana-vc-v1`** (`POST /api/v1/tts/inference` with `speaker_embedding`) | About 2.3 s to synthesise a short reply; every clip is cached on disk |
| Fallback voice (no voice banked) | **Gnani Timbre v3.0**, voice *Prakash*, `hi-IN` | Same endpoint, stock voice |
| Reply suggestions and Hinglish→Devanagari | **NVIDIA Nemotron 3 Nano Omni 30B-A3B (reasoning)** (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`) via NVIDIA's OpenAI-compatible API | Thinking switched off for speed: suggestions p50 about 1.4 s, p95 about 1.9 s (two candidates, best reply per slot). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#4-llm-design) |

## Architecture at a glance

```
Browser (React + Vite + TS + Tailwind, :5173)
   │  microphone audio (webm/mp4) and JSON ── /api/* (Vite proxy) ──►
   ▼
FastAPI backend (Python 3.13, :8000)
   ├─ audio.py    ffmpeg: any browser audio → mono WAV, duration checks
   ├─ gnani.py    Prisma v3 STT, voice-clone embedding, VC TTS, stock TTS (+ retries, usage log)
   ├─ llm.py      Nemotron: suggest_replies(), to_devanagari()
   ├─ lexicon.py  everyday-Hindi word list used to rank LLM reply candidates
   ├─ store.py    voices on disk + TTS cache (sha256(model|voice|text))
   └─ main.py     HTTP API
```

API keys live only on the backend. Details, request flows and design decisions are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Setup

**Prerequisites:** macOS or Linux, Python 3.12+ and [uv](https://docs.astral.sh/uv/), Node 20+ and npm, and ffmpeg (`brew install ffmpeg`).

1. **Keys.** Create `apni-awaaz/.env` in the project root with these three variables. It is git-ignored; never commit it.
   ```
   GNANI_API_KEY=...
   GNANI_KEY=...          # same value as GNANI_API_KEY
   NVIDIA_API_KEY=...
   ```
2. **Backend**
   ```bash
   cd backend
   uv sync
   uv run uvicorn app.main:app --reload --port 8000
   ```
   Check it with `curl localhost:8000/api/health`. It should return `{"ok":true,"gnani_key":true,"llm_key":true}`.
3. **Frontend** (in a second terminal)
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
   Open <http://localhost:5173> in Chrome and allow microphone access.

### Tests and checks

```bash
cd backend && uv run pytest -q                      # unit tests, all HTTP mocked (respx), no credits used
cd frontend && npm run build                        # type-check + production build
cd backend && uv run python ../scripts/eval_llm.py  # live LLM quality + latency eval (NVIDIA only, no Gnani)
scripts/smoke_e2e.sh                                # live API smoke against a running backend (about 3 Gnani calls)
PUPPETEER_DIR=/tmp/apni-pw node scripts/e2e_mic.mjs # live browser flow with a fake mic; see the header of the file
scripts/prewarm_demo.sh <voice_id>                  # cache the demo clips in a voice (at most 3 Gnani calls, then free)
```

### Mock mode (frontend development only)

`VITE_MOCK=1 npm run dev` swaps the backend for canned, contract-shaped responses, so UI work costs no credits. It is **off by default**, and the UI shows a **MOCK** badge whenever it is on. The demo video runs on the real APIs.

## Ethics, consent and privacy

- **Consent first.** Voice banking requires ticking *"This is my own voice and I agree to it being cloned."* The backend rejects enrolment without it. Only clone your own voice.
- **Your voice stays with you.** Voice embeddings and samples are stored on the local machine (`backend/data/voices/`). Deleting a voice also deletes every cached clip spoken in it.
- **The user is always in control.** The LLM only *suggests*. The user picks a reply, a phrase, or types their own words, and nothing is spoken without a tap.
- **Third-party processing.** Audio is sent to Gnani for transcription and cloning, and transcripts are sent to NVIDIA's API for reply suggestions. No keys or data are sent from the browser to these services directly.
- **Synthetic data only.** The demo persona (Ramesh Sharma, 58, and his family) is fictional. No real patients' voices or data were used.
- **Communication aid, not a medical device.** It does not diagnose, monitor or treat anything.

## Honest limitations

- LLM thinking is off for latency, so a suggestion is occasionally odd. To compensate, the backend asks for two candidates in parallel and keeps the best reply per slot. Small rule-based checks drop replies that take over an action the speaker offered, that use the wrong grammatical gender, or that contain invented words. If the LLM fails, the app falls back to generic replies, and the user can always use the phrase board or type.
- "More options" deliberately samples at a higher temperature, so its suggestions vary more, and occasionally one slot falls back to a generic reply.
- The user's grammatical gender is set by an environment variable (`APNI_USER_GENDER`, default `male`) rather than in the UI.
- Voice cloning quality depends on a clean, quiet 20 s recording.

## Models & APIs used (paste-ready for the submission form)

- Gnani Prisma v3.0: speech-to-text (`/stt/v3`, `prisma-v3.0`, `hi-IN`, codemix output)
- Gnani Vachana voice cloning: speaker embedding from about 20 s of the user's own voice (`/api/v1/tts/voice-clone/embeddings`)
- Gnani Vachana `vachana-vc-v1`: text-to-speech in the user's cloned voice (`/api/v1/tts/inference`)
- Gnani Timbre v3.0 (voice "Prakash", `hi-IN`): stock-voice fallback when no voice is banked
- NVIDIA Nemotron 3 Nano Omni 30B-A3B (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, thinking off): Hindi reply suggestions and Hinglish→Devanagari, via the NVIDIA API
- Stack: FastAPI (Python 3.13) backend, React + Vite + TypeScript + Tailwind frontend, ffmpeg
