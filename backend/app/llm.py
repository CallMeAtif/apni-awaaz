"""LLM layer: reply suggestions and Hinglish -> Devanagari conversion.

OWNER: Lead. The two public coroutines below have FROZEN signatures and return shapes
(see docs/TEAM_BRIEF.md, section "Frozen interfaces"):

    async def suggest_replies(transcript, history=None, n=3) -> list[dict]
    async def to_devanagari(text) -> str

Model: NVIDIA Nemotron 3 Nano Omni (30B-A3B reasoning) over the OpenAI-compatible
NVIDIA endpoint, called with httpx. Thinking is switched OFF for latency
(`chat_template_kwargs.enable_thinking=false`; measured p50 ~1.1 s vs ~6 s with thinking).
Note: a "/no_think" system-prompt prefix alone does NOT work on this endpoint (the answer
lands in `reasoning_content` and `content` is null), so it is not used.
Quality comes from the prompt: the model first writes short English notes (speaker, gist,
offer), then the replies, guided by explicit role rules and five few-shot examples. Prompt
length dominates latency (Devanagari is token-heavy), so the examples are kept few.
A small rule-based safety net drops replies that take over the speaker's offered action
or use the wrong grammatical gender.

Neither function ever raises. On a missing key, timeout, HTTP error or unparseable
output, suggest_replies returns generic replies and to_devanagari returns its input.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from app import lexicon

# backend/app/llm.py -> project root is two levels above backend/
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

log = logging.getLogger("apni_awaaz.llm")

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"  # owner's instruction: this model only
TIMEOUT_S = 8.0          # total budget per public call, retries included
RETRY_BACKOFF_S = 0.4    # one retry on 429 / 5xx / connection error, if budget allows
USER_AGENT = "ApniAwaaz/0.1"

# Grammatical gender of the person using the aid (Hindi verbs agree with it).
# Demo persona Ramesh is male; set APNI_USER_GENDER=female for a woman user.
USER_GENDER = os.getenv("APNI_USER_GENDER", "male").strip().lower()

INTENTS = ("yes", "no", "ask", "feeling", "other")
_INTENT_ORDER = ("yes", "no", "ask")  # default intent by position when the model omits it
_INTENT_ALIASES = {
    "agree": "yes", "accept": "yes", "haan": "yes", "affirm": "yes",
    "decline": "no", "alternative": "no", "alt": "no", "refuse": "no", "nahi": "no",
    "question": "ask", "q": "ask", "query": "ask",
    "emotion": "feeling", "feel": "feeling", "feelings": "feeling",
}

FALLBACK_REPLIES: list[dict] = [
    {"text": "हाँ, ठीक है।", "intent": "yes"},
    {"text": "नहीं, अभी नहीं।", "intent": "no"},
    {"text": "एक बार फिर से बताओ?", "intent": "ask"},
    {"text": "मैं ठीक हूँ, चिंता मत करो।", "intent": "feeling"},
    {"text": "थोड़ी देर बाद बात करते हैं।", "intent": "other"},
]

MAX_REPLY_CHARS = 120     # well under the ~180-char VC TTS limit
MAX_HISTORY_TURNS = 6


# --------------------------------------------------------------------------- prompts

def _suggest_system_prompt(n: int) -> str:
    who = "a woman" if USER_GENDER == "female" else "a man"
    verb_note = (
        "Feminine first-person verbs (गई, लूँगी, करती हूँ)."
        if USER_GENDER == "female"
        else "Masculine first-person verbs (गया, लूँगा, करता हूँ)."
    )
    return f"""You write reply options for an Indian speech-aid (AAC) app. THE USER is {who} in their late 50s who cannot speak after throat surgery. Someone at home just spoke to the user. Give exactly {n} short replies; the user taps one and the app speaks it in the user's own voice.

First, short English notes:
- speaker: "child" if they call the user पापा/मम्मी/papa (now or earlier), "spouse" if सुनो जी/सुनिए/जी, else "doctor" or "other". If a "Speaker:" line is given, use it. Someone who only MENTIONS a doctor ("doctor ने कहा...") is not the doctor.
- gist: what they said, max 12 words.
- offer: what the SPEAKER offers or plans to do, or "none".

Then the replies. Rules:
1. Said BY the user TO the speaker, in first person, about the user's own wishes, body and feelings. Never order the speaker around ("बैठो", "पानी पियो") unless asking for help.
2. The offer stays the speaker's job. To "...दूँ? / ...करूँ? / ...बनाऊँ?" (shall I?) answer like "हाँ, कर दो" or "ठीक रहेगा", never "कर दूँगा", "बना लूँगा", "भर दूँगा".
3. Intents, in order: "yes" = accept or agree (for news: acknowledge warmly); "no" = decline, or a simple alternative about THE SAME THING they said (a match or song: "बाद में", "अभी आराम करना है"; food: a simple everyday dish such as रोटी सब्ज़ी, खिचड़ी or दलिया; news: a small request). Never bring up food unless they talked about food. Then "ask" (a short, direct question about the same thing that a parent would really ask, e.g. "साथ में दही भी मिलेगा?", "स्कोर क्या है?", "वो ठीक तो है ना?") or "feeling" (a simple feeling about it). Never start a reply with "अगर".
4. Talk to the speaker only. "बेटा" only for a child, in one or two replies; never for a spouse. "डॉक्टर साहब" only if the doctor is the speaker.
5. Everyday spoken Hindi, warm and simple, max 12 words each. Use only common words every family uses; never invent words or dish names. Playful if they joke.
6. Devanagari only. Write English words in Devanagari (टीवी, ऑनलाइन, डॉक्टर).
7. {verb_note}

Output only JSON: {{"speaker":"..","gist":"..","offer":"..","replies":[{{"intent":"yes","text":".."}},{{"intent":"no","text":".."}},{{"intent":"ask","text":".."}}]}}"""


# Few-shot examples: (history, utterance, speaker, gist, offer, replies). Kept to five
# because Devanagari is token-heavy and prompt length is most of the latency. Utterances
# look like Prisma v3 "codemix" transcripts (Devanagari with English kept in Latin).
_FEW_SHOT: list[tuple[list[dict], str, str, str, str, list[dict]]] = [
    (
        [],
        "पापा, रात को क्या बनाऊँ? आलू के पराठे चलेंगे?",
        "child", "what to cook tonight; are aloo parathas ok", "cook dinner",
        [
            {"intent": "yes", "text": "हाँ बेटा, पराठे बढ़िया रहेंगे।"},
            {"intent": "no", "text": "नहीं बेटा, आज हल्का खाना, खिचड़ी बना दो।"},
            {"intent": "ask", "text": "साथ में दही भी मिलेगा?"},
        ],
    ),
    (
        [],
        "सुनिए, बेटे को WhatsApp पे video call लगा दूँ? आपको पूछ रहा था।",
        "spouse", "son asked about you; shall I video call him", "video call our son",
        [
            {"intent": "yes", "text": "हाँ, लगा दो, उससे बात करने का मन है।"},
            {"intent": "no", "text": "अभी नहीं, शाम को वीडियो कॉल करेंगे।"},
            {"intent": "ask", "text": "वो ठीक तो है ना?"},
        ],
    ),
    (
        [],
        "पापा, आज साँस लेने में तकलीफ़ तो नहीं? Chest भारी तो नहीं लग रही?",
        "child", "asks if I have trouble breathing or chest heaviness", "none",
        [
            {"intent": "yes", "text": "हाँ बेटा, थोड़ी तकलीफ़ है, पर ज़्यादा नहीं।"},
            {"intent": "no", "text": "नहीं, आज साँस ठीक चल रही है।"},
            {"intent": "feeling", "text": "कल से काफ़ी बेहतर लग रहा है, चिंता मत करो।"},
        ],
    ),
    (
        [],
        "पापा, मैं market जा रही हूँ, एक घंटे में आ जाऊँगी।",
        "child", "going to the market, back in an hour", "go to the market",
        [
            {"intent": "yes", "text": "ठीक है बेटा, आराम से जाना।"},
            {"intent": "no", "text": "रुको बेटा, मेरे लिए थोड़े फल ले आना।"},
            {"intent": "ask", "text": "छाता ले लिया?"},
        ],
    ),
    (
        [],
        "पापा, radio पे पुराने गाने लगा दूँ? आपको अच्छा लगेगा।",
        "child", "shall I play old songs on the radio", "play old songs on the radio",
        [
            {"intent": "yes", "text": "हाँ बेटा, लगा दो, मन अच्छा हो जाएगा।"},
            {"intent": "no", "text": "अभी नहीं बेटा, थोड़ी देर बाद सुनेंगे।"},
            {"intent": "ask", "text": "किशोर कुमार के गाने हैं?"},
        ],
    ),
]


def _format_turn(history: list[dict] | None, utterance: str) -> str:
    lines: list[str] = []
    turns = [h for h in (history or []) if isinstance(h, dict) and str(h.get("text", "")).strip()]
    turns = turns[-MAX_HISTORY_TURNS:]
    if turns:
        lines.append("Earlier in this conversation (oldest first):")
        for h in turns:
            who = "User" if h.get("role") == "user" else "Family"
            lines.append(f"{who}: {str(h.get('text', '')).strip()[:200]}")
        lines.append("")
    lines.append(f'They just said to the user: "{utterance.strip()[:500]}"')
    hint = _speaker_hint(turns, utterance)
    if hint:
        lines.append(hint)
    return "\n".join(lines)


# Address cues decide the speaker more reliably than the model does with thinking off
# (live miss: a wife relaying the doctor's message was answered as "डॉक्टर साहब").
_CHILD_CUE = re.compile(r"पापा|मम्मी|पिताजी|डैडी|\b(?:papa|mummy|mumma|daddy|dad|mom)\b", re.I)
_SPOUSE_CUE = re.compile(r"सुनिए|सुनो जी|सुनते हो|अजी|ए जी|\b(?:suniye|suno ji|sunte ho|aji)\b", re.I)
_DOCTOR_MENTION = re.compile(r"डॉक्टर|डाक्टर|\bdoctor\b|\bdr\.?\b", re.I)


def _speaker_hint(turns: list[dict], utterance: str) -> str | None:
    texts = [utterance] + [str(h.get("text", "")) for h in reversed(turns) if h.get("role") != "user"]
    speaker = None
    for t in texts:  # the latest utterance first, then earlier family turns, newest first
        m_child, m_spouse = _CHILD_CUE.search(t), _SPOUSE_CUE.search(t)
        if m_child or m_spouse:
            speaker = ("child", m_child) if m_child else ("spouse", m_spouse)
            break
    if speaker is None:
        return None
    role, m = speaker
    who = "the user's son or daughter" if role == "child" else "the user's wife or husband"
    hint = f'Speaker: {role}, {who} (they said "{m.group(0)}").'
    if _DOCTOR_MENTION.search(utterance):
        hint += " They only mention the doctor; reply to them, not to the doctor."
    return hint


def _suggest_messages(transcript: str, history: list[dict] | None, n: int) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": _suggest_system_prompt(n)}]
    for ex_hist, ex_utt, speaker, gist, offer, ex_replies in _FEW_SHOT:
        answer = {"speaker": speaker, "gist": gist, "offer": offer, "replies": ex_replies}
        msgs.append({"role": "user", "content": _format_turn(ex_hist, ex_utt)})
        msgs.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False, separators=(",", ":"))})
    msgs.append({"role": "user", "content": _format_turn(history, transcript)})
    return msgs


_DEVANAGARI_SYSTEM = """You convert text typed in romanised Hindi / Hinglish into Devanagari so a Hindi text-to-speech voice pronounces it correctly.

RULES
- Keep the exact meaning, words and person. Do not translate, answer, add or drop anything.
- Write English words phonetically in Devanagari (doctor -> डॉक्टर, TV -> टीवी, please -> प्लीज़).
- Use correct standard Hindi spelling with nuqta and chandrabindu where normal (ज़रा, थोड़ा, हूँ).
- Keep question marks; end statements with "।".

Output ONLY JSON: {"text":"<Devanagari>"}"""

_DEVANAGARI_FEW_SHOT = [
    ("mujhe neend aa rahi hai", "मुझे नींद आ रही है।"),
    ("doctor ko phone karo please", "डॉक्टर को फ़ोन करो प्लीज़।"),
    ("tum kab tak aaoge?", "तुम कब तक आओगे?"),
]


def _devanagari_messages(text: str) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": _DEVANAGARI_SYSTEM}]
    for src, dst in _DEVANAGARI_FEW_SHOT:
        msgs.append({"role": "user", "content": src})
        msgs.append({"role": "assistant", "content": json.dumps({"text": dst}, ensure_ascii=False)})
    msgs.append({"role": "user", "content": text.strip()[:400]})
    return msgs


# --------------------------------------------------------------------------- HTTP

_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _get_client() -> httpx.AsyncClient:
    """One pooled client per event loop: reuses the TLS connection between calls,
    which saves a few hundred ms per request."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT_S, connect=3.0),
            headers={"User-Agent": USER_AGENT},
            # httpx's default 5 s keep-alive means a fresh TLS handshake after any pause.
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5, keepalive_expiry=300.0),
        )
        _client_loop = loop
    return _client


async def aclose() -> None:
    """Optional: close the pooled client (e.g. from the FastAPI lifespan shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def warmup() -> bool:
    """Optional: open the pooled TLS connection with one tiny request, so the first
    real suggestion after a server (re)start is not ~0.5 s slower. Never raises."""
    try:
        out = await _chat([{"role": "user", "content": "नमस्ते"}], max_tokens=1, temperature=0.0, timeout=5.0)
        return out is not None
    except Exception:  # pragma: no cover
        return False


async def _chat(
    messages: list[dict], *, max_tokens: int, temperature: float, timeout: float = TIMEOUT_S
) -> str | None:
    """POST a chat completion; return the model's text, or None on any failure."""
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        log.warning("llm: NVIDIA_API_KEY not set; using fallback")
        return None
    body = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.9,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    async def attempt_all() -> str | None:
        client = _get_client()
        for attempt in (1, 2):
            try:
                r = await client.post(NVIDIA_URL, json=body, headers=headers)
            except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
                log.warning("llm: connection error (%s), attempt %d", type(e).__name__, attempt)
                if attempt == 1:
                    await asyncio.sleep(RETRY_BACKOFF_S)
                    continue
                return None
            if r.status_code == 429 or r.status_code >= 500:
                log.warning("llm: HTTP %d, attempt %d", r.status_code, attempt)
                if attempt == 1:
                    await asyncio.sleep(RETRY_BACKOFF_S)
                    continue
                return None
            if r.status_code != 200:
                log.warning("llm: HTTP %d", r.status_code)
                return None
            data = r.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            text = msg.get("content") or ""
            if not str(text).strip():
                # If thinking were somehow on, the answer can land in the reasoning field.
                text = msg.get("reasoning_content") or msg.get("reasoning") or ""
            return str(text)
        return None

    t0 = time.perf_counter()
    try:
        out = await asyncio.wait_for(attempt_all(), timeout=timeout)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        log.warning("llm: timed out after %.1fs", time.perf_counter() - t0)
        return None
    except Exception as e:  # never raise to callers
        log.warning("llm: request failed (%s)", type(e).__name__)
        return None
    log.info("llm: %d ms", int((time.perf_counter() - t0) * 1000))
    return out


# --------------------------------------------------------------------------- parsing

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json|JSON)?")
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")


def _devanagari_ratio(s: str) -> float:
    """Share of letters (incl. Devanagari vowel signs) that are Devanagari."""
    dev = latin = 0
    for c in s:
        if "\u0900" <= c <= "\u097f":
            dev += 1
        elif c.isalpha():
            latin += 1
    total = dev + latin
    return dev / total if total else 0.0


def _clean_model_text(raw: str) -> str:
    s = _THINK_BLOCK.sub("", raw or "")
    if "</think>" in s:  # opening tag missing: keep only what follows the reasoning
        s = s.rsplit("</think>", 1)[1]
    s = s.replace("<think>", "")
    return _FENCE.sub("", s).strip()


def _json_values(text: str) -> list[Any]:
    """Every top-level JSON object/array that can be decoded from `text`, in order."""
    dec = json.JSONDecoder()
    out: list[Any] = []
    for candidate in (text, _TRAILING_COMMA.sub(r"\1", text)):
        i = 0
        while True:
            m = re.search(r"[\[{]", candidate[i:])
            if not m:
                break
            start = i + m.start()
            try:
                val, end = dec.raw_decode(candidate, start)
            except ValueError:
                i = start + 1
                continue
            out.append(val)
            i = end
        if out:
            break
    return out


# Common English words that show up in codemix transcripts and replies. Converting them
# here keeps the TTS input Devanagari without an extra LLM call.
_EN_TO_DEVANAGARI = {
    "tv": "टीवी", "online": "ऑनलाइन", "offline": "ऑफ़लाइन", "cricket": "क्रिकेट", "match": "मैच",
    "doctor": "डॉक्टर", "bill": "बिल", "exam": "एग्ज़ाम", "fail": "फ़ेल", "pass": "पास",
    "office": "ऑफ़िस", "appointment": "अपॉइंटमेंट", "bp": "बीपी", "sugar": "शुगर", "ok": "ओके",
    "okay": "ओके", "sorry": "सॉरी", "thanks": "थैंक्स", "please": "प्लीज़", "phone": "फ़ोन",
    "call": "कॉल", "mobile": "मोबाइल", "hospital": "हॉस्पिटल", "medicine": "मेडिसिन",
    "tablet": "टैबलेट", "test": "टेस्ट", "report": "रिपोर्ट", "smart": "स्मार्ट", "india": "इंडिया",
    "time": "टाइम", "video": "वीडियो", "whatsapp": "व्हाट्सऐप", "wheelchair": "व्हीलचेयर",
    "park": "पार्क", "market": "मार्केट", "school": "स्कूल", "college": "कॉलेज", "bus": "बस",
    "car": "कार", "auto": "ऑटो", "train": "ट्रेन", "birthday": "बर्थडे", "cake": "केक",
    "party": "पार्टी", "movie": "मूवी", "news": "न्यूज़", "ac": "एसी", "fan": "फ़ैन",
    "light": "लाइट", "minute": "मिनट", "minutes": "मिनट", "plan": "प्लान", "game": "गेम",
    "job": "जॉब", "bank": "बैंक", "card": "कार्ड", "ticket": "टिकट", "nurse": "नर्स",
    "checkup": "चेकअप", "injection": "इंजेक्शन", "serial": "सीरियल", "channel": "चैनल",
    "remote": "रिमोट", "coffee": "कॉफ़ी", "tea": "टी", "sir": "सर", "madam": "मैडम",
    "papa": "पापा", "mummy": "मम्मी", "uncle": "अंकल", "aunty": "आंटी", "hello": "हेलो",
    "bye": "बाय", "innings": "इनिंग्स", "team": "टीम", "score": "स्कोर", "result": "रिज़ल्ट",
    "percent": "परसेंट", "highlights": "हाइलाइट्स", "radio": "रेडियो", "good": "गुड", "night": "नाइट", "morning": "मॉर्निंग", "sweet": "स्वीट",
}
_LATIN_WORD = re.compile(r"[A-Za-z]+")


def _has_latin(s: str) -> bool:
    return bool(re.search(r"[A-Za-z]", s))


def _replace_known_english(s: str) -> str:
    return _LATIN_WORD.sub(lambda m: _EN_TO_DEVANAGARI.get(m.group(0).lower(), m.group(0)), s)


def _normalise_intent(raw: Any, position: int) -> str:
    s = str(raw or "").strip().lower()
    s = _INTENT_ALIASES.get(s, s)
    if s in INTENTS:
        return s
    return _INTENT_ORDER[position] if position < len(_INTENT_ORDER) else "other"


def _clean_reply_text(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    t = _EMOJI.sub("", raw)
    t = re.sub(r"\s+", " ", t).strip().strip('"“”\'').strip()
    t = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", t)  # leading list markers
    t = _replace_known_english(t)
    if not t or len(t) > MAX_REPLY_CHARS or _devanagari_ratio(t) < 0.5:
        return None
    if t.endswith("."):
        t = t[:-1].rstrip() + "।"
    elif t[-1] not in "।?!":
        t += "।"
    return t


def _items_to_replies(items: list) -> list[dict]:
    replies: list[dict] = []
    seen: set[str] = set()
    for pos, item in enumerate(items):
        if isinstance(item, dict):
            t = _clean_reply_text(item.get("text") or item.get("reply"))
            intent = _normalise_intent(item.get("intent"), pos)
        else:
            t = _clean_reply_text(item)
            intent = _normalise_intent(None, pos)
        if t and t not in seen:
            seen.add(t)
            replies.append({"text": t, "intent": intent})
    return replies


def _parse_replies(raw: str) -> list[dict]:
    """Extract valid replies from model output; may return fewer than requested."""
    values = _json_values(_clean_model_text(raw))
    for val in reversed(values):  # final answer is usually last
        items: Any = val
        if isinstance(val, dict):
            items = next(
                (val[k] for k in ("replies", "suggestions", "options") if isinstance(val.get(k), list)),
                None,
            )
        if isinstance(items, list) and (replies := _items_to_replies(items)):
            return replies
    # Outer structure broken (seen live: list closed with "}}" instead of "]}"): the
    # scanner still decodes each inner {"intent","text"} object on its own.
    loose = [v for v in values if isinstance(v, dict) and ("text" in v or "reply" in v)]
    return _items_to_replies(loose)


# Safety net for the failure modes seen live with thinking off. The prompt prevents most
# of them; a reply caught here is dropped and its slot is filled from the other candidate
# or a generic reply.
_FIRST_PERSON_FUTURE = ("ूँगा", "ूँगी", "ऊँगा", "ऊँगी", "ूंगा", "ूंगी", "ऊंगा", "ऊंगी")
_FEMININE_FIRST_PERSON = re.compile(r"(?:ूँगी|ऊँगी|ूंगी|ऊंगी)(?=$|[\s।,?!])")
_MASCULINE_FIRST_PERSON = re.compile(r"(?:ूँगा|ऊँगा|ूंगा|ऊंगा)(?=$|[\s।,?!])")
_DEV_WORD = re.compile(r"[\u0900-\u0963\u0966-\u097f]+")  # Devanagari word, without danda ।॥
_SUBJUNCTIVE_1P = ("ऊँ", "ूँ", "ऊं", "ूं")  # बनाऊँ, करूँ, दूँ: "shall I ...?"
_NOT_SUBJUNCTIVE = {"हूँ", "हूं", "क्यूँ", "क्यूं", "यूँ", "यूं", "हूँगा"}
_LIGHT_VERBS = {"दूँ", "दूं", "लूँ", "लूं"}
# First person of दे ("give"/"do for someone"): "भर दूँगा", "कर दूँ", "पैसे दूँगा".
_GIVE_1P = {"दूँ", "दूं", "दूँगा", "दूँगी", "दूंगा", "दूंगी"}
_BRING_1P = {"आऊँगा", "आऊँगी", "आऊँ", "आऊंगा", "आऊंगी", "आऊं"}  # after "ले": "ले आऊँगा"
_TAKE_1P = {"लूँगा", "लूँगी", "लूंगा", "लूंगी"}
# "X कर लूँगा" is the user's own business for these X; otherwise it means "I'll do it myself".
_SELF_ACTIVITIES = {"आराम", "इंतज़ार", "इंतजार", "बात", "कोशिश", "नाश्ता"}


def _speaker_offer(utterance: str) -> tuple[set[str], bool]:
    """(verb roots of the speaker's own offers, whether the speaker offered anything).
    "क्या बनाऊँ?" -> ({"बना"}, True); "online भर दूँ?" -> ({"भर"}, True);
    "मैं साथ चलूँगी" -> (set(), True)."""
    words = _DEV_WORD.findall(utterance)
    roots: set[str] = set()
    offered = False
    for i, w in enumerate(words):
        if w in _NOT_SUBJUNCTIVE:
            continue
        if w.endswith(_FIRST_PERSON_FUTURE):
            offered = True
        if w in _LIGHT_VERBS:
            offered = True
            if i > 0 and len(words[i - 1]) >= 2:
                roots.add(words[i - 1])
            continue
        for suf in _SUBJUNCTIVE_1P:
            if w.endswith(suf) and len(w) - len(suf) >= 2:
                offered = True
                roots.add(w[: -len(suf)])
    return roots, offered


def _violates_roles(text: str, utterance: str) -> bool:
    """True if the reply has the wrong grammatical gender for the user, or the user takes
    over something the speaker offered to do."""
    wrong_gender = _MASCULINE_FIRST_PERSON if USER_GENDER == "female" else _FEMININE_FIRST_PERSON
    if wrong_gender.search(text):
        return True
    roots, offered = _speaker_offer(utterance)
    if not offered:
        return False
    words = _DEV_WORD.findall(text)
    for i, w in enumerate(words):
        if w in _GIVE_1P:
            return True  # "मैं बना दूँ?", "भर दूँगा", "पेमेंट कर दूँगा"
        if w == "ले" and words[i + 1 : i + 2] and words[i + 1] in _BRING_1P:
            return True  # "ले आऊँगा" when they offered to bring it
        if w == "कर" and words[i + 1 : i + 2] and words[i + 1] in _TAKE_1P and (i == 0 or words[i - 1] not in _SELF_ACTIVITIES):
            return True  # "बैंक से निकाल कर लूँगा" after "भर दूँ?" (but "आराम कर लूँगा" is fine)
        for root in roots:
            if w.startswith(root) and w.endswith(_FIRST_PERSON_FUTURE + _SUBJUNCTIVE_1P):
                return True  # "बनाऊँगा" / "बनाऊँ" after the speaker's "बनाऊँ?"
            if w == root and words[i + 1 : i + 2] and words[i + 1].endswith(_FIRST_PERSON_FUTURE + _SUBJUNCTIVE_1P):
                return True  # "बना लूँगा" after "बनाऊँ?"
            if w.startswith(root) and w.endswith(("ता", "ती")) and words[i + 1 : i + 2] in (["हूँ"], ["हूं"]):
                return True  # "बनाता हूँ" after "बनाऊँ?"
            if w.startswith(root) and w.endswith("ना") and words[i + 1 : i + 2] and words[i + 1].startswith("चाह"):
                return True  # "बनाना चाहूँगा" / "बनाना चाहता हूँ" after "बनाऊँ?"
    return False


# ---- choosing the best reply per slot from several candidates

_SLOT = {"yes": 0, "no": 1}  # everything else (ask / feeling / other) is slot 2
MAX_UNKNOWN_WORDS = 2  # a reply with this many unrecognised words is treated as garbled
_FEW_SHOT_VOCAB = lexicon.vocabulary(
    *(ex[1] for ex in _FEW_SHOT), *(r["text"] for ex in _FEW_SHOT for r in ex[5]),
    *(r["text"] for r in FALLBACK_REPLIES),
)


def _conversation_vocab(transcript: str, history: list[dict] | None) -> frozenset[str]:
    hist = [str(h.get("text", "")) for h in (history or []) if isinstance(h, dict)]
    return _FEW_SHOT_VOCAB | lexicon.vocabulary(transcript, *hist)


# Few-shot leakage seen live: "no" replies offering खिचड़ी to a cricket question. Dishes in a
# reply when nobody mentioned food cost a point, so the other candidate's reply wins.
_DISH_WORDS = lexicon.vocabulary("खिचड़ी कढ़ी दलिया पराठे पराठा रोटी सब्ज़ी सब्जी दाल चावल दही पोहा उपमा खाएंगे खाएँगे")
_FOOD_CUES = _DISH_WORDS | lexicon.vocabulary("खाना खाने खाया भूख नाश्ता लंच डिनर चाय दूध पानी फल मिठाई")


def _food_context(transcript: str, history: list[dict] | None) -> bool:
    hist = [str(h.get("text", "")) for h in (history or []) if isinstance(h, dict)]
    return bool(lexicon.vocabulary(transcript, *hist) & _FOOD_CUES)


def _reply_score(text: str, vocab: frozenset[str], food_ok: bool, offered: bool = False) -> int:
    """0 is clean. +1 per unrecognised word, +1 if very long, +1 for off-topic food,
    +1 for an awkward "अगर ... " reply, +1 for the user asking "shall I ...?" after the
    speaker offered to do something ("अगर दाल में तड़का भी डालूँ?")."""
    score = len(lexicon.unknown_words(text, vocab))
    words = _DEV_WORD.findall(text)
    if len(text.split()) > 14:
        score += 1
    if not food_ok and lexicon.vocabulary(text) & _DISH_WORDS:
        score += 1
    if words[:1] == ["अगर"]:
        score += 1
    if offered and text.endswith("?") and any(
        w.endswith(_SUBJUNCTIVE_1P) and w not in _NOT_SUBJUNCTIVE and len(w) > 2 for w in words
    ):
        score += 1
    return score


def _select_replies(
    candidates: list[list[dict]], n: int, vocab: frozenset[str], food_ok: bool = True, offered: bool = False
) -> list[dict]:
    """Merge candidate reply lists (best candidate first) slot by slot: yes / no / third.
    In each slot the reply with the lowest score wins; ties go to the earlier candidate,
    so with a clean temperature-0 answer the result stays deterministic."""
    slots: dict[int, list[tuple[int, int, dict]]] = {}
    extras: list[tuple[int, int, dict]] = []
    for ci, replies in enumerate(candidates):
        seen_slots: set[int] = set()
        for r in replies:
            score = _reply_score(r["text"], vocab, food_ok, offered)
            slot = _SLOT.get(r["intent"], 2)
            if slot in seen_slots:  # a second reply for a slot this candidate already filled
                extras.append((score, ci, r))
                continue
            seen_slots.add(slot)
            slots.setdefault(slot, []).append((score, ci, r))
    out: list[dict] = []
    used: set[str] = set()
    for slot in sorted(slots):
        for score, _ci, r in sorted(slots[slot], key=lambda x: (x[0], x[1])):
            if r["text"] not in used and score < MAX_UNKNOWN_WORDS:
                out.append(r)
                used.add(r["text"])
                break
    for score, _ci, r in sorted(extras, key=lambda x: (x[0], x[1])):  # only used when n > 3
        if len(out) >= n:
            break
        if r["text"] not in used and score < MAX_UNKNOWN_WORDS:
            out.append(r)
            used.add(r["text"])
    return out[:n]


def _pad_with_fallback(replies: list[dict], n: int) -> list[dict]:
    out = list(replies[:n])
    have_text = {r["text"] for r in out}
    have_intent = {r["intent"] for r in out}
    # Prefer generic replies whose intent is still missing, then any unused one.
    pool = sorted(FALLBACK_REPLIES, key=lambda r: r["intent"] in have_intent)
    for r in pool:
        if len(out) >= n:
            break
        if r["text"] not in have_text:
            out.append(dict(r))
            have_text.add(r["text"])
    return out


def _fallback(n: int) -> list[dict]:
    return [dict(r) for r in FALLBACK_REPLIES[: max(n, 0)]]


# --------------------------------------------------------------------------- public API

async def _llm_transliterate(text: str, timeout: float = TIMEOUT_S) -> str | None:
    """Romanised / mixed Hinglish -> Devanagari via the LLM. None if it fails or looks wrong."""
    raw = await _chat(_devanagari_messages(text), max_tokens=300, temperature=0.0, timeout=timeout)
    if raw is None:
        return None
    cleaned = _clean_model_text(raw)
    out: str | None = None
    for val in reversed(_json_values(cleaned)):
        if isinstance(val, dict) and isinstance(val.get("text"), str):
            out = val["text"]
            break
    if out is None:
        out = cleaned  # model ignored the JSON instruction; validate the plain text
    out = re.sub(r"\s+", " ", out).strip().strip('"“”').strip()
    if not out or _devanagari_ratio(out) < 0.8 or len(out) > 3 * len(text) + 20:
        log.warning("llm: transliteration output rejected")
        return None
    return out


async def _fix_residual_latin(replies: list[dict], timeout: float) -> list[dict]:
    """Replies that still contain Latin words after the word map get one parallel LLM
    transliteration pass. If that fails, the reply is kept as is (still readable)."""
    todo = [i for i, r in enumerate(replies) if _has_latin(r["text"])]
    if not todo or timeout <= 0.3:
        return replies
    fixed = await asyncio.gather(*(_llm_transliterate(replies[i]["text"], timeout) for i in todo))
    out = [dict(r) for r in replies]
    for i, new in zip(todo, fixed):
        if new:
            out[i]["text"] = new
    return out


# Two candidates are requested in parallel (2 requests against the ~40 RPM limit).
# /api/listen uses (0.0, 0.5). If the first (temperature-0) candidate is fully clean, it is
# returned as soon as it arrives and the second is cancelled, so the usual latency is one
# call and a rehearsed line gives the same suggestions on the real take. Otherwise the
# second candidate is awaited and the two are merged slot by slot. "More options"
# (variety=True) samples at (0.6, 0.8).
LISTEN_TEMPERATURES = (0.0, 0.5)
VARIETY_TEMPERATURES = (0.6, 0.8)


async def suggest_replies(
    transcript: str, history: list[dict] | None = None, n: int = 3, *, variety: bool = False
) -> list[dict]:
    """Return n short replies the speechless user might say back.

    history: [{"role": "family" | "user", "text": str}, ...] oldest first, at most ~6 turns.
    variety: False for a fresh utterance (stable output); True for "More options".
    Returns: [{"text": "<Devanagari Hindi>", "intent": "yes|no|ask|feeling|other"}, ...]
    Must never raise on LLM failure: fall back to generic replies.
    """
    try:
        n = max(1, min(int(n), 5))
        if not isinstance(transcript, str) or not transcript.strip():
            return _fallback(n)
        t0 = time.perf_counter()
        messages = _suggest_messages(transcript, history, n)
        temps = VARIETY_TEMPERATURES if variety else LISTEN_TEMPERATURES
        vocab = _conversation_vocab(transcript, history)
        tasks = [asyncio.create_task(_chat(messages, max_tokens=400, temperature=t)) for t in temps]

        def usable(raw: str | None) -> tuple[list[dict], int]:
            if raw is None:
                return [], 0
            parsed = _parse_replies(raw)
            kept = [r for r in parsed if not _violates_roles(r["text"], transcript)]
            return kept, len(parsed) - len(kept)

        try:
            food_ok = _food_context(transcript, history)
            offered = _speaker_offer(transcript)[1]
            first, dropped = usable(await tasks[0])
            quick = _select_replies([first], n, vocab, food_ok, offered) if first else []
            clean = (
                not dropped
                and len(quick) == n
                and all(_reply_score(r["text"], vocab, food_ok, offered) == 0 for r in quick)
            )
            if clean:
                replies = quick
            else:
                second, dropped2 = usable(await tasks[1])
                dropped += dropped2
                candidates = [c for c in (first, second) if c]
                if dropped:
                    log.info("llm: dropped %d candidate reply(ies) that broke role/gender rules", dropped)
                if not candidates:
                    log.warning("llm: no usable candidate; using fallback")
                    return _fallback(n)
                replies = _select_replies(candidates, n, vocab, food_ok, offered)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
        if len(replies) < n:
            log.warning("llm: only %d/%d usable replies; padding", len(replies), n)
        replies = _pad_with_fallback(replies, n)
        return await _fix_residual_latin(replies, timeout=min(3.0, TIMEOUT_S - (time.perf_counter() - t0)))
    except Exception as e:  # pragma: no cover - belt and braces
        log.warning("llm: suggest_replies failed (%s); using fallback", type(e).__name__)
        return _fallback(n if isinstance(n, int) and n > 0 else 3)


def is_mostly_devanagari(text: str, threshold: float = 0.6) -> bool:
    return _devanagari_ratio(text) >= threshold


async def to_devanagari(text: str) -> str:
    """Convert typed romanised Hinglish ("mujhe paani chahiye") to Devanagari Hindi
    ("मुझे पानी चाहिए") so the TTS/voice clone pronounces it well. Text already in
    Devanagari is returned unchanged. Must never raise: return the input on failure."""
    try:
        if not isinstance(text, str) or not text.strip():
            return text
        if is_mostly_devanagari(text) or not _has_latin(text):
            # Already (mostly) Devanagari: no LLM call. Common English words in an
            # otherwise Devanagari sentence are mapped locally ("मुझे doctor" -> "मुझे डॉक्टर").
            return _replace_known_english(text)
        out = await _llm_transliterate(text)
        return out if out is not None else text
    except Exception as e:  # pragma: no cover
        log.warning("llm: to_devanagari failed (%s); returning input", type(e).__name__)
        return text
