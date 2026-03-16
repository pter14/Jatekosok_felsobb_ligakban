# list_teams.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; mlsz-team-list-check/1.0; +you@example.com)"}

def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def list_teams(league_url):
    soup = get_soup(league_url)
    teams = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # ugyanazok a heuristikák, amit a Streamlit prototípus használ
        if re.search(r"/team/|/club/|/csapat/", href):
            full = urljoin(league_url, href)
            name = a.get_text(strip=True)
            if name:
                teams.append((name, full))
    # dedupe by URL
    seen = set()
    uniq = []
    for name, url in teams:
        if url not in seen:
            seen.add(url)
            uniq.append((name, url))
    return uniq

if __name__ == "__main__":
    league_url = "https://adatbank.mlsz.hu/league/65/3/32058/10.html"  # cseréld ha más linket akarsz
    teams = list_teams(league_url)
    print(f"Talált csapatok száma: {len(teams)}\\n")
    for i, (name, url) in enumerate(teams, start=1):
        print(f"{i:2d}. {name} -> {url}")
