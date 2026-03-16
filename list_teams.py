# improved_list_teams.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse
import re
import unicodedata

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; mlsz-team-list-check/1.0; +you@example.com)"}

def robust_get(url, timeout=20):
    """Get Response and try to set a sensible .encoding for BeautifulSoup/text decoding."""
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    # prefer requests detected encoding, else try common Hungarian encodings
    enc = r.encoding or r.apparent_encoding or None
    # if encoding looks like ascii/None, try to detect with apparent_encoding
    if not enc or enc.lower() in ("ascii", "us-ascii"):
        enc = r.apparent_encoding or "utf-8"
    # try set encoding and return bytes + encoding
    return r.content, enc, r

def to_soup_from_bytes(content_bytes, encoding_hint):
    # try decode with hint, if fails, fallback to cp1250, iso-8859-2, utf-8
    tries = [encoding_hint] + [c for c in ("cp1250", "windows-1250", "iso-8859-2", "utf-8") if c != encoding_hint]
    text = None
    for enc in tries:
        try:
            text = content_bytes.decode(enc, errors="strict")
            used = enc
            break
        except Exception:
            try:
                # second chance with replace (less strict) to avoid errors
                text = content_bytes.decode(enc, errors="replace")
                used = enc
                break
            except Exception:
                continue
    if text is None:
        # ultimate fallback
        text = content_bytes.decode("utf-8", errors="replace")
        used = "utf-8-replace"
    return BeautifulSoup(text, "html.parser"), used

def normalize_name(name):
    """Normalize team/player names: unicode normalize, collapse spaces, strip."""
    if not name:
        return name
    # remove weird non-breaking spaces etc., normalize Unicode
    s = name.replace("\xa0", " ")
    s = unicodedata.normalize("NFKC", s)
    # collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s

def normalize_url(url):
    """Strip query and fragment, lowercase host, return canonicalized url for dedupe."""
    try:
        p = urlparse(url)
        # remove query and fragment
        clean = urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", "", ""))
        return clean
    except Exception:
        return url

def list_unique_teams(league_url):
    content, enc_hint, resp = robust_get(league_url)
    soup, used_enc = to_soup_from_bytes(content, enc_hint)
    # debug print: encoding used
    # print("Used encoding:", used_enc)

    teams = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Only links that likely point to teams
        if re.search(r"/team/|/club/|/csapat/", href, flags=re.IGNORECASE):
            full = urljoin(league_url, href)
            name_raw = a.get_text(strip=True)
            name = normalize_name(name_raw)
            if not name:
                # try alt text or title attr
                name = normalize_name(a.get("title") or a.get("alt") or "")
            if name:
                teams.append({"name": name, "url": full, "name_raw": name_raw})

    # Deduplicate: prefer unique normalized name + normalized url path
    seen_keys = set()
    unique = []
    for t in teams:
        key = (t["name"].lower(), normalize_url(t["url"]))
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(t)
    return unique

if __name__ == "__main__":
    url = "https://adatbank.mlsz.hu/league/65/3/32058/10.html"
    teams = list_unique_teams(url)
    print(f"Talált UNIQUE csapatok száma: {len(teams)}")
    for i, t in enumerate(teams, 1):
        print(f"{i:2d}. {t['name']} -> {t['url']}")
