# app.py
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict

import requests
from bs4 import BeautifulSoup
import streamlit as st

BASE = "https://www.cantineocatecumenale.it"
START_URL = f"{BASE}/lista-canti/"

# ----------------------------
# Parsing & normalization
# ----------------------------

BOOK_ALIASES = {
    # Antico
    "genesi": "gen", "gen": "gen", "gn": "gen",
    "esodo": "es", "es": "es",
    "numeri": "nm", "nm": "nm",
    "deuteronomio": "dt", "dt": "dt",
    "giosuè": "gs", "gs": "gs",
    "giudici": "gd", "gd": "gd",
    "tobia": "tb", "tb": "tb",
    "salmi": "sal", "salmo": "sal", "sal": "sal",
    "qoelet": "qo", "qo": "qo", "ecclesiaste": "qo",
    "cantico dei cantici": "ct", "cantico": "ct", "ct": "ct",
    "isaia": "is", "is": "is",
    "geremia": "ger", "ger": "ger", "jr": "ger",
    "lamentazioni": "lam", "lam": "lam",
    "ezechiele": "ez", "ez": "ez",
    "daniele": "dn", "dn": "dn",

    # Nuovo
    "matteo": "mt", "mt": "mt",
    "marco": "mc", "mc": "mc",
    "luca": "lc", "lc": "lc",
    "giovanni": "gv", "gv": "gv",
    "atti": "at", "at": "at", "atti degli apostoli": "at",
    "romani": "rm", "rom": "rm", "rm": "rm",
    "1 corinzi": "1cor", "1cor": "1cor", "i corinzi": "1cor",
    "2 corinzi": "2cor", "2cor": "2cor", "ii corinzi": "2cor",
    "efesini": "ef", "ef": "ef",
    "filippesi": "fil", "fil": "fil",
    "colossesi": "col", "col": "col",
    "apocalisse": "ap", "ap": "ap",
}

# Abbreviazioni tipiche in stile "Cfr. Is 12,4-6", "Cfr. Rm 8,15-17"
BOOK_SHORT = {
    "gen": "gen", "gn": "gen",
    "es": "es",
    "nm": "nm",
    "dt": "dt",
    "gs": "gs",
    "gd": "gd",
    "tb": "tb",
    "sal": "sal",
    "qo": "qo",
    "ct": "ct",
    "is": "is",
    "ger": "ger", "jr": "ger",
    "lam": "lam",
    "ez": "ez",
    "dn": "dn",
    "mt": "mt",
    "mc": "mc",
    "lc": "lc",
    "gv": "gv",
    "at": "at",
    "rm": "rm",
    "1cor": "1cor",
    "2cor": "2cor",
    "ef": "ef",
    "fil": "fil",
    "col": "col",
    "ap": "ap",
}

@dataclass(frozen=True)
class Ref:
    book: str                 # canonical key (e.g., "is", "rm", "gv")
    chapter: Optional[int]    # None if unknown
    v1: Optional[int]         # start verse
    v2: Optional[int]         # end verse
    raw: str                  # original snippet

@dataclass
class Song:
    title: str
    url: str
    refs: List[Ref]
    cfr_raw: str

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())

def normalize_book(token: str) -> Optional[str]:
    t = _clean(token).lower()
    t = t.replace(".", "").replace("’", "'")
    t = re.sub(r"\bcapitolo\b", "", t).strip()
    t = re.sub(r"\bvv?\b", "", t).strip()
    t = t.replace("lettera ai ", "").replace("lettera agli ", "").replace("vangelo di ", "")

    # gestisci "1 corinzi", "2 corinzi"
    t = re.sub(r"^i\s+corinzi$", "1 corinzi", t)
    t = re.sub(r"^ii\s+corinzi$", "2 corinzi", t)

    if t in BOOK_ALIASES:
        return BOOK_ALIASES[t]
    if t in BOOK_SHORT:
        return BOOK_SHORT[t]
    return None

def parse_reference_flexible(text: str) -> List[Ref]:
    """
    Estrae uno o più riferimenti da testo libero.
    Supporta:
      - "IS 01, 15-16"
      - "Isaia dal capitolo 30 vv 15 e 16"
      - "Gv 8,31-36"
      - "Rm 8,15-17"
      - "Sal 123 (122)"  -> capitolo=123, versi=None
      - gestisce 'ss' come versi ignoti
    """
    t = _clean(text)
    t_low = t.lower()

    refs: List[Ref] = []

    # 1) pattern stile abbreviato: "Is 12,4-6" / "Rm 8,15-17" / "Gv 8,31-36" / "Sal 65"
    # Consente anche "1 Cor 15" ecc.
    patt1 = re.compile(
        r"\b(?P<book>(?:[12]\s*)?[A-Za-zÀ-ÖØ-öø-ÿ\.]{1,10})\s+"
        r"(?P<chap>\d{1,3})"
        r"(?:\s*,\s*(?P<verses>[\d\-–]+|[\d]+(?:\s*e\s*[\d]+)?|ss))?",
        re.IGNORECASE
    )
    for m in patt1.finditer(t):
        book_raw = _clean(m.group("book"))
        book_key = normalize_book(book_raw.lower().replace(" ", ""))
        if not book_key:
            book_key = normalize_book(book_raw)
        if not book_key:
            continue

        chap = int(m.group("chap"))
        verses = m.group("verses")
        v1 = v2 = None
        if verses:
            verses = verses.strip().lower()
            if verses == "ss":
                v1 = v2 = None
            else:
                # "15-16" or "15–16"
                if re.match(r"^\d+\s*[\-–]\s*\d+$", verses):
                    a, b = re.split(r"[\-–]", verses)
                    v1, v2 = int(a.strip()), int(b.strip())
                # "15 e 16"
                elif re.match(r"^\d+\s*e\s*\d+$", verses):
                    a, b = re.split(r"e", verses)
                    v1, v2 = int(a.strip()), int(b.strip())
                # "15"
                elif re.match(r"^\d+$", verses):
                    v1 = v2 = int(verses)
        refs.append(Ref(book=book_key, chapter=chap, v1=v1, v2=v2, raw=m.group(0)))

    # 2) pattern discorsivo: "isaia dal capitolo 30 vv 15 e 16"
    patt2 = re.compile(
        r"\b(?P<bookname>[A-Za-zÀ-ÖØ-öø-ÿ\s]+?)\s+dal\s+capitolo\s+(?P<chap>\d{1,3})"
        r"(?:\s+v{1,2}\s+(?P<vtext>[\d\s\-–e]+))?",
        re.IGNORECASE
    )
    for m in patt2.finditer(t_low):
        bookname = _clean(m.group("bookname"))
        book_key = normalize_book(bookname)
        if not book_key:
            continue
        chap = int(m.group("chap"))
        vtext = m.group("vtext")
        v1 = v2 = None
        if vtext:
            vtext = _clean(vtext)
            # prova "15-16"
            if re.match(r"^\d+\s*[\-–]\s*\d+$", vtext):
                a, b = re.split(r"[\-–]", vtext)
                v1, v2 = int(a.strip()), int(b.strip())
            # "15 e 16"
            elif re.match(r"^\d+\s*e\s*\d+$", vtext):
                a, b = re.split(r"e", vtext)
                v1, v2 = int(a.strip()), int(b.strip())
            # "15"
            elif re.match(r"^\d+$", vtext):
                v1 = v2 = int(vtext)
        refs.append(Ref(book=book_key, chapter=chap, v1=v1, v2=v2, raw=m.group(0)))

    # dedup grezzo
    uniq = []
    seen = set()
    for r in refs:
        key = (r.book, r.chapter, r.v1, r.v2)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq

def verse_overlap(a: Ref, b: Ref) -> float:
    """Ritorna overlap tra intervalli di versi (0..1). Se versi ignoti -> 0.3 se libro+capitolo match."""
    if a.book != b.book:
        return 0.0
    if a.chapter is None or b.chapter is None:
        return 0.0
    if a.chapter != b.chapter:
        return 0.0
    # se non ho versi, ma ho capitolo: match debole
    if a.v1 is None or b.v1 is None:
        return 0.30
    a1, a2 = a.v1, (a.v2 if a.v2 is not None else a.v1)
    b1, b2 = b.v1, (b.v2 if b.v2 is not None else b.v1)
    inter = max(0, min(a2, b2) - max(a1, b1) + 1)
    union = max(a2, b2) - min(a1, b1) + 1
    return inter / union if union else 0.0

def score_song_for_reading(reading_refs: List[Ref], song: Song) -> float:
    """
    Scoring:
      - match libro+capitolo+versi: fino a 1.0 (max overlap)
      - match libro+capitolo senza versi: 0.30
      - match solo libro: 0.10
      - più riferimenti matching -> somma con cap a 2.5
    """
    if not reading_refs:
        return 0.0

    score = 0.0
    for rr in reading_refs:
        best = 0.0
        for sr in song.refs:
            if rr.book != sr.book:
                continue
            if rr.chapter is None or sr.chapter is None:
                best = max(best, 0.10)
            elif rr.chapter == sr.chapter:
                best = max(best, verse_overlap(rr, sr))
            else:
                best = max(best, 0.10)  # stesso libro, altro capitolo
        score += best
    return min(score, 2.5)

# ----------------------------
# Scraping the site
# ----------------------------

@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_all_songs_from_lista_canti(max_pages: int = 80, polite_sleep: float = 0.25) -> List[Song]:
    """
    Scarica /lista-canti/ e tutte le pagine successive.
    Estrae:
      - titolo canto
      - URL canto
      - righe "Cfr. ..." (se presenti)
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; AuditBot/1.0; +https://example.invalid)"})

    songs: List[Song] = []
    url = START_URL

    for _ in range(max_pages):
        r = session.get(url, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # blocchi: h1 con link (es. # A Te levo i miei occhi)
        # poi un <ul><li> con "Cfr. ...", non sempre presente
        for h in soup.find_all(["h1", "h2"]):
            a = h.find("a", href=True)
            if not a:
                continue
            title = _clean(a.get_text(" ", strip=True))
            href = a["href"]
            if not href.startswith("http"):
                href = BASE + href

            # prova a leggere i riferimenti dal primo <li> successivo
            cfr_text = ""
           # prova a leggere i riferimenti dal primo <ul><li> successivo (saltando i nodi di testo)
ul = h.find_next_sibling("ul")
if ul:
    li = ul.find("li")
    if li:
        cfr_text = _clean(li.get_text(" ", strip=True))
else:
    # fallback: cerca il primo ul dopo l'heading (in caso di markup diverso)
    ul2 = h.find_next("ul")
    if ul2:
        li2 = ul2.find("li")
        if li2:
            cfr_text = _clean(li2.get_text(" ", strip=True))

            # se non è una riga "Cfr." (es. "Canto di Natale") lasciamo refs vuoti
            refs = []
            if cfr_text.lower().startswith("cfr"):
                # può contenere più riferimenti separati da ; oppure virgole
                # prendiamo tutto e lasciamo a parse_reference_flexible il lavoro
                refs = parse_reference_flexible(cfr_text.replace("Cfr.", "").replace("cfr.", "").strip())

            songs.append(Song(title=title, url=href, refs=refs, cfr_raw=cfr_text))

        # trova link "Successivo >>"
        next_link = None
        for a in soup.find_all("a", href=True):
            if "Successivo" in a.get_text():
                next_link = a["href"]
                break
        if not next_link:
            break
        url = next_link if next_link.startswith("http") else BASE + next_link
        time.sleep(polite_sleep)

    # dedup per titolo+url
    uniq = {}
    for s in songs:
        key = (s.title.lower(), s.url)
        if key not in uniq:
            uniq[key] = s
    return list(uniq.values())

# ----------------------------
# UI
# ----------------------------

st.set_page_config(page_title="Canti ↔ Letture (Risuscitò)", layout="wide")

st.title("Abbina canti del Risuscitò alle letture bibliche (3 per lettura)")

with st.expander("Come funziona"):
    st.write(
        "L’app indicizza i canti dalla pagina 'Lista canti' del sito (righe 'Cfr. ...') "
        "e calcola un punteggio di aderenza per ogni lettura inserita."
    )

songs = fetch_all_songs_from_lista_canti()

col1, col2 = st.columns([2, 1])

with col1:
    readings_input = st.text_area(
        "Inserisci le letture (una per riga). Esempi: "
        "`IS 01, 15-16`  |  `Isaia dal capitolo 30 vv 15 e 16`  |  `Gv 8,31-36`",
        height=140
    )

with col2:
    st.metric("Canti indicizzati", value=len(songs))
    min_score = st.slider("Soglia minima (filtra)", 0.0, 2.5, 0.15, 0.05)

if st.button("Trova canti", type="primary"):
    lines = [l.strip() for l in readings_input.splitlines() if l.strip()]
    if not lines:
        st.warning("Inserisci almeno una lettura.")
        st.stop()

    for line in lines:
        refs = parse_reference_flexible(line)
        st.subheader(line)

        if not refs:
            st.info("Non riesco a interpretare il riferimento. Prova con una forma tipo: 'Is 30,15-16' o 'Isaia capitolo 30 vv 15-16'.")
            continue

        scored = []
        for s in songs:
            sc = score_song_for_reading(refs, s)
            if sc >= min_score:
                scored.append((sc, s))
        scored.sort(key=lambda x: x[0], reverse=True)

        top = scored[:3]
        if not top:
            st.write("Nessun canto sopra soglia. Abbassa la soglia oppure prova una forma più canonica del riferimento.")
            continue

        for rank, (sc, s) in enumerate(top, start=1):
            st.markdown(
                f"**{rank}. [{s.title}]({s.url})** — punteggio: `{sc:.2f}`  \n"
                f"<span style='color:gray'>Rif. sito: {s.cfr_raw or '—'}</span>",
                unsafe_allow_html=True
            )

    st.caption("Suggerimento: se vuoi più precisione, usa sempre 'Libro capitolo,versi' (es. 'Is 30,15-16').")
