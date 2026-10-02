"""Расчёт сигналов по снимкам статистики.

VPH         — просмотры в час между двумя последними снимками (≥ min_interval_hours).
Активация   — видео старше activation_min_age_days, у которого VPH вырос
              в activation_mult раз относительно его медианы за baseline_days.
Вечнозелёное — старое видео (≥ evergreen_min_age_days), которое стабильно
              набирает ≥ evergreen_min_vph просмотров в час.
Превышение  — свежее видео (≤ exceed_max_age_days), набравшее в exceed_mult раз
              больше просмотров, чем медианное видео этого же канала.
"""
import statistics
import time

H, D = 3600, 86400


def _median(xs):
    return statistics.median(xs) if xs else None


def _daily_gains(series, n):
    """Прирост просмотров за сутки по последнему снимку каждого дня.
    Возвращает (номер первого дня, [прирост, ...]); номер дня = ts // 86400."""
    byday = {}
    for ts, v in series:
        byday[ts // D] = v
    days = sorted(byday)
    pts = [(b, (byday[b] - byday[a]) / (b - a)) for a, b in zip(days, days[1:])][-n:]
    if not pts:
        return None, []
    return pts[0][0], [round(g) for _, g in pts]


def _vph_points(series, min_gap_h=6, n=40):
    """Скорость (просмотры/час) на каждом промежутке между замерами: [(ts конца, vph), ...]."""
    if not series:
        return []
    pts, anchor = [], series[0]
    for ts, v in series[1:]:
        if ts - anchor[0] >= min_gap_h * H:
            pts.append((ts, round(max(0, v - anchor[1]) / ((ts - anchor[0]) / H), 1)))
            anchor = (ts, v)
    return pts[-n:]


DEFAULTS = {"trend_up": 1.5, "trend_down": 0.5, "trend_min_age_days": 7,
            "evergreen_min_age_days": 180, "evergreen_min_vph": 15}


def compute_videos(db, cfg, now=None):
    now = now or int(time.time())
    m = {**DEFAULTS, **cfg["metrics"]}
    since = now - (m["history_days"] + m["baseline_days"] + 2) * D

    meta = {r["id"]: dict(r) for r in db.execute(
        """SELECT v.id, v.channel_id, v.title, v.published_ts, v.duration_sec, v.lang, v.is_niche,
                  c.title AS ch_title, c.country AS ch_country
           FROM videos v JOIN channels c ON c.id = v.channel_id
           WHERE c.active = 1 AND v.gone = 0 AND v.title IS NOT NULL""")}
    series = {}
    for vid, ts, views in db.execute(
            "SELECT video_id, ts, views FROM video_stats WHERE ts >= ? AND ts <= ? ORDER BY ts", (since, now)):
        if vid in meta:
            series.setdefault(vid, []).append((ts, views))
    subs = {}
    for cid, s in db.execute("SELECT channel_id, subscribers FROM channel_stats ORDER BY ts"):
        subs[cid] = s

    if not series:
        return []
    newest = max(s[-1][0] for s in series.values())

    out = []
    for vid, s in series.items():
        last_ts, last_views = s[-1]
        if last_ts < newest - 2 * D:          # видео больше не отслеживается
            continue
        v = meta[vid]
        age_h = max(1.0, (last_ts - v["published_ts"]) / H)

        prev = next(((ts, vw) for ts, vw in reversed(s[:-1])
                     if last_ts - ts >= m["min_interval_hours"] * H), None)
        if prev:
            vph, est = (last_views - prev[1]) / ((last_ts - prev[0]) / H), False
        else:
            vph, est = last_views / age_h, True   # пока один снимок — средний VPH за жизнь

        base, life = None, None
        if prev:
            pts = [p for p in s if prev[0] - m["baseline_days"] * D <= p[0] <= prev[0]]
            iv = [(b[1] - a[1]) / ((b[0] - a[0]) / H) for a, b in zip(pts, pts[1:]) if b[0] - a[0] >= H]
            if len(iv) >= m["baseline_min_points"]:
                base = _median(iv)
            # средняя скорость за всю жизнь видео на момент предыдущего замера
            life = prev[1] / max(1.0, (prev[0] - v["published_ts"]) / H)
        # пока нет 2 недель истории — сравниваем со средней скоростью за жизнь
        base_src = "hist" if base is not None else ("life" if life is not None else None)
        base_eff = base if base is not None else life
        trend_x = vph / max(life, m["baseline_floor"]) if life is not None else None
        if trend_x is None or age_h / 24 < m["trend_min_age_days"]:
            trend = None
        elif trend_x >= m["trend_up"]:
            trend = "up"
        elif trend_x <= m["trend_down"]:
            trend = "down"
        else:
            trend = "flat"

        out.append(dict(v, views=last_views, vph=vph, vph_est=est, base=base_eff, base_src=base_src,
                        life=life, trend_x=trend_x, trend=trend,
                        age_days=age_h / 24, subs=subs.get(v["channel_id"]),
                        daily=_daily_gains(s, m["history_days"])[1],
                        daily0=_daily_gains(s, m["history_days"])[0],
                        vph_pts=_vph_points(s)))

    # медиана просмотров канала — для «превышения»
    bych = {}
    for r in out:
        if 7 <= r["age_days"] <= 365:
            bych.setdefault(r["channel_id"], []).append(r["views"])

    for r in out:
        vals = bych.get(r["channel_id"], [])
        med = _median(vals) if len(vals) >= m["exceed_min_channel_videos"] else None
        r["ch_median"] = med
        r["exceed_x"] = r["views"] / med if med else None
        r["growth_x"] = r["vph"] / max(r["base"], m["baseline_floor"]) if r["base"] is not None else None
        r["gain"] = r["vph"] - r["base"] if r["base"] is not None else None
        r["activation"] = bool(
            r["growth_x"] is not None and not r["vph_est"]
            and r["age_days"] >= m["activation_min_age_days"]
            and r["vph"] >= m["activation_min_vph"]
            and r["growth_x"] >= m["activation_mult"])
        r["exceedance"] = bool(
            r["exceed_x"] is not None
            and r["age_days"] <= m["exceed_max_age_days"]
            and r["views"] >= m["exceed_min_views"]
            and r["exceed_x"] >= m["exceed_mult"])
        r["evergreen"] = bool(
            not r["vph_est"]
            and r["age_days"] >= m["evergreen_min_age_days"]
            and r["vph"] >= m["evergreen_min_vph"]
            and not r["activation"])

    if cfg["report"]["only_niche"]:
        out = [r for r in out if r["is_niche"]]
    return out


def compute_channels(db, cfg, videos, now=None):
    now = now or int(time.time())
    rows = {}
    for cid, ts, s, v in db.execute(
            "SELECT channel_id, ts, subscribers, views FROM channel_stats WHERE ts >= ? ORDER BY ts",
            (now - 40 * D,)):
        rows.setdefault(cid, []).append((ts, s, v))
    meta = {r["id"]: dict(r) for r in db.execute("SELECT * FROM channels WHERE active = 1")}

    vid_by_ch = {}
    for r in videos:
        vid_by_ch.setdefault(r["channel_id"], []).append(r)

    out = []
    for cid, c in meta.items():
        s = rows.get(cid, [])
        if not s:
            continue
        last = s[-1]

        def delta(days, idx):
            ref = next((p for p in reversed(s) if p[0] <= last[0] - (days - 0.5) * D), None)
            if not ref or last[idx] is None or ref[idx] is None:
                return None
            return last[idx] - ref[idx]

        vids = vid_by_ch.get(cid, [])
        daily = {}
        for r in vids:
            for i, g in enumerate(reversed(r["daily"])):
                daily[i] = daily.get(i, 0) + g
        out.append(dict(
            id=cid, title=c["title"], handle=c["handle"], country=c["country"], source=c["source"],
            subs=last[1], views=last[2],
            subs_d7=delta(7, 1), views_d7=delta(7, 2), views_d1=delta(1, 2),
            niche_videos=len(vids),
            activations=sum(r["activation"] for r in vids),
            exceedances=sum(r["exceedance"] for r in vids),
            max_vph=max((r["vph"] for r in vids), default=0),
            daily=[daily[i] for i in sorted(daily, reverse=True)],
        ))
    return out
