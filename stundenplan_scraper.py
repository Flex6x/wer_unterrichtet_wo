#!/usr/bin/env python3
"""
Stundenplan24 Lehrer-Scraper (Selenium)
Steuert einen echten Browser, klickt alle Klassen durch und liest den Stundenplan.

Installation:
  pip install selenium beautifulsoup4 lxml
  
  Außerdem Chrome + ChromeDriver nötig:
  → Option A (empfohlen): pip install selenium  (ab v4.6 lädt ChromeDriver automatisch)
  → Option B: ChromeDriver manuell von https://chromedriver.chromium.org/ laden

Verwendung:
  python stundenplan_scraper.py
  python stundenplan_scraper.py --lehrer Knu
  python stundenplan_scraper.py --list
  python stundenplan_scraper.py --kein-headless   (Browser sichtbar machen zum Debuggen)
"""

import sys
import time
import argparse
import warnings
from collections import defaultdict

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

URL      = "https://www.stundenplan24.de/10237223/wplan/"
USERNAME = "schueler"
PASSWORD = "Lempel"

TAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]


# ─── Browser ─────────────────────────────────────────────────────────────────

def start_browser(headless=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")

    try:
        # Selenium 4.6+: automatischer ChromeDriver-Download
        from selenium.webdriver.chrome.service import Service as ChromeService
        from selenium.webdriver.common.by import By
        driver = webdriver.Chrome(options=opts)
    except Exception as e:
        print(f"Chrome nicht gefunden: {e}")
        print("Tipp: Chrome installieren oder --kein-headless für sichtbaren Browser")
        sys.exit(1)

    return driver


def login(driver, url, username, password):
    """Öffnet die URL mit HTTP Basic Auth im URL."""
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url)
    auth_url = urlunparse(parsed._replace(
        netloc=f"{username}:{password}@{parsed.netloc}"
    ))
    driver.get(auth_url)
    time.sleep(3)  # Seite laden lassen


def wait_for_plan(driver, timeout=10):
    """Wartet bis der Stundenplan geladen ist."""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, "tableplan"))
        )
        return True
    except Exception:
        return False


def get_klassen_options(driver):
    """Liest alle Klassen aus dem Dropdown."""
    from selenium.webdriver.common.by import By
    try:
        sel = driver.find_element(By.ID, "selectfuer")
        options = sel.find_elements(By.TAG_NAME, "option")
        return [(opt.get_attribute("value"), opt.text.strip()) for opt in options]
    except Exception:
        return []


def select_klasse(driver, value):
    """Wählt eine Klasse im Dropdown aus."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select
    try:
        sel = driver.find_element(By.ID, "selectfuer")
        Select(sel).select_by_value(str(value))
        time.sleep(2)  # Plan neu laden lassen
        return True
    except Exception as e:
        print(f"    Fehler beim Wechseln: {e}")
        return False


def get_page_html(driver):
    """Gibt das aktuelle HTML des Body zurück."""
    return driver.execute_script("return document.body.innerHTML")


# ─── HTML Parser (Indiware tableplan) ────────────────────────────────────────

def parse_stundenplan_html(html, klasse_name):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "tableplan"})
    if not table:
        return []

    rows = list(table.find_all("tr"))
    if not rows:
        return []

    # Tages-Spalten aus Kopfzeile
    day_start_cols = {}
    col = 0
    for cell in rows[0].find_all("td"):
        colspan = int(cell.get("colspan", 1))
        text = cell.get_text()
        for tag in TAGE:
            if tag in text:
                day_start_cols[col] = tag
                break
        col += colspan

    if not day_start_cols:
        return []

    # Grid aufbauen (colspan/rowspan)
    grid = {}
    stunde_per_row = {}

    for row_idx, tr in enumerate(rows):
        col_idx = 0
        for td in tr.find_all("td"):
            while (row_idx, col_idx) in grid:
                col_idx += 1
            colspan = int(td.get("colspan", 1))
            rowspan = int(td.get("rowspan", 1))
            sdiv = td.find("div", class_="divstunde")
            if sdiv:
                stunde_per_row[row_idx] = sdiv.get_text(strip=True)
            for r in range(rowspan):
                for c in range(colspan):
                    grid[(row_idx + r, col_idx + c)] = td
            col_idx += colspan

    entries = []
    visited = set()

    for (row_idx, col_idx), td in sorted(grid.items()):
        if id(td) in visited:
            continue
        if "tdstunde" not in td.get("class", []):
            continue
        visited.add(id(td))

        # Stunde
        stunde_nr = stunde_per_row.get(row_idx, "")
        if not stunde_nr:
            for r in range(max(0, row_idx - 3), row_idx + 4):
                if r in stunde_per_row:
                    stunde_nr = stunde_per_row[r]
                    break

        # Tag
        tag = None
        for sc in sorted(day_start_cols.keys(), reverse=True):
            if col_idx >= sc:
                tag = day_start_cols[sc]
                break
        if not tag:
            continue

        faecher    = [s.get_text(strip=True) for s in td.find_all("span", class_="plfach")   if s.get_text(strip=True)]
        lehrer_raw = [s.get_text(strip=True) for s in td.find_all("span", class_="pllehrer") if s.get_text(strip=True)]
        raeume     = [s.get_text(strip=True) for s in td.find_all("span", class_="plraum")   if s.get_text(strip=True)]

        if not faecher and not lehrer_raw:
            continue

        aenderung = bool(td.find("span", class_="plaenderung"))
        haupt = {"plfach", "pllehrer", "plraum", "plfachquer", "pllehrerquer", "plraumquer"}
        hinweis = " | ".join(set(
            s.get_text(strip=True) for s in td.find_all("span")
            if not any(c in haupt for c in s.get("class", []))
            and s.get_text(strip=True) and s.get_text(strip=True) != "---"
        ))

        lehrer_all = [l.strip() for ls in lehrer_raw for l in ls.split(",") if l.strip()] or [""]

        for i, lehrer in enumerate(lehrer_all):
            entries.append({
                "tag":      tag,
                "stunde":   stunde_nr,
                "fach":     faecher[i] if i < len(faecher) else (faecher[0] if faecher else ""),
                "lehrer":   lehrer,
                "klasse":   klasse_name,
                "raum":     raeume[i] if i < len(raeume) else (raeume[0] if raeume else ""),
                "aenderung": aenderung,
                "hinweis":  hinweis,
            })

    return entries


# ─── Ausgabe ──────────────────────────────────────────────────────────────────

def build_teacher_plan(entries):
    plan = defaultdict(lambda: defaultdict(list))
    for e in entries:
        if e["lehrer"]:
            plan[e["lehrer"]][e["tag"]].append(e)
    return plan


def print_teacher(teacher, teacher_plan):
    print(f"\n{'═'*55}")
    print(f"  Lehrer: {teacher}")
    print(f"{'═'*55}")
    found = False
    for tag in TAGE:
        entries = teacher_plan[teacher].get(tag, [])
        if not entries:
            continue
        found = True
        print(f"\n  ▸ {tag}")
        print(f"    {'Std':<6} {'Fach':<14} {'Klasse':<8} {'Raum':<8} Hinweis")
        print(f"    {'─'*6} {'─'*14} {'─'*8} {'─'*8} {'─'*15}")
        for e in sorted(entries, key=lambda x: x["stunde"]):
            aend = " ⚠" if e["aenderung"] else ""
            print(f"    {e['stunde']:<6} {e['fach']:<14} {e['klasse']:<8} {e['raum']:<8} {e['hinweis']}{aend}")
    if not found:
        print("  (keine Einträge diese Woche)")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stundenplan24 Lehrer-Scraper (Selenium)")
    parser.add_argument("--lehrer",        "-l", help="Lehrerkürzel (Teilstring)")
    parser.add_argument("--list",          action="store_true", help="Alle Lehrer auflisten")
    parser.add_argument("--kein-headless", action="store_true", help="Browser sichtbar anzeigen")
    args = parser.parse_args()

    headless = not args.kein_headless

    print("Starte Browser...")
    driver = start_browser(headless=headless)

    try:
        print("Lade Stundenplanseite (mit Login)...")
        login(driver, URL, USERNAME, PASSWORD)

        if not wait_for_plan(driver):
            print("Fehler: Stundenplan nicht geladen. Versuche --kein-headless zum Debuggen.")
            driver.quit()
            sys.exit(1)

        klassen = get_klassen_options(driver)
        if not klassen:
            print("Fehler: Keine Klassen gefunden.")
            driver.quit()
            sys.exit(1)

        print(f"Gefunden: {len(klassen)} Klassen → lese alle Stundenpläne...\n")

        all_entries = []

        for i, (val, name) in enumerate(klassen):
            print(f"  [{i+1:>2}/{len(klassen)}] Klasse {name}...", end=" ", flush=True)

            if i > 0:
                if not select_klasse(driver, val):
                    print("übersprungen")
                    continue
                if not wait_for_plan(driver, timeout=8):
                    print("Timeout")
                    continue

            html = get_page_html(driver)
            entries = parse_stundenplan_html(html, name)
            all_entries.extend(entries)
            print(f"{len(entries)} Einträge")

    finally:
        driver.quit()

    if not all_entries:
        print("\nKeine Daten geladen.")
        sys.exit(1)

    teacher_plan = build_teacher_plan(all_entries)
    all_teachers = sorted(teacher_plan.keys())
    print(f"\n✓ {len(all_entries)} Einträge · {len(all_teachers)} Lehrer geladen\n")

    if args.list:
        print("Alle Lehrer:")
        for t in all_teachers:
            print(f"  {t}")
        return

    if args.lehrer:
        matches = [t for t in all_teachers if args.lehrer.lower() in t.lower()]
        if not matches:
            print(f"Kein Lehrer mit '{args.lehrer}' gefunden.")
            print(f"Alle Lehrer: {', '.join(all_teachers)}")
        for t in matches:
            print_teacher(t, teacher_plan)
        return

    # Interaktiv
    print("Lehrer eingeben (Kürzel oder Nummer, 'q' zum Beenden):")
    for i, t in enumerate(all_teachers, 1):
        print(f"  [{i:>3}] {t}")

    while True:
        try:
            choice = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if choice.lower() in ("q", ""):
            break
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(all_teachers):
                print_teacher(all_teachers[idx], teacher_plan)
            else:
                print("Ungültige Nummer.")
        else:
            matches = [t for t in all_teachers if choice.lower() in t.lower()]
            if not matches:
                print("Nicht gefunden.")
            for t in matches:
                print_teacher(t, teacher_plan)


if __name__ == "__main__":
    main()
