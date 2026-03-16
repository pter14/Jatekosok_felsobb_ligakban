# improved_teams_debug2.py
import requests, re, unicodedata, html, sys
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse
from ftfy import fix_text
import chardet
from difflib import SequenceMatcher

HEADERS = {"User-Agent":"Mozilla/5.0 (compatible; mlsz-team-list-check/1.0; +you@example.com)"}

def fetch_bytes(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.content, r.headers.get('content-type',''), r.apparent_encoding

def try_decodes(content_bytes, apparent_enc):
    candidates = []
    if apparent_enc:
        candidates.append(apparent_enc)
    detected = chardet.detect(content_bytes).get('encoding')
    if detected and detected not in candidates:
        candidates.append(detected)
    candidates += ["utf-8", "windows-1250", "iso-8859-2", "latin1"]
    seen = set(); results=[]
    for enc in candidates:
        if not enc or enc.lower() in seen: continue
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
    txt = content_bytes.decode("utf-8", errors='replace'); results.append(("utf-8-replace-final", txt))
    return results

def pick_best_decode(decs):
    best = None
    for enc, txt in decs:
        score = 0
        if "Szervez" in txt or "Csapat" in txt or "Részt" in txt or "Csapatok" in txt: score += 10
        if re.search(r"[áéíóöőúüűÁÉÍÓÖŐÚÜŰ]", txt): score += 5
        if best is None or score > best[0]: best = (score, enc, txt)
    if best is None: return decs[0][1], decs[0][0]
    return best[2], best[1]

def fix_text_final(s):
    if not s: return s
    s2 = fix_text(s)
    s2 = html.unescape(s2)
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

def extract_from_links(soup, base_url):
    out=[]
    for a in soup.find_all("a", href=True):
        href=a['href']
        if re.search(r"/team/|/club/|/csapat/", href, flags=re.I):
            full=urljoin(base_url, href)
            raw=a.get_text(" ", strip=True) or a.get("title") or ""
            name=fix_text_final(raw)
            if name and len(name)>1:
                out.append({'name':name,'url':full,'source':'link'})
    return out

def extract_from_tables(soup, base_url):
    out=[]
    # look into tables: find TDs that look like team names (contain uppercase words and SE/FC)
    tables=soup.find_all("table")
    for t in tables:
        for td in t.find_all("td"):
            txt = td.get_text(" ", strip=True)
            if not txt: continue
            candidate = txt.strip()
            # heuristics: contains 'SE' or 'FC' or consists of words with capital letters or U-..
            if re.search(r"\b(SE|FC|SC|KSE|SE\.)\b", candidate, flags=re.I) or re.search(r"\bU-\d{1,2}\b", candidate) is None and re.search(r"[A-ZÁÉÍÓÖŐÚÜŰ]{2,}", candidate):
                name = fix_text_final(candidate)
                if name and len(name)>2:
                    out.append({'name':name,'url':base_url,'source':'table_td'})
    return out

def extract_from_lists(soup, base_url):
    out=[]
    for ul in soup.find_all(["ul","ol"]):
        for li in ul.find_all("li"):
            txt=li.get_text(" ", strip=True)
            if not txt: continue
            if len(txt)>3 and re.search(r"[A-Za-zÁÉÍÓÖŐÚÜŰ]", txt):
                name=fix_text_final(txt)
                out.append({'name':name,'url':base_url,'source':'list_li'})
    return out

def extract_from_scripts(raw_html, base_url):
    out=[]
    # try to find JSON arrays with names
    # pattern: ["Mezőhegyes","Másik csapat"...]  or "teams":[{...,"name":"..."}]
    for m in re.finditer(r'(\[.*?Mez.*?\])', raw_html, flags=re.S|re.I):
        try:
            arr_text = m.group(1)
            # crude extraction of quoted words
            names = re.findall(r'"([^"]{3,})"', arr_text)
            for n in names:
                nm = fix_text_final(n)
                out.append({'name':nm,'url':base_url,'source':'script_array'})
        except Exception:
            pass
    # teams objects
    for m in re.finditer(r'"teams"\s*:\s*\[([^\]]+)\]', raw_html, flags=re.S|re.I):
        block = m.group(1)
        names = re.findall(r'"name"\s*:\s*"([^"]{3,})"', block)
        for n in names:
            nm=fix_text_final(n)
            out.append({'name':nm,'url':base_url,'source':'script_obj'})
    return out

def intelligent_dedupe(candidates):
    # by canonical_url + fuzzy name merging
    by_url={}
    for c in candidates:
        key=canonical_url(c.get('url', ''))
        if key not in by_url:
            by_url[key]=c
    uniq=list(by_url.values())
    merged=[]; used=[False]*len(uniq)
    for i,a in enumerate(uniq):
        if used[i]: continue
        group=[a]; used[i]=True
        for j in range(i+1,len(uniq)):
            if used[j]: continue
            b=uniq[j]
            na=re.sub(r"[^\w\s]", "", a['name'].lower())
            nb=re.sub(r"[^\w\s]", "", b['name'].lower())
            if similar(na, nb) > 0.86:
                group.append(b); used[j]=True
        rep = sorted(group, key=lambda x: len(x.get('name','')), reverse=True)[0]
        merged.append(rep)
    return merged

def main(url):
    content_bytes, content_type, apparent = fetch_bytes(url)
    decs = try_decodes(content_bytes, apparent)
    txt, used_enc = pick_best_decode(decs)
    soup = BeautifulSoup(txt, "html.parser")
    raw_html = txt

    results=[]
    debug = {'used_encoding': used_enc, 'found_link_hits': False, 'found_table_hits': False, 'found_list_hits': False, 'found_script_hits': False}

    # 1) links
    links = extract_from_links(soup, url)
    if links:
        debug['found_link_hits'] = True
        results.extend(links)

    # 2) tables
    tables = extract_from_tables(soup, url)
    if tables:
        debug['found_table_hits'] = True
        results.extend(tables)

    # 3) lists
    lists = extract_from_lists(soup, url)
    if lists:
        debug['found_list_hits'] = True
        results.extend(lists)

    # 4) scripts / JSON
    scripts = extract_from_scripts(raw_html, url)
    if scripts:
        debug['found_script_hits'] = True
        results.extend(scripts)

    debug['raw_candidate_count'] = len(results)
    uniq = intelligent_dedupe(results)
    debug['unique_count'] = len(uniq)

    print("DEBUG:", debug)
    print("SAMPLE candidates (max 50):")
    for i, t in enumerate(results[:50],1):
        print(f"{i:2d}. [{t.get('source')}] {t.get('name')!r} -> {t.get('url')}")
    print("\nUNIQUE FINAL:")
    for i, t in enumerate(uniq,1):
        print(f"{i:2d}. {t.get('name')!r} -> {t.get('url')}")

if __name__ == "__main__":
    if len(sys.argv)>1:
        url=sys.argv[1]
    else:
        url="https://adatbank.mlsz.hu/league/65/3/32058/10.html"
    main(url)
