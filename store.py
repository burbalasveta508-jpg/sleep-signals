"""База данных SQLite: каналы, видео и ежедневные снимки статистики."""
import datetime as dt
import os
import sqlite3

from yt import parse_duration

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels(
  id TEXT PRIMARY KEY, title TEXT, handle TEXT, country TEXT, lang TEXT,
  uploads_playlist TEXT, source TEXT, added_ts INTEGER,
  active INTEGER DEFAULT 1, full_scan_done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_stats(
  channel_id TEXT, ts INTEGER, subscribers INTEGER, views INTEGER, videos INTEGER,
  PRIMARY KEY(channel_id, ts));
CREATE TABLE IF NOT EXISTS videos(
  id TEXT PRIMARY KEY, channel_id TEXT, title TEXT, published_ts INTEGER,
  duration_sec INTEGER, lang TEXT, is_niche INTEGER DEFAULT 0,
  first_seen_ts INTEGER, gone INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS video_stats(
  video_id TEXT, ts INTEGER, views INTEGER, likes INTEGER, comments INTEGER,
  PRIMARY KEY(video_id, ts));
CREATE TABLE IF NOT EXISTS editions(
  date TEXT, video_id TEXT, act INTEGER, exc INTEGER, ever INTEGER, top INTEGER,
  vph REAL, views INTEGER, gx REAL, ex REAL, base REAL, est INTEGER,
  PRIMARY KEY(date, video_id));
CREATE TABLE IF NOT EXISTS translations(key TEXT PRIMARY KEY, text TEXT, src TEXT);
CREATE TABLE IF NOT EXISTS faces(video_id TEXT PRIMARY KEY, has_face INTEGER, checked_ts INTEGER);
CREATE TABLE IF NOT EXISTS market(
  video_id TEXT, lang TEXT, checked_ts INTEGER, query TEXT, status TEXT, best_sim REAL, results TEXT,
  PRIMARY KEY(video_id, lang));
CREATE INDEX IF NOT EXISTS idx_vs_ts ON video_stats(ts);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
"""


def connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute("PRAGMA table_info(editions)")}
    for col, typ in (("tx", "REAL"), ("trend", "TEXT"), ("bsrc", "TEXT")):
        if col not in cols:
            db.execute(f"ALTER TABLE editions ADD COLUMN {col} {typ}")
    db.commit()
    return db


def iso_to_ts(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def is_niche(title, duration_sec, cfg):
    n = cfg["niche"]
    if duration_sec < n["min_duration_min"] * 60:
        return 0
    if not n.get("require_keywords", True):
        return 1
    t = (title or "").lower()
    return int(any(k in t for k in n["title_keywords"]))


def save_channel(db, it, source, now):
    sn, st = it.get("snippet", {}), it.get("statistics", {})
    uploads = (it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
               or "UU" + it["id"][2:])
    db.execute("""INSERT INTO channels(id,title,handle,country,lang,uploads_playlist,source,added_ts)
                  VALUES(?,?,?,?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET title=excluded.title, handle=excluded.handle,
                  country=excluded.country, lang=excluded.lang,
                  uploads_playlist=excluded.uploads_playlist""",
               (it["id"], sn.get("title"), sn.get("customUrl"), sn.get("country"),
                sn.get("defaultLanguage"), uploads, source, now))
    if st:
        subs = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))
        db.execute("INSERT OR REPLACE INTO channel_stats VALUES(?,?,?,?,?)",
                   (it["id"], now, subs, int(st.get("viewCount", 0)), int(st.get("videoCount", 0))))


def save_video(db, it, cfg, now):
    sn, st, cd = it["snippet"], it.get("statistics", {}), it.get("contentDetails", {})
    dur = parse_duration(cd.get("duration"))
    title = sn.get("title", "")
    lang = sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or ""
    db.execute("""INSERT INTO videos(id,channel_id,title,published_ts,duration_sec,lang,is_niche,first_seen_ts)
                  VALUES(?,?,?,?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id, title=excluded.title,
                  published_ts=excluded.published_ts, duration_sec=excluded.duration_sec,
                  lang=excluded.lang, is_niche=excluded.is_niche, gone=0""",
               (it["id"], sn["channelId"], title, iso_to_ts(sn["publishedAt"]), dur, lang,
                is_niche(title, dur, cfg), now))
    if "viewCount" in st:
        db.execute("INSERT OR REPLACE INTO video_stats VALUES(?,?,?,?,?)",
                   (it["id"], now, int(st["viewCount"]), int(st.get("likeCount", 0)),
                    int(st.get("commentCount", 0))))
