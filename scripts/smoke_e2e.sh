#!/usr/bin/env bash
# Live end-to-end smoke test against a RUNNING backend (real Gnani + NVIDIA calls).
#
#   cd backend && uv run uvicorn app.main:app --port 8000      # in another terminal
#   scripts/smoke_e2e.sh                                       # BASE=http://host:port to override
#
# COSTS CREDITS: ~3 Gnani calls per run (1 embedding, 1 VC TTS, 1 STT) plus 1 one-time stock
# TTS call to create fixtures/synthetic_family_hinglish.wav if it does not exist yet.
# The smoke voice is deleted at the end (DELETE -> 204); set KEEP_VOICE=1 to keep it.
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX="$ROOT/fixtures"
ENROLL_WAV="$FIX/synthetic_voice_deepak_9s.wav"
FAMILY_WAV="$FIX/synthetic_family_hinglish.wav"
FAMILY_TEXT="पापा, आज खाने में क्या बनाऊँ? दाल चावल चलेगा?"
SPEAK_TEXT="हाँ बेटा, दाल चावल बढ़िया रहेगा।"
COMPOSE_TEXT="thoda paani de do"
OUT="$(mktemp -d "${TMPDIR:-/tmp}/apni-smoke.XXXXXX")"

command -v jq >/dev/null || { echo "jq is required (brew install jq)"; exit 2; }

TIMINGS=()

wav_seconds() {  # duration of a WAV file in seconds
  if command -v afinfo >/dev/null; then
    afinfo "$1" | awk '/estimated duration/ {printf "%.2f", $3}'
  else
    ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"
  fi
}

# req NAME "EXPECTED CODES" OUTFILE curl-args...   -> sets CODE, MS; headers in $OUT/NAME.h
req() {
  local name=$1 expect=$2 out=$3 res t
  shift 3
  res=$(curl -sS -o "$out" -D "$OUT/$name.h" -w '%{http_code} %{time_total}' "$@") \
    || { echo "FAIL  $name: could not reach $BASE (is uvicorn running?)"; exit 1; }
  CODE=${res% *}
  t=${res#* }
  MS=$(awk -v t="$t" 'BEGIN { printf "%d", t * 1000 }')
  TIMINGS+=("$(printf '%-16s %4s %7d ms' "$name" "$CODE" "$MS")")
  if [[ " $expect " != *" $CODE "* ]]; then
    printf 'FAIL  %-16s HTTP %s (expected %s) %d ms\n' "$name" "$CODE" "$expect" "$MS"
    cat "$out"; echo
    exit 1
  fi
  printf 'OK    %-16s HTTP %s  %6d ms\n' "$name" "$CODE" "$MS"
}

header() { grep -i "^$2:" "$OUT/$1.h" | head -1 | cut -d: -f2- | tr -d '\r ' ; }

echo "== Apni Awaaz live smoke test against $BASE (artifacts in $OUT)"

# 0. One-time synthetic family clip (female timbre-v3.0 voice Riyaa), via the backend's own client.
if [[ ! -s "$FAMILY_WAV" ]]; then
  echo "-- creating $FAMILY_WAV with stock TTS (1 live Gnani call)"
  (cd "$ROOT/backend" && uv run --quiet python - "$FAMILY_WAV" "$FAMILY_TEXT" <<'PY'
import asyncio, sys
from pathlib import Path
from app import gnani, audio

async def main(path: str, text: str) -> None:
    wav = await gnani.speak_stock(text, voice="Riyaa")
    await gnani.aclose()
    Path(path).write_bytes(wav)
    print(f"   wrote {path} ({audio.wav_duration(wav):.2f} s)")

asyncio.run(main(sys.argv[1], sys.argv[2]))
PY
  )
fi

# 1. health
req health "200" "$OUT/health.json" "$BASE/api/health"
jq -c . "$OUT/health.json"
[[ $(jq -r .gnani_key "$OUT/health.json") == true ]] || { echo "FAIL  gnani_key is false"; exit 1; }

# 2. enrol the synthetic voice
req enroll "201" "$OUT/enroll.json" -X POST "$BASE/api/voices" \
  -F "audio=@$ENROLL_WAV;type=audio/wav" -F "name=Ramesh (synthetic smoke)" -F "consent=true"
jq -c . "$OUT/enroll.json"
VOICE_ID=$(jq -r .voice_id "$OUT/enroll.json")

# 3. speak twice in that voice: MISS then HIT
SPEAK_BODY=$(jq -cn --arg v "$VOICE_ID" --arg t "$SPEAK_TEXT" '{voice_id: $v, text: $t}')
req speak_1 "200" "$OUT/speak_1.wav" -X POST "$BASE/api/speak" -H 'Content-Type: application/json' -d "$SPEAK_BODY"
C1=$(header speak_1 x-cache)
echo "   X-Cache: $C1   content-type: $(header speak_1 content-type)   duration: $(wav_seconds "$OUT/speak_1.wav") s"
req speak_2 "200" "$OUT/speak_2.wav" -X POST "$BASE/api/speak" -H 'Content-Type: application/json' -d "$SPEAK_BODY"
C2=$(header speak_2 x-cache)
echo "   X-Cache: $C2   duration: $(wav_seconds "$OUT/speak_2.wav") s"
[[ "$C1" == MISS && "$C2" == HIT ]] || { echo "FAIL  expected X-Cache MISS then HIT, got $C1 then $C2"; exit 1; }
cmp -s "$OUT/speak_1.wav" "$OUT/speak_2.wav" || { echo "FAIL  cached audio differs"; exit 1; }

# 4. listen to the family's Hinglish utterance
req listen "200" "$OUT/listen.json" -X POST "$BASE/api/listen" \
  -F "audio=@$FAMILY_WAV;type=audio/wav" -F 'history=[]'
jq . "$OUT/listen.json"
echo "   Server-Timing: $(header listen server-timing)"
[[ -n $(jq -r .transcript "$OUT/listen.json") ]] || { echo "FAIL  empty transcript"; exit 1; }

# 5. compose: romanised Hinglish -> Devanagari
req compose "200" "$OUT/compose.json" -X POST "$BASE/api/compose" -H 'Content-Type: application/json' \
  -d "$(jq -cn --arg t "$COMPOSE_TEXT" '{text: $t}')"
echo "   \"$COMPOSE_TEXT\" -> $(jq -r .text "$OUT/compose.json")"

# 6. phrases
req phrases "200" "$OUT/phrases.json" "$BASE/api/phrases"
echo "   $(jq 'length' "$OUT/phrases.json") categories, $(jq '[.[].phrases | length] | add' "$OUT/phrases.json") phrases: $(jq -r '[.[].category] | join(", ")' "$OUT/phrases.json")"

# cleanup: delete the smoke voice (also purges its cached clips)
if [[ "${KEEP_VOICE:-0}" != 1 ]]; then
  req delete "204" "$OUT/delete.out" -X DELETE "$BASE/api/voices/$VOICE_ID"
else
  echo "-- keeping voice $VOICE_ID"
fi

echo
echo "== timings (curl wall time, client side)"
printf '   %s\n' "${TIMINGS[@]}"
echo "== PASS"
