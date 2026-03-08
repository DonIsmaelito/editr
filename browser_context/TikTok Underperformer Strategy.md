# TikTok Underperformer Detection & Re-Edit Strategy
### Built from live analysis of @malharpandy | Browser-Use Agent Flow

---

## 📊 LIVE RESULTS — @malharpandy

| Views | Video ID | Title | No-Edit Score | File |
|-------|----------|-------|--------------|------|
| 1,000 | 7577186287996783927 | nov 26: rate cuts during the holiday season | 5/5 ⭐ | 7577186287996783927_1000views.mp4 |
| 1,392 | 7578682882118536503 | nov 30: hottest fintech startups | 4/5 | 7578682882118536503_1392views.mp4 |
| 1,936 | 7576824262741265678 | nov 25: Nvidia vs Michael Burry | 4/5 | 7576824262741265678_1936views.mp4 |
| 2,975 | 7580868499980012814 | Dec 6: Ohio state vs Indiana | 4/5 | 7580868499980012814_2975views.mp4 |
| 3,010 | 7585692949061651726 | Dec 12: hottest biotech startups | 4/5 | 7585692949061651726_3010views.mp4 |
| 3,579 | 7587202736945450253 | New Grad Income Breakdown | 3/5 | 7587202736945450253_3579views.mp4 |
| 4,318 | 7588916858321931575 | dec. 2025: what will pop the AI bubble? | 3/5 | 7588916858321931575_4318views.mp4 |
| 7,410 | 7610944032168070413 | Little update | 3/5 | 7610944032168070413_7410views.mp4 |

**Average views across all 11 videos: 26,802**
**Underperformer threshold: < 13,401 views (50% of average)**
**Outlier viral video: 247,600 views (skews average heavily — see note below)**

> ⚠️ **Important note on the average:** The 247.6K video is a massive outlier. A more honest benchmark is the **median** (3,010 views) or a **trimmed mean** (dropping the top outlier = ~4,400 views). For a small creator, use BOTH thresholds and flag anything below the median.

---

## 🤖 BROWSER-USE AGENT FLOW

### STEP 0 — Input
```
username = "malharpandy"   # passed in by user or hardcoded
```

---

### STEP 1 — Navigate to Profile & Scrape All Videos

**Agent instructions:**
1. Navigate to `https://www.tiktok.com/@{username}`
2. Wait for the page to fully load (2–3s)
3. Dismiss any popups, cookie banners, or login modals (look for "X", "Close", "Skip")
4. Use `browser.get_html()` and BeautifulSoup to find all elements with `data-e2e="user-post-item"`
5. For each item, extract:
   - `href` from the `<a>` tag → this is the full video URL
   - View count text from `[data-e2e="video-views"]` or the `<strong>` inside the item
6. Parse view count text into integers:
   - "247.6K" → 247,600
   - "16.3K" → 16,300
   - "1.2M" → 1,200,000
7. **If fewer than expected videos load:** Scroll down to trigger lazy loading, re-scrape until count stabilizes
8. Store as list: `[{"url": "...", "views": 1000, "video_id": "..."}]`

**Code pattern:**
```python
def parse_views(v):
    v = v.replace(',', '')
    if 'K' in v: return float(v.replace('K','')) * 1000
    if 'M' in v: return float(v.replace('M','')) * 1_000_000
    return float(v)
```

---

### STEP 2 — Calculate Benchmarks & Flag Underperformers

**Agent instructions:**
1. Compute the **mean** and **median** of all view counts
2. Use `threshold = median * 0.75` as the underperformer cutoff
   - Why median? It's resistant to viral outliers that skew the mean
   - 0.75x gives you a meaningful gap, not just slightly below average
3. Flag any video with `views < threshold` as an underperformer
4. Sort underperformers by views ascending (worst first)

**Code pattern:**
```python
import statistics
views_list = [v['views'] for v in videos]
median_views = statistics.median(views_list)
mean_views = statistics.mean(views_list)
threshold = median_views * 0.75

underperformers = [v for v in videos if v['views'] < threshold]
underperformers.sort(key=lambda x: x['views'])
```

---

### STEP 3 — Inspect Each Underperformer for "No Edit" Signals

For each underperforming video URL, the agent navigates to it and runs a visual + metadata analysis.

**Agent instructions:**
1. Navigate to each underperformer URL
2. Use `browser_analyze_state()` with this prompt:
   > *"Describe this TikTok video. What is the caption/description? What audio is used — is it original sound or a trending/licensed song? Are there any text overlays (manual or auto-captions), effects, filters, transitions, green screen, or CapCut edits visible? Is it a raw talking-head style?"*
3. Simultaneously, call the **tikwm.com API** to get structured metadata:
   ```
   GET https://www.tikwm.com/api/?url={video_url}
   ```
   Fields to extract:
   - `data.title` → caption + hashtags
   - `data.music_info.author` → if equals `{username}`, it's original sound
   - `data.music_info.title` → if contains "original sound", confirmed unedited audio
   - `data.duration` → very short duration (< 20s) = likely less edited
   - `data.play` → direct MP4 download URL (no watermark)

**No-Edit Scoring (5 signals, score 0–5):**

| Signal | How to Detect | Points |
|--------|--------------|--------|
| ✅ Original sound | `music_info.author == username` OR `"original sound"` in title | +1 |
| ✅ No edit hashtags | No `#edit`, `#transition`, `#greenscreen`, `#capcut` in caption | +1 |
| ✅ Talking-head keywords | Caption contains finance/news/update words (topic-specific) | +1 |
| ✅ Short/simple duration | Duration ≤ 35 seconds | +1 |
| ✅ Part of a dated series | Caption starts with a date ("nov 26:", "dec 12:") = batch content | +1 |

**Verdict:** Score ≥ 3 → `likely_no_edit = True` → **Priority target for re-editing**

---

### STEP 4 — Download the Target Videos

**Agent instructions:**
1. For each `likely_no_edit = True` video, call the tikwm API (already called in Step 3):
   ```python
   resp = requests.get(f"https://www.tikwm.com/api/?url={video_url}")
   data = resp.json()['data']
   mp4_url = data['play']  # no-watermark direct MP4
   ```
2. Download the MP4:
   ```python
   import time, requests
   time.sleep(1.5)  # tikwm free tier = 1 req/sec — MUST throttle!
   video_bytes = requests.get(mp4_url, headers={'Referer': 'https://www.tiktok.com/'}).content
   filename = f"{video_id}_{views}views.mp4"
   open(filename, 'wb').write(video_bytes)
   ```
3. ⚠️ **Rate limit:** tikwm free API allows 1 request/second. Add `time.sleep(1.5)` between each call.
4. Save all metadata to a JSON manifest:
   ```json
   {
     "video_id": "7577186287996783927",
     "views": 1000,
     "title": "nov 26: rate cuts...",
     "duration": 22,
     "no_edit_score": 5,
     "file": "7577186287996783927_1000views.mp4"
   }
   ```

**Alternative download tool if tikwm goes down:**
- `https://ssstik.io` — paste URL, scrape the download button href
- `yt-dlp` CLI tool (if running in an environment that supports it): `yt-dlp {tiktok_url} -o {output}`

---

### STEP 5 — Output Summary for the Editor

After downloading, produce a report like this:

```
🎯 UNDERPERFORMER RE-EDIT QUEUE
================================
Account: @malharpandy
Average views: 26,802 | Median: 3,010 | Threshold: 2,258

8 videos flagged as low-view + likely unedited:

#1 — 1,000 views (96% below average) — NO EDIT SCORE: 5/5
   Title: "nov 26: rate cuts during the holiday season"
   Why unedited: original sound, no effects, daily series, 22 seconds
   Edit suggestions: Add b-roll of Fed charts, add text hooks, trending finance audio
   File: 7577186287996783927_1000views.mp4

#2 — 1,392 views (95% below average) — NO EDIT SCORE: 4/5
   Title: "nov 30: hottest fintech startups"
   ...
```

---

## 🎬 WHAT TO EDIT ON THESE VIDEOS

Since all of @malharpandy's underperformers are **raw talking-head finance/news videos**, here's what to add:

| Edit Type | Impact | Tool |
|-----------|--------|------|
| **Hook text overlay** (first 1–2s) | Stops the scroll — huge retention boost | CapCut, Premiere |
| **Captions** (auto or styled) | Accessibility + watch time | CapCut auto-caption |
| **B-roll footage** (stock charts, company logos, news clips) | Breaks visual monotony | Pexels, Getty |
| **Trending background music** (low volume under voice) | Boosts FYP distribution | TikTok Sound library |
| **Jump cuts** to remove filler ("um", "uh", pauses) | Tighter pacing | Descript, Premiere |
| **End card / CTA overlay** ("Follow for more") | Boosts follower conversion | CapCut |

---

## 📁 DOWNLOADED FILES (This Session)

All 9 underperforming videos have been downloaded to:
```
/workspace/tiktok_downloads/
```

Files are named: `{video_id}_{views}views.mp4`

See `tiktok_manifest.json` for full metadata on each video.

---

## ⚡ FULL AGENT PSEUDOCODE (Copy-Paste Ready)

```python
def run_tiktok_underperformer_pipeline(username):
    # 1. Scrape profile
    browser.navigate(f"https://www.tiktok.com/@{username}")
    html = browser.get_html()
    videos = scrape_video_cards(html)  # returns [{url, views, video_id}]
    
    # 2. Find underperformers
    median = statistics.median([v['views'] for v in videos])
    threshold = median * 0.75
    underperformers = [v for v in videos if v['views'] < threshold]
    
    # 3. Inspect each + score for no-edit signals
    for video in underperformers:
        time.sleep(1.5)
        api_data = requests.get(f"https://www.tikwm.com/api/?url={video['url']}").json()['data']
        video['no_edit_score'] = score_edit_signals(api_data)
        video['mp4_url'] = api_data['play']
        video['title'] = api_data['title']
        video['duration'] = api_data['duration']
    
    # 4. Download priority targets
    targets = [v for v in underperformers if v['no_edit_score'] >= 3]
    for video in targets:
        time.sleep(1.5)
        mp4_bytes = requests.get(video['mp4_url'], headers={'Referer': 'https://www.tiktok.com/'}).content
        open(f"{video['video_id']}_{video['views']}views.mp4", 'wb').write(mp4_bytes)
    
    # 5. Save manifest
    save_json(targets, 'tiktok_manifest.json')
    return targets
```
