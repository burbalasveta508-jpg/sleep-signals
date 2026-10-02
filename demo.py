"""Демо-база: вымышленные каналы и 40 дней ежедневных замеров, чтобы посмотреть отчёт без API."""
import math
import os
import random
import time

import json

import report
import store
import translate

D = 86400
CHANNELS = [
    ("Сонная история", "RU", "ru", "{t} — лекция для сна"),
    ("Лекции перед сном", "RU", "ru", "{t}: спокойная лекция перед сном"),
    ("Засыпаем с историей", "RU", "ru", "{t} | история для сна, чтобы быстро заснуть"),
    ("Boring History for Sleep", "US", "en", "{t} | Boring History for Sleep"),
    ("Sleepy Scholar", "GB", "en", "{t} — 3 Hours of Calm Facts to Fall Asleep"),
    ("History Before Bed", "US", "en", "Fall Asleep to {t} | Bedtime History"),
    ("Geschichte zum Einschlafen", "DE", "de", "{t} | Geschichte zum Einschlafen"),
    ("Historia para Dormir", "ES", "es", "{t} | Historia para dormir"),
    ("Histoires du Sommeil", "FR", "fr", "{t} — histoire pour trouver le sommeil"),
    ("Wykłady do snu", "PL", "pl", "{t} | wykład do snu"),
]
TOPICS = ["Древний Рим", "Византия", "Викинги", "Средневековая Англия", "Египет фараонов",
          "Империя инков", "Великий шёлковый путь", "Чума в Европе", "Эпоха Мэйдзи",
          "Крестовые походы", "Монгольская империя", "Карфаген", "Ацтеки", "Древний Китай",
          "Пираты Карибского моря", "Тамплиеры", "Шумер", "Спарта", "Эпоха Возрождения", "Космос",
          "Глубины океана", "Динозавры", "Древняя Греция", "Александр Македонский", "Великая Армада"]


def build(cfg, path):
    if os.path.exists(path):
        os.remove(path)
    db = store.connect(path)
    rnd = random.Random(42)
    now = (int(time.time()) // D) * D + 3 * 3600          # «сегодня, 03:00 UTC»
    snaps = [now - k * D for k in range(40, -1, -1)]

    for ci, (name, country, lang, tpl) in enumerate(CHANNELS):
        cid = "UC" + f"demo{ci:02d}".ljust(22, "x")
        subs0 = rnd.randint(20_000, 900_000)
        db.execute("INSERT INTO channels(id,title,handle,country,lang,uploads_playlist,source,added_ts,full_scan_done) "
                   "VALUES(?,?,?,?,?,?,?,?,1)",
                   (cid, name, "@" + name.lower().replace(" ", ""), country, lang, "UU", "демо", snaps[0]))
        videos = []
        for vi in range(rnd.randint(25, 45)):
            pub = now - int((rnd.uniform(1, 60) if rnd.random() < 0.25 else rnd.uniform(1, 700)) * D)
            short = rnd.random() < 0.1
            title = (f"{rnd.choice(TOPICS)}: короткий обзор" if short
                     else tpl.format(t=rnd.choice(TOPICS)))
            dur = rnd.randint(8, 20) * 60 if short else rnd.randint(60, 240) * 60
            scale = subs0 * rnd.uniform(0.05, 0.6)            # «потолок» видео
            tail = scale * rnd.uniform(0.0005, 0.002)         # долгий хвост просмотров в день
            kind = "normal"
            age_now = (now - pub) / D
            r = rnd.random()
            if age_now > 60 and r < 0.08:
                kind = "activation"
            elif age_now < 45 and r < 0.2:
                kind = "outlier"
                scale *= rnd.uniform(4, 9)
            vid = f"demo{ci:02d}v{vi:03d}"
            videos.append((vid, pub, scale, tail, kind))
            db.execute("INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,0)",
                       (vid, cid, title, pub, dur, lang, store.is_niche(title, dur, cfg), snaps[0]))

        act_start = now - rnd.randint(3, 6) * D
        for ts in snaps:
            total = 0
            for vid, pub, scale, tail, kind in videos:
                if ts < pub:
                    continue
                age = (ts - pub) / D
                views = scale * (1 - math.exp(-age / 12)) + tail * age
                if kind == "activation" and ts > act_start:
                    boost_days = (ts - act_start) / D
                    views += tail * 12 * boost_days ** 1.3     # рекомендации «проснулись»
                views = int(views)
                total += views
                db.execute("INSERT OR REPLACE INTO video_stats VALUES(?,?,?,?,?)",
                           (vid, ts, views, views // 40, views // 400))
            subs = int(subs0 * (1 + 0.0015 * (ts - snaps[0]) / D))
            db.execute("INSERT INTO channel_stats VALUES(?,?,?,?,?)", (cid, ts, subs, total, len(videos)))
    db.commit()

    # выпуски за последние 7 дней
    for k in range(6, -1, -1):
        report.build_edition(db, cfg, now - k * D, log=lambda *a: None)
    ids = [r[0] for r in db.execute("SELECT DISTINCT video_id FROM editions")]
    langs = cfg["market"]["langs"]
    for vid in ids:
        v = db.execute("SELECT title, lang FROM videos WHERE id=?", (vid,)).fetchone()
        if v["lang"] != "ru":
            topic = v["title"].split(" | ")[0].split(" — ")[0].replace("Fall Asleep to ", "")
            db.execute("INSERT OR REPLACE INTO translations VALUES(?,?,?)",
                       (translate.key(v["title"], "ru"), f"{topic}: история для сна", v["lang"]))
        db.execute("INSERT OR REPLACE INTO faces VALUES(?,?,?)", (vid, int(rnd.random() < 0.35), now))
    vid_ch = {r[0]: r[1] for r in db.execute("SELECT id, channel_id FROM videos")}
    sig = [r[0] for r in db.execute(
        "SELECT video_id FROM editions WHERE (act=1 OR exc=1) ORDER BY vph DESC LIMIT 25")]
    for vid in sig:
        v = db.execute("SELECT title, lang FROM videos WHERE id=?", (vid,)).fetchone()
        for lang in langs:
            res = []
            for j in range(rnd.randint(1 if lang == v["lang"] else 0, 8)):
                sim = round(rnd.choice([0.1, 0.2, 0.25, 0.4, 0.5, 0.7]), 2)
                views = rnd.randint(500, 900_000)
                pub = now - rnd.randint(5, 900) * D
                own = j == 0 and lang == v["lang"]
                res.append({"id": f"mk{vid}{lang}{j}", "t": f"Похожее видео #{j + 1} ({lang.upper()})",
                            "ch": "свой канал" if own else f"Channel {lang.upper()} {j + 1}",
                            "cid": vid_ch[vid] if own else "UCother", "pub": pub, "views": views,
                            "vph": round(views / ((now - pub) / 3600), 1), "sim": sim})
            res.sort(key=lambda r: -r["sim"])
            best = res[0]["sim"] if res else 0
            st = "busy" if best >= 0.6 else "partial" if best >= 0.35 else "free"
            db.execute("INSERT OR REPLACE INTO market VALUES(?,?,?,?,?,?,?)",
                       (vid, lang, now, f"{v['title']} ({lang})", st, best, json.dumps(res, ensure_ascii=False)))
    db.commit()
    return path, now
