# Apni Awaaz: frontend

Vite + React + TypeScript + Tailwind v4. It consumes the frozen HTTP contract in `../docs/TEAM_BRIEF.md` §5.

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api -> http://localhost:8000 (real backend, the default)
npm run dev:mock   # http://localhost:5174, VITE_MOCK=1: canned contract-shaped responses, red MOCK badge
npm run build      # tsc -b + vite build (mock code is tree-shaken out of production builds)
```

## Layout
- `src/screens/Welcome.tsx`: voice banking (name, consent, passage, 30 s recorder, save, "Hear yourself", skip).
- `src/screens/Talk.tsx`: conversation (Listen → transcript → 3 suggestions), phrase board, type-to-speak, voice settings.
- `src/lib/api.ts`: typed client and defensive normalisers. Also holds an in-memory TTS cache, so replays don't re-bill.
- `src/lib/useRecorder.ts`: MediaRecorder (webm/opus, mp4 fallback for Safari), live level meter, auto-stop, and mic release.
- `src/lib/player.ts`: one shared `<audio>` element, so replies never overlap. It also handles the Safari gesture unlock.
- `src/lib/mock.ts`: the dev-only mock, active only when `VITE_MOCK=1`.

No API keys live in the frontend. Every Gnani or LLM call goes through the backend.
