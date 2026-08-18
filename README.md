# youtube_auto

Faceless YouTube automation. One engine, several channels, no paid services.

Each channel is a config file plus a format plugin. The pipeline is identical
for all of them; only the visual step differs.

```
topic -> script -> voice -> visuals -> captions -> assemble -> package -> review -> publish
```

## Quick start

```bash
.venv/Scripts/python.exe pipeline.py --channel finance                 # next unused topic
.venv/Scripts/python.exe pipeline.py --channel finance --topic rule-of-72
.venv/Scripts/python.exe pipeline.py --channel finance --script-only   # cheap dry run

.venv/Scripts/python.exe -m core.review list
.venv/Scripts/python.exe -m core.review approve rule-of-72 --channel finance
```

Output lands in `out/review/<channel>/<slug>/` as `final.mp4`, `thumbnail.png`
and `metadata.json`. Nothing is uploaded without an explicit approve.

## Layout

| Path | What it does |
|---|---|
| `core/config.py` | Paths, channel YAML loading, ffmpeg discovery |
| `core/llm.py` | Headless `claude -p` wrapper: retry, JSON extraction, schema validation |
| `core/script.py` | Prompt, visual contract, script validation |
| `core/compliance.py` | SEBI lag guard + investment-advice linter |
| `core/fincalc.py` | Verified financial maths (the model never computes figures) |
| `core/voice.py` | edge-tts narration + word-level timings |
| `core/captions.py` | ASS subtitles built from those timings |
| `core/assemble.py` | Per-beat render, concat, caption burn, music mix |
| `core/package.py` | Title, description, chapters, tags, thumbnail |
| `core/ideate.py` | Topic selection with history, so nothing repeats |
| `core/review.py` | The human gate |
| `core/imagegen.py` | Local Stable Diffusion, tuned for 4 GB VRAM |
| `core/affiliate.py` | Amazon links + the static hub site |
| `formats/finance.py` | Minimalist chart/typography renderer |
| `formats/story.py` | AI images + Ken Burns camera moves |
| `formats/product.py` | 9:16 Shorts renderer for affiliate roundups |
| `channels/*.yaml` | Per-channel voice, palette, cadence, topic bank |
| `tools_make_music.py` | Synthesises a royalty-free ambient bed |
| `tools_voice_demo.py` | Renders the same text in several voices to compare |

## Design decisions worth knowing

**The model never computes numbers.** A script declares
`{"fn": "compound_vs_invested", "monthly": 5000, ...}` and `core/fincalc.py`
produces the figures. Language models are confidently wrong at compound
interest, and a wrong number on a finance channel is unrecoverable. The maths
is unit-tested against standard SIP and EMI calculators.

**Compliance is code, not a prompt instruction.** SEBI bars unregistered
finfluencers from using recent market data even in educational content, and
from anything resembling a buy/sell call. `core/compliance.py` enforces a
100-day data lag and lints every script for advice language. A failing script
is retried, never rendered. The current topic bank is financial *literacy*
(compounding, SIP mechanics, budgeting, fees), which needs no market data at
all.

**Captions come from the TTS engine.** edge-tts emits `WordBoundary` events, so
timings are exact rather than inferred. No Whisper, no forced alignment, no GPU
on the caption path. Two traps: edge-tts defaults to `SentenceBoundary`, so the
word-level events must be requested explicitly; and those events carry the bare
word with no punctuation, so sentence breaks are recovered by counting words
against the source text (`captions.sentence_breaks`). Without that, cues
straddle full stops and read as broken subtitles.

**Narration is synthesised before visuals are rendered.** Each beat's visual is
built to the exact length of its own voiceover, which avoids both dead air and
clipped speech.

**NVENC is probed, not assumed.** Listing an encoder in `ffmpeg -encoders` does
not mean it opens: NVENC needs the NVIDIA driver to expose a new enough API
(ffmpeg 9.0 wants 13.1 / driver 610+). `core.config.pick_encoder` probes once
and falls back to libx264, so the pipeline is portable.

**Audio is mixed then loudness-normalised.** `amix` normalises by default,
dividing every input by the input count - worth about 6 dB of unexplained
quiet. The mix passes `normalize=0` and ends with `loudnorm=I=-14`, the
streaming target.

**The review gate is deliberate.** YouTube's inauthentic-content policy is
enforced at the channel level, so one batch of weak output can cost the channel
its monetisation. Set `auto_publish: true` per channel only once a format's
output is consistently good.

## The three channels

| Channel | Format | Shape | Earns from |
|---|---|---|---|
| `finance` - Simple Money | charts + typography, no GPU | 16:9, 3-4 min | ads + curated picks |
| `story` - Nightfall Archive | horror fiction, AI images + camera moves | 16:9, 6-8 min | ads + curated picks |
| `product` - Under Budget | AI stills + big text | 9:16, 40-55 s | Amazon affiliate |

The horror channel is **explicitly fiction**, and enforced as such: passing
invented horror off as a real account is deceptive and is the fabricated-content
pattern that costs channels their monetisation. `formats/story.py` rejects "based
on a true story" and its variants when `script.mode: fiction`, and the prompt
rules ban gore, sexual content, self-harm method and children as victims - dread
comes from restraint, and graphic content is demonetised anyway.

## Adding a channel

1. `channels/<name>.yaml` - voice, palette, cadence, video settings.
2. `channels/topics_<name>.yaml` - the topic bank.
3. `formats/<name>.py` exposing the plugin contract:

```python
build_prompt(topic, channel) -> str
validate_script(data, channel) -> None      # raise to reject and retry
render_beat(beat, duration, out_path, theme[, index]) -> Path
```

The rest of the pipeline is shared and needs no changes.

## Disk

Renders are large: an 8-minute 1080p story video is ~440 MB at YouTube's
recommended 8 Mbps. Work directories hold per-beat clips, frame dumps and
padded audio on top of that.

```bash
python -m core.review disk                    # what is being used
python -m core.review approve <slug> --channel story   # purges the work dir
python -m core.review purge --work --published --images
```

Approving purges the job's work directory; publishing additionally drops the
local mp4, since YouTube then holds the copy that matters. Metadata and status
always survive, because topic history reads them. Pass `--keep` (approve) or
`--keep-local` (publish) to opt out.

## Affiliate honesty

Amazon's Product Advertising API is only granted after Associates approval, so
before that there is no legitimate source of product titles, prices or images.
Rather than invent them, `product` scripts recommend **categories** and link to
an Amazon search; a claim linter rejects invented prices, ratings, review
counts, unsupported superlatives, and "I tested this" claims. Once PA-API access
exists, add an `asin` per item and the same code emits direct product links.

Set `affiliate.associate_tag` in the channel YAML after approval - every link is
rewritten from that one value. Apply only once the channel and hub have real
traffic: approval starts a 180-day, three-qualifying-sales clock.

The finance and horror channels carry a hand-written `affiliate.picks` list in
their config instead of per-video items, appended to every description. That is
deliberate: neither a finance explainer nor a horror story should have a model
inventing product claims inside it.

## Requirements

Python 3.12 venv at `.venv/`, ffmpeg on PATH (auto-discovered from the winget
install), and an authenticated `claude` CLI. Everything else is pip.

```bash
# core pipeline
.venv/Scripts/python.exe -m pip install edge-tts matplotlib numpy pyyaml pydantic mplfinance

# image generation (story + product channels only; ~2.5 GB)
.venv/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu121
.venv/Scripts/python.exe -m pip install diffusers transformers accelerate safetensors

# publishing
.venv/Scripts/python.exe -m pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

The `finance` channel needs none of the image stack - its visuals are drawn
with matplotlib. If torch is missing or a generation fails, `core.imagegen`
falls back to an abstract placeholder plate rather than aborting the run, so
one bad frame never costs a 36-beat render.

Run the tests with:

```bash
./run_tests.sh
```

| Test | Covers |
|---|---|
| `test_fincalc.py` | financial maths against standard SIP/EMI calculators |
| `test_compliance.py` | SEBI lag guard and advice linter, including false positives |
| `test_script_validation.py` | the finance script contract rejects malformed replies |
| `test_product_claims.py` | Shorts reject invented prices/ratings/superlatives; link building |
| `test_captions.py` | cues never straddle a sentence boundary |
