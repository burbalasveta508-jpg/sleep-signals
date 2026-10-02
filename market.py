"""«Рынки и конкуренция»: есть ли похожее видео на других языках.

Название сигнала переводится на язык рынка, по переводу ищутся видео на YouTube,
сходство = доля слов перевода, найденных в названии конкурента
(служебные слова и общие слова ниши не считаются).
"""
import html
import json
import re
import time

from store import iso_to_ts
from yt import QuotaExceeded

STOP = set("""
и в во на с со по к ко о об от до из за для не ни но а же ли бы что как это этот эта эти тот то
the a an of to in on for and or with from by at is are was were be how why what who this that your you
der die das den dem des ein eine einer und oder mit von zu im in am auf für ist sind wie warum was wer
el la los las un una unos unas de del y o con por para en es son como por que qué quién
""".split())

# общие слова ниши — одинаковы почти во всех названиях, поэтому в сходстве не участвуют
NICHE = set("""
сон сна сну сном перед засыпания заснуть засыпать уснуть спать лекция лекции история истории часов часа
sleep sleeping asleep fall bedtime boring history lecture hours hour relaxing calm
schlafen einschlafen schlaf geschichte stunden entspannung langweilige
dormir sueño historia horas relajante aburrida
""".split())


def tokens(text):
    text = html.unescape(text or "").lower()
    words = re.findall(r"[^\W\d_]{3,}", text)
    return {w[:6] for w in words if w not in STOP and w not in NICHE}


def similarity(src_tokens, title):
    if not src_tokens:
        return 0.0
    return len(src_tokens & tokens(title)) / len(src_tokens)


def clean_query(title, max_words=12):
    t = re.sub(r"[|#•\[\]()«»\"“”!?:—–-]+", " ", html.unescape(title or ""))
    words = [w for w in t.split() if w.lower() not in NICHE]
    return " ".join(words[:max_words])


def base_lang(code):
    return (code or "").split("-")[0].lower()


def market_status(results, mc):
    """Статус рынка по самому похожему ролику (ролики своего же канала сюда не передаются)."""
    best = max((r["sim"] for r in results), default=0.0)
    status = ("busy" if best >= mc["busy_similarity"]
              else "partial" if best >= mc["partial_similarity"] else "free")
    return status, best


def check_markets(db, yt, tr, cfg, video_ids, now=None, log=print):
    """Проверить рынки для первых непроверенных видео из списка (в порядке приоритета)."""
    mc = cfg["market"]
    now = now or int(time.time())
    fresh_after = now - mc["recheck_days"] * 86400
    checked = 0
    for vid in video_ids:
        if checked >= mc["checks_per_run"]:
            break
        v = db.execute("SELECT id, title, lang, channel_id FROM videos WHERE id=?", (vid,)).fetchone()
        if not v or not v["title"]:
            continue
        src = base_lang(v["lang"])
        langs = list(mc["langs"])
        done = {r[0] for r in db.execute(
            "SELECT lang FROM market WHERE video_id=? AND checked_ts>=?", (vid, fresh_after))}
        todo = [l for l in langs if l not in done]
        if not todo:
            continue
        need = len(todo) * 101
        if yt.remaining() - need < mc["min_quota_left"]:
            log(f"  рынки: мало квоты, остальное проверю в следующий раз")
            break
        try:
            for lang in todo:
                if lang == src:
                    translated = v["title"]          # свой язык — ищем по оригинальному названию
                else:
                    translated, _ = tr.get(v["title"], lang, src or None)
                if not translated:
                    log("  рынки: перевод недоступен, пропускаю")
                    return checked
                q = clean_query(translated)
                res = yt.search_videos(q, lang, order="relevance", duration="any",
                                       max_results=mc["results_per_search"])
                ids = [it["id"]["videoId"] for it in res.get("items", []) if it.get("id", {}).get("videoId")]
                ids = [i for i in ids if i != vid]
                items = yt.videos(ids) if ids else []
                st = tokens(translated)
                out = []
                for it in items:
                    sn, s = it["snippet"], it.get("statistics", {})
                    pub = sn["publishedAt"]
                    pts = iso_to_ts(pub)
                    views = int(s.get("viewCount", 0))
                    out.append({
                        "id": it["id"], "t": html.unescape(sn.get("title", "")), "ch": sn.get("channelTitle", ""), "cid": sn.get("channelId", ""),
                        "pub": pts, "views": views,
                        "vph": round(views / max(1, (now - pts) / 3600), 1),
                        "sim": round(similarity(st, sn.get("title", "")), 2),
                    })
                out.sort(key=lambda r: -r["sim"])
                status, best = market_status([r for r in out if r["cid"] != v["channel_id"]], mc)
                db.execute("INSERT OR REPLACE INTO market VALUES(?,?,?,?,?,?,?)",
                           (vid, lang, now, translated, status, best, json.dumps(out[:10], ensure_ascii=False)))
            db.commit()
            checked += 1
        except QuotaExceeded:
            db.commit()
            log("  рынки: квота закончилась")
            break
    return checked
