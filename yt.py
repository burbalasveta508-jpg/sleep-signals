"""Минимальный клиент YouTube Data API v3 (только стандартная библиотека Python)."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.googleapis.com/youtube/v3/"
# Стоимость запросов в единицах квоты (бесплатно 10 000 в сутки)
COST = {"search": 100, "channels": 1, "playlistItems": 1, "videos": 1}


class QuotaExceeded(Exception):
    pass


class YouTube:
    def __init__(self, key, budget=9000):
        if not key:
            raise SystemExit("Нет API-ключа. Задайте переменную окружения YT_API_KEY (см. README).")
        self.key = key
        self.budget = budget
        self.used = 0

    def _get(self, endpoint, **params):
        cost = COST[endpoint]
        if self.used + cost > self.budget:
            raise QuotaExceeded(f"достигнут лимит на запуск ({self.budget} ед.)")
        params = {k: v for k, v in params.items() if v is not None}
        params["key"] = self.key
        url = API + endpoint + "?" + urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    self.used += cost
                    return json.load(r)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                self.used += cost
                if e.code == 403 and "quota" in body.lower():
                    raise QuotaExceeded("суточная квота YouTube API исчерпана")
                if e.code == 404:
                    return {"items": []}
                if e.code >= 500 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"{endpoint}: HTTP {e.code}: {body[:300]}")
            except urllib.error.URLError:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise

    # --- каналы ---
    def channels(self, ids):
        return self._get("channels", part="snippet,statistics,contentDetails",
                         id=",".join(ids[:50]), maxResults=50).get("items", [])

    def channel_by_handle(self, handle):
        items = self._get("channels", part="snippet,statistics,contentDetails",
                          forHandle=handle).get("items", [])
        return items[0] if items else None

    # --- видео ---
    def playlist_video_ids(self, playlist_id, pages=1):
        ids, token = [], None
        for _ in range(pages):
            res = self._get("playlistItems", part="contentDetails", playlistId=playlist_id,
                            maxResults=50, pageToken=token)
            ids += [it["contentDetails"]["videoId"] for it in res.get("items", [])]
            token = res.get("nextPageToken")
            if not token:
                break
        return ids

    def videos(self, ids):
        return self._get("videos", part="snippet,contentDetails,statistics",
                         id=",".join(ids[:50]), maxResults=50).get("items", [])

    # --- поиск (дорого: 100 ед.) ---
    def search_videos(self, q, lang=None, published_after=None, page_token=None,
                      order="viewCount", duration="long", max_results=50):
        return self._get("search", part="snippet", type="video", q=q, maxResults=max_results,
                         order=order, videoDuration=duration,
                         relevanceLanguage=lang, publishedAfter=published_after,
                         pageToken=page_token)

    def remaining(self):
        return self.budget - self.used


def parse_duration(s):
    """ISO 8601 (PT1H2M3S / P1DT2H) -> секунды."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se
