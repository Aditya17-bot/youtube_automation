# Setup and operating guide

Two YouTube channels, four pipeline configs, one engine.

| YouTube channel | Long-form config | Shorts config | Affiliate |
|---|---|---|---|
| **Simple Money** | `finance` (16:9, 3-4 min) | `finance_short` (9:16, 40-55 s) | Shorts only |
| **Nightfall Archive** | `story` (16:9, 4-5 min) | `story_short` (9:16, 45-60 s) | Shorts only |

A Short and its long-form parent share one YouTube account and one topic bank,
so the Short on a subject funnels into the full video on the same subject.
Affiliate links sit on the Shorts alone: an explainer that also sells reads as
an advert, and a horror story interrupted by a shopping list loses the only
thing the format has.

(`product` still exists as a standalone Shorts channel. Ignore it unless you
want a third channel; nothing depends on it.)

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

6. Authorise each. A browser opens; pick the matching Brand Account:

   ```bash
   .venv/Scripts/python.exe -m core.publish auth --channel finance
   .venv/Scripts/python.exe -m core.publish auth --channel story
   ```

   It prints the channel it authorised as — check it matches before continuing.
   Tokens land in `secrets/` and are gitignored. Shorts reuse the parent's
   token via `youtube_account`, so there is nothing extra to authorise.

Quota: `videos.insert` has its own bucket of ~100 uploads/day, separate from the
10,000-unit pool. Nowhere near a constraint here.

---

## 3. The daily loop

```bash
# 1. make something (topic -> script -> voice -> visuals -> video -> review)
.venv/Scripts/python.exe pipeline.py --channel finance
.venv/Scripts/python.exe pipeline.py --channel finance_short --topic compound-interest
.venv/Scripts/python.exe pipeline.py --channel story
.venv/Scripts/python.exe pipeline.py --channel story_short

# 2. watch what came out
.venv/Scripts/python.exe -m core.review list

# 3. decide (approve purges the work directory)
.venv/Scripts/python.exe -m core.review approve compound-interest --channel finance
.venv/Scripts/python.exe -m core.review reject  night-shift-motel --channel story --note "pacing drags"

# 4. upload everything approved
.venv/Scripts/python.exe -m core.publish run --channel finance
```

Pass `--topic <id>` to a Short to make it cover the same subject as a long
video you already made — that is the funnel.

Uploads go out **private** by default (`privacy: private` in each channel
config). Check the video in YouTube Studio, then flip it public there, or run
`--privacy public` once you trust the output.

---

## 4. Scheduling it

Already wired up. `daily.py` renders whatever today's cadence calls for and
uploads it **private**; `daily.cmd` is the Task Scheduler wrapper.

```bash
python daily.py --dry-run     # what today would do
python -m core.schedule       # the cadence table and the next two weeks
python daily.py --force       # every channel now, ignoring cadence
```

What runs when comes from `cadence.per_week` in each channel config, spread
evenly across the week rather than bunched:

| channel | per week | days |
|---|---|---|
| finance | 3 | Mon, Wed, Sat |
| finance_short | 5 | Mon, Tue, Thu, Fri, Sun |
| story | 2 | Mon, Fri |
| story_short | 5 | Mon, Tue, Thu, Fri, Sun |
| product | - | never (uses `per_day` and has no OAuth client) |

Change a number, and the schedule changes. Nothing else needs editing.

When a Short runs the same day as its long-form parent, it reuses the parent's
topic, so the Short is a teaser for that video rather than an advert for a
different one.

### The review gate moved, it did not go away

`auto_publish: true` on the four live channels means a finished video lands
`approved` instead of `pending`, so the run uploads it without waiting. It goes
up **private**. You still decide what the world sees - you now decide it in
YouTube Studio instead of `out/review`.

`daily.py` forces `--privacy private` whatever the channel config says.
Overriding that is the one flag that lets this pipeline publish something no
human has watched.

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
removal from the Partner Program. Two good videos a week beats seven weak ones,
and private-by-default exists precisely so this decision stays yours.

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

1. **Now** — publish Shorts with the untagged links already in the descriptions.
   They work; they just earn nothing.
2. **Build the hub** — push the `hub/` directory to a GitHub Pages repo, then
   set `affiliate.hub_url` in `finance_short.yaml` and `story_short.yaml`. The
   hub is also the "live platform with original content" the application wants.
3. **At roughly 500-1,000 subscribers with real traffic** — apply, listing both
   the channel and the hub.
4. **On approval** — set `affiliate.associate_tag` in the two Shorts configs.
   Every link is rewritten from that one value.
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
