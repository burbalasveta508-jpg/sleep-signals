"""Бесплатный перевод названий с кэшем в базе.

Сначала пробует публичный endpoint Google Translate (без ключа), при ошибке — MyMemory.
Если оба недоступны несколько раз подряд — перестаёт пытаться до следующего запуска.
"""
import hashlib
import json
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (sleep-signals)"}


def _gtx(text, tl):
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(
        {"client": "gtx", "sl": "auto", "tl": tl, "dt": "t", "q": text})
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        data = json.load(r)
    out = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])
    src = data[2] if len(data) > 2 and isinstance(data[2], str) else None
    if not out:
        raise ValueError("пустой перевод")
    return out, src


def _mymemory(text, tl, sl):
    url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(
        {"q": text[:480], "langpair": f"{sl or 'en'}|{tl}"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        data = json.load(r)
    out = (data.get("responseData") or {}).get("translatedText")
    if not out or data.get("responseStatus") not in (200, "200"):
        raise ValueError("MyMemory: нет перевода")
    return out, sl


def key(text, tl):
    return hashlib.sha1(f"{tl}|{text}".encode("utf-8")).hexdigest()


class Translator:
    def __init__(self, db, delay=0.15):
        self.db, self.delay = db, delay
        self.fails = 0
        self.calls = 0

    def cached(self, text, tl):
        row = self.db.execute("SELECT text, src FROM translations WHERE key=?", (key(text, tl),)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def get(self, text, tl, sl=None):
        """Вернуть (перевод, язык оригинала) или (None, None)."""
        if not text:
            return None, None
        out, src = self.cached(text, tl)
        if out is not None:
            return out, src
        if self.fails >= 5:
            return None, None
        try:
            out, src = _gtx(text, tl)
        except Exception:
            try:
                out, src = _mymemory(text, tl, sl)
            except Exception:
                self.fails += 1
                return None, None
        self.fails = 0
        self.calls += 1
        self.db.execute("INSERT OR REPLACE INTO translations VALUES(?,?,?)", (key(text, tl), out, src))
        if self.calls % 20 == 0:
            self.db.commit()
        time.sleep(self.delay)
        return out, src
