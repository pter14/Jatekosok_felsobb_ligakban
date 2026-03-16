import requests, re, unicodedata
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse
from ftfy import fix_text

HEADERS = {"User-Agent":"Mozilla/5.0 (compatible; mlsz-team-list-check/1.0; +you@example.com)"}

def robust_get_bytes(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.content, r.encoding or r.apparent_encoding

def to_soup_try_encodings(content_bytes, enc_hint):
    # Try a sequence of encodings, but we will let ftfy fix text-level mojibake later
    tries = []
    if enc_hint:
        tries.append(enc_hint)
    tries += ["utf-8", "windows-1250", "iso-8859-2", "latin1"]
    for enc in tries:
        try:
            text = content_bytes.decode(enc, errors="strict")
            return BeautifulSoup(text, "html.parser"), enc
        except Exception:
            try:
                text = content_bytes.decode(enc, errors="replace")
                return BeautifulSoup(text, "html.parser"), enc + "-replace"
            except Exception:
                continue
    # fallback
    text = content_bytes.decode("utf-8", errors="replace")
    return BeautifulSoup(text, "html.parser"), "utf-8-replace"

def normalize_name_fix(name):
    """Normalize and fix mojibake using ftfy, collapse spaces, unicode normalize"""
    if not name:
        return name
    s = name.strip()
    s = fix_text(s)                     # FIX MOJIBAKE / encoding artifacts
    s = s.replace("\xa0", " ")          # NBSP to space
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def normalize_url(url):
    try:
        p = urlparse(url)
        # keep scheme+netloc+path, drop query/fragment, strip trailing slash in path
        path = p.path.rstrip("/")
        return urlunparse((p.scheme, p.netloc.lower(), path, "", "", ""))
    except:
        return url

def list_unique_teams_with_fix(league_url):
    content, enc_hint = robust_get_bytes(league_url)
    soup, used_enc = to_soup_try_encodings(content, enc_hint)

    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # restrict to likely team links
        if re.search(r"/team/|/club/|/csapat/", href, flags=re.IGNORECASE):
            full = urljoin(league_url, href)
            raw = a.get_text(" ", strip=True) or a.get("title") or a.get("alt") or ""
            name = normalize_name_fix(raw)
            # skip obviously non-name links
            if not name or len(name) < 3:
                continue
            # filter out generic labels
            if re.search(r"^(r[eé]szletek|inform[aá]ci[oó]|tov[aá]bb|stat|statisztika)$", name, flags=re.I):
                continue
            candidates.append({"name": name, "url": full, "raw": raw})

    # dedupe by normalized name + canonical url path
    seen = set()
    unique = []
    for t in candidates:
        key = (t["name"].lower(), normalize_url(t["url"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(t)

    return unique
