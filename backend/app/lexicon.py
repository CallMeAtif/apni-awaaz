"""A small lexicon of everyday spoken Hindi, used to rank LLM reply candidates.

With thinking off, Nemotron occasionally produces invented or garbled words in a reply
("कड़ी पत्ता चिकन", "क़ीद्दारी", "कड़ीची"). llm.py asks for two candidates and, slot by
slot, prefers the reply with fewer words that are neither in this list nor in the
conversation itself. This is a ranking signal, not a spell-checker: a missing common
word only costs a point, and the conversation's own words are always accepted.

Words are compared after normalisation (nukta removed, chandrabindu -> anusvara), and
a word also counts as known if stripping a common inflection leaves a known stem.
"""
from __future__ import annotations

import re
import unicodedata

_DEV_WORD = re.compile(r"[ऀ-ॣ०-ॿ]+")

_WORDS = """
मैं मुझे मुझको मेरा मेरी मेरे हम हमें हमको हमारा हमारी हमारे तुम तुम्हें तुमको तुम्हारा तुम्हारी तुम्हारे तू तुझे
आप आपका आपकी आपके आपको आपसे वो वह वे उन्हें उनका उनकी उनके उसे उसको उसका उसकी उसके उससे उनसे यह ये इसे
इसका इसकी इसके इसमें उसमें कोई कुछ सब सबको सबका सबके सबसे खुद अपना अपनी अपने किसी किसको जिसे
है हैं था थी थे हो हूँ हुआ हुई हुए होगा होगी होंगे होना ना न नहीं मत भी ही तो तक से में पर को का की के ने
और या लेकिन मगर अगर जब तब क्योंकि कि बस जी हाँ अच्छा ठीक बिल्कुल ज़रूर अरे अजी ओह वाह हाहा हा अब अभी
फिर कभी हमेशा आज कल परसों सुबह शाम रात दोपहर दिन हफ़्ते हफ़्ता महीने साल बजे बाद पहले देर जल्दी थोड़ा थोड़ी
थोड़े बहुत ज़्यादा कम सा सी ऐसे वैसे कैसे कैसा कैसी क्या क्यों कब कहाँ कौन कौनसा कितना कितनी कितने यहाँ वहाँ
यहीं वहीं इधर उधर साथ लिए वाला वाली वाले बारे तरह जैसे सही ग़लत एक दो तीन चार पाँच दस सौ हज़ार रुपये आधा
पूरा सारा सारी सारे हर दूसरा दूसरी अगले अगला पिछले बीच अंदर बाहर ऊपर नीचे पास दूर आगे पीछे ज़रा शायद
पापा मम्मी माँ बेटा बेटी बेटे बच्चे बच्चों भाई बहन पति पत्नी दादा दादी नाना नानी पोता पोती अंकल आंटी साहब
डॉक्टर नर्स दोस्त पड़ोसी मेहमान लोग भगवान
खाना दाल चावल रोटी सब्ज़ी खिचड़ी दलिया पोहा उपमा इडली डोसा पराठा पराठे कढ़ी सूप दूध दही चाय कॉफ़ी पानी
फल केला सेब आम मिठाई हलवा खीर नमक चीनी मसाला तेल घी अंडा नाश्ता भूख प्यास स्वाद तड़का हरी
दर्द गला गले सिर पेट पैर हाथ कमर आँख दाँत साँस छाती बुखार खाँसी खराश दवा दवाई गोली तबीयत तकलीफ़ आराम
नींद थकान कमज़ोरी चक्कर ठंड गर्मी बीमार बेहतर अस्पताल चेकअप जाँच बीपी शुगर
घर कमरा बिस्तर कुर्सी पंखा खिड़की दरवाज़ा बाथरूम रसोई टीवी फ़ोन मोबाइल चश्मा कपड़े कंबल छाता शॉल पैसे पैसा
बिल बैंक दुकान बाज़ार मार्केट पार्क मंदिर ऑफ़िस स्कूल गाड़ी ऑटो बस ट्रेन रसीद नंबर नाम ऑनलाइन वीडियो कॉल
अच्छी अच्छे बढ़िया बुरा खुश खुशी उदास परेशान चिंता डर मज़ा प्यार याद शुक्रिया धन्यवाद माफ़ सॉरी हल्का हल्की
हल्के भारी गरम ठंडा नया नई पुराना बड़ा छोटा छोटी मन दिल ज़रूरी आसान मुश्किल सच तैयार प्यारा प्यारी
बात बातें काम समय टाइम मैच क्रिकेट खेल गाना गाने फ़िल्म ख़बर मौसम बारिश धूप रास्ता सामान मदद इंतज़ार हँसी
लिया ली लिए दिया दी दिए किया गया गई गए आया आई आए पिया हुआ चुका चुकी चुके सकता सकती सकते पाऊँगा पाऊँगी
दम मज़ेदार ज़ोर लूँ दूँ लूँगा लूँगी दूँगा दूँगी बड़ी बड़े टीम स्कोर इनिंग्स हाइलाइट्स रिज़ल्ट परसेंट नंबर रेडियो पापड़ पसंद अकेला अकेले अकेली पेमेंट मज़ाक ख़्याल ध्यान परेशानी सलाह उम्मीद इच्छा कोशिश आदत
गुस्सा शांति सुकून तसल्ली फ़िक्र बेचैनी जवाब सवाल नाराज़ आवाज़ सुनाई दिखाई लाजवाब सचमुच बिलकुल जल्द तुरंत
धीरे रोज़ महीना ताज़ा गरमागरम स्वादिष्ट मीठा मीठी नमकीन तीखा फीका जूस बिस्कुट ब्रेड मक्खन सलाद अचार पापड़
रायता पनीर राजमा छोले सांभर दवाइयाँ इलाज रिपोर्ट टेस्ट कमज़ोर ताकत वज़न खाली भरा बंद खुला देर सवेरे
अच्छाई ग़लती माफ़ी शाबाश बधाई प्रणाम नमस्कार राम हे भई यार अम्मा बाबूजी ससुर सास भाभी दीदी भैया चाचा चाची
मामा मामी बुआ फूफा मौसी
पढ़ाई एग्ज़ाम फ़ेल पास मेहनत किस्मत ज़िंदगी दुनिया सपना सहन नमस्ते बधाई मुबारक तारीख़ कमी
"""

# Verb stems; common inflections are generated below (over-generation is harmless here).
_VERB_STEMS = """
हो रह कर आ जा चल बना खा पी दे ले ला लगा लग रख बैठ सो उठ देख सुन बोल बता मिल पूछ सोच समझ भूल रुक कह
दिखा भेज भर बुला पढ़ लिख चाह सक पा दिख बदल खोल बंद धो नहा घूम हँस रो डाल निकाल पहन ढूँढ संभाल छोड़
सह बच मना सुना पहुँच लौट जाग गा कटा करवा दिखवा दिला जगा सुला पड़ समझा
"""

_VERB_ENDINGS = (
    "", "ो", "ा", "ी", "े", "ें", "ना", "नी", "ने", "ता", "ती", "ते", "ेगा", "ेगी", "ेंगे", "ूँगा", "ूँगी",
    "ूँ", "कर", "िए", "या", "यी", "ये", "ई", "ए", "ओ", "ओगे", "ओगी", "एगा", "एगी", "एँगे", "एँ", "ऊँ", "ऊँगा",
    "ऊँगी", "इए", "ाना", "ाओ", "ाया", "ाई", "ाए",
)

# Inflections stripped (longest first) when looking a word up.
_SUFFIXES = sorted(
    {"ों", "ें", "ां", "ियां", "ियों", "ा", "ी", "े", "ो", "ना", "ने", "नी", "ता", "ती", "ते", "गा", "गी", "गे",
     "ेगा", "ेगी", "ेंगे", "ूंगा", "ूंगी", "ऊंगा", "ऊंगी", "कर", "या", "ये", "यी", "ई", "ए", "ओ", "वाला",
     "वाली", "वाले"},
    key=len,
    reverse=True,
)


def _norm(word: str) -> str:
    # NFD splits precomposed nukta letters (U+0958-095F) so the nukta can be dropped; ँ -> ं
    return unicodedata.normalize("NFD", word).replace("़", "").replace("ँ", "ं")


def words(text: str) -> list[str]:
    return _DEV_WORD.findall(text or "")


def _build() -> frozenset[str]:
    out = {_norm(w) for w in _WORDS.split()}
    for stem in _VERB_STEMS.split():
        for end in _VERB_ENDINGS:
            out.add(_norm(stem + end))
    return frozenset(out)


COMMON: frozenset[str] = _build()


def vocabulary(*texts: str) -> frozenset[str]:
    """Normalised words from extra texts (the conversation, few-shot examples, ...)."""
    return frozenset(_norm(w) for t in texts for w in words(t))


def is_known(word: str, extra: frozenset[str] = frozenset()) -> bool:
    w = _norm(word)
    if len(w) <= 1 or w in COMMON or w in extra:
        return True
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            stem = w[: -len(suf)]
            if stem in COMMON or stem in extra:
                return True
    return False


def unknown_words(text: str, extra: frozenset[str] = frozenset()) -> list[str]:
    return [w for w in words(text) if not is_known(w, extra)]
