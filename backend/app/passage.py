"""Voice-banking reading passage for GET /api/enroll/passage. OWNER: Lead.

About 25 seconds read aloud at a calm pace (~67 words). Warm, everyday memories, nothing
medical, so it is pleasant to read while banking one's voice. It is written for phonetic
variety, so the clone hears a wide range of Hindi sounds:
  - aspirated stops: ख घ छ ठ थ ध फ भ
  - retroflex consonants and flaps: ट ठ ड ड़ ढ
  - nuqta sounds: ख़ ज़
  - nasalisation: ँ ं
  - conjuncts: प्य, द्र, ध्य, ट्ट
  - long and short vowels and diphthongs: आ इ ई उ ऊ ए ऐ ओ औ
It mixes statements, one question and one exclamation, for intonation range.

Gnani's voice-clone embedding needs 5-30 s of clean speech; the backend trims anything
longer than 30 s, so reading only part of the passage is fine.
"""
from __future__ import annotations

ENROLL_PASSAGE: str = (
    "आज सुबह की धूप, ठंडी हवा और फूलों की महक बहुत प्यारी है। "
    "खिड़की के बाहर चिड़ियाँ गा रही हैं, और रसोई से अदरक वाली चाय की ख़ुशबू आ रही है। "
    "बचपन में हम घर की छत पर पतंग उड़ाते थे, और शाम को समुद्र किनारे भुट्टा खाते थे। "
    "क्या आपने कभी ध्यान से बारिश की आवाज़ सुनी है? "
    "चलो, आज मिलकर ढेर सारे पुराने गाने गाएँ!"
)
