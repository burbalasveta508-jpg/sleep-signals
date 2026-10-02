#!/usr/bin/env python3
"""Сигналы для ниши «лекции для сна».

Команды:
  Каналы для отслеживания перечислены в channels.txt (по одному в строке).
  Если файл пустой — отслеживаются каналы, найденные через discover.

  python signals.py add @handle https://youtube.com/@channel ...   дописать каналы в channels.txt
  python signals.py discover               найти каналы ниши через поиск YouTube (~800 ед. квоты)
  python signals.py update                 ежедневное обновление: статистика, сигналы, перевод,
                                           лица на обложках, рынки и сразу отчёт
  python signals.py report                 пересобрать отчёт без обращения к YouTube
  python signals.py stats                  какие каналы отслеживаются и что в базе
  python signals.py demo                   демо-база и отчёт без API-ключа
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse

import store
from yt import QuotaExceeded, YouTube

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("db_path", "report_path"):
        if not os.path.isabs(cfg[k]):
            cfg[k] = os.path.join(HERE, cfg[k])
    return cfg


def chunks(xs, n=50):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def parse_ref(s):
    s = urllib.parse.unquote(s.strip())
    if not s or s.startswith("#"):
        return None
    m = re.search(r"(UC[\w-]{22})", s)
    if m:
        return ("id", m.group(1))
    m = re.search(r"@([\w.\-·]+)", s)
    if m:
        return ("handle", "@" + m.group(1))
    return None


def api(cfg):
    return YouTube(os.environ.get("YT_API_KEY"), cfg["quota_budget_per_run"])


# ---------------------------------------------------------------- add / import
def cmd_add_to_list(refs):
    path = os.path.join(HERE, "channels.txt")
    have = {v.lower() for _, v in read_list(path)}
    added = []
    for r in refs:
        p = parse_ref(r)
        if p and p[1].lower() not in have:
            have.add(p[1].lower())
            added.append(r.strip())
    with open(path, "a", encoding="utf-8") as f:
        for r in added:
            f.write(r + "\n")
    print(f"Добавлено в channels.txt: {len(added)}. Они начнут отслеживаться при следующем update.")


def cmd_add(cfg, refs, source="manual"):
    db, yt, now = store.connect(cfg["db_path"]), api(cfg), int(time.time())
    ids, added = [], 0
    for r in refs:
        p = parse_ref(r)
        if not p:
            if r.strip() and not r.strip().startswith("#"):
                print(f"  ? не понял: {r.strip()}")
            continue
        if p[0] == "id":
            ids.append(p[1])
        else:
            it = yt.channel_by_handle(p[1])
            if it:
                store.save_channel(db, it, source, now)
                added += 1
                print(f"  + {it['snippet']['title']}")
            else:
                print(f"  ! канал не найден: {p[1]}")
    for batch in chunks(ids):
        for it in yt.channels(batch):
            store.save_channel(db, it, source, now)
            added += 1
            print(f"  + {it['snippet']['title']}")
    db.commit()
    print(f"Добавлено/обновлено каналов: {added}. Квота: {yt.used} ед.")


def cmd_import(cfg, path):
    with open(path, encoding="utf-8") as f:
        cmd_add(cfg, f.readlines(), source="list")


# ---------------------------------------------------------------- discover
def cmd_discover(cfg):
    db, yt, now = store.connect(cfg["db_path"]), api(cfg), int(time.time())
    d = cfg["discover"]
    after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=d["published_within_days"])
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}
    try:
        for q in d["queries"]:
            token = None
            for _ in range(d["pages_per_query"]):
                res = yt.search_videos(q["q"], q.get("lang"), after, token)
                for it in res.get("items", []):
                    found.setdefault(it["snippet"]["channelId"], q["q"])
                token = res.get("nextPageToken")
                if not token:
                    break
            print(f"  «{q['q']}»: всего каналов найдено {len(found)}")
    except QuotaExceeded as e:
        print(f"  ! {e}; сохраняю то, что успел найти")
    existing = {r[0] for r in db.execute("SELECT id FROM channels")}
    new = [c for c in found if c not in existing]
    added = 0
    for batch in chunks(new):
        for it in yt.channels(batch):
            subs = int(it.get("statistics", {}).get("subscriberCount", 0))
            if subs >= d["min_subscribers"]:
                store.save_channel(db, it, "поиск: " + found[it["id"]], now)
                added += 1
    db.commit()
    print(f"Новых каналов добавлено: {added} (из {len(new)} новых найденных). Квота: {yt.used} ед.")


# ---------------------------------------------------------------- update
def read_list(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return [p for p in (parse_ref(l) for l in f) if p]


def sync_list(cfg, db, yt, now):
    """Если в channels.txt есть каналы — отслеживаем ровно их, остальные отключаем."""
    refs = read_list(os.path.join(HERE, "channels.txt"))
    if not refs:
        return
    known = {(r["handle"] or "").lower(): r["id"] for r in db.execute("SELECT id, handle FROM channels")}
    want, to_fetch, missing = set(), [], []
    for kind, val in refs:
        if kind == "id":
            want.add(val)
            to_fetch.append(val)
        elif val.lower() in known:
            want.add(known[val.lower()])
        else:
            it = yt.channel_by_handle(val)
            if it:
                store.save_channel(db, it, "список", now)
                want.add(it["id"])
            else:
                missing.append(val)
    existing = {r[0] for r in db.execute("SELECT id FROM channels")}
    new_ids = [i for i in to_fetch if i not in existing]
    for batch in chunks(new_ids):
        for it in yt.channels(batch):
            store.save_channel(db, it, "список", now)
    db.execute("UPDATE channels SET active=0")
    db.executemany("UPDATE channels SET active=1 WHERE id=?", [(i,) for i in want])
    db.commit()
    print(f"Список channels.txt: отслеживается каналов {len(want)}.")
    for m in missing:
        print(f"  ! канал не найден: {m} (проверьте ссылку)")


def refresh_niche_flags(cfg, db):
    """Пересчитать, какие видео относятся к нише, если поменялись правила в config.json."""
    rows = db.execute("SELECT id, title, duration_sec FROM videos WHERE title IS NOT NULL").fetchall()
    db.executemany("UPDATE videos SET is_niche=? WHERE id=?",
                   [(store.is_niche(r["title"], r["duration_sec"] or 0, cfg), r["id"]) for r in rows])
    db.commit()


def cmd_update(cfg):
    db, yt, now = store.connect(cfg["db_path"]), api(cfg), int(time.time())
    sync_list(cfg, db, yt, now)
    refresh_niche_flags(cfg, db)
    chans = db.execute("SELECT id, uploads_playlist, full_scan_done FROM channels WHERE active=1").fetchall()
    if not chans:
        print("Нет каналов. Впишите их в channels.txt (по одному в строке) и запустите update снова.")
        return
    new_videos = 0
    try:
        # 1. статистика каналов
        for batch in chunks([c["id"] for c in chans]):
            for it in yt.channels(batch):
                store.save_channel(db, it, "manual", now)
        db.commit()
        # 2. новые видео (первый раз — глубже, потом только свежая страница)
        for c in chans:
            pages = cfg["daily_pages_per_channel"] if c["full_scan_done"] else cfg["initial_pages_per_channel"]
            for vid in yt.playlist_video_ids(c["uploads_playlist"], pages):
                cur = db.execute("INSERT OR IGNORE INTO videos(id, channel_id, first_seen_ts) VALUES(?,?,?)",
                                 (vid, c["id"], now))
                new_videos += cur.rowcount
            db.execute("UPDATE channels SET full_scan_done=1 WHERE id=?", (c["id"],))
        db.commit()
        # 3. снимок статистики видео
        where = "(v.is_niche=1 OR v.title IS NULL)" if cfg["track_only_niche"] else "1=1"
        vids = [r[0] for r in db.execute(
            f"""SELECT v.id FROM videos v JOIN channels c ON c.id=v.channel_id
                WHERE c.active=1 AND v.gone=0 AND {where}""")]
        seen = set()
        for batch in chunks(vids):
            for it in yt.videos(batch):
                store.save_video(db, it, cfg, now)
                seen.add(it["id"])
            for vid in set(batch) - seen:
                db.execute("UPDATE videos SET gone=1 WHERE id=?", (vid,))
            db.commit()
        print(f"Каналов: {len(chans)}, новых видео: {new_videos}, снимков видео: {len(seen)}. "
              f"Квота: {yt.used} ед.")
    except QuotaExceeded as e:
        db.commit()
        print(f"! Остановлено: {e}. Уже собранное сохранено. Квота: {yt.used} ед.")

    # чистка: старые ежедневные замеры для сигналов не нужны
    keep = cfg.get("keep_stats_days", 120)
    old = db.execute("DELETE FROM video_stats WHERE ts < ?", (now - keep * 86400,)).rowcount
    db.execute("DELETE FROM channel_stats WHERE ts < ?", (now - keep * 86400,))
    db.execute("DELETE FROM editions WHERE date < ?",
               (time.strftime("%Y-%m-%d", time.localtime(now - keep * 86400)),))
    db.commit()
    if old:
        db.execute("VACUUM")

    # выпуск дня → перевод, лица, рынки → отчёт
    import report
    date, vids, prio = report.build_edition(db, cfg)
    if date:
        report.enrich(db, cfg, prio, yt)
        path = report.render(db, cfg, cfg["report_path"], vids)
        print(f"Отчёт: {path}. Квота всего: {yt.used} ед.")


# ---------------------------------------------------------------- report / stats
def cmd_report(cfg, db_path=None, out=None, now=None):
    import report
    db = store.connect(db_path or cfg["db_path"])
    path = report.build(db, cfg, out or cfg["report_path"], now=now)
    print(f"Отчёт: {path}")


def cmd_stats(cfg):
    db = store.connect(cfg["db_path"])
    q = lambda s: db.execute(s).fetchone()[0]
    print("Отслеживаемые каналы:")
    for r in db.execute("SELECT title, handle FROM channels WHERE active=1 ORDER BY title"):
        print(f"  {r['title']}  {r['handle'] or ''}")
    print(f"Каналов активно: {q('SELECT COUNT(*) FROM channels WHERE active=1')} из {q('SELECT COUNT(*) FROM channels')}, "
          f"видео: {q('SELECT COUNT(*) FROM videos')} (ниша: {q('SELECT COUNT(*) FROM videos WHERE is_niche=1')}), "
          f"снимков: {q('SELECT COUNT(*) FROM video_stats')}")
    last = q("SELECT MAX(ts) FROM video_stats")
    if last:
        print("Последнее обновление:", dt.datetime.fromtimestamp(last).strftime("%d.%m.%Y %H:%M"))


def main():
    cfg = load_config()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "add":
        cmd_add_to_list(args[1:])
    elif cmd == "import":
        cmd_import(cfg, args[1] if len(args) > 1 else os.path.join(HERE, "channels.txt"))
    elif cmd == "discover":
        cmd_discover(cfg)
    elif cmd == "update":
        cmd_update(cfg)
    elif cmd == "report":
        cmd_report(cfg)
    elif cmd == "stats":
        cmd_stats(cfg)
    elif cmd == "demo":
        import demo
        import report
        db_path = os.path.join(HERE, "data", "demo.db")
        demo.build(cfg, db_path)
        db = store.connect(db_path)
        path = report.render(db, cfg, os.path.join(HERE, "report", "demo.html"))
        print(f"Отчёт: {path}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
