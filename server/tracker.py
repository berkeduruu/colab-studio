"""Çalışan işin ilerlemesi ve kalan süresi.

ComfyUI yalnızca örnekleyicilerde adım bildirir; model yükleme, VAE, ağ işleme gibi düğümler
sessiz geçer. Bu yüzden her düğümün süresi önceki işlerden öğrenilir (iş türü + süreyi etkileyen
ayarlar = "variant"). Kalan süre = çalışan düğümün kalanı (adım bildiriyorsa ölçülen adım hızından)
+ henüz çalışmamış düğümlerin öğrenilmiş süreleri + sıradaki grafikler (adet > 1).

"Soğuk" iş, GPU açıldıktan sonra o türün ilk işidir: modeller diskten okunup GPU'ya yüklenir,
sonrakilerden belirgin uzun sürer; süreleri ayrı öğrenilir.
"""
import re
import time

SAMPLERS = ("KSampler", "SamplerCustomAdvanced")


def variant(job):
    """Süreyi belirleyen ayarlar; aynı variant'lı işler aynı süre tablosunu paylaşır. Eski kayıtlarda
    sonradan eklenen ayarlar olmayabilir (get)."""
    p, kind = job["params"], job["kind"]
    if kind == "image":
        size = f"edit{p.get('edit_resolution')}x{len(job['inputs'])}" if job["inputs"] else p.get("size")
        lora = "-turbo" if p.get("turbo") else ""
        return f"{size}-s{p.get('steps')}-cfg{'1' if p.get('cfg') == 1 else 'n'}{lora}"
    if kind == "video":
        return f"{p.get('quality')}-{p.get('seconds')}s-{'turbo' if p.get('turbo') else 'full'}"
    if kind == "model3d":
        return f"{p.get('resolution')}-{p.get('texture')}"
    if kind == "music":
        return f"{p.get('seconds')}s"
    if kind == "speech":
        return f"{p.get('engine')}-{'clone' if job['inputs'] else 'preset'}"
    if kind == "enhance":
        return "edit" if job["inputs"] else "t2i"
    return "-"


# variant() çıktısının kalıpları (iş türüne göre); süre tablosunda bunlara uymayan anahtarlar eskimiştir.
_NUM = r"\d+(?:\.\d+)?"
KNOWN_VARIANTS = {
    "image": r"(?:1K|1\.5K|2K|edit(?:720p|1080p|1440p|2160p)x\d+)-s\d+-cfg[1n](?:-turbo)?",
    "video": rf"(?:480p|768p)-{_NUM}s-(?:turbo|full)",
    "model3d": r"(?:1024|1536|2048)-(?:pbr|vertex)",
    "music": rf"{_NUM}s",
    "speech": r"(?:chatterbox|qwen3)-(?:clone|preset)",
    "enhance": r"(?:edit|t2i)",
    "transcribe": r"-",
}


def is_known_timing_key(key):
    """Süre anahtarı "{tür}|{variant}|..." bugünkü variant kalıplarına uyuyor mu?"""
    parts = key.split("|")
    pattern = KNOWN_VARIANTS.get(parts[0])
    return len(parts) >= 3 and pattern is not None and re.fullmatch(pattern, parts[1]) is not None


class Tracker:
    def __init__(self, timings, job, graphs, cold):
        self.timings = timings  # öğrenilmiş süreler: anahtar -> sn (yalnızca okunur)
        self.prefix = f"{job['kind']}|{variant(job)}"
        self.known = any(k.startswith(self.prefix + "|") for k in timings)
        self.graphs = graphs
        self.cold = cold
        self.measured = {}  # bu işte ölçülen düğüm süreleri
        self.graph_times = []  # biten grafiklerin süreleri
        self.started = time.time()
        self.gi = 0
        self.graph_started = self.started
        self.done = set()  # (grafik, düğüm): çalışmış ya da önbellekten gelmiş
        self.node = None
        self.node_started = None
        self.steps = None  # {"v", "mx", "t0", "v0", "rate"}
        self.last = 0.0

    # --- ComfyUI olayları ---

    def start_graph(self, gi):
        self._finish_node()
        if gi > self.gi:
            self.graph_times.append(time.time() - self.graph_started)
        self.gi, self.graph_started = gi, time.time()

    def cached(self, nodes):
        self.done.update((self.gi, n) for n in nodes)

    def executing(self, node):
        self._finish_node()
        if node is not None:
            self.node, self.node_started, self.steps = node, time.time(), None

    def progress(self, v, mx):
        now = time.time()
        s = self.steps
        if s is None or v < s["v"] or mx != s["mx"]:
            self.steps = {"v": v, "mx": mx, "t0": now, "v0": v, "rate": None}
            return
        if v > s["v0"]:
            s["rate"] = (now - s["t0"]) / (v - s["v0"])
        s["v"] = v

    def finish(self):
        self._finish_node()
        self.graph_times.append(time.time() - self.graph_started)

    def _finish_node(self):
        if self.node is None:
            return
        self.measured[self._key(self.gi, self.node)] = time.time() - self.node_started
        self.done.add((self.gi, self.node))
        self.node = None

    # --- tahmin ---

    def _key(self, gi, node):
        return f"{self.prefix}|{'cold' if self.cold and gi == 0 else 'warm'}|{node}"

    def expected(self, gi, node):
        """Düğümün öğrenilmiş süresi; sıcak ölçüm yoksa soğuk ölçüm kullanılır (önbellekten gelen
        yükleyiciler zaten sayılmaz)."""
        key = self._key(gi, node)
        if key in self.timings:
            return self.timings[key]
        return self.timings.get(key.replace("|warm|", "|cold|"))

    def _graph_expected(self, gi):
        """Henüz başlamamış bir grafiğin süresi: bu işte biten son sıcak grafik (modeller artık
        yüklü), yoksa öğrenilmiş sıcak grafik süresi."""
        last_warm = self.graph_times and not (self.cold and len(self.graph_times) == 1)
        if last_warm:  # soğuk ilk grafik (model yükleme dahil) örnek alınmaz
            return self.graph_times[-1]
        warm = self.timings.get(f"{self.prefix}|graph|warm")
        return warm if warm is not None else self.timings.get(f"{self.prefix}|graph|cold")

    def current_remaining(self):
        if self.node is None:
            return 0.0
        s = self.steps
        if s and s["rate"]:
            return (s["mx"] - s["v"]) * s["rate"]
        e = self.expected(self.gi, self.node)
        return max(e - (time.time() - self.node_started), 0.0) if e is not None else 0.0

    def remaining(self):
        """Kalan süre (sn); bu iş türü/ayarı daha önce hiç çalışmadıysa None."""
        if not self.known:
            return None
        rest = sum(self.expected(self.gi, n) or 0.0 for n in self.graphs[self.gi]
                   if (self.gi, n) not in self.done and n != self.node)
        later = 0.0
        for gi in range(self.gi + 1, len(self.graphs)):
            g = self._graph_expected(gi)
            if g is None:
                return None
            later += g
        return self.current_remaining() + rest + later

    def _sampler_fraction(self):
        """Süre bilinmezken ilerleme: biten örnekleyiciler ve çalışanın adımları."""
        graph = self.graphs[self.gi]
        samplers = [n for n in graph if graph[n]["class_type"] in SAMPLERS] or [None]
        done = sum(1 for n in samplers if (self.gi, n) in self.done)
        cur = self.steps["v"] / self.steps["mx"] if self.steps and self.node in samplers else 0.0
        return (self.gi + min((done + cur) / len(samplers), 1.0)) / len(self.graphs)

    def snapshot(self):
        """Arayüze giden durum: ilerleme (geri gitmez), kalan süre, çalışan düğümün süresi."""
        now = time.time()
        eta = self.remaining()
        if eta is None:
            p = self._sampler_fraction()
        else:
            elapsed = now - self.started
            p = elapsed / (elapsed + eta) if elapsed + eta > 0 else 0.0
        self.last = max(self.last, min(p, 0.99))
        node = self.graphs[self.gi].get(self.node) if self.node else None
        expected = self.expected(self.gi, self.node) if self.node else None
        return {
            "progress": self.last,
            "eta": round(eta) if eta is not None else None,
            "stage_seconds": round(now - self.node_started) if self.node else None,
            "stage_expected": round(expected) if expected is not None else None,
            # Örnekleyici ilk adımını bitirene kadar model diskten okunup GPU'ya yükleniyordur.
            "loading": bool(node and node["class_type"] in SAMPLERS and self.steps is None),
        }

    def learned(self):
        """İş başarıyla bitince saklanacak ölçümler (düğümler ve grafik süreleri)."""
        out = dict(self.measured)
        for gi, t in enumerate(self.graph_times):
            out[f"{self.prefix}|graph|{'cold' if self.cold and gi == 0 else 'warm'}"] = t
        out[f"{self.prefix}|total"] = time.time() - self.started
        return out
