<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# We're going to investigate an idea that is pretty complex but can be very impactful to many content creators who are struggling to go viral.

Now, what we're going to do is we're going to have a user input their platform usernames and we just sort of make sure there's some strict changes, like being public, I don't know, some things you can choose whenever you're in the chat. By getting this research that would help complete this flow, but after that we're going to spawn a bunch of agents who are going to scrape things across the platform, like a deep clean viewage, seeing what is viral, what is not and then  going ahead and obtaining the videos that are not so viral and then seeing what's wrong with them visually. By this, I mean maybe we can add captions, maybe we can add sort of fake generated images as pop-ups. If the user is explaining something, for example, and then being sort of, I had generated music to fit the background nicely in low volume. And then making it downloadable for the user to, just repost or delete the original and just post this other one. This does it across all of its content, specifically videos here. All their Instagram reels or TikToks.

I would like for you to investigate the nuances and the deep sort of wiring in between.

For example, we're going to have to use third-party APIs and make sure we're able to get things like viewage, access to the clip in terms of downloading it really quickly, and then doing some kind of video understanding, and then having our sort of orchestration of the clip.

Content creator agents, planning out what sort of edits to make and deciding what will make it go viral. After that, we apply a bunch of edits using multimodal models like Nano Banana, Gemini 3.1 (which has video understanding), and then Lyria (which is Google's music generation model).

You’re describing a **multi‑agent “content‑clinic” system** that can ingest a creator’s Instagram Reels / TikToks, analyze why they’re not going viral, and then automatically re‑edit them with AI‑enhanced visuals, captions, and background music before letting the user redownload and repost. Here’s a breakdown of the deep wiring, focusing on APIs, orchestration, and agent architecture.

***

### 1. Platform‑level plumbing: IDs, access, and downloads

To do this, you need three main flows:

- **Discovery \& stats**: know which videos exist, their views, watch‑time proxies, likes, comments, shares, etc.
- **Download \& metadata**: get the raw clip plus key metadata (duration, caption, audio ID, etc.).
- **Account contracts**: enforce “public account” and accepted permissions.


#### TikTok

TikTok exposes a **user‑facing API** and a **Display API** for analytics and content access.

- You request a **user access token** via OAuth (authorization code → `access_token` for that user).[^1_1][^1_2]
- With that token you can:
    - Query the user’s videos (e.g., `/users/{user_id}/videos` or the Display API’s video list endpoints).[^1_3][^1_4]
    - Get metrics such as views, likes, comments, shares, and sometimes watch‑time‑related signals.[^1_5][^1_6]
- For downloading:
    - You can either:
        - Use a **third‑party downloader API** (e.g., Apify TikTok Downloader, TikTok‑specific scrapers) that return a direct download URL.[^1_7][^1_8][^1_9]
        - Or reverse‑engineer the TikTok client’s download URL from the video metadata (if you’re comfortable with the terms).

Key constraints:

- Users must be **public** or your app must have **explicit permissions**; some analytics are only available for business/creator accounts.[^1_6][^1_5]
- TikTok enforces strict rate limits and may require periodic token refreshes.[^1_10][^1_1]


#### Instagram Reels

Instagram exposes the **Instagram Graph API** inside the Meta Graph API ecosystem.[^1_11][^1_12]

- Reels are represented as `media_type: VIDEO` with `media_product_type: REELS`.[^1_13]
- For a given Reel you can:
    - Get basic metadata from the `/media` endpoint.[^1_13]
    - Pull **insights** (likes, comments, shares, plays/views, saves, reach) via `/{ig-media-id}/insights`.[^1_12][^1_14]

To access Reels:

- Account must be a **business/creator** account connected to a **Meta App** with approved permissions.[^1_11][^1_12]
- You need **OAuth flows** toward a Facebook Page and then the Instagram business account.[^1_14][^1_15]

For downloading:

- Meta’s Graph API does **not** expose a direct “download” URL for Reels, but many integrations cobble this together via:
    - Scrapers or third‑party APIs (e.g., Apify, custom scrapers) that fetch the video URL from the page.[^1_16][^1_17]
    - Or browser‑driven automation (e.g., Playwright + link extraction) if you’re ok with the risk of breakage.[^1_17]

***

### 2. Signal‑driven “virality diagnosis”

Once you have all videos and their stats, you need to **flag which ones are underperforming**. That’s where the “diagnostic” layer lives.

#### What to track

Typical virality‑related signals (per platform):[^1_18][^1_5][^1_6]

- Views and **view velocity** (how fast views accumulate in first 24–48 hours).
- **Watch‑time / completion rate**: TikTok surfaces completion‑rate percentiles; both platforms allow you to infer retention from “watch time per video”‑style exports.
- **Engagement rate**: $\text{likes + comments + shares} \div \text{views or followers}$.
- **Shares and saves** are often a stronger proxy for “would go viral” than likes alone.[^1_6]


#### Thresholding logic

You can design a simple or sophisticated scoring model:

- **Baseline**: compute median/comparison‑set metrics for the creator’s own channel (e.g., “anything below 70% of the creator’s median views/completion rate is candidate for editing”).[^1_5][^1_6]
- **Outlier detection**: flag videos with strong **views** but low **completion** or vice versa, which may indicate a broken hook or mid‑video drop‑off.[^1_18][^1_6]

This layer can be a **dedicated “diagnostic agent”** that:

- Queries the database of all crawled videos.
- Emits a priority‑ranked list of Reels/TikToks to re‑edit, plus a brief structured report (e.g., “low completion, high shares → fix hook + pacing”).[^1_6][^1_18]

***

### **3. Video understanding and edit‑planning agents**

You mentioned **Gemini 3.1 Pro**, **Nano Banana** (for visuals), and **Lyria 3** (music). This is where the “deep wiring” becomes most interesting.

#### 3.1 Video understanding via Gemini 3.1

Google’s Gemini 3.x series supports **video understanding** in the API.[^1_19][^1_20]

- You can upload a video file (mp4, webm, etc.) directly into a Gemini request and ask for:
    - Scene breakdown (segments, timestamps, on‑screen text, key actions).
    - Quality signals (e.g., pacing, cuts, text‑to‑action ratio, visual busyness).
    - Pain‑point suggestions (e.g., “hook is too slow”, “no text for hook phrase”, “no close‑ups”).[^1_21][^1_19]

You can design **specialized agents** that:

- **Hook‑diagnostic agent**:
    - Receives frames 0–3s of the video + caption.
    - Signals whether the hook is visually busy, text‑heavy, or conceptually unclear.[^1_20][^1_19]
- **Caption‑design agent**:
    - Uses Gemini + text‑generation to propose on‑screen text overlays at specific timestamps.[^1_19][^1_20]
- **Pacing‑analysis agent**:
    - Breaks the video into chunks (per 1–2 second) and flags “dead zones” where no visual or audio change occurs.[^1_19][^1_6]

Each of these agents can emit **structured JSON patches** over the original video (e.g., “add caption at 00:01.2, add zoom‑in at 00:03.8, add b‑roll at 00:05.1”).[^1_22][^1_19]

#### 3.2 Nano Banana (image pop‑ups and overlays)

Nano Banana is a high‑fidelity image‑generation model that can be used for:

- Generating **on‑screen pop‑ups / illustrations** that match the speaker’s explanation (e.g., formulas, diagrams, comedic cut‑ins).[^1_21]
- Generating **thumbnails** or **interstitial frames** between scenes.[^1_21]

An **“visual enhancement agent”** can:

- For each problematic segment (from Gemini’s analysis), ask Nano Banana to generate:
    - A short animated or still overlay that visually illustrates the concept.
    - A style‑consistent template (aspect ratio, font, color palette) matching the creator’s usual Reels.[^1_21]
- Then, export that as a **PNG/SVG overlay** with a prescribed duration and position.[^1_21]


#### 3.3 Lyria 3 for background music

Lyria 3 is Google’s generative music model, capable of:

- Generating **custom tracks** from text prompts or images.[^1_23][^1_21]
- Matching **length, tempo, and mood** to a video’s duration.[^1_24][^1_23]

An **“audio‑agent”** can:

- Take the video’s duration and a prompt like:
    - “Calm, upbeat background music at 110–120 BPM, low in the mix, no lyrics, for a 30‑second explainer Reel.”
- Get a **30‑second audio track** that fits.[^1_23][^1_24]
- During the final render, mixed‑down at **low volume** (e.g., −18 dB) so the original voice remains clear.[^1_23]

***

### 4. Multi‑agent orchestration architecture

You described **spawning a bunch of agents**. That maps cleanly onto an **AI agent orchestration** style architecture.

#### High‑level components

1. **User‑onboarding agent**
    - Collects usernames and validates:
        - Public account.
        - OAuth tokens for TikTok and Instagram (via your app’s login flow).[^1_2][^1_12][^1_1]
    - Stores those in a secure database (access tokens, refresh tokens, account IDs).[^1_25][^1_1]
2. **Crawler‑scrapers (per platform)**
    - Run periodically or on demand:
        - TikTok crawler: fetch all videos + stats.[^1_26][^1_4][^1_5]
        - Instagram Reels crawler: fetch all Reels + insights.[^1_12][^1_14][^1_13]
    - Persist metadata (ID, URL, duration, caption, views, likes, comments, shares, plays, saves) in your DB.[^1_5][^1_18][^1_6]
3. **Diagnosis coordinator**
    - Prioritizes which videos are candidates for re‑editing.
    - For each candidate, prepares a **work package**:
        - Original video URL + download link.
        - Platform, duration, key metrics.
        - A “prompt bundle” for the agents (e.g., “optimize for virality, keep audio intact, add captions and subtle background music”).[^1_18][^1_6]
4. **Edit‑planning agent pool**

Each candidate can be routed through a **parallel set of agents**:

- **Hook‑diagnostic agent** (Gemini‑based) → findings to the coordinator.[^1_20][^1_19]
- **Pacing‑analysis agent** (Gemini) → suggests cuts, zooms, b‑roll, or inserts.[^1_6][^1_19]
- **Caption‑design agent** (Gemini + Nano Banana) → generates candidate subtitles and on‑screen text.[^1_19][^1_21]
- **Audio‑agent** (Lyria 3) → generates a low‑volume background track.[^1_24][^1_23]

The coordinator then compiles a **final edit plan** (e.g., a JSON‑like “edit script” per video) and passes it to a **rendering agent**.

5. **Rendering agent**

This is the **orchestrated FFmpeg‑style layer**, driven by the edit plan:

- Uses tools like **FFmpeg** or **MoviePy** to:
    - Trim segments.
    - Add captions (burned‑in or soft subtitles).[^1_22]
    - Overlay generated PNGs or short clips from Nano Banana.[^1_21]
    - Mix in the Lyria‑generated background track at low volume.[^1_23]
- Outputs a **new MP4 file** ex‑video length, with the same aspect ratio (9:16 for Reels/TikTok).[^1_22]

6. **User delivery / feedback loop**

- Each edited video is:
    - **Downloadable** for the creator (e.g., a secure signed URL).[^1_27][^1_28]
    - Optionally queued as a **draft** for reposting (via TikTok’s publishing API or Instagram Graph API).[^1_4][^1_3][^1_12]
- Optionally, you can track **post‑edit performance** (new views, completion rate, engagement) and feed that back into the agents to refine future edits.[^1_18][^1_6]

***

### 5. Legal and platform‑risk considerations

There are several important caveats:

- **Mass‑downloading and re‑posting** need to respect:
    - Each platform’s terms of service (TOS).
    - **Public accounts only** is a good constraint; you should explicitly ask the user to make their account public before ingestion.[^1_15][^1_5]
- For **third‑party download APIs**, you may be relying on unofficial scrapers, which can be brittle and may be challenged by the platform.[^1_8][^1_7][^1_16]
- **Automatic re‑posting** should be:
    - Opt‑in per video.
    - Accompanied by clear UX (“this will replace your original” or “this is a new draft”).[^1_4][^1_12]

***

### 6. How you might structure the “flow” in your chat

Conceptually, you can wire this as:

1. **User input**:
    - “Enter your TikTok and Instagram handles.”
    - Chatbot ensures:
        - Accounts are public.
        - Redirects user to TikTok/Instagram OAuth flows to get tokens.[^1_1][^1_12]
2. **Background scheduler**:
    - After consent, spawn a **crawler** that:
        - Fetches all Reels/TikToks.
        - Stores them in a DB with stats.[^1_14][^1_5][^1_6]
3. **Agent activation**:
    - User selects “optimize all underperforming videos” or “just this video”.
    - System spawns:
        - Diagnosis + video‑understanding agents.
        - Caption / visual enhancement agents.
        - Music‑generation agent.[^1_23][^1_19][^1_21]
4. **Output**:
    - For each video, present:
        - A before‑after comparison.
        - An editable “edit plan”.
        - A downloadable MP4 and a “post on [platform]” button.[^1_27][^1_22]

***

If you tell me whether you want to focus on **TikTok only**, **Instagram only**, or **both**, and whether this is intended as a **self‑hosted backend** or a **SaaS product**, the next step can be designing:

- concrete API call patterns (TikTok + Instagram Graph),
- a schema for the “edit plan” JSON,
- and a concrete agent‑orchestration workflow (e.g., pseudo‑code for the coordinator).
<span style="display:none">[^1_29][^1_30][^1_31][^1_32][^1_33][^1_34][^1_35][^1_36][^1_37][^1_38][^1_39]</span>

<div align="center">⁂</div>

[^1_1]: https://developers.tiktok.com/doc/oauth-user-access-token-management

[^1_2]: https://developers.tiktok.com/doc/legacy-user-access-guide

[^1_3]: https://developers.tiktok.com/doc/display-api-get-started?enter_method=left_navigation

[^1_4]: https://business-api.tiktok.com/portal/docs

[^1_5]: https://www.printful.com/blog/tiktok-metrics

[^1_6]: https://www.socialinsider.io/blog/how-to-analyze-tiktok-video-performance/

[^1_7]: https://github.com/Mahmovdsayed/tiktokAPI

[^1_8]: https://apify.com/crawlerbros/tiktok-downloader-api/api

[^1_9]: https://www.browse.ai/t/extract-data-from-tiktok-video

[^1_10]: https://stackoverflow.com/questions/76530652/how-to-implement-tiktok-api-user-access-token-management-v2

[^1_11]: https://stackoverflow.com/questions/72627381/instagram-graph-api-video-reels

[^1_12]: https://www.getphyllo.com/post/a-complete-guide-to-the-instagram-reels-api

[^1_13]: https://stackoverflow.com/questions/72871367/instagram-graph-api-reels-available-but-what-is-the-format

[^1_14]: https://www.reddit.com/r/AppDevelopers/comments/1lu2but/how_can_i_extract_real_time_instagram_reels/

[^1_15]: https://developers.facebook.com/docs/features-reference/instagram-public-content-access/

[^1_16]: https://apify.com/neuro-scraper/instagram-reels-video-downloader/api

[^1_17]: https://sociavault.com/blog/bypass-instagram-graph-api-reels

[^1_18]: https://help-influence.later.com/hc/en-us/articles/20462369521687

[^1_19]: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/video-understanding

[^1_20]: https://ai.google.dev/gemini-api/docs/video-understanding

[^1_21]: https://junaid474.github.io/techblog/blog/Gemini-3.1-Pro.html

[^1_22]: https://arxiv.org/html/2509.16811v1

[^1_23]: https://blog.google/innovation-and-ai/products/gemini-app/lyria-3/

[^1_24]: https://www.youtube.com/watch?v=UKRz33WdaH0

[^1_25]: https://docs.owox.com/packages/connectors/src/sources/tik-tok-ads/credentials/

[^1_26]: https://developers.tiktok.com/doc/tiktok-api-v1-video-list

[^1_27]: https://www.vectara.com/blog/how-to-architect-robust-on-premise-ai-agent-orchestration

[^1_28]: https://www.ibm.com/think/topics/ai-agent-orchestration

[^1_29]: https://www.reddit.com/r/madeinpython/comments/1g83gxr/just_built_an_api_for_downloading_tiktok/

[^1_30]: https://developers.tiktok.com/products/research-api/

[^1_31]: https://github.com/VirtualPirate/insta-reel-api

[^1_32]: https://www.youtube.com/watch?v=B9mZpnVgG5s

[^1_33]: https://www.youtube.com/watch?v=ffNG6pdS2x4

[^1_34]: https://www.reddit.com/r/Automate/comments/1b22a97/how_can_i_automate_pulling_a_handful_of/

[^1_35]: https://apify.com/codebyte/tiktok-trending-videos-insights/api

[^1_36]: https://www.youtube.com/watch?v=N8lMo9HVmfA

[^1_37]: https://docs.getelevar.com/docs/how-to-generate-a-tiktok-access-token

[^1_38]: https://www.reddit.com/r/justinstolpe/comments/1982f7s/tiktok_api_access_tokens_and_login_dialog/

[^1_39]: https://stackoverflow.com/questions/63306324/does-instagram-reels-have-an-api-for-developers


---

# Between YouTube, Instagram, Twitter, TikTok, can you make them easiest to hardest to make this possible?

From easiest to hardest to implement your “viral‑editing assistant” system, the order is:

### 1. **TikTok** – **easiest**

- Public user videos and **view counts / likes / comments** are exposed via the official API (`/video/list` etc.) once you have a user‑access token and the `video.list` scope.[^2_1][^2_2]
- You can get a **direct download URL** from the video metadata or with third‑party scrapers, making bulk ingestion and re‑rendering straightforward.[^2_3][^2_4][^2_1]
- TikTok’s API is relatively well‑documented for creator‑facing stats and publishing, so end‑to‑end flow (crawl → analyze → edit → repost) is tractable with one OAuth flow.[^2_2][^2_5][^2_1]


### 2. **Instagram (Reels)** – **medium‑easy**

- Instagram Reels are exposed via the **Graph API** if the account is a **business/creator** and your app is approved for relevant permissions.[^2_6][^2_7]
- You can get Reel metadata and **insights** (views, likes, comments, shares, saves) from the `/{ig‑media‑id}/insights` endpoint.[^2_8][^2_6]
- Downloading is **not directly exposed** in the official API, but many tools and scrapers fetch the video URL from the page, so you can still get the clip programmatically (with more fragility).[^2_9][^2_10]


### 3. **YouTube** – **medium‑hard**

- The **YouTube Data API** lets you access a user’s videos and statistics, but **watch history is not exposed** and some analytics are limited without using workarounds or custom tools.[^2_11][^2_12][^2_13]
- To get a user’s own video library, you need:
    - A **Google OAuth flow** plus `https://www.googleapis.com/auth/youtube.readonly`‑style scopes.[^2_12][^2_13]
    - Then you can fetch the channel’s videos and their stats (views, likes, comments, etc.).[^2_12]
- Getting **download‑ready video files** is tricky:
    - No official “download” endpoint; you either rely on:
        - unofficial scrapers, or
        - YouTube’s embedded player extraction (more brittle and TOS‑sensitive).[^2_14][^2_15]


### 4. **Twitter / X** – **hardest**

- The **X (Twitter) API v2** provides media metadata (media keys, thumbnails/preview images) but **does not expose the actual video URL** for videos/GIFs in many endpoints.[^2_16]
- Older **v1.1 API** has more access to video URLs in `extended_entities`, but it is deprecated and harder to get production‑scale access for.[^2_16]
- Even if you get the video URL, Twitter’s policies are strict about:
    - Automated downloading and re‑posting.
    - Programmatic use of video content at scale.[^2_17][^2_16]

***

### Ordered list (easiest → hardest)

| Platform | Why it’s ranked here |
| :-- | :-- |
| **TikTok** | Full public‑video API, clear scopes, easy stats + download path. [^2_2][^2_1] |
| **Instagram** | Solid Graph API for Reels’ stats; only download is slightly hacky. [^2_6][^2_7] |
| **YouTube** | Great data API but tricky download layer and no watch‑history API. [^2_11][^2_12] |
| **Twitter / X** | Video files not exposed in v2; legacy v1.1 is fragile and policy‑restricted. [^2_16][^2_17] |

If you want, the next step can be:

- map each platform’s **OAuth flow** and **download strategy** into a unified abstraction layer for your agent system.
<span style="display:none">[^2_18][^2_19][^2_20][^2_21]</span>

<div align="center">⁂</div>

[^2_1]: https://www.youtube.com/watch?v=a3S4YhAU5Bk

[^2_2]: https://developers.tiktok.com/doc/tiktok-api-v1-video-list

[^2_3]: https://github.com/Mahmovdsayed/tiktokAPI

[^2_4]: https://apify.com/crawlerbros/tiktok-downloader-api/api

[^2_5]: https://business-api.tiktok.com/portal/docs

[^2_6]: https://www.getphyllo.com/post/a-complete-guide-to-the-instagram-reels-api

[^2_7]: https://developers.facebook.com/blog/post/2022/06/27/introducing-reels-apis-to-instagram-platform/

[^2_8]: https://stackoverflow.com/questions/72871367/instagram-graph-api-reels-available-but-what-is-the-format

[^2_9]: https://apify.com/neuro-scraper/instagram-reels-video-downloader/api

[^2_10]: https://sociavault.com/blog/bypass-instagram-graph-api-reels

[^2_11]: https://stackoverflow.com/questions/63213016/how-can-i-get-my-watch-history-with-youtube-data-api-v3

[^2_12]: https://developers.google.com/youtube/v3/docs

[^2_13]: https://groups.google.com/g/google-apps-script-community/c/NLiA-Cu9V3I

[^2_14]: https://pypi.org/project/youtubewatched/

[^2_15]: https://forums.meteor.com/t/read-my-google-accounts-youtube-watched-video-history-list/41070

[^2_16]: https://devcommunity.x.com/t/how-to-get-video-from-media-key/152449

[^2_17]: https://devcommunity.x.com/t/posting-video-on-behalf-of-user-does-user-need-to-authorize-twice/222716

[^2_18]: https://www.reddit.com/r/GoogleAppsScript/comments/17z2b8o/youtube_api_including_yt_watch_api_access_from/

[^2_19]: https://www.youtube.com/watch?v=Bzw2T18YDJ4

[^2_20]: https://www.adweek.com/media/instagram-to-extend-reels-to-several-api-endpoints/

[^2_21]: https://www.youtube.com/watch?v=xwfp-580KOE


---

# Could you now fully wire this to make it into a full implementation.

Here's the full wired implementation — 23 files packaged and ready to deploy.

***

## Project Structure

```
viralfix/
├── config.py                  # All API keys, thresholds, paths
├── main.py                    # FastAPI server + all HTTP endpoints
├── requirements.txt
├── .env.example
├── models/
│   ├── video.py               # VideoMeta dataclass (views, ER, completion...)
│   └── edit_plan.py           # EditPlan + CaptionOp, PopupOp, ZoomOp, AudioOp
├── auth/
│   ├── tiktok_auth.py         # PKCE OAuth 2.0 for TikTok
│   ├── instagram_auth.py      # Meta Graph API OAuth + long-lived tokens
│   └── youtube_auth.py        # Google OAuth 2.0 with refresh
├── crawlers/
│   ├── tiktok_crawler.py      # /v2/video/list → VideoMeta[]
│   ├── instagram_crawler.py   # Graph API /media + /insights
│   └── youtube_crawler.py     # YT Data API v3 uploads playlist
├── agents/
│   ├── diagnosis_agent.py     # Flags low-perf videos by channel percentile
│   ├── hook_agent.py          # Gemini → analyzes first 3s, scores hook
│   ├── pacing_agent.py        # Gemini → finds dead zones, suggests zooms
│   ├── caption_agent.py       # Gemini → transcribes + designs TikTok captions
│   ├── visual_agent.py        # Gemini plans → Imagen/Nano Banana generates pops
│   ├── audio_agent.py         # Lyria 3 → background music track
│   ├── edit_planner_agent.py  # Merges all agent outputs → single EditPlan
│   └── orchestrator.py        # Coordinates full pipeline concurrently
├── rendering/
│   └── renderer.py            # FFmpeg: captions + overlays + zooms + audio mix
└── utils/
    └── downloader.py          # httpx stream for TikTok/IG; yt-dlp for YouTube
```


***

## How Each Layer Works

### Auth layer

Each platform gets its own OAuth module. TikTok uses **PKCE** (code challenge/verifier) as required by their v2 API.  Instagram exchanges a short-lived token for a **long-lived 60-day token** automatically.  YouTube stores a **refresh token** so the server re-authenticates without the user.[^3_1][^3_2][^3_3]

### Crawlers

All three crawlers implement `BaseCrawler` with `fetch_all_videos()` → `List[VideoMeta]`. Each `VideoMeta` holds raw stats (views, likes, shares, saves, completion rate).  The YouTube crawler resolves the channel's **uploads playlist** to enumerate all videos without needing the watch history API.[^3_3][^3_4][^3_5]

### Diagnosis Agent

Uses channel-relative thresholds: any video below the **40th percentile** of the creator's own views, below **35% completion rate**, or below **2% engagement rate** gets flagged and queued. This is intentionally relative — a creator with 1M avg views and a creator with 1K avg views both get fair treatment.[^3_6][^3_7]

### Agent pool (parallel)

For each flagged video, four Gemini calls + one Lyria call fire **concurrently via `asyncio.gather`**:

- `HookAgent` → scores hook 0–10, suggests opening caption text
- `PacingAgent` → flags dead zones, recommends zoom timestamps
- `CaptionAgent` → full transcript + TikTok-style caption timings
- `VisualAgent` → plans pop-up placements → calls Imagen for each PNG


### Edit Planner

Merges all agent outputs into a single `EditPlan` object — a structured list of `CaptionOp`, `ZoomOp`, `PopupOp`, and one `AudioOp` — that the renderer consumes.[^3_8][^3_9]

### Renderer

Pure **FFmpeg** pipeline applied in sequence:[^3_8]

1. Burn SRT captions with Arial Black styling
2. Overlay each PNG popup with `between(t,start,end)` enable expression
3. Apply `zoompan` filter for subtle punch-in zooms
4. `amix` the original voice (0 dB) + Lyria track at `−18 dB`

***

## Setup \& Run

```bash
# 1. Clone and install
pip install -r requirements.txt
apt install ffmpeg        # or brew install ffmpeg

# 2. Fill in .env.example → rename to .env
cp .env.example .env

# 3. Start the server
uvicorn main:app --reload --port 8000

# 4. Connect a user's platforms (open in browser)
GET /auth/tiktok/start?user_id=ismael
GET /auth/instagram/start?user_id=ismael
GET /auth/youtube/start?user_id=ismael

# 5. Crawl + optimize all underperforming videos
POST /crawl/ismael
POST /optimize/ismael

# 6. Download an edited video
GET /download/{video_id}
```


***

## Key Wiring Notes

- **Concurrency limit** is set via `cfg.max_concurrent_agents = 6` — prevents rate-limiting on Gemini/Lyria APIs while still processing multiple videos in parallel.
- **TikTok download URL** in `tiktok_crawler.py` has a clearly marked `NotImplementedError` stub — plug in your preferred third-party API (Apify, etc.) since TikTok's official API doesn't return a raw CDN URL.
- **Nano Banana** is wired to the `IMAGEN_API_KEY` and `image_gen_model` config fields — swap the model string once the Nano Banana endpoint is publicly available.
- **Lyria 3** uses the `lyria-realtime` endpoint from the Gemini API namespace as Google began routing it through the Gemini API in early 2026.[^3_10][^3_11]

<div align="center">⁂</div>

[^3_1]: https://developers.tiktok.com/doc/oauth-user-access-token-management

[^3_2]: https://developers.facebook.com/docs/features-reference/instagram-public-content-access/

[^3_3]: https://developers.google.com/youtube/v3/docs

[^3_4]: https://developers.tiktok.com/doc/tiktok-api-v1-video-list

[^3_5]: https://www.getphyllo.com/post/a-complete-guide-to-the-instagram-reels-api

[^3_6]: https://www.socialinsider.io/blog/how-to-analyze-tiktok-video-performance/

[^3_7]: https://help-influence.later.com/hc/en-us/articles/20462369521687

[^3_8]: https://arxiv.org/html/2509.16811v1

[^3_9]: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/video-understanding

[^3_10]: https://blog.google/innovation-and-ai/products/gemini-app/lyria-3/

[^3_11]: https://www.youtube.com/watch?v=UKRz33WdaH0


---

# Okay, maybe this is helpful if I give you a specific stack to use from. This stack is influenced by the amount of credits I have within them, so I want to maximize usage.

1. Browser use
2. Daytona
3. Google Cloud credits
4. Convex database (open source)
5. Any random 3rd party api (probably free)

Given our also technical report on how we can use these naively to at least get a promising sounding of a product description for this idea of doing a page clean to have social media creators get more viral by posting better versions of their reels

Here's the complete stack-mapped implementation.

***

## Product Description

> **ViralFix** — *You made the video. We make it go viral.*
>
> Paste your TikTok or Instagram handle. ViralFix scans every Reel and Short you've ever posted, pinpoints the ones that underperformed, and automatically re-edits them — sharper hooks, animated captions, background music — and hands them back as ready-to-repost MP4s. No login, no API tokens, no video-editing skills required.

***

## How Each Tool Maps, Naively

| Tool | Naive Role | What it Replaces |
| :-- | :-- | :-- |
| **Browser Use** | AI agent that *sees* your profile like a human — no OAuth flows, no API approvals | Platform official APIs (which take weeks to get approved) |
| **Daytona** | A clean sandbox spawned per video — runs FFmpeg + Gemini inside it, then dies | A complex job queue + managed VM setup |
| **Google Cloud** | Gemini 2.5 Pro (video brain) + Lyria 3 (music) + Imagen (pop-ups) + Cloud Run (hosting) + GCS (video storage) | 4–5 separate vendor accounts |
| **Convex** | Real-time reactive DB — frontend just *subscribes* and watches jobs update live | Polling loops, Redis pub/sub, separate WebSocket server |
| **3rd-party free API** | Plugged into `process_script.py` — e.g. AssemblyAI's free tier for higher-accuracy transcription before Gemini caption-writes | A paid Whisper API |


***

## The Naive Flow (Step by Step)

### 1. User inputs a handle

The frontend sends `POST /analyze` with `{ userId, tiktok: "@handle" }`.[^4_1][^4_2]

### 2. Browser Use scrapes the profile

A Gemini Flash-powered `browser-use` agent opens `tiktok.com/@handle`, scrolls the grid, and returns a JSON list of video URLs + view counts — **zero OAuth required**.  The agent is given a plain-English task and uses vision to read numbers off thumbnails.[^4_3][^4_4][^4_5][^4_6][^4_7]

### 3. Convex stores everything live

Videos are upserted into Convex, low-performers flagged. The frontend is already subscribed to `videos:listForUser` — it updates **instantly** without any polling.[^4_8][^4_9][^4_1]

### 4. Daytona spawns one sandbox per bad video

Each sandbox gets FFmpeg installed, the video uploaded, and a self-contained `process_script.py` injected.  Sandboxes run in parallel (capped at 4 concurrent) and are **destroyed** after completion — you only pay for the seconds they run.[^4_10][^4_11][^4_12]

### 5. Inside the sandbox: Gemini + Lyria + FFmpeg

`process_script.py` does three Gemini video calls in sequence: hook diagnosis → caption generation → pacing/zoom suggestions. Lyria 3 generates a 30-second background track. FFmpeg burns it all together — captions in Arial Black at the bottom third, zooms on dead zones, music at −18 dB under the voice.[^4_13][^4_14]

### 6. Output lands in GCS, Convex notifies the frontend

The sandbox uploads the final MP4 to Cloud Storage, calls `convex.markJobDone()`, and the user's browser **immediately** sees a download button appear — no refresh needed.[^4_9][^4_1]

***

## Credit Allocation Strategy

- **Google Cloud** handles the heaviest lifting (Gemini calls, Lyria, Imagen, Cloud Run, GCS) — one billing account covers everything.[^4_15][^4_13]
- **Daytona** credits are burned per-second of sandbox runtime — destroying sandboxes immediately after render keeps costs minimal.[^4_11][^4_10]
- **Browser Use** runs locally or inside Cloud Run with `headless=True` — no separate billing.[^4_4][^4_3]
- **Convex** free tier supports up to 1M function calls/month and 1 GB storage — sufficient for a demo/MVP.[^4_1][^4_9]
- **3rd-party API** (e.g. AssemblyAI) stays on the free tier; Gemini already handles transcription as a fallback.[^4_16]
<span style="display:none">[^4_17][^4_18][^4_19]</span>

<div align="center">⁂</div>

[^4_1]: https://www.convex.dev/realtime

[^4_2]: https://browser-use.com

[^4_3]: https://github.com/browser-use/browser-use

[^4_4]: https://brightdata.com/blog/ai/browser-use-with-scraping-browser

[^4_5]: https://atalupadhyay.wordpress.com/2025/03/26/browser-automation-a-deep-dive-into-ai-powered-web-scraping-with-browser-use/

[^4_6]: https://www.scrapingbee.com/blog/browseruse-how-to-use-ai-browser-automation-to-scrape/

[^4_7]: https://www.youtube.com/watch?v=zGkVKix_CRU

[^4_8]: https://stack.convex.dev/implementing-work-stealing

[^4_9]: https://stack.convex.dev/real-time-database

[^4_10]: https://github.com/daytonaio/daytona

[^4_11]: https://www.daytona.io

[^4_12]: https://www.daytona.io/docs/en/sandboxes/

[^4_13]: https://blog.google/innovation-and-ai/products/gemini-app/lyria-3/

[^4_14]: https://www.youtube.com/watch?v=UKRz33WdaH0

[^4_15]: https://junaid474.github.io/techblog/blog/Gemini-3.1-Pro.html

[^4_16]: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/video-understanding

[^4_17]: https://www.youtube.com/watch?v=wfC8kmkvC-Q

[^4_18]: https://www.reddit.com/r/AI_Agents/comments/1on3ft6/is_browserbased_web_scraping_the_next_major/

[^4_19]: https://mastra.ai/reference/workspace/daytona-sandbox


---

# Cool, now it's time to explore all the specifics and wiring of the models, talk about ideas and the prompting for these models, and seeing if there are any nuances that we need to figure out. Mini solution.

This all just depends on the platform the user gives us input in. Once we visit that specific platform, we have to somehow get a bunch of metrics to classify what videos to actually clean for the user.

Ideally, we won't be looking at images, just videos, so reels and the ones that we do choose to claim should be no longer than 150 seconds, just because of the possible limitations on some of the models for video understanding or generating a long enough music track for a video.

This brings us towards the main models we will be using, and that is Gemini 3.1, which is a newly released model. It has video understanding capabilities.

Ideally, we first need to get context on the video if we can't see a transcript or if we can't pull the transcript naively. We need to understand what the user says specifically; try to extract possibly the audio to get a transcript of the video somehow on the user's page.

This is where nuances come up when it comes to choosing: what platforms are we actually able to accommodate, not because we have to download the video, do video understanding in it, get a transcript possibly since video understanding might have some limitations with total output (but if it can literally just copy word for word what the video says that is ideal), then we have to let Lyra, the music generation model, get some context on the overall tone and mood of this video to generate a type of music track to stitch together. The maximum length of tracks this model can generate, so we might need to find clever moments to clone the track quickly and overlay it into the video in a stitched manner.

If that's completely not possible (which I don't think so), we can figure out a type of prompt that can repeat the same tracks. Then we have music.

Now we will have, ideally, captions created for the video if there's no captions. That seems to be a video edit that gets a lot of viewage since it engages the user.

Nano Banana is creating some images to stitch into frames. This is going to be purely based on the video context, so we need to time it correctly on searching for the specific moment and getting the frames to stitch, generated that help the users visualize what the user is saying in certain moments.

This requires you to understand in a visual-based example, so I'm going to send you a screenshot specifically for your context on this usage of an image generation model.

We probably figure out some other things to touch up on the video, possibly adding slow moments or slow mini-segments on specific words the user says just to make it more engaging.

You can also help me think of more. Search on actual articles or blogs on how to take a boring video into a viral one, and you can get more ideas there.

We create that clip, and then we can make it downloadable for the user on the platform. With this context in mind, you sort of talked about how the specific services in our stack we can use them for.

Now you can wire in a full story with this context in mind, with prompting and the specific edits to the videos and overall just the user flow.

Now I have all the context I need on each model's real capabilities and limitations. Let me write the full story.

***

## The Big Picture: One Smart Gemini Call as the "Brain"

The most important architectural insight is to make **one comprehensive Gemini 3.1 Pro call** per video rather than four or five separate calls. Since Gemini 3.1 Pro is natively multimodal — processing the video track and audio track simultaneously — a single well-engineered prompt can return the transcript, hook analysis, caption timings, popup opportunities, zoom moments, and the music mood descriptor all in one shot.  This saves latency, cuts token cost, and keeps the edit plan coherent because all decisions come from the same context window.[^5_1][^5_2]

***

## Platform Filtering: What We Can Actually Handle

Before any AI runs, you need to decide whether a video is worth processing. Your 150-second cap is smart because Gemini 3.1 Pro processes video at approximately 1 frame per second with a default of 70 resolution tokens per frame — well within its ~45-minute video understanding ceiling — but Lyria 3 is capped at **30 seconds per generation**, so anything over 2.5 minutes would require aggressive music stitching.[^5_3][^5_4][^5_1]

The per-platform reality:

- **TikTok**: Browser Use scrapes view count, likes, and the video URL from the grid thumbnail. Works reliably without any API key.
- **Instagram Reels**: Browser Use navigates to the `/reels/` tab. View counts are visible on thumbnails. Same flow.
- **YouTube Shorts**: View count is on the Shorts shelf. `yt-dlp` handles the download.[^5_5]
- **Twitter/X**: Skip entirely for now — video URLs are not exposed reliably and download is legally murky.

Filtering criteria to run before downloading anything:

- Duration **≤ 150 seconds** (skip longer videos entirely)
- `media_type = VIDEO` (skip carousels, images, text posts)
- Not already edited by the user (check caption for "cc:", auto-caption markers, or existing subtitle tracks)

***

## The Single Gemini 3.1 Prompt (The "Master Analysis")

This is the most important prompt in the entire system. You upload the raw video and send this once.[^5_2][^5_1]

```python
MASTER_ANALYSIS_PROMPT = """
You are a professional short-form video producer. You are watching a creator's video
that is underperforming in views and engagement. Your job is to produce a complete
edit plan that will make this video go viral.

Analyze BOTH the visual content AND the audio/speech simultaneously.

Return ONLY valid JSON matching this exact schema. No markdown, no extra text.

{
  "transcript": [
    {
      "word": "exact spoken word",
      "start_sec": 0.00,
      "end_sec": 0.00,
      "is_emphasis": false     // true for key/punchy words the speaker stresses
    }
  ],

  "hook_analysis": {
    "score": 0,              // 0-10, how strong is the first 3 seconds
    "issues": [],            // e.g. ["slow intro", "no text on screen", "no face visible"]
    "hook_caption": {
      "text": "",            // max 8 words, ALL CAPS for impact, curiosity-gap style
      "start_sec": 0.0,
      "end_sec": 3.0,
      "position": "top"
    }
  },

  "captions": [
    {
      "text": "",            // max 6 words, key words in CAPS
      "start_sec": 0.0,
      "end_sec": 0.0,
      "position": "bottom_third",
      "style": "bold_white"
    }
  ],

  "image_popups": [
    {
      "concept": "",         // what the speaker is explaining at this moment
      "imagen_prompt": "",   // detailed Imagen/Nano Banana prompt for a clean visual
      "style": "flat illustration",   // or "diagram" | "meme panel" | "infographic"
      "start_sec": 0.0,
      "end_sec": 0.0,        // keep each popup 2-4 seconds max
      "position": "top_right"  // or "top_left" | "center_right"
    }
  ],

  "zoom_moments": [
    {
      "start_sec": 0.0,
      "end_sec": 0.0,
      "zoom_type": "slow_zoom",   // "slow_zoom" | "snapzoom" | "double_zoom"
      "zoom_factor": 1.12,
      "reason": ""
    }
  ],

  "slowmo_moments": [
    {
      "start_sec": 0.0,
      "end_sec": 0.0,
      "slowmo_factor": 0.7,   // 0.5 = half speed, 0.7 = 70% speed
      "trigger_word": ""      // the emphasis word that triggered this
    }
  ],

  "dead_zones": [
    {
      "start_sec": 0.0,
      "end_sec": 0.0,
      "description": ""   // what is happening / what is wrong
    }
  ],

  "music": {
    "lyria_prompt": "",    // detailed prompt for Lyria: genre, BPM, mood, instruments
    "energy_curve": [],    // e.g. ["low:0-5", "rising:5-15", "high:15-30"]
    "suggested_volume_db": -18
  },

  "overall_engagement_score": 0,   // your assessment 0-10
  "top_3_issues": []               // plain English, prioritized
}

Rules:
- Transcribe every single spoken word with accurate timestamps
- Captions should never exceed 6 words and should be line-broken at natural speech pauses
- Image popups only at EXPLANATORY moments (not at hook, not during CTA)
- Slow zooms highlight key statements, snapzooms on punchlines or emphasis words
- Slowmo only on single emphasis words the speaker punches (0.5–0.7x speed, max 0.5s of real audio)
- Dead zones are any stretch > 1.5s with no visual change AND no speech
- The Lyria prompt must specify BPM, genre, mood, key instruments, and energy level
"""
```


***

## Model-by-Model: Nuances and Prompting

### Gemini 3.1 Pro — Video Understanding

Gemini 3.1 Pro can process both the visual frames and the audio track in the same call.  The model was released in preview on February 19, 2026, and is the first "point-one" release Google has done, signaling a focused intelligence upgrade over Gemini 3.[^5_6][^5_7][^5_1][^5_2]

**Key nuances:**

- Max video length with audio: **~45 minutes** — your 150s cap is trivial for this model[^5_1]
- Upload the video as `video/mp4` inline data (base64) for files < ~20 MB, or use the Files API for larger clips[^5_8][^5_1]
- The model genuinely **word-for-word transcribes** speech while simultaneously reasoning about what's on screen — this is the core differentiator you should lean into[^5_2]
- Output token limit matters: a 150s video can produce a large JSON. Cap your transcript to individual words with timestamps, not full sentences, and strip unnecessary whitespace in your prompt schema

**Transcript extraction nuance**: If the video has native captions or subtitle tracks embedded (common on TikToks), you can instruct the model to "prefer the embedded subtitle track for timing accuracy but independently verify against spoken audio." This reduces hallucinated timestamps.[^5_9]

***

### Lyria 3 — Music Generation

Lyria 3 is capped at **30 seconds per generation**.  For a 150-second video, this is a real constraint. Here's the strategy:[^5_4][^5_3]

**The looping stitch approach (primary):**

```python
LYRIA_PROMPT_TEMPLATE = """
Generate a {duration_sec}-second background music track for a short-form social video.
Do NOT generate: vocals, lyrics, spoken word.
Mood: {mood}
Genre: {genre}
BPM: {bpm}
Key instruments: {instruments}
Energy: starts {energy_start}, builds to {energy_peak} by {peak_sec}s, maintains through end.
Designed for looping: end the track on a note that can seamlessly connect back to the start.
Mix level: suitable for -18 dB under voice narration.
"""
```

The trick: generate one 30s track with explicit **loop-friendly endings** (instruct it to "end on a note that connects back to the start"). Then in FFmpeg, loop it with a **2-second crossfade** to mask the seam:

```bash
ffmpeg -stream_loop 4 -i bg.wav \
       -af "afade=t=in:st=0:d=1,afade=t=out:st=148:d=2" \
       -t 150 bg_looped.wav
```

**The continuation approach (better quality, more credits):**

Generate the first 30s, then generate a second 30s clip whose prompt ends with: *"This is a continuation of a track that started as: [description of first 30s]. Match the exact BPM, key, and mood. Continue seamlessly."* Stitch with a 0.5s crossfade. Repeat until you cover the video duration.  Based on the YouTube tutorial showing extensions up to 4 minutes, this is viable if you have the credits.[^5_3]

**What to feed Lyria from Gemini's output:**

```
"lyria_prompt": "Lo-fi hip-hop, 92 BPM, warm and curious mood, soft piano lead with
light percussion and subtle bass, no vocals, energy stays consistent but slightly
builds at 15s, loop-friendly ending, background mix level"
```


***

### Nano Banana — Image Pop-ups

Nano Banana is a **natively image-editing** model — it excels at taking an existing frame and modifying it, generating start/end keyframes, and creating stylized visual overlays.  For your use case, you're using it in pure **generation mode**: given a concept from the Gemini transcript, generate a clean PNG overlay.[^5_10][^5_11][^5_12]

**Timing nuance from Gemini's output:**
The `image_popups` array from your master analysis includes the `start_sec` and `end_sec` alongside an `imagen_prompt`. The popup should appear **0.3 seconds after** the speaker first says the concept word — not at the exact word start, because there's a slight visual processing lag that feels more natural with a small delay.[^5_13]

**Prompt style that works best:**

```python
# When speaker is explaining a concept visually
"imagen_prompt": "Flat illustration, white background, simple diagram of compound 
interest growing exponentially, large upward arrow, bar chart in corner, 
clean minimal design, no text, 1:1 aspect ratio, transparent background"

# When speaker makes a humorous or relatable statement
"imagen_prompt": "Meme-style panel, simple cartoon surprised face, bold outlines,
white background, no text, 1:1 ratio, transparent background"

# When speaker describes a product or place
"imagen_prompt": "Realistic photo-style render of [concept], clean bright background,
centered subject, no text, 1:1 ratio"
```

After generating the PNG, it gets composited into the video at the `position` specified (e.g., top-right at 30% frame width) using FFmpeg's `overlay` filter with `enable='between(t,{start},{end})'`.[^5_14][^5_15]

***

## The Full Set of Video Edits (with Techniques)

Beyond captions, popups, and music, here's every edit your system should apply based on what actually drives virality:[^5_16][^5_17][^5_15][^5_13][^5_14]

### 1. Hook Caption (0–3s)

A bold, curiosity-gap text overlay burned in at the very top of frame.[^5_17][^5_13]

- Max 8 words
- ALL CAPS
- Style: white text, thick black outline
- Examples Gemini should generate: *"I WASN'T SUPPOSED TO FIND THIS OUT"*, *"THIS CHANGED EVERYTHING FOR ME"*


### 2. Animated Word-by-Word Captions

Rather than block subtitles, each word pops in individually on its spoken beat — the "TikTok karaoke" style.  Your transcript array (word + timestamps) from Gemini makes this trivial to implement. The current word highlights in yellow while the rest fades slightly.[^5_16][^5_17]

Rendered via FFmpeg `drawtext` filter chained per word, or via a Python `Pillow`-rendered subtitle layer.

### 3. Slow Zoom on Key Statements

When the speaker makes their most important point, a slow `1.0 → 1.15x` scale zoom over 2–3 seconds communicates visual emphasis without cutting.  Use `zoompan` in FFmpeg. Gemini's `zoom_moments` array provides the timestamps.[^5_18][^5_15][^5_14]

### 4. SnapZoom / Double Zoom on Punchlines

A fast `1.0 → 1.20x` zoom in 4 frames, then snapping back — creates a jarring "pop" that resets viewer attention at punchlines.  This is a **pattern interrupt**: the viewer's scrolling brain snaps back to focus.[^5_15][^5_13]

```bash
# SnapZoom: zoom in over 4 frames, hold 8 frames, snap back over 4 frames
-vf "zoompan=z='if(between(n,{sf},{sf+4}),1+0.20*(n-{sf})/4,if(between(n,{sf+4},{sf+12}),1.20,if(between(n,{sf+12},{sf+16}),1.20-0.20*(n-{sf+12})/4,1)))':d=1:fps=30"
```


### 5. Slow-Motion on Emphasis Words

When the speaker punches a power word (identified by `is_emphasis: true` in the transcript), the frame rate for that 0.3–0.5 second segment drops to 0.6x speed.  This creates a subtle "weight" on the word. Done via `setpts=1.667*PTS` in FFmpeg for that time segment only.[^5_14][^5_15]

### 6. Jump Cuts on Dead Zones

Any pause > 1.5 seconds with no visual change and no speech gets cut entirely using FFmpeg's `trim` + `concat` pipeline.  This is the single highest-ROI edit — dead zones are the primary reason people scroll away.[^5_17][^5_16]

### 7. Beat-Sync Cuts (If Multiple Clips)

If the video has natural cuts or transitions, re-align them to the beat of the Lyria-generated music track.  This requires detecting the BPM from the generated audio (`librosa` in Python) and nudging cut points ±2 frames to land on a downbeat.[^5_17]

### 8. Text Highlight Animation

At the moment the hook caption appears, animate an underline or highlight box that sweeps from left to right under the key word over 0.5s.  Done via an animated `drawbox` or `Pillow` sequence overlay.[^5_15]

***

## Lyria → FFmpeg Audio Stitching in Detail

```
Video: 90 seconds
Lyria Track 1: 0s–30s  → generated with loop-friendly ending
Lyria Track 2: 28s–58s → generated as "continuation" of Track 1, crossfaded at 28s
Lyria Track 3: 56s–86s → same
Lyria Track 4: 84s–90s → truncated at video end with fade-out
```

The FFmpeg command for crossfade stitching:

```bash
ffmpeg -i track1.wav -i track2.wav -i track3.wav -i track4.wav \
  -filter_complex "
    [^5_0][^5_1]acrossfade=d=2:c1=tri:c2=tri[a01];
    [a01][^5_2]acrossfade=d=2:c1=tri:c2=tri[a012];
    [a012][^5_3]acrossfade=d=2:c1=tri:c2=tri[aout]
  " \
  -map "[aout]" music_full.wav
```

Then mix with the original voice at −18 dB as shown in the earlier architecture.[^5_19][^5_3]

***

## Complete User Flow (End to End)

```
1. USER INPUT
   └─ "Here is my TikTok handle: @username"
      Frontend sends POST /analyze { userId, tiktok: "username" }

2. BROWSER USE SCRAPE (Google Cloud Run → Browser Use + Gemini Flash)
   ├─ Navigate to tiktok.com/@username
   ├─ Scroll grid, extract: [url, views, likes, caption] for each video
   └─ Filter: duration ≤ 150s, media_type = video
      → Persist all to Convex (frontend shows full video grid instantly)

3. DIAGNOSIS (in-memory, Python)
   ├─ Compute channel-relative p40 view threshold
   ├─ Flag videos below p40 views OR < 2% engagement rate
   └─ Sort worst-first
      → Convex marks flagged videos with isLowPerf=true
        (frontend highlights them in red in real-time)

4. FOR EACH LOW-PERF VIDEO (Daytona sandbox, max 4 parallel)
   │
   ├─ 4a. DOWNLOAD (Browser Use → get CDN URL → httpx stream)
   │       Convex job.status = "downloading"
   │
   ├─ 4b. MASTER GEMINI 3.1 CALL (single API call)
   │       Upload video → run MASTER_ANALYSIS_PROMPT
   │       Returns: transcript, hook_analysis, captions,
   │                image_popups, zoom_moments, slowmo_moments,
   │                dead_zones, music descriptor
   │       Convex job.status = "analyzing"
   │
   ├─ 4c. PARALLEL GENERATION (asyncio.gather)
   │       ├─ Nano Banana: generate PNG for each image_popup.imagen_prompt
   │       ├─ Lyria 3: generate track 1 (30s) using music.lyria_prompt
   │       │           generate track 2–N as continuations
   │       │           stitch with FFmpeg crossfade
   │       └─ Caption renderer: build .srt file from transcript + captions
   │       Convex job.status = "generating_assets"
   │
   ├─ 4d. RENDER PIPELINE (FFmpeg inside Daytona)
   │       Step 1: Jump-cut dead zones (trim + concat)
   │       Step 2: Apply slow-mo segments (setpts filter per segment)
   │       Step 3: Apply zoom_moments (zoompan filter)
   │       Step 4: Burn captions (subtitles filter, Arial Black)
   │       Step 5: Composite Nano Banana PNGs (overlay filter, timed)
   │       Step 6: Mix Lyria track at -18dB (amix filter)
   │       → Output: {video_id}_viralfix.mp4
   │       Convex job.status = "rendering"
   │
   └─ 4e. UPLOAD + NOTIFY
           Upload to Google Cloud Storage (public URL)
           Call Convex markJobDone(jobId, outputUrl, hookScore, pacingScore)
           Daytona sandbox auto-destroys
           → Frontend INSTANTLY shows download button + before/after scores
             (Convex real-time subscription fires without any polling)

5. USER DOWNLOADS
   └─ Clicks "Download Edited Version"
      Gets signed GCS URL → downloads {video_id}_viralfix.mp4
      Option: "Repost directly" → calls TikTok/Instagram publish API
```


***

## One Key Nuance You'll Hit

The biggest practical challenge is **dead zone cutting on talking-head videos**. If the speaker pauses for 2 seconds to breathe, cutting that out creates a jarring visual jump because the background and face position change slightly between frames. The fix: extract the **last frame before** the dead zone and the **first frame after** and crossfade them over 3 frames (0.1s) rather than hard-cutting. This makes the jump invisible to most viewers and is a 4-line FFmpeg filter change.

Also note: the screenshot of Nano Banana usage you mentioned — I don't see it attached yet. Once you share it, I can refine the exact popup compositing style to match whatever visual pattern you have in mind.
<span style="display:none">[^5_20][^5_21][^5_22][^5_23][^5_24][^5_25][^5_26][^5_27][^5_28][^5_29][^5_30][^5_31][^5_32]</span>

<div align="center">⁂</div>

[^5_1]: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-1-pro

[^5_2]: https://www.datacamp.com/blog/gemini-3-1

[^5_3]: https://www.youtube.com/watch?v=hmVzP9gwExI

[^5_4]: https://ucstrategies.com/news/googles-free-ai-music-tool-caps-at-30-seconds-and-thats-the-whole-strategy/

[^5_5]: https://developers.google.com/youtube/v3/docs

[^5_6]: https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-pro/

[^5_7]: https://deepmind.google/models/model-cards/gemini-3-1-pro/

[^5_8]: https://ai.google.dev/gemini-api/docs/video-understanding

[^5_9]: https://towardsdatascience.com/unlocking-multimodal-video-transcription-with-gemini/

[^5_10]: https://www.youtube.com/watch?v=S4pjkkcBXy8

[^5_11]: https://www.reddit.com/r/Bard/comments/1n1mi95/trying_to_use_nanobanana_to_create_a_video_frame/

[^5_12]: https://www.youtube.com/watch?v=9yBMtvD_CFw

[^5_13]: https://www.jcasablancas.com/5-editing-techniques-to-make-your-videos-go-viral/

[^5_14]: https://www.youtube.com/watch?v=R9Uu0JtvyMg

[^5_15]: https://www.youtube.com/watch?v=YmeqSYQi7g0

[^5_16]: https://www.reddit.com/r/NewTubers/comments/1mzw5ko/how_i_turn_boring_clips_into_viral_edits_for/

[^5_17]: https://www.editorskeys.com/blogs/news/creating-viral-video-edits-how-to-make-engaging-content-for-youtube-social-media

[^5_18]: https://www.instagram.com/reel/C6tjMuJr7T1/?hl=en

[^5_19]: https://blog.google/innovation-and-ai/products/gemini-app/lyria-3/

[^5_20]: https://developers.googleblog.com/gemini-2-5-video-understanding/

[^5_21]: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/2-5-pro

[^5_22]: https://www.reddit.com/r/Bard/comments/1ohnvgq/gemini_25_pro_tts_limitations_how_to_generate/

[^5_23]: https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-model-thinking-updates-march-2025/

[^5_24]: https://www.opus.pro/blog/google-io-2026-ai-video-generation-gemini-updates-09b90

[^5_25]: https://www.reddit.com/r/SunoAI/comments/1n280hn/i_cant_get_the_45_model_to_make_a_song_longer/

[^5_26]: https://www.youtube.com/watch?v=RNODXga669Q

[^5_27]: https://www.youtube.com/watch?v=vmuL83Nn9U4

[^5_28]: https://developers.googleblog.com/introducing-veo-3-1-and-new-creative-capabilities-in-the-gemini-api/

[^5_29]: https://www.youtube.com/watch?v=ODPaGaClZhI

[^5_30]: https://gemini.google/overview/video-generation/

[^5_31]: https://www.reddit.com/r/Bard/comments/1o8qs13/whats_new_in_veo_31_have_you_noticed_any_upgrades/

[^5_32]: https://www.instagram.com/reel/DA8bA9yOAWJ/?hl=en


---

# Yeah, based on your answer, I have just a couple clarifying questions to understand overall what you're going ahead and telling me.

1. When it comes to your approach with using Gemini as the main generator for all the contacts we need, in order to go ahead and edit the videos using the other two models, are you sure this wouldn't be a better approach if we batch a bunch of API calls in parallel, each doing one specific task?

For example, getting transcript, getting summary, and a description of a track that would fit the video, or another call that gives us cue context on where to possibly place the AI-generated images after the user says something that can be visualized by the viewer of the reel to be more engaged this can be something funny or specific (all depends on what the user is saying). Then, obviously, we get context on how to generate the specific track and stitch it.

We might default the track to be a little bit quieter and reverb slower, so it doesn't really overlap with things the user is saying or make it hard to hear them. That should be done for each of the clips.

After that, each of the clips will be downloadable and able to be given to the user in a viewage format. But this is a different problem, but yeah.

2. When you specifically say "scraping," I'm super confused.

We use the browser agents, and as you know, they have capabilities to take screenshots. This can help us possibly see view edge, we can run some type of vision understanding on these screenshots to get the view edge, or we can scrape.

I need you to be specific in which one you're using, or if it's some other service. And note, if we can do a standardized scrapping manner for all platforms. If not, make it very distinct on the most efficient and fastest approach to 10% accurately get what we need from each platfom.

3. For the image model, I want to know the magnitude on what we can do with it within this specific case.

Whenever you see an Instagram, TikTok, Reel, you will notice that some content creators like to use images or pop-ups. To not make it choppy, are we supposed to generate an image and then overlay it into that image using some type of public library? Or should we ask Nano Banana to directly edit the specific frame?

Put it through the image gen model, make it seem exactly like this screenshot I will send you as an example, but add the specific visual image cues within the frame, and then put it back into the video. That will be very cool, and I think faster, if we can do that.

4. Talk more about the specifics on what we will use using FFmpeg. Then explain the specific use cases it's limited towards, even going down all the way to the input and outputs of things.

Look at latency, efficiency, possible optimizations. Because remember, this is probably going to be downloaded on each sandbox.

This means you should look into Daytona and its documentation to understand how we can do all the work within that specific sandbox and then return the downloadable clip, or if there's another implementation you're talking about.

It's good to restate that clearly.

Now I have everything I need for a precise, comprehensive answer to all four questions. Let me walk through each one with real specificity.

***

## Q1: Parallel Batched Gemini Calls > One Master Call

You're right to challenge this. A single mega-prompt is a trap — it creates token pressure, forces the model to juggle too many tasks at once, and a single malformed JSON key breaks the entire edit plan.  The correct architecture is **4 parallel Gemini 3.1 calls**, each sharpened for one job, all firing after the video is uploaded **once** to the Files API (so you don't re-upload 20 MB four times):[^6_1][^6_2]

```
Upload video once → get file_uri
        │
        ├─── Call A: TRANSCRIPT AGENT ────────────────────────────────────────
        │     Focus: Audio only. Word-by-word timestamps. Emphasis detection.
        │     Output: [{word, start_sec, end_sec, is_emphasis}]
        │
        ├─── Call B: VISUAL CUE AGENT ────────────────────────────────────────
        │     Focus: What is being EXPLAINED at each moment that
        │            a viewer would benefit from seeing visualized?
        │            (funny reaction, concept diagram, product, place)
        │     Output: [{concept, imagen_prompt, start_sec, end_sec, position}]
        │
        ├─── Call C: MUSIC CONTEXT AGENT ─────────────────────────────────────
        │     Focus: Overall tone, pace, energy, topic of the video.
        │            Describe the exact Lyria track that should accompany it.
        │     Output: {lyria_prompt, bpm, mood, energy_curve, volume_db}
        │
        └─── Call D: EDIT MECHANICS AGENT ────────────────────────────────────
              Focus: Hook strength, dead zones, zoom moments, slowmo words.
              Output: {hook_score, hook_caption, dead_zones, zoom_moments,
                       slowmo_moments, top_3_issues}
```

All four run with `asyncio.gather()` simultaneously. Total latency ≈ time of the **slowest single call**, not the sum. The transcript call (Call A) is typically the fastest since it's pure audio parsing. The visual cue call (Call B) is the slowest because it requires scanning every frame.

### The four prompts, written precisely

**Call A — Transcript Agent**

```python
TRANSCRIPT_PROMPT = """
Listen to the audio track of this video. Transcribe every spoken word with
precise timestamps. Do not describe what you see visually.

Return ONLY valid JSON, no markdown:
{
  "words": [
    {
      "word": "exact word as spoken",
      "start_sec": 0.00,
      "end_sec": 0.00,
      "is_emphasis": false   // true if speaker vocally stresses/punches this word
    }
  ],
  "total_duration_sec": 0.0
}

Rules:
- Every word gets its own entry, even filler words (uh, like, you know)
- Timestamps accurate to 0.05s
- is_emphasis = true only for clear vocal stress, not just important words
- Do not skip any words or merge multiple words into one entry
"""
```

**Call B — Visual Cue Agent**

```python
VISUAL_CUE_PROMPT = """
Watch this video. Identify moments where a POP-UP IMAGE would help the viewer
visualize what the creator is saying, making the video more engaging.

This can be:
- A funny/relatable reaction image when they say something humorous
- A real photo or diagram when they explain a concept
- A product/place image when they reference something specific
- A "proof" image (screenshot, result, before/after) when they make a claim

Return ONLY valid JSON, no markdown:
{
  "popups": [
    {
      "trigger_phrase": "exact words that trigger this popup",
      "start_sec": 0.0,       // 0.3s AFTER the trigger phrase ends
      "end_sec": 0.0,         // 2-4 seconds max on screen
      "concept": "one sentence: what this image should show",
      "imagen_prompt": "detailed Nano Banana prompt — be specific about style,
                        content, NO TEXT IN IMAGE, transparent/white background,
                        1:1 aspect ratio, flat illustration OR photo-realistic",
      "tone": "funny | informative | proof | aesthetic",
      "position": "bottom_left | bottom_right | top_right",
      "scale_pct": 45        // % of frame width the popup occupies
    }
  ]
}

Rules:
- Maximum 3 popups per video
- Never place a popup in the first 3 seconds (hook) or last 2 seconds (CTA)
- Popups should feel like the creator is showing evidence or adding humor
- Each popup concept must directly relate to the words spoken at that moment
"""
```

**Call C — Music Context Agent**

```python
MUSIC_PROMPT = """
Watch this video and describe the MOOD, TOPIC, and PACING of the content.
Your job is to write a precise Lyria music generation prompt for a background track.

Return ONLY valid JSON, no markdown:
{
  "lyria_prompt": "Complete music prompt for Lyria. Specify: genre, exact BPM,
                   key instruments, mood, energy progression. End with:
                   'Loop-friendly ending, no vocals, background mix level (-18dB),
                   designed to sit beneath a speaking voice without competing.'",
  "bpm": 0,
  "mood": "",
  "energy_curve": [
    {"phase": "intro", "start_sec": 0, "end_sec": 0, "level": "low|medium|high"}
  ],
  "num_30s_segments_needed": 0,   // ceil(video_duration / 28) accounting for crossfades
  "continuation_hint": ""         // one sentence describing the first segment's
                                  // ending motif for the continuation prompt
}
"""
```

**Call D — Edit Mechanics Agent**

```python
EDIT_MECHANICS_PROMPT = """
Watch this video and identify specific editing opportunities.

Return ONLY valid JSON, no markdown:
{
  "hook_analysis": {
    "score": 0,
    "issues": [],
    "hook_caption": {
      "text": "MAX 8 WORDS ALL CAPS CURIOSITY-GAP STYLE",
      "start_sec": 0.0,
      "end_sec": 3.0
    }
  },
  "dead_zones": [
    {
      "start_sec": 0.0,
      "end_sec": 0.0,
      "type": "pause | visual_freeze | filler_words"
    }
  ],
  "zoom_moments": [
    {
      "start_sec": 0.0,
      "end_sec": 0.0,
      "type": "slow_zoom | snapzoom",
      "factor": 1.12,
      "reason": "key statement | punchline | emphasis"
    }
  ],
  "slowmo_moments": [
    {
      "word": "the emphasis word",
      "start_sec": 0.0,
      "end_sec": 0.0,
      "factor": 0.65
    }
  ],
  "top_3_issues": []
}
"""
```

After all four calls return, a lightweight Python **Edit Planner** merges the four JSON outputs and cross-references Call A's word timestamps with Call B's popup triggers and Call D's slowmo words — syncing everything to the exact same timeline.[^6_1]

***

## Q2: Scraping — Exact Approach Per Platform

"Browser Use" is a general term — inside it, you have **three distinct methods** available. Here's which one to use, per platform, for speed and accuracy:

### The Three Methods (ranked fastest to slowest)

| Method | How it works | Speed | Reliability |
| :-- | :-- | :-- | :-- |
| **`page.evaluate()` (JS DOM read)** | Playwright executes JS directly in the page context, reads data from the DOM or window object | ~0.5s | High if selectors stable |
| **Screenshot + Gemini Flash vision** | Take screenshot, send to Gemini Flash to read numbers | ~3-5s | High (vision adapts to layout changes) |
| **Network interception (JSON intercept)** | Intercept XHR/fetch responses before they hit the DOM, pull raw JSON from the API the page itself calls | ~1-2s | Highest (platform's own data format) |

### Per-platform breakdown

**TikTok** → **Network JSON interception** (fastest + most complete)

TikTok's profile page fires a `__NEXT_DATA__` JSON blob in the initial HTML and subsequent XHR calls with `playCount`, `diggCount`, `commentCount`, `shareCount` for every video.  You intercept this rather than reading the DOM at all:[^6_3][^6_4]

```python
async def scrape_tiktok(handle: str) -> list:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        video_data = []

        # Intercept API responses before DOM renders
        async def handle_response(response):
            if "api/post/item_list" in response.url or "aweme/v1" in response.url:
                try:
                    body = await response.json()
                    for item in body.get("aweme_list", body.get("itemList", [])):
                        stats = item.get("statistics", item.get("stats", {}))
                        video_data.append({
                            "id": item.get("aweme_id") or item.get("id"),
                            "url": f"https://www.tiktok.com/@{handle}/video/{item.get('aweme_id') or item.get('id')}",
                            "views": stats.get("play_count") or stats.get("playCount", 0),
                            "likes": stats.get("digg_count") or stats.get("diggCount", 0),
                            "comments": stats.get("comment_count") or stats.get("commentCount", 0),
                            "shares": stats.get("share_count") or stats.get("shareCount", 0),
                            "duration": item.get("video", {}).get("duration", 0),
                            "caption": item.get("desc", ""),
                            "download_url": item.get("video", {}).get("play_addr", {}).get("url_list", [None])[^6_0],
                        })
                except: pass

        page.on("response", handle_response)
        await page.goto(f"https://www.tiktok.com/@{handle}", wait_until="networkidle")

        # Scroll to trigger more API loads
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)

        await browser.close()
        # Filter: duration <= 150s, has download URL
        return [v for v in video_data if 0 < v["duration"] <= 150 and v["download_url"]]
```

The `download_url` from the intercepted JSON is a **direct CDN URL** — no third-party API needed.[^6_4][^6_3]

***

**Instagram** → **DOM `data-e2e` selectors + JS evaluate** (no vision needed)

Instagram Reels renders view counts as `<span>` elements with `data-e2e` attributes in the Reels grid.[^6_5]

```python
async def scrape_instagram(handle: str) -> list:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()
        await page.goto(f"https://www.instagram.com/{handle}/reels/", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Scroll to load reels
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

        # Pull structured data from IG's embedded JSON in <script type="application/ld+json">
        # OR fall back to DOM scraping of the grid
        reels = await page.evaluate("""
            () => {
                const items = [];
                document.querySelectorAll('a[href*="/reel/"]').forEach(a => {
                    const views = a.querySelector('span')?.innerText || '0';
                    items.push({
                        url: 'https://www.instagram.com' + a.getAttribute('href'),
                        views_raw: views,  // "1.2M", "45K" — parse in Python
                    });
                });
                return items;
            }
        """)
        await browser.close()
        return [_parse_ig_reel(r) for r in reels]

def _parse_ig_reel(r: dict) -> dict:
    raw = r["views_raw"].strip().upper()
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    for s, m in mult.items():
        if s in raw:
            return {**r, "views": int(float(raw.replace(s,"")) * m)}
    return {**r, "views": int(raw.replace(",","") or 0)}
```

Instagram does **not** expose the direct download URL in the DOM. For the download URL, you intercept the `*.cdninstagram.com` network request the page makes when the Reel autoplays on hover — same interception pattern as TikTok.[^6_5]

***

**YouTube** → **Official Data API v3 only** (zero scraping, zero Browser Use)

YouTube's API gives you `viewCount`, `likeCount`, `commentCount`, `duration` — everything you need — with zero browser overhead. Already implemented in the previous codebase. Use this as the gold standard and compare it to the scraping pipelines above.

***

**No screenshot + vision** for metrics on any platform. Screenshots are the slowest and most brittle option — they should be reserved only as a **final fallback** when both DOM scraping and network interception fail (e.g. a new IG layout). When that fallback fires, you pass the screenshot to Gemini Flash 2.0 (not 3.1 — Flash is 10x cheaper and fine for reading numbers off a page) with: *"Read the view counts from this grid of Reels thumbnails and return them as JSON."*

***

## Q3: Nano Banana Frame Editing — The Right Mental Model

Looking at your screenshot exactly: the creator has **two rectangular photo pop-ups** sitting in the bottom third of the frame, side by side. The creator's hands and body are **in front of the overlays** in some cases. The images feel like evidence — *"here is me at that beach, here is that Erewhon drink I mentioned."*

The user's suggested approach — pass the actual video frame + generated image to Nano Banana and have it composite them together — is the **right instinct** but needs one important correction:

**You don't freeze the frame. You generate the composited frame ONCE as a reference, then use FFmpeg to overlay the popup PNG over the live video.**

Here's why: Nano Banana compositing the popup onto a frame produces a beautiful result for that one static frame. But the creator is still moving (talking, gesturing). If you replace all 90 frames (at 30fps for 3 seconds) with that one composited static image, the creator freezes — uncanny valley.[^6_6][^6_7][^6_8]

The correct two-step:

### Step 1: Nano Banana generates the popup image

```python
POPUP_IMAGEN_PROMPT = """
Generate a {style} image showing: {concept}.

Technical requirements:
- Aspect ratio: 4:3 (to match the screenshot pop-up style)
- White or transparent background
- NO text, NO captions, NO watermarks inside the image
- Clean, high contrast, immediately recognizable subject
- Style: {tone} — if "funny": exaggerated cartoon reaction face;
          if "informative": clean flat diagram with arrows;
          if "aesthetic": photo-realistic lifestyle image;
          if "proof": photo-realistic product/place image

The image will be placed as a rectangular pop-up in the corner of a vertical
9:16 video, approximately {scale_pct}% of frame width. Design for this size.
"""
```


### Step 2: Pass the popup PNG + a reference frame to Nano Banana for style matching

This is where the user's idea becomes powerful. Instead of just a flat FFmpeg overlay:

```python
FRAME_EDIT_PROMPT = """
I have a video frame (the background) and a popup image (to be added as an overlay).

Edit the background frame to include the popup image as a natural-looking
TikTok-style pop-up in the {position} of the frame.

Style rules for the pop-up:
- Rectangular, no rounded corners
- Slight drop shadow (4px offset, 60% opacity black)
- 2px white border/stroke around the image
- The popup should appear to sit ON TOP of everything in the frame
- Scale: the popup should be {scale_pct}% of the frame width

Do NOT modify anything else in the frame. Only add the popup.
Return the composited image.
"""
```

This gives you **one composite PNG** that shows exactly how the popup looks on top of a real frame — with the correct shadow, border, and scale.[^6_7][^6_8][^6_6]

### Step 3: FFmpeg overlays it dynamically over the live video

Now you take the popup PNG (not the composited frame — just the popup itself with its shadow/border pre-applied by Nano Banana) and use FFmpeg's `overlay` filter:

```bash
# Overlay popup1.png at bottom-left, popup2.png at bottom-right
# Scale each to 45% of frame width (for a 1080px wide video = 486px)
# Fade in over 0.15s, stay, fade out over 0.15s
ffmpeg -y \
  -i captioned.mp4 \
  -i popup1_with_shadow.png \
  -i popup2_with_shadow.png \
  -filter_complex "
    [1:v]scale=486:-1[p1];
    [2:v]scale=486:-1[p2];
    [0:v][p1]overlay=
      x=20:y=H-h-20:
      enable='between(t,{p1_start},{p1_end})':
      format=auto[with_p1];
    [with_p1][p2]overlay=
      x=W-w-20:y=H-h-20:
      enable='between(t,{p2_start},{p2_end})':
      format=auto[out]
  " \
  -map "[out]" -map "0:a" -c:a copy output.mp4
```

The key insight: **Nano Banana's job is to generate a beautiful, properly-styled popup PNG** (with shadow, border, correct aesthetic). **FFmpeg's job is to composite it over the live moving video.** Each tool does what it does best.[^6_9][^6_10][^6_11][^6_12]

***

## Q4: FFmpeg in Daytona — Full Wiring

### The FFmpeg-not-installed problem

By default, Daytona's sandbox snapshot does **not include FFmpeg** — this is confirmed in a recent GitHub issue.  Installing it fresh each sandbox via `apt-get install ffmpeg` takes 45–90 seconds and wastes your Daytona credits on setup time.[^6_13]

**Solution: a custom Daytona Snapshot with FFmpeg pre-baked.**[^6_14]

```dockerfile
# Dockerfile for ViralFix Daytona Snapshot
FROM daytonaio/sandbox:latest

# Pre-install FFmpeg and all Python AI deps
RUN apt-get update && \
    apt-get install -y ffmpeg libsm6 libxext6 --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    google-generativeai>=0.7.0 \
    google-cloud-storage>=2.16.0 \
    httpx pillow

# Pre-warm the Google genai import (saves ~0.3s on first use)
RUN python -c "import google.generativeai; from google.cloud import storage"
```

Build, push to Google Artifact Registry, register as a Daytona Snapshot. Every subsequent sandbox creation skips all setup entirely — cold start drops from ~90s to **~3s**.[^6_15][^6_14]

***

### The single-pass FFmpeg render strategy

The biggest latency win is running **one FFmpeg command** that chains all operations in a single encode pass instead of six sequential passes. Each intermediate re-encode costs ~8–15 seconds and degrades quality:

```python
def build_ffmpeg_command(
    input_video: str,
    srt_path: str,
    popup_images: list,      # [{"path": "...", "start": 12.3, "end": 15.1, "x": "W-w-20", "y": "H-h-20"}]
    zoom_moments: list,      # [{"start": 5.0, "end": 8.0, "factor": 1.12}]
    slowmo_segments: list,   # [{"start": 10.1, "end": 10.6, "factor": 0.65}]
    music_path: str,
    output_path: str,
    fps: int = 30,
) -> list:

    inputs = ["-i", input_video]
    for p in popup_images:
        inputs += ["-i", p["path"]]
    inputs += ["-i", music_path]

    # Build zoom expression from all zoom_moments
    zoom_expr_parts = []
    for z in zoom_moments:
        sf = int(z["start"] * fps)
        ef = int(z["end"]   * fps)
        zoom_expr_parts.append(f"between(n,{sf},{ef})*{z['factor']-1:.3f}")
    zoom_expr = "1+" + "+".join(zoom_expr_parts) if zoom_expr_parts else "1"

    # Chain: zoom → captions → popups
    filter_parts = [
        f"[0:v]zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:fps={fps},scale=1080:1920[zoomed]",
        f"[zoomed]subtitles={srt_path}:force_style='FontName=Arial Black,FontSize=22,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,MarginV=80'[capped]",
    ]

    last = "[capped]"
    for i, p in enumerate(popup_images):
        lbl = f"[ov{i}]"
        filter_parts.append(
            f"[{i+1}:v]scale=486:-1[sc{i}];"
            f"{last}[sc{i}]overlay=x={p['x']}:y={p['y']}:"
            f"enable='between(t,{p['start']},{p['end']})'{lbl}"
        )
        last = lbl

    music_idx = len(popup_images) + 1
    filter_parts.append(
        f"[0:a]volume=1.0[orig];"
        f"[{music_idx}:a]volume=0.126[bg];"
        f"[orig][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )

    # Final map: last video label → audio output
    filter_str = ";".join(filter_parts)
    return [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_str,
        "-map", last,
        "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",   # enables streaming before full download
        output_path,
    ]
```

**`-movflags +faststart`** is critical — it moves the MOOV atom to the front of the file so the user can start watching/downloading before it fully transfers.[^6_11][^6_9]

***

### Daytona file I/O: the exact calls

```python
from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

async def render_in_sandbox(
    video_bytes: bytes,
    popup_pngs: dict[str, bytes],   # {"popup_0.png": <bytes>, ...}
    music_bytes: bytes,
    srt_content: str,
    ffmpeg_cmd: list,
    job_id: str,
) -> bytes:

    daytona = Daytona(DaytonaConfig(api_key=DAYTONA_API_KEY))

    # Spawn from pre-built snapshot (ffmpeg + deps pre-installed)
    sandbox = daytona.create(CreateSandboxFromSnapshotParams(
        snapshot="viralfix-ffmpeg-snapshot",
        auto_stop_interval=5,        # stop after 5 min inactivity → saves credits
        auto_delete_interval=10,     # auto-delete after 10 min stopped
    ))

    # Upload all inputs in parallel
    files_to_upload = {
        "/workspace/input.mp4":       video_bytes,
        "/workspace/captions.srt":    srt_content.encode(),
        "/workspace/music.wav":       music_bytes,
    }
    for name, data in popup_pngs.items():
        files_to_upload[f"/workspace/{name}"] = data

    sandbox.filesystem.upload_files([          # bulk upload in one call
        {"path": path, "content": content}
        for path, content in files_to_upload.items()
    ])

    # Run single-pass FFmpeg command
    cmd_str = " ".join(f'"{c}"' if " " in c else c for c in ffmpeg_cmd)
    result  = sandbox.process.exec(cmd_str, timeout=300)

    if result.exit_code != 0:
        daytona.delete(sandbox)
        raise RuntimeError(f"FFmpeg failed: {result.result[-800:]}")

    # Download output — returns raw bytes
    output_bytes = sandbox.filesystem.download_file("/workspace/output.mp4")

    # Destroy sandbox immediately
    daytona.delete(sandbox)

    return output_bytes
```

After this function returns, `output_bytes` is the finished MP4. You then either:

- Upload directly to Google Cloud Storage → return a public URL to Convex
- Or stream it directly to the user via a FastAPI `StreamingResponse`

***

### Full latency breakdown for a 60s video

| Step | Operation | Time |
| :-- | :-- | :-- |
| Sandbox create (custom snapshot) | Daytona boot | ~3s |
| File uploads (video + PNGs + music) | Daytona filesystem.upload_files | ~4s |
| 4x Gemini 3.1 calls in parallel | asyncio.gather | ~12s |
| Nano Banana PNG generation (3 popups) | asyncio.gather | ~8s |
| Lyria 3 music (2 segments + stitch) | sequential + ffmpeg crossfade | ~15s |
| Single-pass FFmpeg render (60s video) | libx264 fast preset | ~18s |
| Daytona file download + GCS upload | network transfer | ~5s |
| **Total** |  | **~65s per video** |

Gemini + Nano Banana + Lyria run **while the video is still downloading**, so the wall-clock time from "video queued" to "edited file ready" is roughly 65 seconds for a 60-second Reel — faster than a human editor's first cup of coffee.

[^6_1]: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-1-pro

[^6_2]: https://www.datacamp.com/blog/gemini-3-1

[^6_3]: https://decodo.com/blog/scrape-tiktok

[^6_4]: https://hasdata.com/blog/tiktok-scraping-python

[^6_5]: https://dev.to/dnature/web-scraping-scrape-data-from-your-instagram-page-with-nodejs-playwright-and-firebase-29d1

[^6_6]: https://evolink.ai/blog/nano-banana-pro-api-review-pricing-features-integration

[^6_7]: https://www.cometapi.com/how-to-use-the-nano-banana-pro-api/

[^6_8]: https://nanobananaapi.ai

[^6_9]: https://stackoverflow.com/questions/48351855/ffmpeg-how-to-make-timed-picture-overlay-changes-that-repeat

[^6_10]: https://www.youtube.com/watch?v=dGFXAk-KClA

[^6_11]: https://www.bannerbear.com/blog/how-to-add-a-png-overlay-on-a-video-using-ffmpeg/

[^6_12]: https://creatomate.com/blog/how-to-add-a-transparent-overlay-on-a-video-using-ffmpeg

[^6_13]: https://github.com/daytonaio/daytona/issues/3921

[^6_14]: https://www.daytona.io/docs/en/snapshots/

[^6_15]: https://pypi.org/project/daytona_sdk/

[^6_16]: Screenshot-2026-03-05-at-11.33.22-AM.jpg

[^6_17]: https://www.daytona.io/docs/en/file-system-operations/

[^6_18]: https://google.github.io/adk-docs/integrations/daytona/

[^6_19]: https://github.com/daytonaio/daytona/issues/3413

[^6_20]: https://reference.langchain.com/javascript/langchain-daytona/index/DaytonaSandboxOptions

[^6_21]: https://www.daytona.io/dotfiles/run-ai-generated-code-safely-with-daytona-sandboxes-part-1

[^6_22]: https://crawlee.dev/blog/scrape-using-jsdom

[^6_23]: https://mastra.ai/reference/workspace/daytona-sandbox

[^6_24]: https://www.daytona.io/docs/en/tools/api/

[^6_25]: https://stackoverflow.com/questions/41950873/python-scrape-views-count-from-instagram-video-load-to-json-format

[^6_26]: https://docs.openhands.dev/openhands/usage/v0/runtimes/V0_daytona

[^6_27]: https://www.youtube.com/watch?v=iu6knmWFFzk

[^6_28]: https://www.reddit.com/r/ffmpeg/comments/rvzmw8/adding_overlay_to_video_which_fades_in_and_out/

[^6_29]: https://dev.to/oskarahl/ffmpeg-overlay-a-video-on-a-video-after-x-seconds-4fc9

[^6_30]: https://www.reddit.com/r/ffmpeg/comments/18dpwx4/png_overlay_fade_in_out_start_end_of_video/

[^6_31]: https://www.octoparse.com/blog/tiktok-video-scraper

[^6_32]: https://www.abyssale.com/blog/ffmpeg-overlay-image-on-video
