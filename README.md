# youtube_auto

Faceless YouTube automation. One engine, several channels, no paid services.
**Vertical Shorts only** - the long-form configs were deleted after they
settled at 0-2 views a video while the Shorts on the same channels cleared 500+.

Each channel is a config file plus a format plugin. The pipeline is identical
for all of them; only the visual step differs.

```
topic -> script -> voice -> visuals -> captions -> assemble -> package -> review -> publish
```

Setting up channels, OAuth, scheduling and the affiliate sequence:
see **[SETUP.md](SETUP.md)**.

## Quick start

```bash
.venv/Scripts/python.exe pipeline.py --channel clips_short                   # next unused topic
.venv/Scripts/python.exe pipeline.py --channel clips_short --topic sleep-study
.venv/Scripts/python.exe pipeline.py --channel clips_short --script-only     # cheap dry run

.venv/Scripts/python.exe -m core.review list
.venv/Scripts/python.exe -m core.review approve sleep-study --channel clips_short
```

`clips_short` cuts real film, so it needs footage on disk before it can render.
Once per source, ever:

```bash
.venv/Scripts/python.exe -m core.footage fetch --source carnival_of_souls
.venv/Scripts/python.exe -m core.footage index --source carnival_of_souls
```

Output lands in `out/review/<channel>/<slug>/` as `final.mp4`, `thumbnail.png`
and `metadata.json`. `pipeline.py` itself never uploads; `daily.py` does.

## Layout

| Path | What it does |
|---|---|
| `core/config.py` | Paths, channel YAML loading, ffmpeg discovery |
| `core/llm.py` | Headless `claude -p` wrapper: retry, JSON extraction, schema validation |
| `core/script.py` | Prompt, visual contract, script validation |
| `core/compliance.py` | SEBI lag guard + investment-advice linter |
| `core/fincalc.py` | Verified financial maths (the model never computes figures) |
| `core/voice.py` | Narration (edge-tts or Azure) + word-level timings |
| `core/emotion.py` | Delivery tags in narration text, and stripping them back out |
| `core/captions.py` | ASS subtitles built from those timings |
| `core/assemble.py` | Per-beat render, concat, caption burn, music mix |
| `core/package.py` | Title, description, chapters, tags, thumbnail |
| `core/ideate.py` | Topic selection with history, so nothing repeats |
| `core/review.py` | Queue and status ledger; the manual gate for `auto_publish: false` |
| `core/schedule.py` | Turns `cadence.per_week`/`per_day` into what runs today |
| `core/retention.py` | Deletes what nothing reads; never a pending video |
| `core/notify.py` | Emails when an unattended run needs a human |
| `core/imagegen.py` | Local Stable Diffusion, tuned for 4 GB VRAM |
| `core/footage.py` | Licensed source film: the licence gate, downloads, the shot index |
| `core/affiliate.py` | Amazon links + the static hub site (unused by the live channel) |
| `core/topicgen.py` | Extends a topic bank before it runs dry |
| `formats/finance.py` | Minimalist chart/typography renderer |
| `formats/story.py` | AI images + Ken Burns camera moves |
| `formats/clips.py` | Real film shots, reframed, graded and cut to the narration |
| `formats/product.py` | 9:16 Shorts renderer for affiliate roundups |
| `channels/*.yaml` | Per-channel voice, palette, cadence, topic bank |
| `daily.py` | Unattended run: today's cadence, rendered and uploaded public |
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

**Captions come from the TTS engine.** Both providers emit word-boundary
events, so timings are exact rather than inferred. No Whisper, no forced
alignment, no GPU on the caption path. Three traps: edge-tts defaults to
`SentenceBoundary`, so the word-level events must be requested explicitly;
Azure reports no word boundaries for SSML unless
`SpeechServiceResponse_RequestWordBoundary` is set; and the events carry the
bare word with no punctuation, so sentence breaks are recovered by counting
words against the source text (`captions.sentence_breaks`). Without that, cues
straddle full stops and read as broken subtitles.

**Delivery is directed per sentence.** A horror Short lives on how a line is
read, and edge-tts reads every line at the same temperature. So a beat's `vo`
carries inline tags - `[whispering] The light was already on. [terrified] She
had not been home.` - which `core.voice` turns into Azure
`<mstts:express-as>` spans. The tags are direction, never speech: everything
that counts, measures, aligns or displays narration goes through
`emotion.strip()` first. A tag left in is not a visible bug but a silent one -
it counts as a word, the count disagrees with the engine's, and
`captions.sentence_breaks` responds by emitting no breaks for that whole beat.

Azure costs nothing at this volume. The F0 tier covers 500k characters a
month; three Shorts a day spend about 72k. With no key configured the pipeline
falls back to edge-tts and reads the script flat rather than failing, which is
why a channel's `voice.fallback_name` must name a voice the free endpoint
actually serves - its catalogue is far smaller than a keyed account's, and an
Azure-only name there returns no audio at all rather than a flat reading.

**The pictures had to start moving.** `story_short` is a generated still with a
slow zoom on it, and at feed size that is a slideshow. The writing, the voice
and the thumbnails were all working; views still settled under a hundred,
because the Shorts feed wants a visual change every 1.5-2 seconds and a still
cannot give it one. `formats/clips.py` is the answer: each beat is a shot of
real film, reframed to 9:16, graded to the channel's palette, with a push-in
that settles and then re-frames every two seconds.

Nothing else in the pipeline changed. The plugin contract asks a format for a
silent mp4 of exactly one beat's length, and a cut of film satisfies it as well
as a Ken Burns still does, so narration, captions, music, thumbnail, packaging,
review, upload and cadence are all the same code the other channels run.

**Footage is gated on its licence, at the door.** `channels/footage_horror.yaml`
is a whitelist, and `core/footage.py` refuses to download anything whose licence
is not one of `ALLOWED_LICENSES` - which excludes every NC and ND variant,
because a monetised Short is a commercial derivative of whatever it was cut
from. The claim in the bank is then checked against the HOST's own metadata
before a byte is written, and both have to agree.

That second check earns its keep. Several films everyone calls public domain are
served by archive.org under CC BY-NC; `TheLastManOnEarth` is one, and a bank
written from memory would have sailed straight past a whitelist that only read
itself. The policy this protects is enforced at the channel level, and
`clips_short` uploads to the same channel as everything else here.

**What makes a clip ours is checked, not hoped for.** Reused content without
meaningful addition is what loses a channel its monetisation, so the additions
are enforced in `validate_script`: the story is original fiction, first person,
and it may not describe what is on screen. `_SCREEN_TALK` rejects "you can see",
"the camera", "this footage" outright. That rule is editorial as much as
defensive - narration that describes the picture is a commentary track, and the
first shot that disagrees with it kills the whole video. Writing the part the
picture does *not* show is both more original and more robust.

**Caption defaults follow the frame.** The first channels here were 16:9 and the
caption style was set for that: 46px, a 90px bottom margin, not bold. On a
vertical frame all three are wrong - 46px is unreadable at 1080 wide, and the
Shorts UI covers roughly the bottom 250px with the title, the handle and the
action rail, so a 90px margin puts the line behind it. `core.assemble` now picks
64px, a 300px margin and bold whenever the frame is taller than it is wide, and
`captions.size` / `margin_v` / `bold` override that per channel.

**A day's videos are rendered together and released apart.** Three uploads
landing in the same minute is a burst, not a schedule - it is the shape the
inauthentic-content policy looks for, and the feed will not show one channel
three times running to the same viewer anyway. `publish.slots` in a channel
config turns one render run into three scheduled releases:

```yaml
publish:
  slots: ["05:30", "08:30", "23:30"]   # local time
  min_gap_hours: 3
```

YouTube does the waiting, not this machine: `videos.insert` accepts
`status.publishAt`, so a video uploaded at noon can go out at midnight with the
PC asleep. That also keeps it inside the existing `youtube.upload` scope -
uploading and then calling `videos.update` would need a write scope the shared
token does not carry, and widening `SCOPES` invalidates it.

A scheduled video has to be inserted as `private`; YouTube flips it public
itself at the slot. So `privacy: public` still describes what the video
becomes, it just gets there on a timer. `min_gap_hours` is enforced on top of
the configured times rather than trusted to them, and the slot a video was
given is written into its `status.json`, so a second publish run the same day
does not hand the same slot out twice. A channel with no `publish.slots`
publishes immediately, as before; `core.publish run --now` forces that for one
run.

**The hook is a field, not a hope.** Beat 0 is one sentence of twelve words or
fewer, it may not be a question, and `core/captions.py` holds it on screen as a
card for the first two seconds with the running captions suppressed underneath.
Most of this audience scrolls muted, so the opening line has to be read rather
than heard, and a Short is decided before the second sentence starts.

**One shot is never used twice.** `core.footage index` walks a source once with
ffmpeg, taking scene cuts and the luma of each new shot's first frame out of the
same `showinfo` pass, and drops what cannot carry narration: anything under
2.5s, fades, flat title cards, and the head and tail of the film where the
credits live. Each shot records which video spent it. A beat draws from a run of
*consecutive* shots, so the cuts read as coverage of one scene instead of a
slideshow of unrelated stills.

```bash
python -m core.footage list                            # the bank and its licences
python -m core.footage fetch   --source carnival_of_souls
python -m core.footage index   --source carnival_of_souls
python -m core.footage contact --source carnival_of_souls   # eyeball it
python -m core.footage drop    --source carnival_of_souls --shots 4,17,18
python -m core.footage runway                          # days of footage left
python -m core.footage release --slug <topic-id>       # a video was binned
```

A feature runs 250-400 usable shots. At 8 shots a video and 3 videos a day that
is about a fortnight per film, which is what `runway` reports in days.

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

**The guards moved in front of the renderer.** YouTube's inauthentic-content
policy is enforced at the channel level, so one batch of weak output can cost
the channel its monetisation.

The live channel runs `auto_publish: true` and `privacy: public`, so its
Shorts go out without anyone watching them first. That is a deliberate trade for
cadence, and it only holds because nothing weak reaches the renderer: the
compliance linter, the duration ceiling and the fiction check all reject a
script during generation, and a rejection is fed back to the model as a retry.
`out/review` is now a record of what shipped rather than a gate.

Hold a batch back with `daily.py --privacy private`, which overrides the config
for that run.

## The channels

| Channel | Format | Shape | Cadence | Earns from |
|---|---|---|---|---|
| `clips_short` - Nightfall Archive | horror fiction over licensed film | 9:16, 40-55 s | 3/day, spaced | ads, once monetised |
| `story_short` - Nightfall Archive | horror fiction, AI images + camera moves | 9:16, 40-55 s | retired | - |
| `finance_short` - Simple Money | charts + typography, no GPU | 9:16, 35-52 s | paused | - |
| `product` - Under Budget | standalone Shorts | 9:16, 35-52 s | idle | affiliate |

**`clips_short` and `story_short` are the same YouTube channel.** Both declare
`youtube_account: story`, which is Nightfall Archive, and they share a topic
history through `script.history_from` so the newer one does not work back
through premises the older one already published. `clips_short` replaced
`story_short` on 2026-09-17: same channel, same voice, same thumbnails,
different pictures. Exactly one of them may have a non-zero
`cadence.per_week` - both at once would double the day's uploads and work
through the same topic bank twice, and `test_schedule.py` pins that.

`finance_short` is paused at `cadence.per_week: 0` and the whole nightly budget
goes to horror. Restarting it is two lines of YAML - the config, topic bank,
OAuth token and history are all still there, untouched.

`finance` and `story` were the long-form halves of the first two channels and
are gone. Their YouTube channels are unchanged - only the format they publish
in - so the configs keep the old identities:

- `youtube_account: finance` points at `secrets/token_finance.json`, so no
  re-authorisation was needed.
- `script.topics_from: finance` names the topic BANK
  (`channels/topics_finance.yaml`), which was never tied to a channel config.

### Cadence has two numbers

`cadence.per_week` is which days a channel runs; `cadence.per_day` is how many
videos it makes on one of those days. They answer different questions and
neither implies the other: `per_week` stays the sole on/off switch, so
`product`, which declares only `per_day` from an earlier design, still reads as
zero and never enters a plan. `per_day` is clamped to 10 - a typo should not
spend a night rendering fifty videos no account will accept.

Each video in a day gets its own topic. Topic history is only written once a
render finishes, so the runner tracks what earlier videos in the same run
already claimed; without that, three videos a day would be the same video three
times.

### Topics do not run out

At 3/day a sixty-topic bank lasts twenty days, and an empty bank is a silent
failure - nothing crashes, the channel just stops posting. So `core/topicgen.py`
tops a bank up at the start of a daily run once it holds under two weeks of
runway, using the `brief:` written at the top of each `topics_*.yaml` as its
instructions. New topics are rejected here rather than trusted: duplicate ids,
and titles that share 70% of their content words with an existing one, are
dropped. Entries are appended as text, so the comments in a bank file survive.

```bash
python -m core.topicgen --channel story_short          # top up if low
python -m core.topicgen --bank story --count 40        # extend regardless
```

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

Renders are smaller than they were - a 50-second vertical Short is tens of
MB, not the ~440 MB an 8-minute 1080p story cost - but ten a week plus the
per-beat clips, frame dumps and padded audio in each work directory still add
up, and the generated-image cache grows fastest of all.

Source films are the biggest thing on disk by far - a feature is 300-800 MB and
`assets/footage/` is not purged by anything, deliberately: re-downloading a
source would also mean re-indexing it, and the index is what guarantees no shot
goes out twice. Delete a film by hand once `runway` shows it spent.

```bash
python -m core.review disk                    # what is being used
python -m core.review approve <slug> --channel story_short   # purges the work dir
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

**The Shorts channels carry no affiliate links.** They used to append a
hand-written `affiliate.picks` list to every description; that was removed.
Amazon links under a horror Short earn nothing and read as spam under a story,
and a description that opens with a product list buries the thing the viewer
came for. `affiliate.picks` is simply absent from both configs now, and
`affiliate.static_block()` returns an empty string when it is - the code path
still exists for `product`, which is where per-video links belong.

## Requirements

Python 3.12 venv at `.venv/`, ffmpeg on PATH (auto-discovered from the winget
install), and an authenticated `claude` CLI. Everything else is pip.

```bash
# core pipeline
.venv/Scripts/python.exe -m pip install edge-tts azure-cognitiveservices-speech matplotlib numpy pyyaml pydantic mplfinance

# image generation (story_short + product channels only; ~2.5 GB)
.venv/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu121
.venv/Scripts/python.exe -m pip install diffusers transformers accelerate safetensors

# publishing
.venv/Scripts/python.exe -m pip install google-api-python-client google-auth-oauthlib google-auth-httplib2

# only for Creative Commons YouTube sources in a footage bank; archive.org
# sources need nothing beyond the standard library
.venv/Scripts/python.exe -m pip install yt-dlp
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
| `test_captions.py` | cues never straddle a sentence boundary, and wait for the hook card |
| `test_footage.py` | the licence gate, shot filtering, and one shot never used twice |
| `test_clips.py` | the hook fits its card, and narration never describes the picture |
