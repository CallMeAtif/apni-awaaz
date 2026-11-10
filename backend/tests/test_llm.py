"""Unit tests for app.llm. Every HTTP call is mocked with respx: no live NVIDIA calls
(respx raises on any unmocked request). The API key is a fake set per test."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app import llm

URL = llm.NVIDIA_URL
DEMO = "पापा, आज खाने में क्या बनाऊँ? दाल चावल चलेगा?"
FALLBACK_3 = [dict(r) for r in llm.FALLBACK_REPLIES[:3]]


def completion(content: str | None, reasoning: str | None = None) -> httpx.Response:
    msg = {"role": "assistant", "content": content, "reasoning_content": reasoning}
    return httpx.Response(200, json={"choices": [{"index": 0, "message": msg}]})


def replies_json(*items: tuple[str, str], **extra) -> str:
    return json.dumps(
        {"speaker": "child", "gist": "x", "offer": "none", **extra,
         "replies": [{"intent": i, "text": t} for i, t in items]},
        ensure_ascii=False,
    )


GOOD = replies_json(
    ("yes", "हाँ बेटा, दाल चावल बढ़िया रहेगा।"),
    ("no", "नहीं बेटा, आज रोटी सब्ज़ी बना दो।"),
    ("ask", "तुमने खुद कुछ खाया?"),
)


def sent_body(route: respx.Route, i: int = -1) -> dict:
    return json.loads(route.calls[i].request.content)


def is_transliteration(request: httpx.Request) -> bool:
    return json.loads(request.content)["messages"][0]["content"].startswith("You convert")


def by_temperature(contents: dict[float, str]):
    """respx side effect: answer each parallel candidate call according to its temperature."""
    def handler(request: httpx.Request) -> httpx.Response:
        return completion(contents[json.loads(request.content)["temperature"]])
    return handler


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-not-real")
    monkeypatch.setattr(llm, "RETRY_BACKOFF_S", 0)
    monkeypatch.setattr(llm, "USER_GENDER", "male")
    yield


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        yield m


# ----------------------------------------------------------------- suggest_replies: happy paths

async def test_parses_clean_json_and_sends_thinking_off(mock):
    route = mock.post(URL).mock(return_value=completion(GOOD))
    out = await llm.suggest_replies(DEMO)
    assert out == [
        {"text": "हाँ बेटा, दाल चावल बढ़िया रहेगा।", "intent": "yes"},
        {"text": "नहीं बेटा, आज रोटी सब्ज़ी बना दो।", "intent": "no"},
        {"text": "तुमने खुद कुछ खाया?", "intent": "ask"},
    ]
    body = sent_body(route)
    assert body["model"] == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["messages"][0]["role"] == "system"
    assert DEMO in body["messages"][-1]["content"]
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer test-key-not-real"
    assert req.headers["user-agent"] == "ApniAwaaz/0.1"


async def test_strips_think_block_and_code_fence(mock):
    raw = "<think>daughter offers to cook, so...</think>\n```json\n" + GOOD + "\n```"
    mock.post(URL).mock(return_value=completion(raw))
    out = await llm.suggest_replies(DEMO)
    assert [r["intent"] for r in out] == ["yes", "no", "ask"]
    assert out[0]["text"].startswith("हाँ बेटा")


async def test_uses_reasoning_content_when_content_empty(mock):
    mock.post(URL).mock(return_value=completion(None, reasoning="\n" + GOOD))
    out = await llm.suggest_replies(DEMO)
    assert out[0]["text"] == "हाँ बेटा, दाल चावल बढ़िया रहेगा।"


async def test_recovers_reply_objects_from_broken_outer_json(mock):
    # Seen live: the list is closed with "}}" instead of "]}".
    broken = GOOD[: GOOD.rindex("]")] + "}"
    with pytest.raises(json.JSONDecodeError):
        json.loads(broken)
    mock.post(URL).mock(return_value=completion(broken))
    out = await llm.suggest_replies(DEMO)
    assert [r["intent"] for r in out] == ["yes", "no", "ask"]


async def test_accepts_bare_list_of_strings(mock):
    mock.post(URL).mock(return_value=completion('["हाँ, ठीक रहेगा", "नहीं बेटा", "कब तक?"]'))
    out = await llm.suggest_replies(DEMO)
    assert out == [
        {"text": "हाँ, ठीक रहेगा।", "intent": "yes"},  # terminal danda added
        {"text": "नहीं बेटा।", "intent": "no"},
        {"text": "कब तक?", "intent": "ask"},
    ]


async def test_known_english_words_become_devanagari_without_extra_call(mock):
    raw = replies_json(("yes", "हाँ, online भर दो।"), ("no", "नहीं, TV बंद कर दो।"), ("ask", "कौन सा match है?"))
    route = mock.post(URL).mock(return_value=completion(raw))
    out = await llm.suggest_replies("सुनो जी, bill online भर दूँ?")
    assert [r["text"] for r in out] == ["हाँ, ऑनलाइन भर दो।", "नहीं, टीवी बंद कर दो।", "कौन सा मैच है?"]
    assert not any(is_transliteration(c.request) for c in route.calls)  # no extra LLM call


async def test_unknown_latin_word_gets_one_transliteration_call(mock):
    raw = replies_json(("yes", "हाँ, Sharma जी को बुला लो।"), ("no", "नहीं, अभी नहीं।"), ("ask", "कब आएँगे?"))
    def handler(request):
        if is_transliteration(request):
            return completion('{"text":"हाँ, शर्मा जी को बुला लो।"}')
        return completion(raw)
    route = mock.post(URL).mock(side_effect=handler)
    out = await llm.suggest_replies("शर्मा जी आए हैं।")
    assert out[0]["text"] == "हाँ, शर्मा जी को बुला लो।"
    translit = [c for c in route.calls if is_transliteration(c.request)]
    assert len(translit) == 1
    assert json.loads(translit[0].request.content)["messages"][-1]["content"] == "हाँ, Sharma जी को बुला लो।"


async def test_history_is_included_oldest_first(mock):
    route = mock.post(URL).mock(return_value=completion(GOOD))
    history = [{"role": "family", "text": "पापा, बोर हो रहे हो?"}, {"role": "user", "text": "थोड़ा सा।"}]
    await llm.suggest_replies("TV लगा दूँ?", history)
    last = sent_body(route)["messages"][-1]["content"]
    assert "Family: पापा, बोर हो रहे हो?" in last
    assert last.index("Family:") < last.index("User: थोड़ा सा।") < last.index("TV लगा दूँ?")


async def test_n_is_respected(mock):
    route = mock.post(URL).mock(return_value=completion(GOOD))
    out = await llm.suggest_replies(DEMO, n=2)
    assert len(out) == 2
    assert "exactly 2 short replies" in sent_body(route)["messages"][0]["content"]


async def test_listen_is_deterministic_and_variety_samples_higher(mock):
    route = mock.post(URL).mock(return_value=completion(GOOD))

    async def temps_for(**kw) -> set[float]:
        before = route.call_count
        out = await llm.suggest_replies(DEMO, **kw)
        return out, {json.loads(c.request.content)["temperature"] for c in route.calls[before:]}

    a, ta = await temps_for()
    b, tb = await temps_for()  # the same line again (rehearsal, then the real take)
    _, tv = await temps_for(variety=True)  # "More options"
    assert a == b and 0.0 in ta and ta <= {0.0, 0.5} and tb <= {0.0, 0.5}
    assert 0.6 in tv and tv <= {0.6, 0.8}


async def test_clean_first_candidate_returns_without_waiting_for_the_second(mock):
    import asyncio

    async def handler(request):
        if json.loads(request.content)["temperature"] == 0.5:
            await asyncio.sleep(5)  # would blow the test's time if it were awaited
        return completion(GOOD)
    mock.post(URL).mock(side_effect=handler)
    t0 = asyncio.get_running_loop().time()
    out = await llm.suggest_replies(DEMO)
    assert asyncio.get_running_loop().time() - t0 < 1.0
    assert out[0]["text"] == "हाँ बेटा, दाल चावल बढ़िया रहेगा।"


# ----------------------------------------------------------------- two candidates, per-slot choice

CLEAN_NO = replies_json(
    ("yes", "हाँ बेटा, दाल चावल बढ़िया रहेगा।"), ("no", "नहीं बेटा, आज खिचड़ी बना दो।"), ("ask", "कितना चावल चाहिए?"),
)
GARBLED_NO = replies_json(
    ("yes", "हाँ बेटा, दाल चावल बनाओ।"), ("no", "नहीं बेटा, आज कड़ी पत्ता चिकन बनाओ।"), ("ask", "कितना चावल चाहिए?"),
)


async def test_temperature_zero_wins_ties(mock):
    mock.post(URL).mock(side_effect=by_temperature({0.0: GOOD, 0.5: CLEAN_NO}))
    out = await llm.suggest_replies(DEMO)
    assert [r["text"] for r in out] == [
        "हाँ बेटा, दाल चावल बढ़िया रहेगा।", "नहीं बेटा, आज रोटी सब्ज़ी बना दो।", "तुमने खुद कुछ खाया?",
    ]


async def test_garbled_slot_is_taken_from_the_other_candidate(mock):
    mock.post(URL).mock(side_effect=by_temperature({0.0: GARBLED_NO, 0.5: CLEAN_NO}))
    out = await llm.suggest_replies(DEMO)
    assert out[0]["text"] == "हाँ बेटा, दाल चावल बनाओ।"  # temperature 0 kept where it is clean
    assert out[1] == {"text": "नहीं बेटा, आज खिचड़ी बना दो।", "intent": "no"}


async def test_garbled_in_every_candidate_falls_back_for_that_slot(mock):
    mock.post(URL).mock(return_value=completion(GARBLED_NO))
    out = await llm.suggest_replies(DEMO)
    assert "नहीं बेटा, आज कड़ी पत्ता चिकन बनाओ।" not in [r["text"] for r in out]
    assert len(out) == 3 and out[0]["text"] == "हाँ बेटा, दाल चावल बनाओ।"


async def test_role_slip_in_one_candidate_is_replaced_from_the_other(mock):
    slip = replies_json(("yes", "हाँ बेटा, बढ़िया।"), ("no", "अगर दाल चले तो मैं बना दूँ?"), ("ask", "कब?"))
    mock.post(URL).mock(side_effect=by_temperature({0.6: slip, 0.8: CLEAN_NO}))
    out = await llm.suggest_replies(DEMO, variety=True)
    texts = [r["text"] for r in out]
    assert "अगर दाल चले तो मैं बना दूँ?" not in texts
    assert "नहीं बेटा, आज खिचड़ी बना दो।" in texts


async def test_one_candidate_failing_still_gives_model_replies(mock):
    def handler(request):
        if json.loads(request.content)["temperature"] == 0.0:
            return httpx.Response(500)
        return completion(GOOD)
    mock.post(URL).mock(side_effect=handler)
    out = await llm.suggest_replies(DEMO)
    assert out[0]["text"] == "हाँ बेटा, दाल चावल बढ़िया रहेगा।"


# ----------------------------------------------------------------- speaker hint

async def test_spouse_relaying_the_doctor_gets_a_speaker_hint(mock):
    route = mock.post(URL).mock(return_value=completion(GOOD))
    utt = "सुनिए, doctor ने कहा है कल सुबह check-up के लिए आना है, आप तैयार रहना।"
    await llm.suggest_replies(utt)
    last = sent_body(route)["messages"][-1]["content"]
    assert 'Speaker: spouse' in last and "only mention the doctor" in last


def test_speaker_hint_uses_history_when_the_utterance_has_no_cue():
    hint = llm._speaker_hint([{"role": "family", "text": "Papa, bore ho rahe ho?"}], "TV लगा दूँ?")
    assert hint is not None and hint.startswith("Speaker: child")
    assert llm._speaker_hint([], "खाना लगा दूँ?") is None


# ----------------------------------------------------------------- suggest_replies: safety net

async def test_drops_reply_that_takes_over_speakers_offer(mock):
    raw = replies_json(("yes", "हाँ बेटा, बढ़िया।"), ("no", "नहीं, मैं बना लूँगा।"), ("ask", "क्या बनाओगी?"))
    mock.post(URL).mock(return_value=completion(raw))
    out = await llm.suggest_replies(DEMO)
    texts = [r["text"] for r in out]
    assert "नहीं, मैं बना लूँगा।" not in texts
    assert len(out) == 3 and {"yes", "no", "ask"} == {r["intent"] for r in out}


async def test_drops_feminine_first_person_for_male_user(mock):
    raw = replies_json(("yes", "हाँ बेटा, बढ़िया।"), ("no", "नहीं, आज मैं खाना खाऊँगी।"), ("ask", "कब?"))
    mock.post(URL).mock(return_value=completion(raw))
    out = await llm.suggest_replies("खाना लगा दूँ?")
    assert all("खाऊँगी" not in r["text"] for r in out)


def test_role_filter_keeps_legitimate_replies():
    assert not llm._violates_roles("हाँ बेटा, दाल चावल बनाओ।", DEMO)
    assert not llm._violates_roles("हाँ, ऑनलाइन भर दो।", "online भर दूँ?")
    assert not llm._violates_roles("आंटी भी आएँगी?", "वर्मा जी आ रहे हैं।")  # 3rd person fem. is fine
    assert llm._violates_roles("हाँ जी, ऑनलाइन भर दूँगा।", "online भर दूँ?")
    assert llm._violates_roles("नहीं बेटा, आज चिकन बनाता हूँ।", DEMO)
    assert not llm._violates_roles("मैं ठीक महसूस करता हूँ।", DEMO)
    # Generalised: any first-person "do it for you" (दूँ / दूँगा) after an offer.
    bill = "सुनो जी, बिजली का bill आया है, तीन हज़ार रुपये। online भर दूँ?"
    assert llm._violates_roles("अगर दाल चले तो मैं बना दूँ?", DEMO)
    assert llm._violates_roles("मैं भी बैंक से पेमेंट कर दूँगा।", bill)
    assert llm._violates_roles("मैं खुद ले आऊँगा।", "पापा, BP की गोली ला दूँ?")
    assert not llm._violates_roles("हाँ, थोड़ी देर में खा लूँगा।", "खाना लगा दूँ?")  # his own action
    assert llm._violates_roles("नहीं, मैं सुबह बैंक से निकाल कर लूँगा।", bill)
    assert not llm._violates_roles("नहीं बेटा, थोड़ा आराम कर लूँगा।", "पापा, बाहर चलें? मैं wheelchair ले आऊँ?")
    assert not llm._violates_roles("मैं पैसे दूँगा।", "आज बहुत गर्मी है।")  # no offer was made


def test_lexicon_flags_invented_words_only():
    from app import lexicon
    vocab = llm._conversation_vocab(DEMO, None)
    assert lexicon.unknown_words("हाँ बेटा, दाल चावल बढ़िया रहेगा।", vocab) == []
    # The precomposed nukta letter U+095C and ड + nukta are the same word.
    assert lexicon.unknown_words("थो\u095c\u093e पानी, पड़ेगा", vocab) == []
    assert lexicon.unknown_words("नहीं बेटा, आज कड़ी क़ीद्दारी, खिचड़ी बना दो।", vocab) == ["कड़ी", "क़ीद्दारी"]


async def test_partial_output_is_padded_without_duplicates(mock):
    raw = replies_json(("yes", "हाँ, ठीक है।"), ("no", "No thanks"))  # 2nd is Latin -> dropped
    mock.post(URL).mock(return_value=completion(raw))
    out = await llm.suggest_replies(DEMO)
    texts = [r["text"] for r in out]
    assert len(out) == 3 and len(set(texts)) == 3
    assert texts[0] == "हाँ, ठीक है।"


# ----------------------------------------------------------------- suggest_replies: failures

async def test_fallback_on_timeout(mock):
    mock.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    assert await llm.suggest_replies(DEMO) == FALLBACK_3


async def test_fallback_on_500_after_one_retry(mock):
    route = mock.post(URL).mock(return_value=httpx.Response(500, text="boom"))
    assert await llm.suggest_replies(DEMO) == FALLBACK_3
    assert route.call_count == 4  # 2 candidates x (1 try + 1 retry)


async def test_retry_on_429_then_success(mock):
    first = [True]

    def handler(request):
        if first[0]:
            first[0] = False
            return httpx.Response(429)
        return completion(GOOD)
    route = mock.post(URL).mock(side_effect=handler)
    out = await llm.suggest_replies(DEMO)
    assert out[0]["intent"] == "yes" and out[0]["text"].startswith("हाँ बेटा")
    assert route.call_count >= 2  # the 429 was retried


async def test_no_retry_on_401(mock):
    route = mock.post(URL).mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    assert await llm.suggest_replies(DEMO) == FALLBACK_3
    assert route.call_count == 2  # one per candidate, no retries


async def test_fallback_on_garbage_text(mock):
    mock.post(URL).mock(return_value=completion("Sorry, I can't help with that."))
    assert await llm.suggest_replies(DEMO) == FALLBACK_3


async def test_fallback_on_non_json_body(mock):
    mock.post(URL).mock(return_value=httpx.Response(200, text="<html>gateway</html>"))
    assert await llm.suggest_replies(DEMO) == FALLBACK_3


async def test_fallback_on_connection_error(mock):
    mock.post(URL).mock(side_effect=httpx.ConnectError("dns"))
    assert await llm.suggest_replies(DEMO) == FALLBACK_3


async def test_empty_transcript_makes_no_call(mock):
    route = mock.post(URL).mock(return_value=completion(GOOD))
    assert await llm.suggest_replies("   ") == FALLBACK_3
    assert not route.called


async def test_missing_key_makes_no_call(mock, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY")
    route = mock.post(URL).mock(return_value=completion(GOOD))
    assert await llm.suggest_replies(DEMO) == FALLBACK_3
    assert not route.called


async def test_malformed_history_never_raises(mock):
    mock.post(URL).mock(return_value=completion(GOOD))
    out = await llm.suggest_replies(DEMO, history=[None, "x", {"role": "user"}, {"text": 5}])  # type: ignore[list-item]
    assert len(out) == 3


# ----------------------------------------------------------------- to_devanagari

async def test_devanagari_input_short_circuits_without_call(mock):
    route = mock.post(URL).mock(return_value=completion('{"text":"x"}'))
    assert await llm.to_devanagari("मुझे भूख लगी है") == "मुझे भूख लगी है"
    assert not route.called


async def test_mostly_devanagari_maps_known_english_locally(mock):
    route = mock.post(URL).mock(return_value=completion('{"text":"x"}'))
    assert await llm.to_devanagari("मुझे doctor के पास जाना है") == "मुझे डॉक्टर के पास जाना है"
    assert not route.called


async def test_romanised_input_is_converted(mock):
    route = mock.post(URL).mock(return_value=completion('```json\n{"text":"थोड़ा पानी दे दो।"}\n```'))
    assert await llm.to_devanagari("thoda paani de do") == "थोड़ा पानी दे दो।"
    body = sent_body(route)
    assert body["temperature"] == 0.0
    assert body["messages"][-1]["content"] == "thoda paani de do"


async def test_to_devanagari_returns_input_on_failure(mock):
    mock.post(URL).mock(return_value=httpx.Response(500))
    assert await llm.to_devanagari("thoda paani de do") == "thoda paani de do"


async def test_to_devanagari_returns_input_on_timeout(mock):
    mock.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    assert await llm.to_devanagari("mujhe neend aa rahi hai") == "mujhe neend aa rahi hai"


async def test_to_devanagari_rejects_non_devanagari_output(mock):
    mock.post(URL).mock(return_value=completion('{"text":"Give me some water"}'))
    assert await llm.to_devanagari("thoda paani de do") == "thoda paani de do"


async def test_to_devanagari_empty_and_digits(mock):
    route = mock.post(URL).mock(return_value=completion('{"text":"x"}'))
    assert await llm.to_devanagari("") == ""
    assert await llm.to_devanagari("123 !") == "123 !"
    assert not route.called


# ----------------------------------------------------------------- warmup

async def test_warmup_is_one_tiny_call_and_never_raises(mock):
    route = mock.post(URL).mock(side_effect=[completion("न"), httpx.ConnectError("x"), httpx.ConnectError("x")])
    assert await llm.warmup() is True
    assert sent_body(route, 0)["max_tokens"] == 1
    assert await llm.warmup() is False  # connection error twice (with retry): False, no raise


# ----------------------------------------------------------------- final polish: leakage + slips

async def test_off_topic_dish_loses_to_an_on_topic_reply(mock):
    cricket = "पापा, TV पे cricket match लगा दूँ? India खेल रहा है।"
    leaky = replies_json(("yes", "हाँ बेटा, लगा दो।"), ("no", "नहीं बेटा, आज आराम से खिचड़ी खाएंगे।"), ("ask", "स्कोर क्या है?"))
    on_topic = replies_json(("yes", "हाँ बेटा, लगा दो।"), ("no", "नहीं बेटा, बाद में देखेंगे।"), ("ask", "स्कोर क्या है?"))
    mock.post(URL).mock(side_effect=by_temperature({0.0: leaky, 0.5: on_topic}))
    out = await llm.suggest_replies(cricket)
    assert out[1] == {"text": "नहीं बेटा, बाद में देखेंगे।", "intent": "no"}


async def test_dishes_are_fine_when_they_talked_about_food(mock):
    mock.post(URL).mock(side_effect=by_temperature({0.0: GOOD, 0.5: CLEAN_NO}))
    out = await llm.suggest_replies(DEMO)
    assert out[0]["text"] == "हाँ बेटा, दाल चावल बढ़िया रहेगा।"  # the demo line is unchanged


def test_wanting_to_do_the_offered_job_is_a_role_slip():
    assert llm._violates_roles("नहीं बेटा, आज खिचड़ी बनाना चाहूँगा।", DEMO)
    assert not llm._violates_roles("आज खिचड़ी खाना चाहूँगा।", DEMO)  # eating is his own business
