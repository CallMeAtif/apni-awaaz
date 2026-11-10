#!/usr/bin/env bash
# Pre-cache the voice clips for the demo lines in one banked voice, and warm the upstream
# connections, so every tap on camera is a cache HIT (audio starts in well under a second).
#
#   scripts/prewarm_demo.sh <voice_id>          # BASE=http://host:port to override
#   scripts/prewarm_demo.sh                     # lists the banked voices
#
# COSTS CREDITS only for clips that are not cached yet: at most 3 Gnani TTS calls per voice
# (the same clone output, just cached on disk). Running it again costs nothing (all HIT).
# Speaking the same text through the app later reuses these exact clips.
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
LINES=(
  "हाँ बेटा, दाल चावल बढ़िया रहेगा।"   # the demo reply (what /api/listen suggests for the demo line)
  "दर्द हो रहा है"                     # phrase tile
  "थोड़ा पानी दे दो।"                  # what /api/compose returns for "thoda paani de do"
)

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <voice_id>"
  echo "banked voices:"
  curl -sf "$BASE/api/voices" | python3 -c 'import json,sys
for v in json.load(sys.stdin): print("  %s  %s  (%s)" % (v["voice_id"], v["name"], v["created_at"]))'
  exit 2
fi
VOICE_ID="$1"

curl -sf "$BASE/api/health" >/dev/null || { echo "backend not reachable at $BASE"; exit 1; }
curl -sf "$BASE/api/voices" | python3 -c "import json,sys; ids=[v['voice_id'] for v in json.load(sys.stdin)]; sys.exit(0 if '$VOICE_ID' in ids else 1)" \
  || { echo "unknown voice_id: $VOICE_ID (run without arguments to list voices)"; exit 1; }

echo "warmup: $(curl -sf -X POST "$BASE/api/warmup")"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/apni-prewarm.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
fail=0
for i in "${!LINES[@]}"; do
  text="${LINES[$i]}"
  body="$(python3 -c 'import json,sys; print(json.dumps({"voice_id": sys.argv[1], "text": sys.argv[2]}, ensure_ascii=False))' "$VOICE_ID" "$text")"
  out="$TMP/$i.wav"
  read -r code cache secs < <(curl -s -o "$out" -D "$TMP/$i.h" -w '%{http_code} - %{time_total}\n' \
    -H 'Content-Type: application/json' -d "$body" "$BASE/api/speak" | awk '{print $1, $2, $3}')
  cache="$(grep -i '^x-cache:' "$TMP/$i.h" | tr -d '\r' | awk '{print $2}')"
  if [[ "$code" == "200" ]]; then
    printf '  %-5s %5.2fs  %6d bytes  %s\n' "${cache:-?}" "$secs" "$(wc -c < "$out")" "$text"
  else
    printf '  FAIL  HTTP %s  %s  %s\n' "$code" "$text" "$(head -c 200 "$out")"
    fail=1
  fi
done
[[ $fail -eq 0 ]] && echo "done: demo clips cached for $VOICE_ID" || { echo "some clips failed"; exit 1; }
