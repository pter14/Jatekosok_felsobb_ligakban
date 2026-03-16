"""
Streamlit prototype: MLSZ U-age scanner

Usage:
1. Install dependencies:
   pip install streamlit requests beautifulsoup4 pandas openpyxl
2. Run:
   streamlit run mlsz_u_age_scanner_streamlit.py

Notes:
- This is a prototype. The app will fetch the provided league page, suggest "Szervező" (organizers) and available "Liga" names, and let you choose organizers and U-11..U-17 checkboxes.
- Inputs: season (default 2025/2026), min_starts (Kezdő threshold), birth_year (optional), run on all teams in leagues matching choices.
- Output: table and Excel download. Errors are logged to a CSV in-memory and downloadable.

Caveats:
- The scraping logic uses heuristics for the MLSZ adatbank site. The page structure may vary; if some pages use JS to render content, the requests-based approach may not see it.
- Respect robots.txt and use gentle rate limits.
"""

import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
from urllib.parse import urljoin
from io import BytesIO

# --- Config ---
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; mlsz-scanner/1.0; +you@example.com)"}
DEFAULT_RATE = 1.0  # seconds between requests
DEFAULT_SEASON = "2025/2026"

# --- Helper functions ---

def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def extract_league_metadata(league_url):
    """Fetch league page and extract available 'Szervező' (organizers) and league names + team links.
    Returns: dict with 'organizers' set, 'leagues' set, 'teams' list of (team_name, team_url, organizer, league_name)
    """
    soup = get_soup(league_url)

    # Heuristics: find organizer label on page
    page_text = soup.get_text(separator="\n")

    # organizers - attempt to find occurrences like 'Szervez\u0151: <name>' or a page field
    organizers = set()
    # Search for typical patterns
    for match in re.finditer(r"Szervez[eó]?:\s*([A-Za-z0-9\-\u00C0-\u024F ]+)", page_text):
        organizers.add(match.group(1).strip())

    # League names found on the page (common pattern: 'U-13', 'U-14' etc.)
    leagues = set()
    for u in range(11, 18):
        pattern = f"U-{u}"
        if pattern in page_text:
            leagues.add(pattern)

    # Team links: try to find links that lead to team pages by href containing '/team/' or '/club/' or '/csapat/'
    teams = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"/team/|/club/|/csapat/", href):
            full = urljoin(league_url, href)
            name = a.get_text(strip=True)
            # best-effort organizer/league for the team: we may not have them here; set None
            teams.append({"team_name": name or "(no-name)", "team_url": full, "organizer": None, "league_name": None})

    # Deduplicate teams by URL
    seen = {}
    uniq_teams = []
    for t in teams:
        if t["team_url"] not in seen:
            seen[t["team_url"]] = True
            uniq_teams.append(t)

    return {"organizers": sorted(list(organizers)), "leagues": sorted(list(leagues)), "teams": uniq_teams}


def extract_team_players(team_url):
    """Return list of (player_name, player_url) from a team page (heuristic)."""
    soup = get_soup(team_url)
    players = []
    # player links often contain '/player/' or '/jatekos/'
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"/player/|/jatekos/|/playerprofile", href):
            full = urljoin(team_url, href)
            name = a.get_text(strip=True)
            if name:
                players.append({"player_name": name, "player_url": full})
    # dedupe
    seen = set()
    out = []
    for p in players:
        if p["player_url"] not in seen:
            seen.add(p["player_url"])
            out.append(p)
    return out


def parse_player_birth_year(soup):
    """Extract birth year from player page soup. Return int or None."""
    text = soup.get_text(separator=" ", strip=True)
    # try patterns: 'Született: 2015.05.12' or year alone
    m = re.search(r"Született[:\s]*([0-9]{4})[.\-/]([0-9]{1,2})[.\-/]([0-9]{1,2})", text)
    if m:
        return int(m.group(1))
    m2 = re.search(r"Született[:\s]*([0-9]{4})", text)
    if m2:
        return int(m2.group(1))
    # fallback: any 4-digit year in reasonable range
    m3 = re.search(r"\b(19|20)([0-9]{2})\b", text)
    if m3:
        y = int(m3.group(0))
        if 1980 <= y <= 2025:
            return y
    return None


def extract_kezdo_for_season_and_league(player_soup, season=DEFAULT_SEASON, league_exact=""):
    """From player page soup, find the season block and exact league row, return Kezd\u0151 int or None.
    We search for the season header like '2025/2026 -' and then find the league_exact in following lines/tables.
    """
    text = player_soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # find season block start
    season_idx = None
    for i, ln in enumerate(lines):
        if ln.startswith(season + " -"):
            season_idx = i
            break
    if season_idx is None:
        return None
    snippet = lines[season_idx: season_idx + 80]
    # find header index
    header_idx = None
    for j, ln in enumerate(snippet):
        if "Kezd" in ln or "Kezdő" in ln or "Kezdő".encode().decode(errors='ignore') in ln:
            header_idx = j
            break
    if header_idx is None:
        # still try to find league row anywhere in snippet
        for j, ln in enumerate(snippet):
            if league_exact and league_exact in ln:
                # try to parse numbers at end
                nums = re.findall(r"\b(\d+)\b", ln)
                if len(nums) >= 2:
                    return int(nums[1])
                return None
        return None
    for j in range(header_idx + 1, len(snippet)):
        row = snippet[j]
        if league_exact and league_exact in row:
            nums = re.findall(r"\b(\d+)\b", row)
            if len(nums) >= 2:
                return int(nums[1])
            return None
    return None

# --- Streamlit UI ---

st.set_page_config(page_title="MLSZ U-age scanner", layout="wide")
st.title("MLSZ U-age scanner — prototípus")

st.markdown("Egy gyors prototípus amely a megadott liga-oldalról kinyeri a csapatokat, majd a játékosoknál ellenőrzi a 2025/2026 szezonban a kiválasztott U-xx ligasor 'Kezd\u0151' mezőt.\n\nHasználd felelősen, tartsd be a robots.txt-et.")

with st.form(key='main'):
    league_url = st.text_input("Liga oldal URL", value="https://adatbank.mlsz.hu/league/65/3/32058/10.html")
    season = st.text_input("Szezon", value=DEFAULT_SEASON)
    col1, col2 = st.columns([2,1])
    with col1:
        st.write("Válassz szervező(ke)t (megyék):")
        organizers_placeholder = st.empty()
    with col2:
        st.write("Válassz korosztály(oka)t:")
        u_checks = {}
        for u in range(11,18):
            u_checks[f"U-{u}"] = st.checkbox(f"U-{u}")
    min_starts = st.number_input("Minimális kezd\u0151 meccsek", min_value=0, value=3)
    birth_year = st.text_input("Szuletesi ev (pl. 2015) - hagyd uresen, ha nem szuresz kulon ev szerint", value="")
    run_button = st.form_submit_button("Futtat")

# After submit, we need to fetch league metadata and populate organizers; then re-run once user chooses organizers & U's and clicks run again.
if run_button:
    try:
        with st.spinner("Lekérem a liga oldalt..."):
            meta = extract_league_metadata(league_url)
            # show organizers and leagues detected
            st.write("Talált szervezők:", meta['organizers'])
            st.write("Talált U-xx-ek a lap szövegében:", meta['leagues'])
            # let user confirm organizers selection (simple: if none detected, we'll assume all)
            chosen_orgs = []
            if meta['organizers']:
                st.write("Válaszd ki a szervezőket (ha ures, mindet vegye):")
                chosen_orgs = st.multiselect("Szervezők", meta['organizers'], default=meta['organizers'])
            else:
                st.info("Nem találtam küzöl szervezőket a lap szövegében. Az osszes csapatot vizsgaljuk.")
                chosen_orgs = []

            # confirm u-checks: if none were checked, default to those in meta
            checked_us = [k for k,v in u_checks.items() if v]
            if not any(u_checks.values()):
                # if none selected, default to all detected in page (or all U-11..U-17)
                checked_us = meta['leagues'] if meta['leagues'] else [f"U-{u}" for u in range(11,18)]
                st.info(f"Alapertelmezett korosztalyok: {checked_us}")

            # proceed to crawl teams
            teams = meta['teams']
            st.write(f"Csapatok szama a liga-oldalon (talalt linkek): {len(teams)}")

            # We'll iterate teams and players, but to keep UI responsive, show progress
            results = []
            errors = []
            total = len(teams)
            pbar = st.progress(0)
            for idx, t in enumerate(teams):
                try:
                    time.sleep(DEFAULT_RATE)
                    team_players = extract_team_players(t['team_url'])
                except Exception as e:
                    errors.append({"player_url": None, "team_name": t.get('team_name'), "error": f"team-fetch-failed: {e}"})
                    team_players = []
                for p in team_players:
                    try:
                        time.sleep(DEFAULT_RATE)
                        psoup = get_soup(p['player_url'])
                        byear = parse_player_birth_year(psoup)
                        # for each selected U, check if league string exists in player's season block and get starts
                        for u in checked_us:
                            kezdo = extract_kezdo_for_season_and_league(psoup, season=season, league_exact=u)
                            if kezdo is None:
                                # not in that league or no data
                                continue
                            # if birth_year filter present, check
                            if birth_year:
                                try:
                                    by_int = int(birth_year)
                                except:
                                    by_int = None
                                if by_int is not None and byear is not None and byear != by_int:
                                    continue
                                if by_int is not None and byear is None:
                                    # cannot verify birth year - log and skip
                                    errors.append({"player_url": p['player_url'], "team_name": t.get('team_name'), "error": "missing-birthyear"})
                                    continue
                            if kezdo >= min_starts:
                                results.append({
                                    "Bajnoksag": u,
                                    "Csapat": t.get('team_name'),
                                    "Jatekos": p.get('player_name'),
                                    "SzuletesiEv": byear if byear else "N/A",
                                    "Kezdo": kezdo,
                                    "Profil": p.get('player_url')
                                })
                    except Exception as e:
                        errors.append({"player_url": p.get('player_url'), "team_name": t.get('team_name'), "error": f"player-fetch-failed: {e}"})
                pbar.progress(int((idx+1)/total*100) if total>0 else 100)

            df = pd.DataFrame(results)
            st.success(f"Kesz. Talalatok szama: {len(df)}")
            if not df.empty:
                st.dataframe(df)
                # Excel download
                towrite = BytesIO()
                df.to_excel(towrite, index=False, engine='openpyxl')
                towrite.seek(0)
                st.download_button("Letoltes Excel (.xlsx)", data=towrite, file_name=f"mlsz_scan_{season.replace('/','-')}.xlsx")
            else:
                st.info("Nincsen talalat a megadott szuresi feltetelek alapjan.")

            # errors table
            if errors:
                errdf = pd.DataFrame(errors)
                st.warning(f"Hibak / hiányzó adatok: {len(errdf)} rekord")
                st.dataframe(errdf)
                err_buf = BytesIO()
                errdf.to_csv(err_buf, index=False)
                err_buf.seek(0)
                st.download_button("Hibas rekordok letoltese (.csv)", data=err_buf, file_name=f"mlsz_errors_{season.replace('/','-')}.csv")

    except Exception as e:
        st.error(f"Hiba a feldolgozas soran: {e}")

st.markdown("---")
st.markdown("Prototype by assistant. Ha szeretned, kibovitjuk tobb hibakezelessel, cache-el, tobb mezovel es pontosabb DOM parsolassal.")
