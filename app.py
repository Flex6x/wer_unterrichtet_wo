#!/usr/bin/env python3
"""
Stundenplan24 Web-App
Flask-Backend – basiert 1:1 auf dem funktionierenden stundenplan_scraper.py

Installation:
  pip install flask selenium beautifulsoup4 lxml

Starten:
  python app.py
  → http://localhost:5000
"""

import sys
import time
import threading
import warnings
from datetime import datetime
from collections import defaultdict
from urllib.parse import urlparse, urlunparse

from flask import Flask, jsonify, render_template
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

URL      = "https://www.stundenplan24.de/10237223/wplan/"
USERNAME = "schueler"
PASSWORD = "Lempel"
TAGE     = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]

# ─── Cache ────────────────────────────────────────────────────────────────────

cache = {
    "entries":     [],
    "teachers":    [],
    "last_update": None,
    "status":      "idle",
    "error_msg":   "",
    "klassen":     [],
    "woche_info":  "",
}
cache_lock = threading.Lock()

# ─── Scraper (identisch mit stundenplan_scraper.py) ───────────────────────────

def start_browser():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    # KEIN headless – genau wie --kein-headless im Script
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    return webdriver.Chrome(options=opts)


def login(driver):
    """Identisch mit der funktionierenden login()-Funktion."""
    parsed = urlparse(URL)
    auth_url = urlunparse(parsed._replace(
        netloc=f"{USERNAME}:{PASSWORD}@{parsed.netloc}"
    ))
    driver.get(auth_url)
    time.sleep(3)


def wait_for_plan(driver, timeout=10):
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
    from selenium.webdriver.common.by import By
    try:
        sel = driver.find_element(By.ID, "selectfuer")
        options = sel.find_elements(By.TAG_NAME, "option")
        return [(opt.get_attribute("value"), opt.text.strip()) for opt in options]
    except Exception:
        return []


def get_woche_info(driver):
    try:
        text = driver.execute_script(
            "var s=document.getElementById('selectsw'); return s ? s.options[s.selectedIndex].text : '';"
        )
        return text or ""
    except Exception:
        return ""


def select_klasse(driver, value):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select
    try:
        sel = driver.find_element(By.ID, "selectfuer")
        Select(sel).select_by_value(str(value))
        time.sleep(2)
        return True
    except Exception:
        return False


def parse_stundenplan_html(html, klasse_name):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "tableplan"})
    if not table:
        return []

    rows = list(table.find_all("tr"))
    if not rows:
        return []

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

        stunde_nr = stunde_per_row.get(row_idx, "")
        if not stunde_nr:
            for r in range(max(0, row_idx - 3), row_idx + 4):
                if r in stunde_per_row:
                    stunde_nr = stunde_per_row[r]
                    break

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
                "tag":       tag,
                "stunde":    stunde_nr,
                "fach":      faecher[i] if i < len(faecher) else (faecher[0] if faecher else ""),
                "lehrer":    lehrer,
                "klasse":    klasse_name,
                "raum":      raeume[i] if i < len(raeume) else (raeume[0] if raeume else ""),
                "aenderung": aenderung,
                "hinweis":   hinweis,
            })

    return entries


def run_scraper():
    with cache_lock:
        if cache["status"] == "scraping":
            return
        cache["status"]    = "scraping"
        cache["error_msg"] = ""

    driver = None
    try:
        driver = start_browser()
        login(driver)

        # Nach Login erscheint die Auswahlseite – erste Klasse per JS laden
        try:
            driver.execute_script("ElementWechsel('5a', 25);")
        except Exception:
            pass
        time.sleep(2)

        if not wait_for_plan(driver, timeout=12):
            raise RuntimeError("Stundenplan nicht geladen – Login fehlgeschlagen?")

        klassen = get_klassen_options(driver)
        if not klassen:
            raise RuntimeError("Keine Klassen gefunden")

        woche_info   = get_woche_info(driver)
        all_entries  = []
        loaded_kl    = []

        for i, (val, name) in enumerate(klassen):
            if i > 0:
                if not select_klasse(driver, val):
                    continue
                if not wait_for_plan(driver, timeout=8):
                    continue

            html = driver.execute_script("return document.body.innerHTML")
            entries = parse_stundenplan_html(html, name)
            all_entries.extend(entries)
            loaded_kl.append(name)

        teachers = sorted(set(e["lehrer"] for e in all_entries if e["lehrer"]))

        with cache_lock:
            cache["entries"]     = all_entries
            cache["teachers"]    = teachers
            cache["last_update"] = datetime.now()
            cache["status"]      = "ready"
            cache["klassen"]     = loaded_kl
            cache["woche_info"]  = woche_info

    except Exception as e:
        import traceback
        with cache_lock:
            cache["status"]    = "error"
            cache["error_msg"] = traceback.format_exc()
    finally:
        if driver:
            driver.quit()


# ─── Flask ────────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    with cache_lock:
        last = cache["last_update"]
        return jsonify({
            "status":      cache["status"],
            "error":       cache["error_msg"],
            "last_update": last.strftime("%d.%m.%Y %H:%M") if last else None,
            "teachers":    len(cache["teachers"]),
            "entries":     len(cache["entries"]),
            "klassen":     cache["klassen"],
            "woche_info":  cache["woche_info"],
        })


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    with cache_lock:
        if cache["status"] == "scraping":
            return jsonify({"ok": False, "msg": "Läuft bereits"}), 409
    threading.Thread(target=run_scraper, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/teachers")
def api_teachers():
    with cache_lock:
        return jsonify(cache["teachers"])


@app.route("/api/teacher/<name>")
def api_teacher(name):
    with cache_lock:
        entries = [e for e in cache["entries"] if e["lehrer"].lower() == name.lower()]

    if not entries:
        return jsonify({"error": "Nicht gefunden"}), 404

    result = {}
    for tag in TAGE:
        day = [e for e in entries if e["tag"] == tag]
        if day:
            result[tag] = sorted(day, key=lambda x: x["stunde"])

    return jsonify({"teacher": name, "tage": result, "total": len(entries)})


@app.route("/api/debug")
def api_debug():
    with cache_lock:
        return jsonify({
            "status":        cache["status"],
            "error":         cache["error_msg"],
            "entries_count": len(cache["entries"]),
            "teachers_count":len(cache["teachers"]),
            "sample_entries":cache["entries"][:5],
        })


if __name__ == "__main__":
    print("=" * 50)
    print("  Stundenplan24 Web-App")
    print("  http://localhost:5000")
    print("  Beim Start wird automatisch gescrapt.")
    print("  Ein Chrome-Fenster öffnet sich kurz – normal.")
    print("=" * 50)
    threading.Thread(target=run_scraper, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
