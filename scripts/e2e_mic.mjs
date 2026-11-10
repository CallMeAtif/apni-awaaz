// Live end-to-end test of the microphone flows in headless Chrome with a fake mic.
//
//   Flow "bank": Welcome → name + consent → record fixtures/synthetic_voice_deepak_9s.wav → save →
//                "Hear yourself" (plays the clone).
//   Flow "welcome": Welcome screen with name + consent, no recording (layout only, no credits).
//   Flow "listen": the talk flow up to the 3 suggestions only (1 STT call, no TTS).
//   Flow "talk": Talk → Listen to fixtures/synthetic_family_hinglish.wav → transcript + 3 suggestions →
//                tap one (plays in the cloned voice) → phrase tile "दर्द हो रहा है" → type
//                "thoda paani de do" → Speak.
//
// Usage (needs puppeteer-core; it is NOT a project dependency):
//   npm i --prefix /tmp/apni-pw puppeteer-core@23
//   PUPPETEER_DIR=/tmp/apni-pw node scripts/e2e_mic.mjs --flows=bank,talk --viewport=1280x800
//   PUPPETEER_DIR=/tmp/apni-pw PROFILE=<same dir> node scripts/e2e_mic.mjs --flows=talk --viewport=390x844
//
// Env: BASE (default http://localhost:5173), CHROME (path), PROFILE (Chrome user-data dir; reuse it to
// keep the banked voice in localStorage), OUT (screenshot dir, default docs/qa/screenshots).
// COSTS CREDITS against the real backend: bank ≈ 2 Gnani calls (embedding + "hear yourself" TTS),
// talk ≈ 1 STT + up to 3 TTS (cached clips cost nothing). Run against BASE with VITE_MOCK=1 to dry-run.

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(here, '..')
const require = createRequire(
  process.env.PUPPETEER_DIR ? path.join(process.env.PUPPETEER_DIR, 'noop.js') : import.meta.url,
)
const puppeteer = require('puppeteer-core')

const arg = (name, dflt) => {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`))
  return hit ? hit.slice(name.length + 3) : dflt
}
const BASE = process.env.BASE ?? 'http://localhost:5173'
const CHROME = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const FLOWS = arg('flows', 'bank,talk').split(',')
const [W, H] = arg('viewport', '1280x800').split('x').map(Number)
const TAG = `${W}`
const OUT = process.env.OUT ?? path.join(ROOT, 'docs/qa/screenshots')
const PROFILE = process.env.PROFILE ?? fs.mkdtempSync(path.join(os.tmpdir(), 'apni-e2e-profile-'))
fs.mkdirSync(OUT, { recursive: true })

// Chrome's fake-capture flag is happier with a path without spaces.
const audioDir = fs.mkdtempSync(path.join(os.tmpdir(), 'apni-e2e-audio-'))
const wav = (name) => {
  const dst = path.join(audioDir, name)
  fs.copyFileSync(path.join(ROOT, 'fixtures', name), dst)
  return dst
}

const timings = {}
const problems = []
const log = (...a) => console.log(`[${TAG}]`, ...a)

async function launch(fakeWav) {
  const mobile = W < 600
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    userDataDir: PROFILE,
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      `--use-file-for-fake-audio-capture=${fakeWav}%noloop`,
      // The out-of-process (sandboxed) audio service cannot read the fake-capture file on macOS
      // and silently records silence; run audio in the browser process instead.
      '--disable-features=AudioServiceOutOfProcess',
      '--autoplay-policy=no-user-gesture-required',
      '--no-first-run',
      '--no-default-browser-check',
    ],
    defaultViewport: { width: W, height: H, deviceScaleFactor: 2, isMobile: mobile, hasTouch: mobile },
  })
  const page = await browser.newPage()
  // Record when blob audio (TTS clips) actually starts playing.
  await page.evaluateOnNewDocument(() => {
    window.__ev = []
    const orig = HTMLMediaElement.prototype.play
    HTMLMediaElement.prototype.play = function (...args) {
      const src = this.currentSrc || this.src || ''
      if (src.startsWith('blob:')) {
        window.__ev.push({ type: 'play', t: performance.now() })
        this.addEventListener('playing', () => window.__ev.push({ type: 'playing', t: performance.now() }), {
          once: true,
        })
      }
      return orig.apply(this, args)
    }
  })
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push(`console.error: ${m.text()}`)
  })
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
  const started = new Map()
  page.on('request', (r) => started.set(r, Date.now()))
  page.on('requestfailed', (r) => {
    if (!r.url().includes('/api/')) return
    const ms = Date.now() - (started.get(r) ?? Date.now())
    problems.push(`requestfailed after ${ms} ms: ${r.method()} ${r.url()} ${r.failure()?.errorText}`)
  })
  page.on('response', async (r) => {
    const u = r.url()
    if (!u.includes('/api/')) return
    if (r.status() >= 400) problems.push(`HTTP ${r.status()} ${r.request().method()} ${u}`)
    if (u.endsWith('/api/listen') && r.ok()) {
      try {
        timings.listen_response = await r.json()
        timings.listen_server_timing = r.headers()['server-timing']
      } catch {}
    }
    if (u.endsWith('/api/speak')) (timings.speak_x_cache ??= []).push(r.headers()['x-cache'] ?? '?')
    if (u.endsWith('/api/warmup')) {
      let body = null
      try { body = await r.json() } catch {}
      ;(timings.warmup ??= []).push({ status: r.status(), client_ms: Date.now() - (started.get(r.request()) ?? Date.now()), body })
    }
  })
  return { browser, page }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const now = (page) => page.evaluate(() => performance.now())
const shot = async (page, name) => {
  const file = path.join(OUT, `${TAG}-${name}.png`)
  await page.screenshot({ path: file, fullPage: false })
  log('screenshot', path.relative(ROOT, file))
}

async function clickText(page, text, selector = 'button, [role="tab"], label') {
  const handle = await page.waitForFunction(
    (sel, txt) => [...document.querySelectorAll(sel)].find((el) => el.textContent.includes(txt) && !el.disabled),
    { timeout: 30_000 },
    selector,
    text,
  )
  const el = handle.asElement()
  await el.evaluate((e) => e.scrollIntoView({ block: 'center' }))
  await el.click()
}

const waitText = (page, text, timeout = 30_000) =>
  page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, text)

/** ms from `t0` (page clock) until a blob clip starts playing. */
async function waitPlaying(page, t0, timeout = 20_000) {
  const h = await page.waitForFunction(
    (since) => window.__ev.find((e) => e.type === 'playing' && e.t > since)?.t,
    { timeout, polling: 50 },
    t0,
  )
  return Math.round((await h.jsonValue()) - t0)
}

async function flowBank() {
  const { browser, page } = await launch(wav('synthetic_voice_deepak_9s.wav'))
  try {
    await page.goto(`${BASE}/?screen=welcome`, { waitUntil: 'networkidle0' })
    await page.waitForSelector('input[aria-label="Your name"]')
    await page.waitForFunction(() => !document.querySelector('[aria-label="Loading passage"]'), { timeout: 15_000 })
    await shot(page, '01-welcome')
    await page.type('input[aria-label="Your name"]', 'E2E Deepak (synthetic)')
    await clickText(page, 'यह मेरी अपनी आवाज़ है', 'label')
    await shot(page, '02-consent')

    await page.click('button[aria-label="Start recording"]')
    // The fake mic starts the file when getUserMedia opens; time the take from then.
    await page.waitForSelector('button[aria-label="Stop recording"]', { timeout: 10_000 })
    const tRec = Date.now()
    await sleep(2_000)
    await shot(page, '03-recording')
    await sleep(Math.max(0, 9_800 - (Date.now() - tRec)))
    await page.click('button[aria-label="Stop recording"]')
    timings.bank_recorded_s = +((Date.now() - tRec) / 1000).toFixed(1)
    await waitText(page, 'आवाज़ सुरक्षित करें')
    await shot(page, '04-recorded')

    let t0 = await now(page)
    await clickText(page, 'आवाज़ सुरक्षित करें')
    await waitText(page, 'Voice saved', 45_000)
    timings.bank_save_to_saved_ms = Math.round((await now(page)) - t0)
    await shot(page, '05-saved')

    t0 = await now(page)
    await clickText(page, 'अपनी आवाज़ सुनिए')
    timings.hear_yourself_tap_to_audio_ms = await waitPlaying(page, t0)
    await shot(page, '06-hear-yourself')
    await sleep(500)
    await clickText(page, 'बात शुरू करें')
    await page.waitForSelector('button[aria-label="Listen"]')
    await shot(page, '07-talk-empty')
  } finally {
    await browser.close()
  }
}

/** Welcome screen states without recording (no Gnani calls): for layout checks. */
async function flowWelcome() {
  const { browser, page } = await launch(wav('synthetic_voice_deepak_9s.wav'))
  try {
    await page.goto(`${BASE}/?screen=welcome`, { waitUntil: 'networkidle0' })
    await page.waitForSelector('input[aria-label="Your name"]')
    await shot(page, '01-welcome')
    await page.type('input[aria-label="Your name"]', 'Layout check')
    await clickText(page, 'यह मेरी अपनी आवाज़ है', 'label')
    await page.evaluate(() => document.querySelector('button[aria-label="Start recording"]')?.scrollIntoView({ block: 'center' }))
    await shot(page, '02-consent-recorder')
  } finally {
    await browser.close()
  }
}

async function flowTalk() {
  const { browser, page } = await launch(wav('synthetic_family_hinglish.wav'))
  try {
    await page.goto(`${BASE}/?screen=talk`, { waitUntil: 'networkidle0' })
    await page.waitForSelector('button[aria-label="Listen"]')
    await sleep(800) // let the mount-time /api/warmup finish, as it would before a real take
    await shot(page, '10-talk')

    let tClick = Date.now()
    await page.click('button[aria-label="Listen"]')
    await page.waitForSelector('button[aria-label="Stop listening"]', { timeout: 10_000 })
    timings.listen_tap_to_mic_live_ms = Date.now() - tClick
    const tRec = Date.now()
    await sleep(1_200)
    await shot(page, '11-listening')
    await sleep(Math.max(0, 5_000 - (Date.now() - tRec))) // the clip is 4.4 s
    let t0 = await now(page)
    await page.click('button[aria-label="Stop listening"]')
    await sleep(150)
    await shot(page, '12-understanding')
    await page.waitForFunction(() => document.querySelectorAll('[data-suggestion]').length >= 3, {
      timeout: 20_000,
      polling: 50,
    })
    timings.stop_to_suggestions_ms = Math.round((await now(page)) - t0)
    if (!timings.listen_response?.transcript) throw new Error('empty transcript: the fake mic delivered no speech')
    await sleep(600)
    await shot(page, '13-suggestions')
    if (!FLOWS.includes('talk')) return // "listen": stop here (1 STT call, no TTS)

    const first = await page.$('[data-suggestion]')
    timings.tapped_suggestion = await first.evaluate((b) => b.querySelector('[lang="hi"]')?.textContent?.trim())
    t0 = await now(page)
    await first.click()
    timings.suggestion_tap_to_audio_ms = await waitPlaying(page, t0)
    await sleep(300)
    await shot(page, '14-speaking-suggestion')
    await sleep(2_500)

    // Open whichever category holds the tile (real data: "दर्द और एहसास"; mock data differs).
    const PHRASE = process.env.PHRASE ?? 'दर्द हो रहा है'
    for (const tab of await page.$$('[role="tab"]')) {
      if (await page.evaluate((t) => document.querySelector('[role="tabpanel"]')?.textContent.includes(t), PHRASE)) break
      await tab.evaluate((e) => e.scrollIntoView({ block: 'center' }))
      await tab.click()
      await sleep(200)
    }
    t0 = await now(page)
    await clickText(page, PHRASE, '[role="tabpanel"] button')
    timings.phrase_tap_to_audio_ms = await waitPlaying(page, t0)
    await sleep(300)
    await shot(page, '15-phrase')
    await sleep(1_500)

    const input = await page.$('input[placeholder="जैसे: thoda paani de do"]')
    await input.evaluate((e) => e.scrollIntoView({ block: 'center' }))
    await input.type('thoda paani de do')
    t0 = await now(page)
    await input.press('Enter')
    timings.typed_enter_to_audio_ms = await waitPlaying(page, t0)
    await waitText(page, 'थोड़ा पानी दे दो')
    await sleep(300)
    await shot(page, '16-typed')
    await sleep(1_500)
    await page.screenshot({ path: path.join(OUT, `${TAG}-17-final-full.png`), fullPage: true })
  } finally {
    await browser.close()
  }
}

try {
  if (FLOWS.includes('welcome')) await flowWelcome()
  if (FLOWS.includes('bank')) await flowBank()
  if (FLOWS.includes('talk') || FLOWS.includes('listen')) await flowTalk()
} catch (e) {
  problems.push(`FAILED: ${e.message}`)
}
const lr = timings.listen_response
if (lr) {
  timings.listen_response = { transcript: lr.transcript, suggestions: lr.suggestions?.map((s) => s.text), timings: lr.timings }
}
const report = { base: BASE, viewport: `${W}x${H}`, profile: PROFILE, timings, problems }
fs.writeFileSync(path.join(OUT, `${TAG}-report.json`), JSON.stringify(report, null, 2))
console.log(JSON.stringify(report, null, 2))
process.exit(problems.some((p) => p.startsWith('FAILED') || p.startsWith('pageerror')) ? 1 : 0)
