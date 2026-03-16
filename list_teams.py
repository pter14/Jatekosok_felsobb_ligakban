# improved_teams_debug.py
import requests, re, unicodedata, html
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse
from ftfy import fix_text
from difflib import SequenceMatcher
import chardet   # pip install chardet
import sys

HEADERS = {"User-Agent":"Mozilla/5.0 (compatible; mlsz-team-list-check/1.0; +you@example.com)"}

def fetch_bytes(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.content, r.headers.get('content-type', ''), r.apparent_encoding

def try_decodes(content_bytes, apparent_enc):
    # try sequence: apparent, chardet detection, utf-8, windows-1250, iso-8859-2, latin1
    candidates = []
    if apparent_enc:
        candidates.append(apparent_enc)
    detected = chardet.detect(content_bytes).get('encoding')
    if detected and detected not in candidates:
        candidates.append(detected)
    candidates += ["utf-8", "windows-1250", "iso-8859-2", "latin1"]
    seen = set()
    results = []
    for enc in candidates:
        if not enc or enc.lower() in seen:
            continue
        seen.add(enc.lower())
        try:
            txt = content_bytes.decode(enc, errors='strict')
            results.append((enc, txt))
        except Exception:
            try:
                txt = content_bytes.decode(enc, errors='replace')
                results.append((enc+"-replace", txt))
            except Exception:
                pass
    # final fallback
    txt = content_bytes.decode("utf-8", errors='replace')
    results.append(("utf-8-replace-final", txt))
    return results

def make_soup_from_best(content_bytes, apparent_enc):
    decs = try_decodes(content_bytes, apparent_enc)
    # pick best candidate by heuristic: contains common Hungarian characters or 'Szervező' or 'Csapat'
    best = None
    for enc, txt in decs:
        score = 0
        if "Szervez" in txt or "Csapat" in txt or "Részt" in txt or "Csapatok" in txt:
            score += 10
        # presence of Hungarian accented vowels
        if re.search(r"[áéíóöőúüűÁÉÍÓÖŐÚÜŰ]", txt):
            score += 5
        if best is None or score > best[0]:
            best = (score, enc, txt)
    # fallback to first if none matched
    if best is None:
        enc, txt = decs[0]
        return BeautifulSoup(txt, "html.parser"), enc
    _, enc, txt = best
    return BeautifulSoup(txt, "html.parser"), enc

def fix_text_final(s):
    if not s:
        return s
    s2 = fix_text(s)            # fix mojibake
    s2 = html.unescape(s2)      # unescape HTML entities
    s2 = s2.replace("\xa0", " ")
    s2 = unicodedata.normalize("NFKC", s2)
    s2 = re.sub(r"\s+", " ", s2).strip()
    return s2

def canonical_url(u):
    try:
        p = urlparse(u)
        path = p.path.rstrip("/")
        return urlunparse((p.scheme, p.netloc.lower(), path, "", "", ""))
    except:
        return u

def similar(a,b):
    return SequenceMatcher(None, a, b).ratio()

def find_candidate_container(soup):
    # look for headings that likely introduce the team list
    headings = soup.find_all(re.compile('^h[1-6]$'))
    for h in headings:
        txt = h.get_text(" ", strip=True)
        if txt and re.search(r"Csapat|Csapatok|Résztvev|Résztvevők|Résztvevok", txt, flags=re.I):
            return h.find_parent() or h
    # fallback: look for tables or ULs with many links
    candidates = soup.find_all(['table','ul','div'])
    best = None
    for c in candidates:
        links = c.find_all('a', href=True)
        if len(links) >= 6:
            # prefer elements that contain 'csapat' near them
            txt = c.get_text(" ", strip=True)
            score = len(links)
            if re.search(r"Csapat", txt, flags=re.I):
                score += 5
            if best is None or score > best[0]:
                best = (score, c)
    if best:
        return best[1]
    return None

def extract_team_links_from_container(container, base_url):
    out = []
    for a in container.find_all('a', href=True):
        href = a['href']
        if re.search(r"/team/|/club/|/csapat/", href, flags=re.I):
            full = urljoin(base_url, href)
            raw = a.get_text(" ", strip=True) or a.get('title') or ""
            name = fix_text_final(raw)
            if name and len(name) >= 2:
                out.append({'name': name, 'url': full, 'raw': raw})
    return out

def intelligent_dedupe(candidates):
    # dedupe by canonical_url first, then by normalized name fuzzy merging
    by_url = {}
    for c in candidates:
        key = canonical_url(c['url'])
        if key not in by_url:
            by_url[key] = c
    uniq = list(by_url.values())
    # now further dedupe by fuzzy name: merge if similarity > 0.88
    merged = []
    used = [False]*len(uniq)
    for i, a in enumerate(uniq):
        if used[i]:
            continue
        group = [a]
        used[i] = True
        for j in range(i+1, len(uniq)):
            if used[j]:
                continue
            b = uniq[j]
            # compare lower-case fixed names without punctuation
            na = re.sub(r"[^\w\s]", "", a['name'].lower())
            nb = re.sub(r"[^\w\s]", "", b['name'].lower())
            if similar(na, nb) > 0.88:
                group.append(b)
                used[j] = True
        # pick the "best" representative: prefer one whose raw text appears longer (likely full name)
        rep = sorted(group, key=lambda x: len(x.get('name','')) , reverse=True)[0]
        merged.append(rep)
    return merged

def list_unique_teams(url):
    content_bytes, content_type, apparent_enc = fetch_bytes(url)
    soup, used_enc = make_soup_from_best(content_bytes, apparent_enc)
    container = find_candidate_container(soup)
    debug = {}
    if container is None:
        # fallback to whole page
        container = soup
        debug['container_hint'] = 'page'
    else:
        debug['container_hint'] = 'found_heading_or_block'
    candidates = extract_team_links_from_container(container, url)
    debug['raw_count'] = len(candidates)
    uniq = intelligent_dedupe(candidates)
    debug['unique_count'] = len(uniq)
    debug['used_encoding'] = used_enc
    return uniq, debug

if __name__ == "__main__":
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = "https://adatbank.mlsz.hu/league/65/3/32058/10.html"
    uniq, debug = list_unique_teams(url)
    print("DEBUG:", debug)
    print("UNIQUE TEAMS:", len(uniq))
    for i, t in enumerate(uniq, 1):
        print(f"{i:2d}. {t['name']} -> {t['url']}")
