# Apni Awaaz — Team Brief (read fully before writing code)

Project root: the repository root (`apni-awaaz/`)
Written 10 Nov 2026 ~00:45 IST by the engineering manager (the orchestrating Claude session).

## 1. Why this exists and what "winning" means

This is the entry of a two-student team (Mumbai) for **Gnani AI's "Great Indian AI Internship Challenge"**.
- **Hard deadline: 10 Nov 2026, 11:59 PM IST.** No extensions.
- The entry must be a working app built on Gnani's APIs, shown working in a **public 60-second video** on LinkedIn/X.
- **Interview shortlists are decided by real engagement on that post.** The demo therefore has to work flawlessly on camera, look polished, and be emotionally clear within 5 seconds.
- Gnani interviewers will then read the code, so it must be clean and honest: no faked demo, no hard-coded "AI" output pretending to be live.
- Timeline: **feature-complete MVP by ~05:00 IST**, integrated and polished by ~07:00. The humans record the video ~08:00–09:30 and post by 10:30.
- **Scope rule: reliability > features.** Anything that might break on camera gets cut.

## 2. The product

**Apni Awaaz ("your own voice")** is a speech aid (AAC app) for people who are losing or have lost their ability to speak: throat/laryngeal cancer surgery, ALS/MND, stroke. India carries a large share of the world's laryngeal cancer burden. Existing Indian AAC apps speak in a generic robot voice.

The user does two things:
1. **Bank their voice:** record ~20 s of their own speech while they still can. Gnani voice cloning turns this into a voice embedding.
2. **Talk in their own voice:**
   - **Listen:** the family speaks in everyday Hinglish. Gnani Prisma v3 transcribes it and the app shows the text.
   - **Reply:** the LLM proposes three short replies in Hindi. The user taps one and it is spoken **in their own cloned voice**.
   - **Phrase board:** one-tap tiles like "पानी चाहिए" and "दर्द हो रहा है", spoken in their voice.
   - **Type:** the user types in romanised Hinglish ("mujhe neend aa rahi hai"). The app converts it to Devanagari and speaks it in their voice.

**Fictional demo persona (synthetic data only, never real patients):**
- Ramesh Sharma, 58, retired teacher, Mumbai. Lost his voice after laryngectomy.
- Family: daughter Priya, wife Sunita.

**Ethics features (also interview talking points):**
- Consent checkbox on voice banking: "This is my own voice and I agree to it being cloned."
- Voices are stored locally and can be deleted.
- Footer note: "Communication aid, not a medical device."

### 60-second demo shot list (build for exactly this)
1. **0–5 s.** Hook text: "Ramesh lost his voice to cancer surgery. His family still hears *him*."
2. **5–15 s.** Voice banking screen. Ramesh reads the passage for 20 s, then "Voice saved ✓", then "Hear yourself" plays his clone.
3. **15–40 s.** The Talk screen.
   - Priya, off-camera, taps Listen and says: "Papa, aaj khaane mein kya banaun? Dal chawal chalega?"
   - The transcript appears, followed by 3 suggestions.
   - Ramesh taps one, and *his own voice* says "हाँ बेटा, दाल चावल बढ़िया रहेगा।"
4. **40–50 s.** He taps the phrase tile "दर्द हो रहा है". Then he types "thoda paani de do" and the app speaks it in his voice.
5. **50–60 s.** End card: "Built on Gnani Prisma v3 + Vachana voice cloning".

**Latency targets per step:**

| Step | Target |
|---|---|
| Listen → transcript | < 1.5 s |
| Transcript → suggestions | < 2 s |
| Tap → audio starts | < 3 s |

Every wait needs a visible state ("सुन रहे हैं…", "समझ रहे हैं…", "बोल रहे हैं…").

## 3. Architecture and stack

```
Browser (React + Vite + TS + Tailwind, port 5173)
   │  MediaRecorder (webm/opus or mp4) ──multipart──►
   ▼  /api/* proxied by Vite to :8000
FastAPI backend (Python 3.13, uv, port 8000)
   ├─ audio.py   ffmpeg (/opt/homebrew/bin/ffmpeg): any browser audio → wav mono; duration checks; WAV header fix
   ├─ gnani.py   httpx client: STT (Prisma v3), voice-clone embedding, VC TTS, stock TTS; retry/backoff; usage log
   ├─ llm.py     Nemotron via NVIDIA API: suggest_replies(), to_devanagari()       [Lead]
   ├─ phrases.py phrase-board data                                                 [Lead]
   ├─ store.py   voices on disk: backend/data/voices/{voice_id}.json; TTS cache backend/data/cache/
   └─ main.py    routes (contract below)
```

**Environment**
- Secrets live in `apni-awaaz/.env`: `GNANI_API_KEY`, `GNANI_KEY` (same value), `NVIDIA_API_KEY`. Load them with python-dotenv from the project root.
  - **Never print, log, commit or copy a key into any other file.**
  - Never put keys in frontend code.
- The backend venv already exists: `backend/.venv` (Python 3.13). Deps are already installed: fastapi, uvicorn, httpx, python-multipart, python-dotenv, pydantic, pytest, pytest-asyncio, respx.
  - Run with `cd backend && uv run uvicorn app.main:app --reload --port 8000`.
  - Only the Backend dev edits `backend/pyproject.toml`.
- Machine: Apple M1, 8 GB RAM. Node 24, npm, ffmpeg and uv are installed. No GPU.

## 4. Verified API facts (tested live tonight — trust these over memory)

### Gnani (base URL `https://api.vachana.ai`, header `X-API-Key-ID: <GNANI_API_KEY>`)

**Gotchas**
- **Cloudflare blocks Python urllib's default User-Agent** with `403 "error code: 1010"`. httpx and requests pass. Always set `User-Agent: ApniAwaaz/0.1` explicitly.
- **Stock TTS returns a streaming-style WAV header** (RIFF/data sizes = 0xFFFFFFFF). Normalise it (rewrite sizes, or `ffmpeg -i in.wav -c:a pcm_s16le out.wav`) before serving or feeding it to the embedding endpoint. The VC output header was already valid, but normalise everything to be safe.

**Errors**

| Code | Meaning | Handling |
|---|---|---|
| 401 | `INVALID_API_KEY` | — |
| 402 | Out of credit | Surface as error code `OUT_OF_CREDITS` |
| 429 | `RATE_LIMITED` | Back off and retry (max 2) |
| 500 | Includes "text too long" and bad embedding | — |

**STT (Prisma v3).** `POST /stt/v3`, multipart.
- Fields:
  - `audio_file`: the format is detected by **file extension**. wav/mp3/ogg/flac/aac/m4a only. **No webm**, so convert. Max 30 s, 8–44.1 kHz.
  - `model=prisma-v3.0`
  - `language_code=hi-IN`. 1–4 comma-separated codes are allowed; `indic-auto` also exists but confuses Hindi and Marathi on short clips.
  - `format=codemix`: Hindi in Devanagari with English words kept in Latin, e.g. "एक cup चाय".
  - Optional `keyterms` as a JSON array string, e.g. `["Papa","Priya","Sunita"]`.
  - Optional `topic` (≤1600 chars) and `previous_text`.
- Measured: 0.39 s for a 3 s clip. Response:
  `{"success":true,"request_id":"…","transcript":"आज मुझे थोड़ा आराम चाहिए, क्या तुम मेरे लिए एक cup चाय बना दोगे?","model":"prisma-v3.0","processing_time":0.15,"end_to_end_latency":0.20,"detected_language_code":"hi-IN"}`

**Voice-clone embedding.** `POST /api/v1/tts/voice-clone/embeddings`, multipart field `audio_file`.
- Needs 5–30 s of clean speech. Measured 0.95 s.
- Response: `{"success":true,"message":"…","data":{"voice_clone_embedding":{"embedding":"<base64, 4096 chars>","shape":[1,768],"dtype":"torch.bfloat16"}}}`
- Example: `fixtures/synthetic_embedding.json`.

**Voice-cloned TTS.** `POST /api/v1/tts/inference`, JSON.
- Body: `{"text":"<Devanagari>","model":"vachana-vc-v1","speaker_embedding":<the whole voice_clone_embedding object>,"audio_config":{"sample_rate":44100,"num_channels":1,"sample_width":2,"encoding":"linear_pcm","container":"wav"}}`
- No `voice` or `language` field.
- Measured: 2.4 s to generate 3.2 s of audio. Prisma transcribed the output back word for word.
- Keep text short (≤ ~180 chars per call). Split longer text on sentence boundaries and concatenate.

**Stock TTS** (fallback when no voice is banked). Same endpoint.
- Body: `{"text":"…","voice":"Chetana","model":"timbre-v3.0","language":"hi-IN","speed":1.0,"audio_config":{…same…}}`
- Timbre v3 voices:
  - Female: Shivani, Lakshmi, Kalpana, Chetana, Vasudha, Riyaa, Tarika.
  - Male: Avinash, Shubham, Prakash, Devansh, Yashraj, Harshad.
- Use **Prakash** (male) for the Ramesh fallback.
- v2.5 voices (e.g. Deepak, Nalini) only work with `timbre-v2.5`.

**Billing (credits are scarce: 5,000 total for the whole contest).**
- TTS is billed **per character** (tags included). STT is billed **per audio second**.
- **Live-call budget during the build:**

  | Role | Live Gnani calls |
  |---|---|
  | Backend dev | ≤ 25 |
  | Lead | ≤ 25 (integration) |
  | Frontend | 0 (use mock mode) |

- Unit tests must mock Gnani (respx).
- **Cache every TTS result on disk** keyed by sha256(model|voice_id|text). The phrase board then costs credits only once per phrase.
- Log every live call as one JSON line to `backend/data/usage.jsonl`: ts, endpoint, chars or audio_seconds, http status, ms.

### NVIDIA LLM — use ONLY `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` (owner's instruction)
- OpenAI-compatible endpoint: `POST https://integrate.api.nvidia.com/v1/chat/completions`, header `Authorization: Bearer <NVIDIA_API_KEY>`. Call it with httpx; no openai SDK is installed.
- **Thinking ON** (default): 5.8 s. Good replies: "हाँ, दाल चावल बनाते हैं। / बिल्कुल, दाल चावल चलेगा। / ठीक है, आज दाल चावल।" 355 reasoning tokens. The answer is in `message.content`; reasoning is separate.
- **Thinking OFF** (`"chat_template_kwargs":{"enable_thinking":false}` plus `/no_think` at the start of the system prompt; test which one actually does it): **0.6 s**, but the replies were weaker. Example: "बस इतना ही कहना है।" makes no sense, and "बना लूँगा" wrongly makes Papa the cook. **Fix this with prompt design and few-shot examples, not by turning thinking on.**
- Rate limit is ~40 requests/min per account (community-reported). Use a timeout (8 s) and a graceful fallback.

## 5. Frozen interfaces (do not change without the Lead appending a note to docs/TEAM_LOG.md)

### HTTP contract (backend serves; frontend consumes)

**Shared rules**
- All JSON. Errors are `{"error":{"code":"AUDIO_TOO_SHORT|AUDIO_TOO_LONG|BAD_AUDIO|OUT_OF_CREDITS|UPSTREAM_ERROR|NOT_FOUND|BAD_REQUEST","message":"human readable"}}` with an appropriate 4xx/5xx.
- Audio uploads use multipart field **`audio`**, with a filename whose extension matches the mime (`.webm`, `.mp4`, `.ogg`, `.wav`, `.m4a`).

**Endpoints**

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| GET | `/api/health` | — | `{"ok":true,"gnani_key":bool,"llm_key":bool}` | |
| GET | `/api/enroll/passage` | — | `{"text":"<~25 s Hindi passage>"}` | Warm, natural sentences with varied sounds |
| POST | `/api/voices` | multipart `audio`, `name`, `consent=true` | `201 {"voice_id","name","duration_s","created_at"}` | Rejects < 5 s and missing consent; trims > 30 s to 30 s |
| GET | `/api/voices` | — | `[{"voice_id","name","created_at"}]` | |
| DELETE | `/api/voices/{voice_id}` | — | `204` | |
| POST | `/api/listen` | multipart `audio` (≤ 30 s), optional `history` (JSON string) | `{"transcript":str,"suggestions":[{"text","intent"}],"timings":{"stt_ms":int,"llm_ms":int}}` | STT then `llm.suggest_replies`. Empty transcript → `suggestions: []` (no LLM call) |
| POST | `/api/suggest` | JSON `{"transcript":str,"history":[…]}` | `{"suggestions":[…]}` | "More options" button |
| POST | `/api/compose` | JSON `{"text":str}` | `{"text":"<Devanagari>"}` | Uses `llm.to_devanagari` |
| POST | `/api/speak` | JSON `{"voice_id":str\|null,"text":str}` | `audio/wav` bytes; header `X-Cache: HIT\|MISS` | null voice → stock fallback voice **Prakash** (timbre-v3.0, hi-IN). Text ≤ 300 chars |
| GET | `/api/phrases` | — | `[{"category":"ज़रूरतें","category_en":"Needs","phrases":[{"text":"पानी चाहिए","en":"I need water","emoji":"💧"}]}]` | |
| WS | `/api/live` | — | — | **Stretch only.** Do not start until the MVP is integrated. Live captions via `wss://api.vachana.ai/stt/v3/stream`. Browsers can't send the auth header, so the backend must proxy it |

`history` items in `/api/listen` and `/api/suggest`: `[{"role":"family"|"user","text":str}]`, oldest first.

### Python interface: `backend/app/llm.py` (a stub exists; the Lead implements it)
```python
async def suggest_replies(transcript: str, history: list[dict] | None = None, n: int = 3) -> list[dict]
async def to_devanagari(text: str) -> str
```
`backend/app/phrases.py` (Lead): `def get_phrases() -> list[dict]` returns the `/api/phrases` shape.
`backend/app/passage.py` (Lead): `ENROLL_PASSAGE: str`.

## 6. Team, ownership and rules

| Role | Owns (only edit these) |
|---|---|
| **Lead (Principal Full-stack)** | `backend/app/llm.py`, `backend/app/phrases.py`, `backend/app/passage.py`, `backend/tests/test_llm.py`, `scripts/eval_llm.py`, `README.md`, `docs/ARCHITECTURE.md`, `docs/DEMO_SCRIPT.md`. Phase 2: integration QA and fixes anywhere |
| **Backend dev** | everything else in `backend/` (`main.py`, `config.py`, `gnani.py`, `audio.py`, `store.py`, `tests/*` except `test_llm.py`, `pyproject.toml`), plus `scripts/smoke_e2e.sh` |
| **Frontend dev** | everything in `frontend/` |

**Rules for everyone**
- Don't edit files you don't own. If you need a change in someone else's file, append a request to `docs/TEAM_LOG.md`.
- Append progress to `docs/TEAM_LOG.md` at each milestone: `HH:MM IST [role] message`. The manager monitors this file.
- No git commands (no init, commit or push). The owner decides that.
- **Synthetic data only.** Fictional names; no real people's voices except the team members' own.
- No secrets in code, logs, tests, fixtures or the frontend.
- **Keep it honest.** Mock mode must be clearly a dev flag, off by default. The demo must run on real APIs.
- **Done means verified.** Run your tests and try your feature for real before you report it as done. Report failures honestly.

## 7. Fixtures (synthetic, safe to use)
- `fixtures/synthetic_voice_deepak_9s.wav`: a 9.4 s Gnani stock voice reading Hindi. A valid enrolment sample.
- `fixtures/synthetic_clone_reply_3s.wav`: a VC output sample. Good for frontend mock playback.
- `fixtures/synthetic_embedding.json`: a real embedding response.
