#!/usr/bin/env python3
"""Daily job collector for the Development Jobs Atlas (khounkham.org).

Reads public job feeds that need no registration and writes docs/jobs.json,
which GitHub Pages serves to the map page.

Sources
  - UN Careers RSS (all UN Secretariat openings): https://careers.un.org/jobfeed
  - ReliefWeb Jobs RSS (UN agencies, NGOs, Red Cross ...): https://reliefweb.int/jobs/rss.xml
    plus one search feed per country in COUNTRY_SEARCHES for wider coverage
  - UNICEF Careers RSS (all UNICEF vacancies, official PageUp feed): https://careers.pageuppeople.com/671/cw/en-us/rss
  - Optional: the ReliefWeb API, used instead of RSS when the repository secret
    RW_APPNAME holds an approved appname (gives complete, structured data).

Each job is matched to a city and country with the offline GeoNames gazetteer
(geonamescache, CC-BY GeoNames) so the map can place it.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import geonamescache

UA = "khounkham.org Development Jobs Atlas collector (+https://khounkham.org)"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "jobs.json")
TODAY = dt.date.today()

UN_FEED = "https://careers.un.org/jobfeed"
UNICEF_FEED = "https://careers.pageuppeople.com/671/cw/en-us/rss"
RW_FEED = "https://reliefweb.int/jobs/rss.xml"
# Extra ReliefWeb search feeds widen coverage beyond the newest items.
COUNTRY_SEARCHES = [
    "Afghanistan", "Bangladesh", "Burkina Faso", "Cameroon", "Central African Republic", "Chad",
    "Colombia", "Democratic Republic of the Congo", "Denmark", "Ethiopia", "Haiti", "India", "Indonesia",
    "Iraq", "Jordan", "Kenya", "Lao People's Democratic Republic", "Lebanon", "Libya", "Madagascar",
    "Mali", "Mozambique", "Myanmar", "Nepal", "Niger", "Nigeria", "occupied Palestinian territory",
    "Pakistan", "Philippines", "Senegal", "Somalia", "South Sudan", "Sudan", "Switzerland",
    "Syrian Arab Republic", "Thailand", "Türkiye", "Uganda", "Ukraine", "United States of America",
    "Venezuela", "Yemen", "Zimbabwe",
]

# ---------------------------------------------------------------- gazetteer
gc = geonamescache.GeonamesCache()
COUNTRIES = gc.get_countries()                       # by ISO2
ISO3 = {c["iso3"]: c for c in COUNTRIES.values()}
CITIES = list(gc.get_cities().values())

def norm(s):
    s = html.unescape(s or "").lower()
    s = re.sub(r"[’'`]", "", s)
    return re.sub(r"[^a-z0-9à-ÿ]+", " ", s).strip()

COUNTRY_ALIASES = {
    "occupied palestinian territory": "PS", "palestine": "PS", "state of palestine": "PS",
    "democratic republic of the congo": "CD", "dr congo": "CD", "drc": "CD", "congo": "CG",
    "lao peoples democratic republic": "LA", "lao pdr": "LA", "laos": "LA",
    "syrian arab republic": "SY", "syria": "SY", "iran islamic republic of": "IR", "iran": "IR",
    "united republic of tanzania": "TZ", "tanzania": "TZ", "viet nam": "VN", "vietnam": "VN",
    "türkiye": "TR", "turkiye": "TR", "turkey": "TR", "russian federation": "RU", "russia": "RU",
    "republic of korea": "KR", "south korea": "KR", "democratic peoples republic of korea": "KP",
    "bolivia plurinational state of": "BO", "bolivia": "BO", "venezuela bolivarian republic of": "VE",
    "united kingdom of great britain and northern ireland": "GB", "united kingdom": "GB", "uk": "GB",
    "united states of america": "US", "united states": "US", "usa": "US", "cote divoire": "CI",
    "côte divoire": "CI", "ivory coast": "CI", "eswatini": "SZ", "swaziland": "SZ",
    "north macedonia": "MK", "moldova": "MD", "republic of moldova": "MD", "myanmar": "MM",
    "netherlands kingdom of the": "NL", "netherlands": "NL", "czechia": "CZ", "kosovo": "XK",
    "cabo verde": "CV", "timor leste": "TL", "micronesia federated states of": "FM",
}
COUNTRY_BY_NAME = {norm(c["name"]): iso for iso, c in COUNTRIES.items()}
COUNTRY_BY_NAME.update(COUNTRY_ALIASES)
COUNTRY_NAMES_SORTED = sorted(COUNTRY_BY_NAME, key=len, reverse=True)

CITY_INDEX = {}
for c in CITIES:
    for n in {c["name"], *[a for a in c.get("alternatenames", []) if a.isascii() and len(a) > 2]}:
        CITY_INDEX.setdefault(norm(n), []).append(c)
for k in CITY_INDEX:
    CITY_INDEX[k].sort(key=lambda c: -c["population"])

CITY_FIXES = {  # UN duty-station spellings → gazetteer names
    "new york": "New York City", "addis ababa": "Addis Ababa", "ulan bator": "Ulaanbaatar",
    "almaty alma ata": "Almaty", "mogadiscio": "Mogadishu", "ndjamena": "N'Djamena",
    "nuku alofa": "Nuku‘alofa", "ashkhabad": "Ashgabat", "cox s bazaar": "Cox's Bazar",
    "coxs bazaar": "Cox's Bazar", "east jerusalem": "Jerusalem", "port au prince": "Port-au-Prince",
    "dar es salaam": "Dar es Salaam", "nyhq": "New York City", "new york hq": "New York City", "nyc": "New York City", "the hague": "The Hague", "jakarta pusat": "Jakarta",
}

def country_from_text(text):
    t = " " + norm(text) + " "
    for n in COUNTRY_NAMES_SORTED:
        if len(n) > 3 and f" {n} " in t:
            return COUNTRY_BY_NAME[n]
    return None

def country_iso2(name):
    if not name:
        return None
    n = norm(name)
    return COUNTRY_BY_NAME.get(n) or (ISO3[name.upper()]["iso"] if name.upper() in ISO3 else None)

def geocode(city, iso2=None):
    """Return (city_name, iso2, lat, lon) or None."""
    if not city:
        return None
    raw = city
    m = re.match(r"^([A-Za-z]{3})\s+-\s+", raw.strip())  # e.g. "PAN - Other cities"
    if m and m.group(1).upper() in ISO3:
        return (None, ISO3[m.group(1).upper()]["iso"], None, None)
    city = re.sub(r"\(.*?\)", " ", city)
    city = re.sub(r"(?i)\b(hq|local|other cities|home ?based|remote|various|multiple)\b", " ", city)
    city = re.sub(r"-\s*$", "", city).strip(" -,")
    key = norm(city)
    key = norm(CITY_FIXES.get(key, city))
    cands = CITY_INDEX.get(key) or []
    if iso2:
        cands = [c for c in cands if c["countrycode"] == iso2] or ([] if cands else [])
    if not cands:
        m = re.match(r"^([A-Za-z]{3})\s*-", raw.strip())  # e.g. "PAN - Other cities"
        if m and m.group(1).upper() in ISO3:
            return (None, ISO3[m.group(1).upper()]["iso"], None, None)
        return None
    c = cands[0]
    return (c["name"], c["countrycode"], round(c["latitude"], 4), round(c["longitude"], 4))

def capital_point(iso2):
    cap = COUNTRIES.get(iso2, {}).get("capital")
    g = geocode(cap, iso2) if cap else None
    return (g[2], g[3]) if g and g[2] is not None else (None, None)

# ---------------------------------------------------------------- helpers
def fetch(url, data=None, headers=None, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            print(f"  ! {url[:90]} → {e}", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None

def text(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""

def strip_html(s):
    s = re.sub(r"<br\s*/?>|</(p|div|li|tr)>", "\n", s or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", s))

def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return dt.date(*map(int, m.groups()))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    m = re.search(r"(\d{1,2}) ([A-Za-z]{3})[a-z]* (\d{4})", s)
    if m:
        try:
            return dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date()
        except ValueError:
            pass
    return None

def field(blob, *labels):
    for lab in labels:
        m = re.search(rf"{lab}[ \t]*:[ \t]*([^\n<]*\S)", blob, flags=re.I)
        if m:
            return m.group(1).strip()
    return ""

# ---------------------------------------------------------------- sources
def from_un_careers():
    raw = fetch(UN_FEED)
    if not raw:
        return []
    out = []
    for it in ET.fromstring(raw).iter("item"):
        d = strip_html(text(it, "description"))
        station = field(d, "Duty Station", "Location")
        g = geocode(station)
        close = parse_date(field(d, "Deadline"))
        out.append({
            "t": html.unescape(text(it, "title")).strip(),
            "org": field(d, "Department/Office", "Department", "Organization", "Entity") or "United Nations",
            "grade": field(d, "Level", "Grade"),
            "station": station.title(),
            "geo": g,
            "close": close.isoformat() if close else "",
            "posted": (parse_date(field(d, "Posted Date")) or TODAY).isoformat(),
            "url": text(it, "link") or text(it, "guid"),
            "src": "UN Careers",
        })
    return out

def local(tag):
    return tag.rsplit("}", 1)[-1]

GRADE_RE = re.compile(r"\b(ASG|USG|[PDG]-?[1-7]|NO-?[A-E]|GS-?[1-7])\b")

def unicef_url(u):
    """Old PageUp job links now redirect to the generic search page; use the job's own page on jobs.unicef.org."""
    m = re.search(r"pageuppeople\.com/671/[^/]+/[^/]+/job/(\d+)", u or "")
    return f"https://jobs.unicef.org/en-us/job/{m.group(1)}" if m else u

def from_unicef():
    """UNICEF's own vacancy feed (PageUp). Fields arrive as job:* elements."""
    raw = fetch(UNICEF_FEED)
    if not raw:
        return []
    out = []
    for it in ET.fromstring(raw).iter("item"):
        f = {}
        for ch in it:
            f.setdefault(local(ch.tag), (ch.text or "").strip())
        title = html.unescape(f.get("title", "")).strip()
        loc = f.get("location", "")                       # e.g. "Headquarters|Denmark", "Eastern and Southern Africa|Kenya"
        country = loc.split("|")[-1].strip() if loc else ""
        iso2 = country_iso2(country) or country_from_text(title)
        g = None
        # the duty station is usually one of the comma-separated parts of the title
        for part in reversed(re.split(r"[,;/]| - ", title)):
            part = re.sub(r"#\d+|\(.*?\)|\d+\s*(months?|mois|meses)|remote|home.?based|\bUNICEF\b", " ", part, flags=re.I).strip(" -–")
            if 2 < len(part) < 40:
                g2 = geocode(part, iso2)
                if g2 and g2[0]:
                    g = g2
                    break
        if not g and iso2:
            g = (None, iso2, None, None)
        m = GRADE_RE.search(title.upper())
        grade = " · ".join(x for x in [m.group(1) if m else "", f.get("workType", "")] if x)
        cats = [c.split("|")[-1].strip() for c in f.get("category", "").split(",") if c.strip()]
        close = parse_date(f.get("closingDate", ""))
        out.append({
            "t": re.sub(r"\s*#\d+\s*$", "", title), "org": "UNICEF", "grade": grade,
            "cat": " · ".join(dict.fromkeys(cats)), "station": "", "geo": g,
            "close": close.isoformat() if close else "",
            "posted": (parse_date(f.get("pubDate", "")) or TODAY).isoformat(),
            "url": unicef_url(f.get("link", "") or f.get("guid", "")), "src": "UNICEF Careers",
        })
    return out

def rw_items(url):
    raw = fetch(url)
    if not raw:
        return []
    out = []
    ns = {"dc": "http://purl.org/dc/elements/1.1/", "content": "http://purl.org/rss/1.0/modules/content/"}
    for it in ET.fromstring(raw).iter("item"):
        body = text(it, "description") + "\n" + (it.findtext("content:encoded", "", ns) or "")
        d = strip_html(body)
        cats = [c.text.strip() for c in it.findall("category") if c.text]
        country = field(d, "Country", "Countries", "Location")
        iso2 = None
        for c in [country, *cats]:
            iso2 = country_iso2(c.split(",")[0]) if c else None
            if iso2:
                break
        title = html.unescape(text(it, "title"))
        iso2 = iso2 or country_from_text(title) or country_from_text(d[:600])
        city = field(d, "City", "Duty station", "Duty Station")
        g = geocode(city.split(",")[0], iso2) if city else None
        if not g and iso2:
            for m in re.finditer(r"\b([A-Z][a-zà-ÿ'\-]+(?: [A-Z][a-zà-ÿ'\-]+)?)\b", title):
                g2 = geocode(m.group(1), iso2)
                if g2 and g2[0]:
                    g = g2
                    break
        if not g and iso2:
            g = (None, iso2, None, None)
        org = field(d, "Organization", "Organisation", "Source") or it.findtext("dc:creator", "", ns) or text(it, "author")
        close = parse_date(field(d, "Closing date", "Closing Date", "Deadline"))
        out.append({
            "t": title.strip(), "org": org.strip(), "grade": field(d, "Type", "Job type", "Experience"),
            "station": city, "geo": g, "close": close.isoformat() if close else "",
            "posted": (parse_date(text(it, "pubDate")) or TODAY).isoformat(),
            "url": text(it, "link"), "src": "ReliefWeb",
        })
    return out

def from_reliefweb_rss():
    jobs = rw_items(RW_FEED)
    for c in COUNTRY_SEARCHES:
        jobs += rw_items(RW_FEED + "?" + urllib.parse.urlencode({"search": c}))
        time.sleep(1.0)  # be polite
    return jobs

def from_reliefweb_api(appname):
    jobs, offset = [], 0
    while offset < 3000:
        body = {"limit": 1000, "offset": offset, "sort": ["date.created:desc"],
                "filter": {"field": "date.closing", "value": {"from": TODAY.isoformat() + "T00:00:00+00:00"}},
                "fields": {"include": ["title", "url_alias", "url", "source.shortname", "source.name", "country.name",
                                       "country.iso3", "city.name", "date.closing", "date.created", "type.name", "experience.name"]}}
        raw = fetch(f"https://api.reliefweb.int/v2/jobs?appname={urllib.parse.quote(appname)}", json.dumps(body).encode(),
                    {"Content-Type": "application/json"})
        if not raw:
            break
        data = json.loads(raw).get("data", [])
        for j in data:
            f = j.get("fields", {})
            iso3 = ((f.get("country") or [{}])[0].get("iso3") or "").upper()
            iso2 = ISO3[iso3]["iso"] if iso3 in ISO3 else None
            city = ((f.get("city") or [{}])[0].get("name") or "")
            g = geocode(city.split(",")[0], iso2) if city else (None, iso2, None, None) if iso2 else None
            src = (f.get("source") or [{}])[0]
            close = (f.get("date") or {}).get("closing", "")[:10]
            jobs.append({"t": f.get("title", ""), "org": src.get("shortname") or src.get("name", ""),
                         "grade": " · ".join([x["name"] for x in f.get("type", [])] + [x["name"] for x in f.get("experience", [])]),
                         "station": city, "geo": g, "close": close,
                         "posted": (f.get("date") or {}).get("created", "")[:10] or TODAY.isoformat(),
                         "url": f.get("url_alias") or f.get("url"), "src": "ReliefWeb"})
        if len(data) < 1000:
            break
        offset += 1000
    return jobs

# ---------------------------------------------------------------- alert feeds
# Ready-made RSS feeds people can follow in a feed reader or an RSS-to-email
# service of their choice. Nobody's personal data passes through this project.
SITE = "https://www.khounkham.org/dev-jobs"
AREAS = [
    ("fin", "Finance, budget & accounting", r"financ|budget|account|treasur|payroll|fiscal|comptab|contabil|tesorer"),
    ("audit", "Audit, oversight & ethics", r"audit|oversight|investigat|ethic|complian|risk|fraud|integrit|inspect"),
    ("hr", "Human resources", r"human resource|\bhr\b|recruit|talent|staff development|people (and|&) culture|ressources humaines|recursos humanos"),
    ("supply", "Supply chain, procurement & logistics", r"supply|procure|logistic|warehouse|fleet|shipping|customs|contract management|approvisionnement|achat|adquisicion|compras"),
    ("admin", "Administration & operations", r"admin|operations|office manag|facilit|driver|clerk|assistant|secretar|travel|general service|support officer"),
    ("it", "IT, data & digital", r"\bit\b|information tech|\bict\b|digital|software|developer|data|database|cyber|\bgis\b|systems|analytics|information management|technolog"),
    ("prog", "Programme & project management", r"programme|program|project|coordinat|portfolio|grant|manager|programa|proyecto|projet"),
    ("mel", "Monitoring, evaluation & research", r"monitor|evaluat|\bm&e\b|\bmeal\b|research|survey|statistic|knowledge|analys|assessment|suivi|investigaci"),
    ("comms", "Communications & advocacy", r"communicat|media|advoca|editor|writer|public information|outreach|campaign|content|social media|press"),
    ("part", "Partnerships & resource mobilization", r"partnership|resource mobili|fundrais|donor|business development|private sector"),
    ("health", "Health & nutrition", r"health|medic|nurse|doctor|nutrition|epidemi|clinic|pharmac|hiv|malaria|tuberc|immuni|vaccin|santé|salud|mental"),
    ("wash", "Water, sanitation & hygiene", r"\bwash\b|water|sanitat|hygien|\beau\b|agua"),
    ("edu", "Education", r"educat|teacher|school|learning|éducation|educaci"),
    ("prot", "Protection & child protection", r"protect|child|gbv|gender.based|safeguard|psychosocial|case work|caseworker|refugee status|resettlement|protecci"),
    ("hum", "Humanitarian & emergency", r"humanitarian|emergenc|crisis|disaster|relief|preparedness|response|\bcash\b|shelter|\bcamp\b|urgence|emergencia"),
    ("food", "Food security, agriculture & livelihoods", r"food|agricult|livelihood|farm|rural|fisher|livestock|resilien"),
    ("env", "Environment, climate & energy", r"environment|climate|energy|biodivers|sustainab|forest|ocean|renewable|carbon|ambient|climat"),
    ("econ", "Economics, policy & governance", r"econom|policy|governance|public financ|trade|development effect|planning|poverty|social protection|politi|gobernanza"),
    ("legal", "Legal & human rights", r"legal|lawyer|counsel|juris|human rights|justice|rule of law|jurid|derecho"),
    ("gender", "Gender & inclusion", r"gender|women|inclusion|disabilit|diversity|equality"),
    ("eng", "Engineering & infrastructure", r"engineer|infrastruct|construct|architect|civil works|technician|electric|mechanic|ingénieur|ingenier"),
    ("sec", "Security & safety", r"security|safety|guard|seguridad|sécurité"),
    ("lang", "Languages & conference services", r"translat|interpret|verbatim|conference|langu|traduct"),
    ("peace", "Political affairs & peacebuilding", r"political|peace|mediat|conflict|electoral|civil affairs|disarm|demobil"),
]
AREA_RE = [(k, n, re.compile(p, re.I)) for k, n, p in AREAS]
LEVELS = {"P": "Professional (P)", "NO": "National Officer (NO)", "G": "General Service (G)", "D": "Director (D)",
          "CON": "Consultancy", "INT": "Internship"}

def areas_of(j):
    text = j["t"] + (" | " + j.get("cat", "") if j.get("cat") else "")
    out = []
    for k, _, rx in AREA_RE:
        if rx.search(text):
            if k == "sec" and re.search(r"(food|nutrition|social|water|energy) security", text, re.I) and not re.search(r"\bsecurity (officer|assistant|advisor|adviser|analyst|coordinator|manager|specialist)", text, re.I):
                continue
            out.append(k)
    return out

def level_of(j):
    g, x = j.get("grade", "").upper(), (j.get("grade", "") + " | " + j["t"]).upper()
    if re.search(r"\b(ASG|USG|D-?[12])\b", g): return "D"
    if re.search(r"\bP-?[1-6]\b", g): return "P"
    if re.search(r"\bNO-?[A-E]\b|NATIONAL OFFICER", g): return "NO"
    if re.search(r"\bG-?[1-7]\b|\bGS-?\d\b|GENERAL SERVICE", g): return "G"
    if re.search(r"\bI-?1\b|\bINTERN(S|SHIP|SHIPS)?\b|\bSTAGIAIRE\b|\bPASANT", x): return "INT"
    if re.search(r"\bCON\b|CONSULT|\bIC\b|INDIVIDUAL CONTRACT|LICA|IICA", x): return "CON"
    if re.search(r"\bP-?[1-6]\b", j["t"].upper()): return "P"
    return ""

def rss(path, title, jobs):
    from email.utils import format_datetime
    items = []
    for j in sorted(jobs, key=lambda j: (j.get("first_seen", ""), j["close"]), reverse=True)[:100]:
        where = ", ".join(x for x in [j.get("city"), j.get("country")] if x)
        bits = [b for b in [j.get("org"), where, j.get("grade"), ("closes " + j["close"]) if j.get("close") else ""] if b]
        d = dt.datetime.fromisoformat((j.get("first_seen") or TODAY.isoformat()) + "T06:00:00+00:00")
        items.append(
            "<item><title>{}</title><link>{}</link><guid isPermaLink=\"true\">{}</guid><pubDate>{}</pubDate><description>{}</description></item>".format(
                html.escape(f'{j["t"]} — {j.get("org","")}' + (f" ({where})" if where else "")), html.escape(j["url"]), html.escape(j["url"]),
                format_datetime(d), html.escape(" · ".join(bits))))
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0"><channel>'
           f"<title>{html.escape(title)} — Development Jobs Atlas</title><link>{SITE}</link>"
           f"<description>{html.escape(title)}. Collected daily from UN Careers, UNICEF Careers and ReliefWeb. Always apply on the employer's official site.</description>"
           f"<lastBuildDate>{dt.datetime.now(dt.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>"
           + "".join(items) + "</channel></rss>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)

def write_feeds(jobs):
    base = os.path.join(os.path.dirname(OUT), "feeds")
    os.makedirs(base, exist_ok=True)
    for old in os.listdir(base):
        os.remove(os.path.join(base, old))
    index = {"all": {"title": "All new development jobs", "count": len(jobs)}}
    rss(os.path.join(base, "all.xml"), "All new development jobs", jobs)
    by = {}
    for j in jobs:
        if j.get("iso"):
            by.setdefault(("country", j["iso"], j.get("country") or j["iso"]), []).append(j)
        for a in areas_of(j):
            by.setdefault(("area", a, dict((k, n) for k, n, _ in AREAS)[a]), []).append(j)
        lv = level_of(j)
        if lv:
            by.setdefault(("level", lv, LEVELS[lv]), []).append(j)
    for (kind, key, name), js in by.items():
        title = {"country": f"Jobs in {name}", "area": f"{name} jobs", "level": f"{name} jobs"}[kind]
        rss(os.path.join(base, f"{kind}-{key}.xml"), title, js)
        index[f"{kind}-{key}"] = {"title": title, "count": len(js)}
    with open(os.path.join(base, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {len(index)} alert feeds → {base}")

# ---------------------------------------------------------------- main
def main():
    appname = os.environ.get("RW_APPNAME", "").strip()
    sources = {}
    print("UN Careers …")
    un = from_un_careers(); sources["UN Careers"] = len(un)
    print(f"  {len(un)} items")
    print("UNICEF Careers …")
    uc = from_unicef(); sources["UNICEF Careers"] = len(uc)
    print(f"  {len(uc)} items")
    if appname:
        print("ReliefWeb API …"); rw = from_reliefweb_api(appname)
    else:
        print("ReliefWeb RSS …"); rw = from_reliefweb_rss()
    sources["ReliefWeb"] = len(rw)
    print(f"  {len(rw)} items")

    # remember when each job was first collected, so the map can flag new jobs
    prev = {}
    try:
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
        for oj in old.get("jobs", []):
            prev[oj.get("url")] = oj.get("first_seen") or oj.get("posted") or TODAY.isoformat()
    except (OSError, ValueError):
        pass

    seen, jobs, unplaced = set(), [], 0
    for j in un + uc + rw:
        if not j["url"] or j["url"] in seen or not j["t"]:
            continue
        seen.add(j["url"])
        if j["close"]:
            try:
                if dt.date.fromisoformat(j["close"]) < TODAY:
                    continue
            except ValueError:
                j["close"] = ""
        j["first_seen"] = prev.get(j["url"]) or (TODAY.isoformat() if prev else j.get("posted") or TODAY.isoformat())
        g = j.pop("geo")
        if not g:
            unplaced += 1
            j.update(city="", iso="", country="", lat=None, lon=None, approx=False)
        else:
            city, iso2, lat, lon = g
            approx = lat is None
            if approx:
                lat, lon = capital_point(iso2)
            c = COUNTRIES.get(iso2, {})
            j.update(city=city or "", iso=str(c.get("isonumeric", "")).zfill(3) if c else "", country=c.get("name", ""),
                     lat=lat, lon=lon, approx=approx)
        jobs.append(j)

    jobs.sort(key=lambda j: (j["close"] or "9999", j["t"]))
    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "sources": sources,
        "count": len(jobs),
        "new_today": sum(1 for j in jobs if j["first_seen"] == TODAY.isoformat()),
        "unplaced": unplaced,
        "attribution": "Job data: UN Careers (careers.un.org) and ReliefWeb (reliefweb.int, UN OCHA). Places: GeoNames (CC BY 4.0).",
        "jobs": jobs,
    }
    if not jobs:
        print("No jobs collected — keeping the previous file.", file=sys.stderr)
        sys.exit(0)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {len(jobs)} jobs ({unplaced} without a location) → {OUT}")
    write_feeds(jobs)

if __name__ == "__main__":
    main()
