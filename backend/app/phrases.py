"""Phrase board data for GET /api/phrases. OWNER: Lead.

One-tap phrases a speechless user needs most often, in short spoken Hindi (Devanagari),
each with an English gloss and an emoji for quick visual scanning. Phrases are written
to be gender-neutral where Hindi allows it (e.g. "थकान लग रही है" rather than
"थक गया/गई"), since a static board cannot know the user's gender.

TTS for these is cached on disk after the first play, so each phrase costs Gnani
credits only once per voice. Keep them short.
"""
from __future__ import annotations

import copy

_PHRASES: list[dict] = [
    {
        "category": "ज़रूरतें",
        "category_en": "Needs",
        "phrases": [
            {"text": "पानी चाहिए", "en": "I need water", "emoji": "💧"},
            {"text": "भूख लगी है", "en": "I'm hungry", "emoji": "🍲"},
            {"text": "बाथरूम जाना है", "en": "I need the bathroom", "emoji": "🚻"},
            {"text": "दवाई का समय हो गया", "en": "It's time for my medicine", "emoji": "💊"},
            {"text": "थोड़ा आराम करना है", "en": "I want to rest a little", "emoji": "🛏️"},
            {"text": "मुझे उठकर बैठना है", "en": "Help me sit up", "emoji": "🪑"},
            {"text": "मेरा चश्मा दे दो", "en": "Please give me my glasses", "emoji": "👓"},
            {"text": "पंखा चला दो", "en": "Please switch on the fan", "emoji": "🌀"},
        ],
    },
    {
        "category": "दर्द और एहसास",
        "category_en": "Pain & feelings",
        "phrases": [
            {"text": "दर्द हो रहा है", "en": "I'm in pain", "emoji": "🤕"},
            {"text": "गले में तकलीफ़ है", "en": "My throat is bothering me", "emoji": "😣"},
            {"text": "साँस लेने में दिक्कत है", "en": "I'm having trouble breathing", "emoji": "🫁"},
            {"text": "ठंड लग रही है", "en": "I feel cold", "emoji": "🥶"},
            {"text": "गर्मी लग रही है", "en": "I feel hot", "emoji": "🥵"},
            {"text": "थकान लग रही है", "en": "I feel tired", "emoji": "😴"},
            {"text": "मैं ठीक हूँ", "en": "I'm fine", "emoji": "🙂"},
            {"text": "मन उदास है", "en": "I feel low", "emoji": "😔"},
        ],
    },
    {
        "category": "जवाब",
        "category_en": "Quick answers",
        "phrases": [
            {"text": "हाँ", "en": "Yes", "emoji": "✅"},
            {"text": "नहीं", "en": "No", "emoji": "❌"},
            {"text": "ठीक है", "en": "Okay", "emoji": "👌"},
            {"text": "पता नहीं", "en": "I don't know", "emoji": "🤷"},
            {"text": "थोड़ी देर बाद", "en": "A little later", "emoji": "⏳"},
            {"text": "एक मिनट रुको", "en": "Wait a minute", "emoji": "✋"},
            {"text": "फिर से बोलो", "en": "Please say that again", "emoji": "🔁"},
            {"text": "धन्यवाद", "en": "Thank you", "emoji": "💐"},
        ],
    },
    {
        "category": "परिवार",
        "category_en": "Family",
        "phrases": [
            {"text": "इधर आओ, मेरे पास बैठो", "en": "Come, sit with me", "emoji": "🤗"},
            {"text": "बेटी को बुलाओ", "en": "Please call my daughter", "emoji": "👧"},
            {"text": "बेटे को बुलाओ", "en": "Please call my son", "emoji": "👦"},
            {"text": "डॉक्टर को फ़ोन करो", "en": "Please call the doctor", "emoji": "🩺"},
            {"text": "बच्चों से वीडियो कॉल करनी है", "en": "I want to video-call the children", "emoji": "📱"},
            {"text": "तुम्हारी बहुत याद आई", "en": "I missed you so much", "emoji": "❤️"},
            {"text": "चिंता मत करो", "en": "Don't worry", "emoji": "💛"},
            {"text": "मुझे थोड़ी देर अकेले रहना है", "en": "I'd like some time alone", "emoji": "🚪"},
        ],
    },
    {
        "category": "मेल-जोल",
        "category_en": "Social",
        "phrases": [
            {"text": "नमस्ते", "en": "Hello", "emoji": "🙏"},
            {"text": "आप कैसे हैं?", "en": "How are you?", "emoji": "😊"},
            {"text": "आइए, बैठिए", "en": "Please come in and sit", "emoji": "🛋️"},
            {"text": "चाय पीकर जाइए", "en": "Have some tea before you go", "emoji": "☕"},
            {"text": "मिलकर बहुत अच्छा लगा", "en": "So nice to see you", "emoji": "🤝"},
            {"text": "फिर आना", "en": "Do come again", "emoji": "👋"},
            {"text": "बहुत बढ़िया!", "en": "Wonderful!", "emoji": "👏"},
            {"text": "जन्मदिन मुबारक हो", "en": "Happy birthday", "emoji": "🎂"},
        ],
    },
]


def get_phrases() -> list[dict]:
    """Return the /api/phrases payload (a fresh copy, safe for callers to mutate)."""
    return copy.deepcopy(_PHRASES)
