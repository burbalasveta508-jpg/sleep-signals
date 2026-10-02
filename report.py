"""Выпуски сигналов по датам, обогащение (перевод, лица, рынки) и HTML-отчёт."""
import datetime as dt
import json
import os
import time

import metrics
import translate
from market import base_lang, check_markets, market_status

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- выпуск (подборка) дня
def build_edition(db, cfg, now=None, log=print):
    """Посчитать сигналы на последний замер и сохранить их как выпуск этого дня."""
    last_ts = now or db.execute("SELECT MAX(ts) FROM video_stats").fetchone()[0]
    if not last_ts:
        return None, [], []
    vids = metrics.compute_videos(db, cfg, last_ts)
    date = dt.datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d")
    sig = [r for r in vids if r["activation"] or r["exceedance"] or r["evergreen"]]
    sig_ids = {r["id"] for r in sig}
    top = [r for r in sorted(vids, key=lambda r: -r["vph"]) if r["id"] not in sig_ids][:cfg["editions"]["top_by_vph"]]
    rows = sig + top
    db.execute("DELETE FROM editions WHERE date=?", (date,))
    db.executemany("""INSERT INTO editions(date, video_id, act, exc, ever, top, vph, views, gx, ex, base, est,
                                         tx, trend, bsrc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
        (date, r["id"], int(r["activation"]), int(r["exceedance"]), int(r["evergreen"]),
         int(r["id"] not in sig_ids), r["vph"], r["views"], r["growth_x"], r["exceed_x"], r["base"],
         int(r["vph_est"]), r["trend_x"], r["trend"], r["base_src"]) for r in rows])
    db.commit()
    # приоритет: активация/превышение по убыванию VPH, затем остальное
    prio = sorted(rows, key=lambda r: (not (r["activation"] or r["exceedance"]), -r["vph"]))
    log(f"Выпуск {date}: активация {sum(r['activation'] for r in vids)}, "
        f"превышение {sum(r['exceedance'] for r in vids)}, вечнозелёные {sum(r['evergreen'] for r in vids)}.")
    return date, vids, [r["id"] for r in prio]


# ---------------------------------------------------------------- перевод, лица, рынки
def enrich(db, cfg, ids, yt=None, log=print):
    ec = cfg["enrich"]
    tl = ec["translate_to"]
    tr = translate.Translator(db)
    n = 0
    for vid in ids:
        v = db.execute("SELECT title, lang FROM videos WHERE id=?", (vid,)).fetchone()
        if not v or not v["title"] or base_lang(v["lang"]) == tl:
            continue
        out, src = tr.get(v["title"], tl)
        if out is None and tr.fails >= 5:
            log("  перевод: сервис недоступен, попробую в следующий раз")
            break
        if src and not v["lang"]:
            db.execute("UPDATE videos SET lang=? WHERE id=?", (src, vid))
        n += 1
    db.commit()
    log(f"  перевод названий: {tr.calls} новых")

    if ec.get("detect_faces"):
        import faces
        detect = faces.make_detector()
        if detect is None:
            log("  лица на обложках: пропущено (установите: py -m pip install opencv-python-headless)")
        else:
            done = {r[0] for r in db.execute("SELECT video_id FROM faces")}
            k = 0
            for vid in ids:
                if vid in done:
                    continue
                f = detect(vid)
                if f is not None:
                    db.execute("INSERT OR REPLACE INTO faces VALUES(?,?,?)", (vid, f, int(time.time())))
                    k += 1
            db.commit()
            log(f"  лица на обложках: проверено {k}")

    if yt is not None:
        hot = [r[0] for r in db.execute(
            "SELECT video_id FROM editions WHERE date=(SELECT MAX(date) FROM editions) AND (act=1 OR exc=1) "
            "ORDER BY vph DESC")]
        k = check_markets(db, yt, tr, cfg, hot, log=log)
        log(f"  рынки: проверено видео {k}")
    db.commit()


# ---------------------------------------------------------------- HTML
def _categories(text, cats):
    t = " " + (text or "").lower() + " "
    return [i for i, (_, kws) in enumerate(cats) if any(k in t for k in kws)]


def _r(x, n=1):
    return None if x is None else round(x, n)


def render(db, cfg, out_path, vids=None, now=None):
    last_ts = db.execute("SELECT MAX(ts) FROM video_stats").fetchone()[0] or int(time.time())
    if vids is None:
        vids = metrics.compute_videos(db, cfg, now or last_ts)
    cur = {r["id"]: r for r in vids}
    chans = metrics.compute_channels(db, cfg, vids, now or last_ts)

    dates = [r[0] for r in db.execute(
        "SELECT DISTINCT date FROM editions ORDER BY date DESC LIMIT ?", (cfg["editions"]["keep_days_in_report"],))]
    editions, ids = [], set()
    for d in dates:
        rows = []
        for r in db.execute("SELECT * FROM editions WHERE date=?", (d,)):
            ids.add(r["video_id"])
            rows.append({"id": r["video_id"], "a": r["act"], "e": r["exc"], "g": r["ever"], "t": r["top"],
                         "vph": _r(r["vph"]), "views": r["views"], "gx": _r(r["gx"], 2), "ex": _r(r["ex"], 2),
                         "base": _r(r["base"]), "est": r["est"],
                         "tx": _r(r["tx"], 2), "tr": r["trend"], "bs": r["bsrc"]})
        editions.append({"date": d, "rows": rows})

    first_sig = {r[0]: r[1] for r in db.execute(
        "SELECT video_id, MIN(date) FROM editions WHERE act=1 OR exc=1 OR ever=1 GROUP BY video_id")}
    faces = {r[0]: r[1] for r in db.execute("SELECT video_id, has_face FROM faces")}
    market = {}
    for r in db.execute("""SELECT m.*, v.channel_id AS src_cid, c.title AS src_ch FROM market m
                           LEFT JOIN videos v ON v.id=m.video_id LEFT JOIN channels c ON c.id=v.channel_id"""):
        if r["video_id"] in ids:
            res = json.loads(r["results"] or "[]")
            for z in res:   # ролики того же канала — не конкуренты
                z["same"] = int((z.get("cid") or "") == r["src_cid"] or
                                (not z.get("cid") and z.get("ch") == r["src_ch"]))
            st, best = market_status([z for z in res if not z["same"]], cfg["market"])
            market.setdefault(r["video_id"], {})[r["lang"]] = {
                "st": st, "sim": best, "q": r["query"],
                "chk": dt.datetime.fromtimestamp(r["checked_ts"]).strftime("%d.%m.%Y"), "res": res}
    cats = list(cfg.get("categories", {}).items())
    tl = cfg["enrich"]["translate_to"]
    subs = {}
    for cid, s in db.execute("SELECT channel_id, subscribers FROM channel_stats ORDER BY ts"):
        subs[cid] = s

    V = {}
    for vid in ids:
        v = db.execute("""SELECT v.*, c.title AS ch_title, c.country AS ch_country
                          FROM videos v LEFT JOIN channels c ON c.id=v.channel_id WHERE v.id=?""", (vid,)).fetchone()
        if not v:
            continue
        trans = None
        if base_lang(v["lang"]) != tl:
            trans, src = db.execute("SELECT text, src FROM translations WHERE key=?",
                                    (translate.key(v["title"], tl),)).fetchone() or (None, None)
            if src == tl:
                trans = None
        c = cur.get(vid)
        V[vid] = {
            "t": v["title"], "tr": trans, "ch": v["ch_title"], "cid": v["channel_id"],
            "lang": base_lang(v["lang"]) or None, "pub": v["published_ts"], "dur": round((v["duration_sec"] or 0) / 60),
            "subs": subs.get(v["channel_id"]), "face": faces.get(vid), "fs": first_sig.get(vid),
            "cats": _categories(f"{v['title']} {trans or ''}", cats), "m": market.get(vid, {}),
            "now": None if not c else {
                "vph": _r(c["vph"]), "views": c["views"], "gx": _r(c["growth_x"], 2), "ex": _r(c["exceed_x"], 2),
                "base": _r(c["base"]), "est": int(c["vph_est"]),
                "tx": _r(c["trend_x"], 2), "tr": c["trend"], "bs": c["base_src"]},
            "s": None if not c or not c["vph_pts"] else {
                "t": [p[0] for p in c["vph_pts"]], "v": [p[1] for p in c["vph_pts"]]},
        }
    for ch in chans:
        ch["daily"] = ch["daily"][-30:]

    data = {
        "updated": dt.datetime.fromtimestamp(last_ts).strftime("%d.%m.%Y %H:%M"),
        "editions": editions, "videos": V, "channels": chans,
        "cats": [c[0] for c in cats], "langs": cfg["market"]["langs"], "cfg": {**metrics.DEFAULTS, **cfg["metrics"]},
    }
    with open(os.path.join(HERE, "report_template.html"), encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", blob)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def build(db, cfg, out_path, now=None):
    """Выпуск + отчёт без обращения к интернету (перевод/рынки берутся из кэша)."""
    date, vids, _ = build_edition(db, cfg, now, log=lambda *a: None)
    return render(db, cfg, out_path, vids or None, now)
