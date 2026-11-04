"""GPU duman testi: çalışan stüdyo servisine her iş türünden birer iş gönderir, GPU'yu açtırır,
sonuçları denetler ve sonunda GPU'yu kapatır. Kredi harcar; büyük değişikliklerden sonra elle çalıştır.

Stüdyonun çalıştığı makinede:  python3 server/tests/gpu_smoke.py [iş türü ...]
(STUDIO_URL ile başka adres verilebilir; varsayılan http://127.0.0.1:8190)
"""
import json
import os
import sys
import time
import urllib.request
import uuid

BASE = os.environ.get("STUDIO_URL", "http://127.0.0.1:8190").rstrip("/")
IMAGE = {"ratio": "1:1", "size": "1K", "count": 1, "steps": 20, "cfg": 3, "sampler": "euler", "scheduler": "simple",
         "seed": 7, "negative": "blurry"}
SPEECH = {"engine": "chatterbox", "language": "Turkish", "voice_preset": "Vivian", "instruct": "", "reference_text": "",
          "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "seed": 1}


def call(method, path, body=None, ctype="application/json"):
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": ctype} if data else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw


def upload(data, filename, ctype):
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    return call("POST", "/api/uploads", body, f"multipart/form-data; boundary={b}")["name"]


def job(kind, prompt, params=None, inputs=()):
    return call("POST", "/api/jobs", {"thread_id": None, "kind": kind, "prompt": prompt, "params": params or {}, "inputs": list(inputs)})


def wait(j, timeout=1800):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = call("GET", f"/api/jobs/{j['id']}")
        if j["status"] in ("done", "error"):
            return j
        time.sleep(5)
    raise TimeoutError(j["id"])


def check(name, j, min_bytes=1000):
    if j["status"] != "done":
        return f"{name}: HATA {j['error']}"
    if j["kind"] in ("enhance", "transcribe"):
        return f"{name}: tamam · {(j['text'] or '')[:80]!r}" if j["text"] else f"{name}: boş metin"
    sizes = [len(call("GET", f"/media/{o}")) for o in j["outputs"]]
    ok = sizes and all(s >= min_bytes for s in sizes)
    took = round(j["finished_at"] - j["created_at"])
    return f"{name}: {'tamam' if ok else 'KÜÇÜK ÇIKTI'} · {len(sizes)} dosya {sizes} · {took} sn"


def main(kinds):
    results, t0 = [], time.time()
    try:
        if "image" in kinds:
            img = wait(job("image", "Ahşap masada seramik kahve fincanı, sabah ışığı", IMAGE))
            results.append(check("görsel", img))
            if img["status"] == "done":
                ref = upload(call("GET", f"/media/{img['outputs'][0]}"), "ref.png", "image/png")
                results.append(check("görsel düzenleme", wait(job("image", "Fincanı kırmızı yap", IMAGE, [ref]))))
                if "model3d" in kinds:  # görsel sonucu doğrudan 3D'ye verilir
                    m = {"resolution": 1024, "texture": "pbr", "faces": 50000, "seed": 1}
                    results.append(check("3D model (TRELLIS.2)", wait(job("model3d", "", m, [img["outputs"][0]]))))
        if "enhance" in kinds:
            results.append(check("istem geliştirme", wait(job("enhance", "Sisli çam ormanında kırmızı montlu kadın"))))
        speech = None
        if "speech" in kinds:
            speech = wait(job("speech", "Merhaba! Bu stüdyonun Türkçe seslendirme testidir.", SPEECH))
            results.append(check("seslendirme (Chatterbox)", speech))
            if speech["status"] == "done":
                voice = upload(call("GET", f"/media/{speech['outputs'][0]}"), "voice.mp3", "audio/mpeg")
                results.append(check("ses klonlama (Chatterbox)", wait(job("speech", "Bu ses klonlandı.", SPEECH, [voice]))))
                q = {**SPEECH, "engine": "qwen3", "language": "English", "temperature": 0.9}
                results.append(check("ses klonlama (Qwen3)", wait(job("speech", "This voice was cloned.", q, [voice]))))
                results.append(check("hazır ses (Qwen3)", wait(job("speech", "Welcome to the studio.", {**q, "instruct": "Speak warmly."}))))
        if "transcribe" in kinds and speech and speech["status"] == "done":
            voice = upload(call("GET", f"/media/{speech['outputs'][0]}"), "rec.mp3", "audio/mpeg")
            results.append(check("yazıya dökme", wait(job("transcribe", "", {"language": "Turkish"}, [voice]))))
        if "music" in kinds:
            results.append(check("müzik", wait(job("music", "Instrumental lo-fi hip hop, 80 BPM, mellow piano", {"lyrics": "", "seconds": 30, "seed": 3}))))
        if "video" in kinds:
            v = {"quality": "480p", "ratio": "16:9", "seconds": 3, "turbo": True, "seed": 1}
            results.append(check("video", wait(job("video", "Gün batımında Boğaz'da ilerleyen vapur, martı sesleri", v))))
    finally:
        call("POST", "/api/runtime/stop")
    print("\n".join(results))
    print(f"toplam {round(time.time() - t0)} sn; GPU kapatıldı")
    return 0 if all("tamam" in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["image", "model3d", "enhance", "speech", "transcribe", "music", "video"]))
