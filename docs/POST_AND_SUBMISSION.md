# LinkedIn post and submission form drafts

Edit these into your own words before posting. Honest beats hype: reviewers read the code.

## LinkedIn post (primary registrant posts; teammate reshares with their own line)

> What if losing your voice didn't mean losing *your* voice?
>
> People who lose their speech after throat-cancer surgery, ALS or a stroke often end up "speaking" through apps with a generic robot voice. To their own family, they stop sounding like themselves.
>
> In under 24 hours, we built **Apni Awaaz** for the #GreatIndianAIInternshipChallenge by @Gnani.ai:
>
> 🎙️ **Bank your voice.** Record about 20 seconds while you still can. Gnani's voice cloning learns how you sound.
> 👂 **Listen.** Family speaks in everyday Hinglish, and Gnani Prisma v3 transcribes it in about 0.3 s.
> 💬 **Reply.** The app suggests three short Hindi replies. Tap one and it's spoken in **your own voice**.
> 🧩 There's also a one-tap phrase board ("पानी चाहिए", "दर्द हो रहा है") and type-in-Hinglish → speak.
>
> Consent is built in: you can only bank your own voice, and you can delete it any time. It's a communication aid, not a medical device. The demo uses a fictional persona.
>
> Built with Gnani Prisma v3.0 (speech-to-text), Vachana voice cloning + vachana-vc-v1, and NVIDIA Nemotron for reply suggestions. The stack is FastAPI and React.
>
> 🎥 60-second demo below. We'd love your feedback, especially from anyone who works in speech therapy or oncology care.
>
> #GnaniAI #GreatIndianAIInternshipChallenge #VoiceAI #Accessibility #AAC #BuildInPublic

Rules reminder:
- **Allowed:** sharing the post with friends, classmates and college groups, and asking them to watch it.
- **Not allowed:** engagement pods, like-for-like swaps, bots or paid promotion.
- Only one post per platform can be nominated, so the teammate **reshares** rather than making a separate post.

## Submission form (arrives at the primary registrant's email)

- **Project name:** Apni Awaaz (अपनी आवाज़)
- **One-line description:** A speech aid that lets people who've lost their voice reply to their family in their own cloned voice. It listens to Hinglish, suggests replies, and speaks them in the user's banked voice.
- **Gnani models/APIs used:**
  - Prisma v3.0 STT
  - Vachana voice-clone embeddings
  - vachana-vc-v1 voice-cloned TTS
  - Timbre v3.0 TTS as the fallback voice
- **Other tech:** NVIDIA Nemotron 3 Nano Omni (reply suggestions and Hinglish→Devanagari), FastAPI, React/Vite/TypeScript/Tailwind, ffmpeg
- **Nominated post link(s):** <LinkedIn URL> (and an X URL if you also post there)
