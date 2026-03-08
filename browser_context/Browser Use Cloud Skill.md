---
name: tiktok-underperformer-detection
description: Scrape a TikTok profile, identify low-view underperforming videos, score them for lack of edits, and download them via the tikwm.com API.
user-invocable: true
disable-model-invocation: false
---

# TikTok Underperformer Detection & Download

Given a TikTok username, find all videos with below-average views, determine which ones are raw/unedited, and download them as MP4s for re-editing.

## Key Facts

- Profile URL: `https://www.tiktok.com/@{username}`
- Video cards use `data-e2e="user-post-item"` — each has an `<a href>` (full video URL) and a `<strong>` with view count text
- **Download API:** `https://www.tikwm.com/api/?url={video_url}` — free, returns no-watermark MP4 URL at `data.play`
- **Rate limit:** tikwm free tier = 1 req/sec → always `time.sleep(1.5)` between API calls
- MP4 download requires header: `{'Referer': 'https://www.tiktok.com/'}`

## Step 1 — Scrape Profile

```python
browser.navigate(f"https://www.tiktok.com/@{username}")
# dismiss popups, wait 2s
html = browser.get_html()
soup = BeautifulSoup(html, 'html.parser')
items = soup.select('[data-e2e="user-post-item"]')
videos = []
for item in items:
    url = item.find('a')['href']
    views_text = item.find('strong').get_text(strip=True)
    videos.append({'url': url, 'video_id': url.split('/')[-1], 'views': parse_views(views_text)})

def parse_views(v):
    v = v.replace(',', '')
    if 'K' in v: return float(v.replace('K','')) * 1000
    if 'M' in v: return float(v.replace('M','')) * 1_000_000
    return float(v)
```

## Step 2 — Find Underperformers

Use **median** (not mean) as the benchmark — one viral outlier won't poison the threshold.

```python
import statistics
median = statistics.median([v['views'] for v in videos])
threshold = median * 0.75
underperformers = sorted([v for v in videos if v['views'] < threshold], key=lambda x: x['views'])
```

## Step 3 — Score for "No Edit" Signals

Call tikwm API for each underperformer to get metadata:

```python
resp = requests.get(f"https://www.tikwm.com/api/?url={video['url']}").json()
d = resp['data']
music_author = d.get('music_info', {}).get('author', '')
title = d.get('title', '').lower()

score = 0
if music_author == username or 'original sound' in title: score += 1  # original audio
if not any(kw in title for kw in ['#edit','#transition','#greenscreen','#capcut']): score += 1
if any(kw in title for kw in ['update','breakdown','rate','startup','stock','grad','bubble']): score += 1
if d.get('duration', 99) <= 35: score += 1  # short = less edited
if any(m in title for m in ['jan ','feb ','mar ','apr ','may ','jun ','jul ','aug ','sep ','oct ','nov ','dec ']): score += 1  # dated series

video['no_edit_score'] = score  # >= 3 = priority target
video['mp4_url'] = d['play']
video['title'] = d['title']
video['duration'] = d['duration']
```

## Step 4 — Download Videos

```python
import time, os
os.makedirs('tiktok_downloads', exist_ok=True)
targets = [v for v in underperformers if v['no_edit_score'] >= 3]
for video in targets:
    time.sleep(1.5)  # REQUIRED — tikwm rate limit
    mp4 = requests.get(video['mp4_url'], headers={'Referer': 'https://www.tiktok.com/'}).content
    open(f"tiktok_downloads/{video['video_id']}_{int(video['views'])}views.mp4", 'wb').write(mp4)
```

## Visual Inspection (Optional)

For richer edit detection, navigate to each video and use `browser_analyze_state` with:
> *"Is this a raw talking-head video? What audio is used — original sound or a trending song? Are there text overlays, effects, filters, transitions, green screen, or CapCut edits visible?"*

## No-Edit Signal Scoring Table

| Signal | Detection Method | Points |
|--------|-----------------|--------|
| Original audio | `music_info.author == username` | +1 |
| No edit hashtags | No `#edit #transition #greenscreen #capcut` | +1 |
| Talking-head topic words | Finance/news/update keywords in caption | +1 |
| Short duration | `duration <= 35s` | +1 |
| Dated series format | Caption starts with month ("nov 26:") | +1 |

**Score ≥ 3 = priority re-edit target**

## What to Edit on Raw Talking-Head Videos

| Edit | Impact | Tool |
|------|--------|------|
| Hook text overlay (first 1–2s) | Stops scroll, huge retention boost | CapCut, Premiere |
| Styled captions | Accessibility + watch time | CapCut auto-caption |
| B-roll (charts, logos, clips) | Breaks visual monotony | Pexels, Getty |
| Low-volume trending music | Boosts FYP distribution | TikTok Sound library |
| Jump cuts (remove filler) | Tighter pacing | Descript, Premiere |
| End card / CTA overlay | Follower conversion | CapCut |

## Expected Output

- List of underperforming video URLs sorted by views (ascending)
- No-edit score (0–5) per video
- Downloaded MP4 files named `{video_id}_{views}views.mp4`
- JSON manifest with full metadata

## Gotchas

- TikTok HTML is JS-rendered — BeautifulSoup works on `browser.get_html()` after page load, but metadata fields like captions/audio may be empty; use tikwm API instead for structured data
- `browser_analyze_screenshot` returns 422 errors — use `browser_analyze_state` only
- Scroll down the profile page if fewer videos than expected are loaded (lazy loading)
- tikwm `code: 0` = success; any other code = failure (retry with sleep)
