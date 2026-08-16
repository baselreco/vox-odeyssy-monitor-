import os
import re
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

VOX_URL = "https://egy.voxcinemas.com/showtimes/city-centre-almaza/the-odyssey"
START_DATE = date(2026, 8, 19)
TZ = ZoneInfo("Africa/Cairo")

PUSHOVER_USER = os.environ["PUSHOVER_USER_KEY"]
PUSHOVER_TOKEN = os.environ["PUSHOVER_APP_TOKEN"]

def notify(title, message, priority=0, url=None):
    data = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": title,
        "message": message,
        "priority": priority,
    }
    if url:
        data["url"] = url
        data["url_title"] = "Open VOX booking"
    r = requests.post("https://api.pushover.net/1/messages.json", data=data, timeout=30)
    r.raise_for_status()

def parse_date_from_text(text):
    # Handles examples such as "Wed 19 Aug", "Thu 20 Aug", etc.
    m = re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})\s+([A-Za-z]{3})\b", text)
    if not m:
        return None
    day = int(m.group(1))
    mon = m.group(2)
    year = datetime.now(TZ).year
    try:
        return datetime.strptime(f"{day} {mon} {year}", "%d %b %Y").date()
    except ValueError:
        return None

def text_has_any(text, phrases):
    low = text.lower()
    return any(p in low for p in phrases)

def inspect_booking_page(page, href):
    """Try to distinguish a real seat-selection page from a dead/unavailable showtime."""
    try:
        page.goto(href, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
    except Exception as e:
        return ("error", f"Could not open booking page: {type(e).__name__}")

    body = page.locator("body").inner_text(timeout=10000)
    low = body.lower()

    unavailable = [
        "sold out", "soldout", "house full", "no seats available",
        "no seats", "unavailable", "not available", "session unavailable",
    ]
    if text_has_any(body, unavailable):
        return ("unavailable", "VOX reports the session as unavailable/sold out.")

    login_markers = ["sign in", "log in", "login", "register"]
    # If a login wall is present but there is no seat map, don't claim availability.
    seat_select_markers = [
        "select your seats", "select seats", "choose your seats",
        "seat selection", "available seats", "screen"
    ]
    seat_dom = page.locator(
        "[class*='seat'], [id*='seat'], [data-seat], "
        "button[aria-label*='seat' i]"
    ).count()

    if text_has_any(body, seat_select_markers) and seat_dom > 0:
        return ("available", "Seat-selection page loaded.")

    if text_has_any(body, login_markers):
        return ("needs_login", "VOX appears to require login before seat selection.")

    # Some VOX booking pages can render a seat map with little text.
    if seat_dom > 0:
        return ("available", "Seat elements were detected.")

    return ("unknown", "Booking page opened, but a seat map could not be confirmed.")

def collect_dates(page):
    """Get date links currently exposed by VOX."""
    result = []
    links = page.locator("a").all()
    for a in links:
        try:
            txt = a.inner_text().strip()
            d = parse_date_from_text(txt)
            href = a.get_attribute("href")
            if d and d >= START_DATE and href:
                result.append((d, href))
        except Exception:
            pass

    # De-duplicate while preserving order.
    seen = set()
    out = []
    for item in sorted(result):
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

def scan():
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    findings = []
    dates_seen = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="en-US",
            timezone_id="Africa/Cairo",
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
                "Mobile/15E148 Safari/604.1"
            ),
        )
        page = context.new_page()
        page.goto(VOX_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)

        date_links = collect_dates(page)

        # If VOX only exposes the current date range, that's okay: the next hourly
        # run will discover any newly released dates.
        for d, href in date_links:
            dates_seen.append(d.isoformat())
            if href.startswith("/"):
                full_href = "https://egy.voxcinemas.com" + href
            else:
                full_href = href

            date_page = context.new_page()
            try:
                date_page.goto(full_href, wait_until="domcontentloaded", timeout=45000)
                date_page.wait_for_timeout(1800)

                # Locate the The Odyssey section, then its IMAX showtime links.
                headings = date_page.get_by_text("The Odyssey", exact=True)
                if headings.count() == 0:
                    continue

                # The page is structured with movie heading followed by cinema/format.
                # Use the full page text to confirm Almaza + IMAX, then collect links
                # whose visible text looks like a time and which are inside the IMAX
                # area when possible.
                body_text = date_page.locator("body").inner_text()
                if "City Centre Almaza" not in body_text:
                    continue

                imax_locator = date_page.get_by_text("IMAX", exact=True)
                if imax_locator.count() == 0:
                    continue

                # Get nearby links by walking up a few DOM levels from the IMAX label.
                show_links = []
                for idx in range(imax_locator.count()):
                    node = imax_locator.nth(idx)
                    for _ in range(5):
                        try:
                            parent = node.locator("xpath=..")
                            if parent.count() == 0:
                                break
                            node = parent
                        except Exception:
                            break

                    links = node.locator("a").all()
                    for a in links:
                        try:
                            txt = a.inner_text().strip()
                            href2 = a.get_attribute("href")
                            if href2 and re.fullmatch(r"\d{1,2}:\d{2}\s*(?:am|pm)", txt, re.I):
                                show_links.append((txt, href2))
                        except Exception:
                            pass

                # Fallback: all time-looking links on the page if the DOM layout changes.
                if not show_links:
                    for a in date_page.locator("a").all():
                        try:
                            txt = a.inner_text().strip()
                            href2 = a.get_attribute("href")
                            if href2 and re.fullmatch(r"\d{1,2}:\d{2}\s*(?:am|pm)", txt, re.I):
                                show_links.append((txt, href2))
                        except Exception:
                            pass

                seen_show = set()
                show_links = [x for x in show_links if not (x in seen_show or seen_show.add(x))]

                for showtime, show_href in show_links:
                    if show_href.startswith("/"):
                        show_href = "https://egy.voxcinemas.com" + show_href
                    booking_page = context.new_page()
                    try:
                        status, detail = inspect_booking_page(booking_page, show_href)
                    finally:
                        booking_page.close()

                    findings.append({
                        "date": d.strftime("%a %d %b"),
                        "time": showtime,
                        "status": status,
                        "detail": detail,
                        "url": show_href,
                    })
            finally:
                date_page.close()

        browser.close()

    return now, dates_seen, findings

def main():
    try:
        now, dates_seen, findings = scan()

        available = [x for x in findings if x["status"] == "available"]
        login = [x for x in findings if x["status"] == "needs_login"]
        unknown = [x for x in findings if x["status"] == "unknown"]

        lines = [
            f"Checked: {now} Cairo time",
            "Cinema: VOX City Centre Almaza",
            "Format: IMAX",
            "Start date: 19 Aug 2026",
            "",
        ]

        if available:
            lines.append("🚨 TICKETS / SEATS APPEAR AVAILABLE:")
            for x in available:
                lines.append(f"• {x['date']} — {x['time']}")
            notify("🚨 VOX IMAX — The Odyssey", "\n".join(lines),
                   priority=1, url=available[0]["url"])
        else:
            lines.append("❌ No confirmed seat availability.")
            if login:
                lines.append(f"⚠️ {len(login)} showtime(s) require VOX login.")
            if unknown:
                lines.append(f"⚠️ {len(unknown)} showtime(s) could not be confirmed.")
            if dates_seen:
                lines.append("Dates currently exposed by VOX: " + ", ".join(dates_seen))
            else:
                lines.append("VOX has not exposed any monitored dates yet.")
            notify("VOX IMAX — The Odyssey", "\n".join(lines), priority=0)

    except Exception as e:
        notify(
            "⚠️ VOX monitor error",
            f"The hourly check failed: {type(e).__name__}: {e}",
            priority=0,
        )
        raise

if __name__ == "__main__":
    main()
