# Demo script: 60-second video

**Goal:** the emotion is clear within 5 seconds, everything works live on camera, and the post stays honest.

**Cast:**
- **Ramesh** (on camera): a team member, banking *their own* voice with consent.
- **Priya** (off camera): the other team member, speaking as the daughter.
- **Camera:** a phone filming the laptop and Ramesh, or a screen recorder with system audio (see §4).

The persona is fictional. The voice is a real team member's own voice, cloned with consent.

---

## 1. Shot list

| Time | Screen | What happens | Exact words |
|---|---|---|---|
| **0–5 s** | Title card (black, large white text) | Hook | On screen: **"Ramesh lost his voice to cancer surgery. His family still hears *him*."** Small caption: *"Dramatization · fictional persona · voice cloned from a team member with consent"* |
| **5–15 s** | **Welcome / Bank your voice** | Caption: *"Before surgery: 20 seconds to bank his voice."* Ramesh types the name, ticks the consent box, taps record and reads the passage. Jump cut after about 3 s of reading. He taps **आवाज़ सुरक्षित करें**, **Voice saved ✓** appears, then he taps **अपनी आवाज़ सुनिए** and his clone plays | Ramesh reads the on-screen passage: "आज सुबह की धूप, ठंडी हवा और फूलों की महक बहुत प्यारी है…". The clone says: "नमस्ते, यह मेरी अपनी आवाज़ है। अब मेरी बात मेरी ही आवाज़ में होगी।" |
| **15–40 s** | **Talk** (बातचीत) | Caption: *"After surgery."* Priya taps **सुनिए** (Listen) and speaks, then taps again to stop. The UI shows **सुन रहे हैं…**, then **समझ रहे हैं…**; the transcript and 3 suggestions appear. Ramesh taps the "yes" suggestion. **बोल रहे हैं…** shows and his voice plays | Priya: **"Papa, aaj khaane mein kya banaun? Dal chawal chalega?"** The app, in Ramesh's voice, says the live "yes" suggestion, usually **"हाँ बेटा, दाल चावल बनाओ, बढ़िया रहेगा।"** |
| **40–50 s** | **Talk: phrase board, then type box** | Ramesh taps the 🤕 tile, then types in the box and taps **बोलिए**. The Devanagari appears and is spoken | The tile says **"दर्द हो रहा है"**. He types **thoda paani de do**, and the app says **"थोड़ा पानी दे दो।"** |
| **50–60 s** | End card | Logo/name and credits | **"Apni Awaaz · अपनी आवाज़"**, **"Built on Gnani Prisma v3 + Vachana voice cloning"**, team names, *"Communication aid, not a medical device."* |

**Burned-in English subtitles.** Most viewers watch muted, so subtitle every line:
- "Papa, what should I cook today? Is dal-rice okay?"
- "Yes beta, make dal-rice, that'll be great." (Match whatever the app actually said.)
- "I'm in pain."
- "Please give me a little water."

**Honesty rules for the edit:**
- Every voice you hear is produced live by the app. Do not overdub, and do not swap in audio from another take.
- Jump cuts only remove waiting, such as the 20 s of reading. Do not cut a slow response so that the app looks faster than it is.
- The suggestion text is generated live. If the "yes" reply differs from the line above, use what the app said and match the subtitle to it.

## 2. Pre-flight checklist

### T-60 min: systems
- [ ] Start the backend **without** `--reload`, so it cannot restart mid-take: `cd backend && uv run uvicorn app.main:app --port 8000`
- [ ] `curl localhost:8000/api/health` returns `{"ok":true,"gnani_key":true,"llm_key":true}`.
- [ ] Start the frontend with `cd frontend && npm run dev`. Open <http://localhost:5173> in Chrome and check there is **no MOCK badge** (`VITE_MOCK` must be unset).
- [ ] Credits: check the remaining balance. Count today's live calls with `wc -l backend/data/usage.jsonl`. If a call returns `OUT_OF_CREDITS`, **stop** and tell the lead; do not retry.

### T-30 min: room and devices
- [ ] Quiet room: fan, AC and TV off, phones silent, doors closed. Soft furnishings reduce echo.
- [ ] macOS Sound settings: pick the right **input** mic. Priya speaks 30–50 cm from it.
- [ ] Output: laptop volume about 70%, or a small external speaker, so the clone is clearly audible on camera.
- [ ] Chrome: microphone permission for `localhost:5173` set to **Allow**. Test one Listen tap and check the transcript is right.
- [ ] Turn on Do Not Disturb. Close other tabs, hide the bookmarks bar, and use full screen (⌃⌘F). Set browser zoom to about 125% so the Hindi text is readable on a phone.

### T-15 min: bank the final voice once
- [ ] Clean start: the backend has no voices (`curl localhost:8000/api/voices` returns `[]`), and the app opens on the Welcome screen. A voice this browser remembers but the backend no longer has is forgotten automatically. If the app still opens on Talk with an old voice, open voice settings and delete it, or clear the site data for `localhost:5173`.
- [ ] Bank Ramesh's voice **once** for real, in a quiet room, reading for about 20 s. This can also be the take you film for 5–15 s.
- [ ] **Reuse this voice for every Talk take.** The TTS cache is per voice, so re-banking means every clip is a cache miss again: slower and more credits.

### T-10 min: pre-warm (with the final voice)
- [ ] Pre-cache the three demo clips in the final voice. This costs at most 3 Gnani calls, and nothing if you run it again.
  ```bash
  scripts/prewarm_demo.sh               # lists voice ids
  scripts/prewarm_demo.sh <voice_id>    # expect MISS the first time, then HIT on every later run
  ```
  It caches "हाँ बेटा, दाल चावल बढ़िया रहेगा।", "दर्द हो रहा है" and "थोड़ा पानी दे दो।". In the build-day test, every tap on these lines started audio in **under 0.1 s**, and the typed line in about 0.6 s (most of that is the Hinglish→Devanagari conversion).
- [ ] Tap **अपनी आवाज़ सुनिए** once. This caches the greeting clip.
- [ ] Connection warmup is **automatic**: on backend start, when the Talk screen opens, and every 4 minutes while the tab is visible. If the backend restarts, reload the Talk page, or run `curl -X POST localhost:8000/api/warmup`. That call is free; it makes no billable Gnani call.
- [ ] Optional mic check: do one rehearsal of Priya's line. `/api/listen` suggestions are deterministic, so the real take normally shows the same three. Only **और विकल्प** (More options) asks for different ones.
- [ ] Do **not** tap through the whole phrase board. It costs about 540 characters of TTS credits per voice.

### Optional: automated check of the whole flow (engineers)
`scripts/e2e_mic.mjs` drives headless Chrome with a fake microphone through the Listen → suggestion → phrase → type flow and saves screenshots to `docs/qa/screenshots/`. With the demo clips cached it costs about 1 Gnani call (STT). The usage notes are at the top of the file.

## 3. If something goes wrong on camera

| Symptom | Do this |
|---|---|
| A suggestion looks odd | Tap **और विकल्प** (More options) once, or retake the shot |
| The transcript misheard | Retake; Priya speaks slightly slower and closer to the mic |
| A long wait at **समझ रहे हैं…** (over 3 s) | Retake. If it repeats, reload the Talk page, which re-warms the connections |
| The error toast `UPSTREAM_ERROR` | Wait about 10 s and retake |
| `OUT_OF_CREDITS` | Stop recording and tell the lead |
| No audio | Check the output device and volume, then tap the reply again (it is now cached) |

## 4. Recording tips

- **Easiest:** film the laptop and Ramesh with a phone, so the speaker audio is captured naturally. Keep the screen in focus and avoid glare.
- **Screen recording:** macOS QuickTime does not capture system audio. Use OBS (desktop audio plus mic), or a phone.
- Use a **1:1 or 4:5** aspect ratio for LinkedIn and X mobile feeds. Make the first frame the hook text, not a loading screen.
- Record 2–3 clean takes of each segment, then cut to 60 s.
