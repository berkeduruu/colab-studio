"""API mesajlarının İngilizcesi: sunucunun durum ve hata metinleri Türkçe yazılır; arayüz bunları kendi dil
seçimine göre çevirir (web/src/i18n.ts). Arayüz dışındaki istemciler (MCP, curl, betikler) için aynı çeviri
tablosu (web/src/en.ts) burada okunur ve yanıttaki metinler İngilizceye çevrilir.

Arayüz isteklerine `X-Studio-UI` başlığını ekler ve çeviriyi kendi yapar. Başlıksız bir istemci
`Accept-Language: tr` ile Türkçe isteyebilir; yoksa İngilizce alır (README'deki varsayılan dil).
Tanınmayan metin (ör. ComfyUI'nin ham hatası) olduğu gibi kalır. Tablo okunamazsa metinler Türkçe kalır.
"""
import json
import re
from pathlib import Path

EN_TS = Path(__file__).parent / "web" / "src" / "en.ts"
_STRING = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''
# Yanıtta çevrilen alanlar; kullanıcının istemi ya da dosya adı gibi alanlara dokunulmaz.
TEXT_KEYS = {"error", "detail", "stage", "setup", "label"}
LIST_KEYS = {"steps", "lines"}


def _unquote(token):
    body = token[1:-1]
    if token[0] == "'":
        body = body.replace("\\'", "'").replace('"', '\\"')
    else:
        body = body.replace("\\'", "'")
    return json.loads(f'"{body}"')


def _compile(template):
    names, source = [], []
    for part in re.split(r"(\{\w+\})", template):
        m = re.fullmatch(r"\{(\w+)\}", part)
        if m:
            names.append(m.group(1))
            source.append(r"([\s\S]*?)")
        else:
            source.append(re.escape(part))
    return re.compile("^" + "".join(source) + "$"), names


def _load():
    try:
        text = EN_TS.read_text()
    except OSError:
        return {}, []
    patterns_at = text.find("SERVER_PATTERNS")
    en_at = text.find("export const EN")
    patterns_part, en_part = (text[patterns_at:en_at], text[en_at:]) if patterns_at < en_at else (text[patterns_at:], text[en_at:patterns_at])
    exact = {}
    for m in re.finditer(rf"^\s*({_STRING}):\s*({_STRING}),", en_part, re.M):
        exact[_unquote(m.group(1))] = _unquote(m.group(2))
    patterns = []
    for m in re.finditer(rf"\[\s*({_STRING}),\s*({_STRING})\s*\]", patterns_part):
        regex, names = _compile(_unquote(m.group(1)))
        patterns.append((regex, names, _unquote(m.group(2))))
    return exact, patterns


EXACT, PATTERNS = _load()


def to_english(message):
    """Türkçe sunucu mesajını İngilizceye çevirir (önce birebir eşleşme, sonra yer tutuculu kalıplar)."""
    if not isinstance(message, str) or not message:
        return message
    if message in EXACT:
        return EXACT[message]
    for regex, names, english in PATTERNS:
        m = regex.match(message)
        if m:
            values = {name: to_english(m.group(i + 1)) for i, name in enumerate(names)}
            return re.sub(r"\{(\w+)\}", lambda k: str(values.get(k.group(1), k.group(0))), english)
    return message


def wants_english(headers):
    """Arayüz kendi çevirir; başka istemciler Accept-Language'in ilk dili Türkçe değilse İngilizce alır."""
    if headers.get("X-Studio-UI"):
        return False
    first = headers.get("Accept-Language", "").split(",")[0].strip().lower()
    return not first.startswith("tr")


def translate_json(value, key=None):
    """JSON gövdesindeki bilinen metin alanlarını çevirir (iç içe nesne ve listeler dahil)."""
    if isinstance(value, dict):
        return {k: translate_json(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [translate_json(v, key) for v in value]
    if isinstance(value, str) and (key in TEXT_KEYS or key in LIST_KEYS):
        return to_english(value)
    return value
