"""Live quality + latency eval for backend/app/llm.py (NVIDIA Nemotron; no Gnani calls).

Run from the backend venv:
    cd backend && uv run python ../scripts/eval_llm.py [--repeat N]

All utterances are synthetic. Each suggestion makes 2 parallel NVIDIA calls (two
candidates), so a repeat is ~35 calls; cases are paced to stay under ~40 requests/min.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import lexicon, llm  # noqa: E402

# (label, history, utterance). Mostly Prisma-v3 "codemix" style, some romanised.
SUGGEST_CASES: list[tuple[str, list[dict], str]] = [
    ("food (demo)", [], "पापा, आज खाने में क्या बनाऊँ? दाल चावल चलेगा?"),
    ("medicine", [], "Papa, BP की गोली ली आपने? मैं ला दूँ?"),
    ("pain", [], "पापा, आज गला कैसा है? दर्द ज़्यादा तो नहीं?"),
    ("visitors", [], "सुनो जी, शर्मा जी शाम को मिलने आ रहे हैं, ठीक है ना?"),
    ("TV", [{"role": "family", "text": "पापा, आप बोर तो नहीं हो रहे?"},
            {"role": "user", "text": "थोड़ा सा।"}],
     "TV पे cricket match लगा दूँ? India का match है।"),
    ("sleep", [], "Papa, raat ko neend aayi theek se?"),
    ("going out", [], "पापा, मैं office जा रही हूँ, शाम को सात बजे तक आऊँगी।"),
    ("doctor appt", [], "पापा, कल सुबह दस बजे doctor का appointment है, मैं साथ चलूँगी।"),
    ("money", [], "सुनो जी, बिजली का bill आया है, तीन हज़ार रुपये। online भर दूँ?"),
    ("joke", [], "पापा, सुनो, पप्पू बोला मैं इतना smart हूँ कि exam से पहले ही fail हो गया! हाहा"),
    # Phase 2 regressions from the manager's live spot-check:
    ("doctor relay (spouse)", [], "सुनिए, doctor ने कहा है कल सुबह check-up के लिए आना है, आप तैयार रहना।"),
    # Final polish: unrelated topics, to catch few-shot leakage (खिचड़ी in a cricket reply).
    ("cricket (TV)", [], "पापा, TV पे cricket match लगा दूँ? India खेल रहा है।"),
    ("neighbour visit", [], "पापा, पड़ोस वाली गुप्ता आंटी आपसे मिलने आई हैं, अंदर बुला लूँ?"),
    ("grandchild result", [], "पापा, आपके पोते का result आया है, उसके 90 percent आए हैं!"),
    ("rain outside", [], "सुनो जी, बाहर बहुत तेज़ बारिश हो रही है, खिड़की बंद कर दूँ?"),
]

# "More options" (variety=True): where the garbled "no" and the "मैं बना दूँ?" slip were seen.
VARIETY_CASES: list[tuple[str, list[dict], str]] = [
    ("food (demo) more", [], "पापा, आज खाने में क्या बनाऊँ? दाल चावल चलेगा?"),
    ("money more", [], "सुनो जी, बिजली का bill आया है, तीन हज़ार रुपये। online भर दूँ?"),
    ("doctor relay more", [], "सुनिए, doctor ने कहा है कल सुबह check-up के लिए आना है, आप तैयार रहना।"),
]
PACE_S = 2.5  # 2 requests per suggestion: keeps the run under ~40 requests/min

DEVANAGARI_CASES: list[str] = [
    "thoda paani de do",
    "mujhe neend aa rahi hai",
    "doctor ko kal phone karna",
    "aaj khaana bahut accha tha beta",
    "मुझे भूख लगी है",  # already Devanagari: must short-circuit (no call)
]


class _CountWarnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _flags(reply: dict, vocab: frozenset[str]) -> str:
    words = len(reply["text"].split())
    flags = []
    if words > 12:
        flags.append(f"{words}w>12")
    if llm._devanagari_ratio(reply["text"]) < 1.0:
        flags.append("latin")
    unknown = lexicon.unknown_words(reply["text"], vocab)
    if unknown:
        flags.append("unknown:" + ",".join(unknown))
    return f"  [{' '.join(flags)}]" if flags else ""


async def main(repeat: int) -> None:
    warn = _CountWarnings()
    logging.getLogger("apni_awaaz.llm").addHandler(warn)
    logging.getLogger("apni_awaaz.llm").setLevel(logging.INFO)

    print(f"model={llm.MODEL} thinking=OFF timeout={llm.TIMEOUT_S}s repeat={repeat} "
          f"candidates: listen={llm.LISTEN_TEMPERATURES} variety={llm.VARIETY_TEMPERATURES}\n")
    await llm.warmup()  # as the backend does at startup; keeps the first case from being a cold outlier
    lat: list[float] = []
    fallbacks = 0
    distinct_intent_fail = 0
    cases = [(label, hist, utt, False) for label, hist, utt in SUGGEST_CASES]
    cases += [(label, hist, utt, True) for label, hist, utt in VARIETY_CASES]
    for r in range(repeat):
        for label, hist, utt, variety in cases:
            before = len(warn.messages)
            t0 = time.perf_counter()
            replies = await llm.suggest_replies(utt, hist, variety=variety)
            dt = time.perf_counter() - t0
            lat.append(dt)
            new_warn = warn.messages[before:]
            if new_warn:
                fallbacks += 1
            intents = [x["intent"] for x in replies]
            if len(set(intents)) < len(intents):
                distinct_intent_fail += 1
            print(f"--- [{label}] {dt * 1000:.0f} ms" + (f"  WARN: {new_warn}" if new_warn else ""))
            print(f"    family: {utt}")
            vocab = llm._conversation_vocab(utt, hist)
            for x in replies:
                print(f"    {x['intent']:>7}: {x['text']}{_flags(x, vocab)}")
            await asyncio.sleep(PACE_S)

    print("\n=== to_devanagari ===")
    dlat: list[float] = []
    for text in DEVANAGARI_CASES:
        t0 = time.perf_counter()
        out = await llm.to_devanagari(text)
        dt = time.perf_counter() - t0
        dlat.append(dt)
        print(f"  {dt * 1000:5.0f} ms  {text!r} -> {out!r}")
        await asyncio.sleep(0.3)

    print("\n=== summary ===")
    print(f"suggest_replies: n={len(lat)} p50={_pct(lat, 0.5) * 1000:.0f} ms "
          f"p95={_pct(lat, 0.95) * 1000:.0f} ms max={max(lat) * 1000:.0f} ms "
          f"mean={statistics.mean(lat) * 1000:.0f} ms")
    print(f"  calls with warnings/fallback: {fallbacks}; repeated intents: {distinct_intent_fail}")
    llm_d = [d for d, t in zip(dlat, DEVANAGARI_CASES) if not llm.is_mostly_devanagari(t)]
    if llm_d:
        print(f"to_devanagari (LLM calls): p50={_pct(llm_d, 0.5) * 1000:.0f} ms max={max(llm_d) * 1000:.0f} ms")
    await llm.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    asyncio.run(main(ap.parse_args().repeat))
