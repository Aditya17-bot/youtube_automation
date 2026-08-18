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

   > **Do not skip publishing.** While the consent screen sits in "Testing",
   > Google expires refresh tokens after **7 days** and every scheduled upload
   > breaks until you re-authorise by hand.

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

Once a format's output is consistently good, set `auto_publish: true` in that
channel's config and let Windows Task Scheduler drive it.

Create `daily.cmd`:

```bat
cd /d C:\adi\youtube_auto
.venv\Scripts\python.exe pipeline.py --channel finance
.venv\Scripts\python.exe pipeline.py --channel finance_short
.venv\Scripts\python.exe -m core.publish run --channel finance
```

Then: Task Scheduler → Create Task → daily trigger → run `daily.cmd`, with
"Run whether user is logged on or not" unticked (image generation needs the GPU
session).

**Do not schedule volume before quality.** YouTube's inauthentic-content policy
is enforced at the channel level and carries a three-strike path to permanent
removal from the Partner Program. Two good videos a week beats seven weak ones,
and the review gate exists precisely so this decision stays yours.

---

## 5. Disk

An 8-minute 1080p render is ~440 MB at YouTube's recommended bitrate; at 4-5
minutes it is ~250 MB. Work directories add per-beat clips and frame dumps on
top.

```bash
.venv/Scripts/python.exe -m core.review disk
.venv/Scripts/python.exe -m core.review purge --work --published --images
```

Approving drops the work directory. Publishing additionally drops the local mp4,
since YouTube then holds the copy that matters. Metadata, status and script
always survive, because topic history reads them.

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
