"""«Кто в кадре»: есть ли лицо на обложке. Нужен пакет opencv-python-headless (необязательно)."""
import urllib.request


def make_detector():
    try:
        import cv2
        import numpy as np
        front = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
        if front.empty():
            return None
    except Exception:          # нет пакета или несовместимая версия — просто пропускаем шаг
        return None

    def detect(video_id):
        url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = r.read()
        except Exception:
            return None
        try:
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        except Exception:
            return None
        if img is None:
            return None
        h = img.shape[0]
        img = img[int(h * 0.125):int(h * 0.875)]          # hqdefault 4:3 с чёрными полосами → 16:9
        img = cv2.equalizeHist(img)
        kw = dict(scaleFactor=1.1, minNeighbors=6, minSize=(30, 30))
        found = len(front.detectMultiScale(img, **kw)) or len(profile.detectMultiScale(img, **kw))
        return int(found > 0)

    return detect
