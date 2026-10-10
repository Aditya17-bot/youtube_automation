# Setup and operating guide

Two YouTube channels, one of them running. **Shorts only.**

| YouTube channel | Config | Shape | Cadence |
|---|---|---|---|
| **Nightfall Archive** | `clips_short` | 9:16, 40-55 s | **3/day, every day, spaced** |
| **Nightfall Archive** | `story_short` | 9:16, 40-55 s | retired 2026-09-17 |
| **Simple Money** | `finance_short` | 9:16, 35-52 s | paused |

`clips_short` and `story_short` are the same YouTube channel and only one may
run. `story_short` narrated over generated stills; `clips_short` cuts real
public-domain film instead, because the stills read as a slideshow and the
channel had settled under 100 views a video. Putting `story_short` back is
`per_week: 7` in its config **and** `per_week: 0` in the other - two configs
posting to one channel would double the day's uploads and work through the same
topic bank twice. See section 2c for the footage that feeds `clips_short`.

Simple Money is paused, not deleted: `cadence.per_week: 0` keeps it out of
every plan, and `auto_publish: false` / `privacy: private` mean even a manual
`--channels finance_short` run lands in the review queue instead of going
public. Its token, topic bank and history are untouched, so restarting it is
setting `per_week` back to 5 and `auto_publish` back to true.

The long-form configs (`finance`, `story`) were deleted. They were producing
0-2 views a video while the Shorts on the same two channels cleared 500+, so
the whole budget moved to Shorts. The YouTube channels themselves are
unchanged - the videos already uploaded are still there, and taking them down
or hiding them is a manual decision in YouTube Studio.

Two names survive from that arrangement and are worth reading correctly:

- `youtube_account: finance` is the OAuth identity, so uploads keep using
  `secrets/token_finance.json` with no re-authorisation.
- `script.topics_from: finance` names the topic BANK
  (`channels/topics_finance.yaml`). Banks were never tied to channel configs.

Neither Shorts config carries affiliate links any more. The `affiliate.picks`
block that used to append Amazon links to every description was removed from
both - it earned nothing and pushed the actual description down the page. Only
`product` builds links, and it is idle.

(`product` still exists as a third standalone Shorts channel, idle. Ignore it
unless you want it; nothing depends on it.)

---

## 1. Create the channels

One Google account can own several YouTube channels through Brand Accounts.

1. YouTube → your avatar → **Switch account → View all channels → Create a channel**.
2. Do this twice: **Simple Money** and **Nightfall Archive**.
3. In YouTube Studio for each: set the description below, upload a banner, and
   set the country to India.

### Simple Money — channel description

> Money, explained in plain language.
>
> Short videos on how compound interest, SIPs, EMIs, inflation and fees
> actually work — with the arithmetic shown on screen rather than asserted.
> No tips, no stock calls, no predictions, no "guaranteed returns".
>
> Educational content only. This is not investment advice and I am not a
> SEBI-registered analyst or adviser. Markets carry risk; speak to a registered
> adviser before investing.
>
> New explainer every week.

### Nightfall Archive — channel description

> Original horror fiction, read slowly.
>
> Quiet stories about empty places, long nights, and things that never quite
> resolve. Motel corridors at 3am. Roads that loop. Rooms that are occupied.
>
> Every story here is invented. No true accounts, no found footage, no
> "based on real events" — the fiction is the point.
>
> Best with headphones. Worst before bed. New story every week.

---

## 2. Wire up uploading (once per channel, ~10 minutes)

The pipeline uploads through the **YouTube Data API v3** using OAuth. You
authorise once in a browser; after that it runs unattended.

1. [console.cloud.google.com](https://console.cloud.google.com) → new project.
2. **APIs & Services → Library → YouTube Data API v3 → Enable.**
3. **OAuth consent screen** → External → fill in the basics → **PUBLISH APP**.

   **App name:** anything neutral, e.g. `Faceless Studio Uploader`. It is only a
   label on the consent screen you see when authorising, and it does not have to
   match a channel name.

   > Do **not** put "YouTube", "Google" or "Gmail" in the app name. Google's
   > branding rules reject app names containing their trademarks.

   Support email and developer contact are your own address. You do not need to
   add scopes here - the code requests them at auth time.

   > **Do not skip publishing.** While the consent screen sits in "Testing",
   > Google expires refresh tokens after **7 days** and every scheduled upload
   > breaks until you re-authorise by hand.

   On first authorisation you will see **"Google hasn't verified this app"**.
   That is expected: `youtube.upload` is a sensitive scope, and unverified apps
   are capped at 100 users. You are the only user, so choose
   **Advanced → Go to (app name)**. Verification is not needed for personal use.

   One project and one OAuth app cover both channels - you simply authorise
   twice, picking a different Brand Account each time.

4. **Credentials → Create credentials → OAuth client ID → Desktop app** →
   download the JSON.
5. Save it twice, once per channel:

   ```
   secrets/client_secret_finance.json
   secrets/client_secret_story.json
   ```

   Named for the OAuth identity, not the channel config: `finance_short` sets
   `youtube_account: finance`, so it looks for `client_secret_finance.json` and
   caches its token as `token_finance.json`.

6. Authorise each. A browser opens; pick the matching Brand Account:

   ```bash
   .venv/Scripts/python.exe -m core.publish auth --channel finance_short
   .venv/Scripts/python.exe -m core.publish auth --channel story_short
   ```

   It prints the channel it authorised as — check it matches before continuing.
   Tokens land in `secrets/` and are gitignored. Shorts reuse the parent's
   token via `youtube_account`, so there is nothing extra to authorise.

Quota: `videos.insert` has its own bucket of ~100 uploads/day, separate from the
10,000-unit pool. Nowhere near a constraint here.

---

## 2b. Give the horror channel a voice that acts (~5 minutes)

Nightfall Archive narrates with Azure Speech rather than edge-tts, because
Azure accepts per-sentence delivery direction and edge-tts does not. The script
writes `[whispering] The light was already on. [terrified] She had not been
home.`, and each tag becomes an `<mstts:express-as>` span.

**This is free at this volume.** The F0 tier gives 500,000 characters a month
and never expires; three Shorts a day spend roughly 72,000. Azure still asks
for a card on the account, and nothing here will reach the paid tier on its
own — but set a spending alert if that matters to you.

1. Create a **Speech service** resource in the Azure portal. Pick region
   `eastus` unless you have a reason not to, and pricing tier **F0 (free)**.
   Only one free Speech resource is allowed per subscription per region.

2. Open the resource, then **Keys and Endpoint**, and copy KEY 1 and the region.

3. Give them to the pipeline, either as environment variables:

   ```powershell
   setx AZURE_SPEECH_KEY  "<key 1>"
   setx AZURE_SPEECH_REGION "eastus"
   ```

   or as `secrets/azure_speech.json`, which is gitignored like everything else
   there:

   ```json
   {"key": "<key 1>", "region": "eastus"}
   ```

   `setx` only affects new shells, and the scheduled task reads the environment
   it was created with — restart the shell, and re-check the task, or use the
   file and avoid the question.

4. Confirm it works, and see what the account really offers:

   ```bash
   .venv/Scripts/python.exe -m core.voice --styles
   .venv/Scripts/python.exe -m core.voice --provider azure --voice en-US-GuyNeural        --style-degree 1.3 --out out/work/style_test.mp3        --text "[whispering] The light was already on. [terrified] She had not been home."
   ```

   Listen to it. `style_degree` is the dial worth turning: 1.3 is the shipped
   value, and much above 1.5 the voice tips from frightened into comic.

5. Re-measure the delivery rate, because the voice changed and every runtime
   estimate depends on it:

   ```bash
   .venv/Scripts/python.exe -m core.voice --measure
   ```

   Put the number it prints for `story_short` into
   `channels/story_short.yaml` as `voice.words_per_minute`.

**If you skip this section entirely, nothing breaks.** With no key, the run
prints `azure unavailable`, strips the tags, and narrates with edge-tts. The
horror just stops acting.

---

## 2c. Footage for the clip channel (~20 minutes per film, once each)

`clips_short` does not generate its pictures, it cuts them out of real film, so
it needs footage on disk before it can render anything. Everything it may touch
is listed in `channels/footage_horror.yaml`, which is a **licence whitelist**:
`core/footage.py` refuses to download a source whose licence is not one of
`public-domain`, `cc0`, `cc-by` or `cc-by-sa`, and then refuses again if
archive.org's own metadata disagrees with what the bank claims. Non-commercial
and no-derivatives licences are rejected on purpose - a monetised Short is a
commercial derivative of whatever it was cut from.

```bash
.venv/Scripts/python.exe -m core.footage list       # the bank and its licences
.venv/Scripts/python.exe -m core.footage fetch --source carnival_of_souls
.venv/Scripts/python.exe -m core.footage index --source carnival_of_souls
```

`fetch` pulls the h.264 derivative (300-800 MB, not the 2 GB master). `index`
decodes it once and writes every usable shot to `state/footage_horror.json`.
That file is the one that matters: it records which video spent which shot, so
no two Shorts ever carry the same footage. The film itself can be deleted and
re-fetched; the index cannot.

Then look at what it found, because the scorer has no taste:

```bash
.venv/Scripts/python.exe -m core.footage contact --source carnival_of_souls
```

That writes a numbered contact sheet to `out/work/`. Open it, note the shots
that are junk - a title card the detector missed, a boom mic, a face mid-blink -
and blacklist them by number:

```bash
.venv/Scripts/python.exe -m core.footage drop --source carnival_of_souls --shots 4,17,18
```

Two more commands worth knowing:

```bash
.venv/Scripts/python.exe -m core.footage runway                 # days of footage left
.venv/Scripts/python.exe -m core.footage release --slug <topic-id>
```

`release` hands a video's shots back when a render failed or the video was
binned. Footage is claimed the moment a script validates, so a run that dies in
the renderer leaves its shots spent until you say otherwise.

A feature gives 250-400 usable shots, about a fortnight at 3 videos a day.
`runway` says so in days; add the next source before it reaches zero, the same
way `topicgen` tops up a topic bank.

---

## 3. The daily loop

```bash
# 1. make something (topic -> script -> voice -> visuals -> video -> review)
.venv/Scripts/python.exe pipeline.py --channel finance_short
.venv/Scripts/python.exe pipeline.py --channel finance_short --topic compound-interest
.venv/Scripts/python.exe pipeline.py --channel story_short

# 2. watch what came out
.venv/Scripts/python.exe -m core.review list

# 3. decide (approve purges the work directory)
.venv/Scripts/python.exe -m core.review approve compound-interest --channel finance_short
.venv/Scripts/python.exe -m core.review reject  night-shift-motel --channel story_short --note "pacing drags"

# 4. upload everything approved
.venv/Scripts/python.exe -m core.publish run --channel finance_short
```

Pass `--topic <id>` to pick a subject out of the bank instead of taking the
next unused one.

Uploads go out **public**, but not all at once. `clips_short` declares
`publish.slots`, so a run uploads the day's three videos and hands YouTube a
release time for each - 05:30, 08:30 and 23:30 local, at least three hours
apart. YouTube does the waiting, so the machine can be asleep when they go out.

```bash
.venv/Scripts/python.exe -m core.publish run --channel clips_short          # spaced
.venv/Scripts/python.exe -m core.publish run --channel clips_short --now    # all at once
```

A scheduled video sits `private` in YouTube Studio until its slot, with the
release time shown against it. The slot each video was given is recorded in its
`status.json`, so a second run the same day does not hand out the same one
twice.

`--privacy private` on either `core.publish run` or `daily.py` holds a batch
back for one run.

---

## 4. Scheduling it

Already wired up. `daily.py` renders whatever today's cadence calls for and
uploads it **public**; `daily.cmd` is the Task Scheduler wrapper.

```bash
python daily.py --dry-run     # what today would do
python -m core.schedule       # the cadence table and the next two weeks
python daily.py --force       # every channel now, ignoring cadence
```

What runs when comes from two keys in each channel config. `cadence.per_week`
is which days run, spread evenly across the week rather than bunched;
`cadence.per_day` is how many videos on one of those days:

| channel | per week | per day | days |
|---|---|---|---|
| story_short | 7 | 3 | every day |
| finance_short | 0 | - | never (paused) |
| product | - | 1 | never (`per_day` alone does not schedule anything) |

Change a number, and the schedule changes. Nothing else needs editing.

`per_week` is the on/off switch: 0 means the channel never runs whatever
`per_day` says. `per_day` defaults to 1 and is clamped to 10, which is roughly
where an unverified YouTube account starts refusing uploads.

**Before raising `per_day` further, watch a run finish.** Three renders plus
three uploads have to fit inside the Task Scheduler window; a run killed
part-way leaves the day short, and the log in `state/logs/` is the only place
that shows how long it actually took.

### Where the release slots come from

They are a guess, and the config says so. Nightfall Archive has no analytics
token, so nothing in this project knows where its viewers actually are; the
three slots cover US evening, US late night and UK evening rather than three
slots in one market. Mint a read-only analytics token and replace them with the
real country split:

```bash
.venv/Scripts/python.exe C:/adi/clip_farm/tools/audience.py --auth
```

That asks for `yt-analytics.readonly` only, as a separate token. Do not widen
`core.publish.SCOPES` to get it - that invalidates `token_story.json` and breaks
the daily upload.

### Topics refill themselves

Three a day empties a sixty-topic bank in twenty days. At the start of every
real run (not `--dry-run`), any bank holding under two weeks of runway is
extended by `core/topicgen.py`, which reads the `brief:` at the top of the
`topics_*.yaml` for what belongs in it and rejects anything duplicating what is
already there.

```bash
python -m core.topicgen --channel story_short     # top up if low
python -m core.topicgen --bank story --count 40   # extend regardless
python -m core.ideate stats                       # how much bank is left
python daily.py --no-topic-refill                 # skip it for one run
```

Edit the `brief:` if the topics start drifting - it is the entire instruction
the generator gets.

### There is no review gate any more

`auto_publish: true` plus `privacy: public` on the live channel means a
finished video lands `approved` instead of `pending` and goes straight out,
public, without anyone watching it. `out/review` is now a record of what
shipped rather than a gate in front of it.

That is a real trade, made deliberately for a daily Shorts cadence. What holds
it up is that the checks all run *before* the render, during script generation:
the SEBI compliance linter, the duration ceiling, the fiction check and the
visual contract each reject a script and feed the reason back to the model as a
retry. Nothing that fails them is ever voiced, let alone uploaded.

Put the gate back for a run with `daily.py --privacy private`, or permanently
by setting `privacy: private` in a channel config.

### The scheduled task

```powershell
schtasks /query /tn "youtube_auto daily" /fo LIST     # check it
schtasks /change /tn "youtube_auto daily" /st 07:30   # move the time
schtasks /run   /tn "youtube_auto daily"              # run it now
schtasks /delete /tn "youtube_auto daily" /f          # stop it entirely
```

Registered "Interactive only" on purpose: image generation needs the logged-in
GPU session and renders black frames without it. So the machine has to be on
and logged in at the scheduled time. `StartWhenAvailable` is set, so a run
missed because the machine was off happens at the next opportunity instead of
being skipped.

In practice that next opportunity is the moment you log in - which is also when
Claude Code installs its own update, deleting and rewriting the `claude` binary
for the length of a global npm install. A run that starts inside that window
finds no binary at all. `daily.py` therefore waits up to `llm.BIN_WAIT` seconds
for it before touching the topic bank, and `ask_json` retries a call the swap
broke mid-run instead of counting it as a bad answer. If the binary never comes
back the run stops before spending anything and emails you why.

Each run appends to `state/logs/daily-YYYY-MM-DD.log`. Task Scheduler discards
stdout, so that file is the only record of a run that failed overnight. One
channel failing does not stop the others.

### Email when something breaks

Every failure this pipeline has is silent. A render dies and the channel just
has no video that day; a refresh token is revoked and uploads stop; a topic bank
runs dry and the channel goes quiet. None of it surfaces until you notice, which
takes about a week.

`secrets/smtp.json` already exists with your address filled in. It needs one
value — a Google **app password**, not your account password, which Gmail's SMTP
refuses outright:

1. 2-Step Verification must be on: [myaccount.google.com/security](https://myaccount.google.com/security).
2. [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   → name it anything → copy the 16 characters.
3. Paste it over `PASTE_APP_PASSWORD_HERE`. Spaces are fine, they get stripped.

```bash
.venv/Scripts/python.exe -m core.notify --test    # confirm it arrives
.venv/Scripts/python.exe -m core.notify           # config status + bank levels
```

> That file holds a working credential for your Google account in plain text.
> `secrets/` is gitignored, so it will not be committed, but anything with read
> access to the machine can use it. Revoke it from the same app-passwords page
> if that ever matters — it grants mail access only, not account access.

Mail goes out only when something is wrong, so silence means the run was clean.
It reports render failures, upload failures, a channel that was never
authorised, a crash before the run could report anything itself, and a topic
bank with under two weeks left — that last one arrives *before* the channel
stops, which is the only useful time to hear about it.

With no config file the whole thing is a no-op that prints what it would have
sent. Nothing in it can fail a run.

**Do not schedule volume before quality.** YouTube's inauthentic-content policy
is enforced at the channel level and carries a three-strike path to permanent
removal from the Partner Program. Two good videos a week beats seven weak ones.
Now that uploads are public on sight, spot-check what went out - `python -m
core.review list` shows every published slug and its video URL - rather than
finding out from a strike.

---

## 5. Disk

Handled automatically. Every `daily.py` run ends with a sweep, so nothing needs
doing by hand.

```bash
.venv/Scripts/python.exe -m core.review disk        # what is being used
.venv/Scripts/python.exe -m core.retention --dry-run # what the sweep would drop
.venv/Scripts/python.exe -m core.retention           # drop it now
```

Three things already delete themselves at the right moment: the work directory
when a job is approved, and `final.mp4` when the upload succeeds — YouTube then
holds the copy that matters. The sweep is for everything those two miss.

| what | when it goes |
|---|---|
| `final.mp4` of a published video | at upload; the sweep retries if that failed |
| `thumbnail.png` of a published video | after 3 days (YouTube has its own copy) |
| `assets/generated` images | after 3 days |
| work directory of a crashed render | after 3 days of no writes |
| `state/logs/daily-*.log` | after 30 days |
| **anything `pending` or `approved`** | **never** |
| **metadata, status and script json** | **never** |

Change the window with `--retain-days 7` on either command.

The images are the reason this exists. `assets/generated` is keyed on a hash of
the prompt text, and prompts come from per-beat script lines, so two videos
essentially never share one — it looks like a cache but behaves like a scratch
directory. It measured ~25 MB per video, which is ~19 GB a year at the current
cadence, all of it dead the moment the render finishes. With the sweep in place
the whole project sits at roughly 250 MB and stays there.

Nothing `pending` is ever touched, at any age. An undecided video is work a
human has not looked at yet, and no disk figure justifies deleting it — if a
parked channel is holding space, reject it explicitly:

```bash
.venv/Scripts/python.exe -m core.review reject desk-tidy --channel product --note "parked"
```

Videos are **not** kept as local backups. Re-uploading is not what the local
copy is for and it was never a reliable archive; `script.json` survives forever,
which is what a re-render actually needs.

---

## 6. Affiliate, in order

Do not apply to Amazon Associates yet. Approval starts a **180-day,
three-qualifying-sales** clock, and a channel with no traffic will burn it.

1. **Now** — nothing. The Shorts descriptions carry no links at all; the
   `affiliate.picks` blocks were removed from both configs. Re-adding a
   `picks:` list to a channel YAML is what turns the block back on.
2. **Build the hub** — push the `hub/` directory to a GitHub Pages repo, then
   set `affiliate.hub_url` in whichever config is meant to carry links. The
   hub is also the "live platform with original content" the application wants.
3. **At roughly 500-1,000 subscribers with real traffic** — apply, listing both
   the channel and the hub.
4. **On approval** — set `affiliate.associate_tag` in any config carrying
   links. Every link is rewritten from that one value.
5. **Also apply to the Amazon Influencer Program** — separate from Associates,
   gives you a storefront, and is a better fit for video.
6. **India-friendly fallbacks while waiting:** Cuelinks, EarnKaro, vCommission
   approve new creators far more readily than Amazon.

Once you have PA-API access, add an `asin` to any pick and the same code emits a
direct product link instead of a category search.

---

## 7. Honest expectations

- **Ad revenue needs 1,000 subscribers and 4,000 watch hours.** Months away, and
  no amount of automation shortens it.
- **Affiliate income before ~1,000 engaged subscribers is near zero.** The
  pipeline is built so that it costs nothing to have it ready early.
- **The review gate is the whole safety model.** Everything else in this repo is
  recoverable; a channel-level policy strike is not.
