import os
import re
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

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

    r = requests.post(
        "https://api.pushover.net/1/messages.json",
        data=data,
        timeout=30,
    )
    r.raise_for_status()


def parse_date_from_text(text):
    m = re.search(
        r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})\s+([A-Za-z]{3})\b",
        text,
    )

    if not m:
        return None

    try:
        return datetime.strptime(
            f"{m.group(1)} {m.group(2)} {datetime.now(TZ).year}",
            "%d %b %Y",
        ).date()
    except ValueError:
        return None


def get_page(page, url):
    """
    Open VOX with settings that reduce HTTP/2/QUIC problems
    on GitHub Actions.
    """

    page.set_default_timeout(15000)

    page.goto(
        url,
        wait_until="commit",
        timeout=60000,
    )

    page.wait_for_timeout(5000)

    return page


def collect_dates(page):
    result = []

    for link in page.locator("a").all():

        try:
            text = link.inner_text().strip()
            current_date = parse_date_from_text(text)
            href = link.get_attribute("href")

            if (
                current_date
                and current_date >= START_DATE
                and href
            ):
                result.append((current_date, href))

        except Exception:
            pass

    seen = set()
    output = []

    for item in sorted(result):

        if item not in seen:
            seen.add(item)
            output.append(item)

    return output


def inspect_booking_page(page, url):

    try:

        get_page(page, url)

        body = page.locator("body").inner_text(
            timeout=15000
        )

    except Exception as e:

        return (
            "error",
            f"Booking page error: {type(e).__name__}: {e}",
        )

    low = body.lower()

    unavailable_words = [
        "sold out",
        "soldout",
        "house full",
        "no seats available",
        "no seats",
        "unavailable",
        "not available",
        "session unavailable",
    ]

    for word in unavailable_words:

        if word in low:

            return (
                "unavailable",
                "VOX reports this session as unavailable.",
            )

    seat_words = [
        "select your seats",
        "select seats",
        "choose your seats",
        "seat selection",
        "available seats",
    ]

    seat_dom = page.locator(
        "[class*='seat'],"
        "[id*='seat'],"
        "[data-seat],"
        "button[aria-label*='seat' i]"
    ).count()

    if any(word in low for word in seat_words) and seat_dom > 0:

        return (
            "available",
            "Seat-selection page loaded.",
        )

    if seat_dom > 0:

        return (
            "available",
            "Seat elements detected.",
        )

    login_words = [
        "sign in",
        "log in",
        "login",
        "register",
    ]

    if any(word in low for word in login_words):

        return (
            "needs_login",
            "VOX requires login before seat selection.",
        )

    return (
        "unknown",
        "Booking page opened but availability could not be confirmed.",
    )


def scan():

    now = datetime.now(TZ).strftime(
        "%Y-%m-%d %H:%M"
    )

    findings = []
    dates_seen = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-http2",
                "--disable-quic",
                "--disable-features=UseDnsHttpsSvcb",
            ],
        )

        context = browser.new_context(
            locale="en-US",
            timezone_id="Africa/Cairo",
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/139.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        # Open VOX
        try:

            get_page(
                page,
                VOX_URL,
            )

        except Exception as e:

            browser.close()

            raise RuntimeError(
                "VOX main page could not be opened: "
                f"{type(e).__name__}: {e}"
            )

        date_links = collect_dates(page)

        for current_date, href in date_links:

            dates_seen.append(
                current_date.isoformat()
            )

            if href.startswith("/"):

                full_href = (
                    "https://egy.voxcinemas.com"
                    + href
                )

            else:

                full_href = href

            date_page = context.new_page()

            try:

                try:

                    get_page(
                        date_page,
                        full_href,
                    )

                except Exception:

                    continue

                body = date_page.locator(
                    "body"
                ).inner_text(
                    timeout=15000
                )

                if "City Centre Almaza" not in body:
                    continue

                if "The Odyssey" not in body:
                    continue

                imax = date_page.get_by_text(
                    "IMAX",
                    exact=True,
                )

                if imax.count() == 0:
                    continue

                show_links = []

                for i in range(imax.count()):

                    node = imax.nth(i)

                    for _ in range(6):

                        try:

                            parent = node.locator(
                                "xpath=.."
                            )

                            if parent.count() == 0:
                                break

                            node = parent

                        except Exception:

                            break

                    for link in node.locator(
                        "a"
                    ).all():

                        try:

                            text = link.inner_text().strip()
                            href2 = link.get_attribute(
                                "href"
                            )

                            if (
                                href2
                                and re.fullmatch(
                                    r"\d{1,2}:\d{2}\s*(?:am|pm)",
                                    text,
                                    re.I,
                                )
                            ):

                                show_links.append(
                                    (text, href2)
                                )

                        except Exception:

                            pass

                # Fallback
                if not show_links:

                    for link in date_page.locator(
                        "a"
                    ).all():

                        try:

                            text = link.inner_text().strip()
                            href2 = link.get_attribute(
                                "href"
                            )

                            if (
                                href2
                                and re.fullmatch(
                                    r"\d{1,2}:\d{2}\s*(?:am|pm)",
                                    text,
                                    re.I,
                                )
                            ):

                                show_links.append(
                                    (text, href2)
                                )

                        except Exception:

                            pass

                unique = []
                seen = set()

                for item in show_links:

                    if item not in seen:

                        seen.add(item)
                        unique.append(item)

                for showtime, show_href in unique:

                    if show_href.startswith("/"):

                        show_href = (
                            "https://egy.voxcinemas.com"
                            + show_href
                        )

                    booking_page = context.new_page()

                    try:

                        status, detail = inspect_booking_page(
                            booking_page,
                            show_href,
                        )

                    finally:

                        booking_page.close()

                    findings.append(
                        {
                            "date": current_date.strftime(
                                "%a %d %b"
                            ),
                            "time": showtime,
                            "status": status,
                            "detail": detail,
                            "url": show_href,
                        }
                    )

            finally:

                date_page.close()

        browser.close()

    return now, dates_seen, findings


def main():

    try:

        now, dates_seen, findings = scan()

        available = [
            x for x in findings
            if x["status"] == "available"
        ]

        login = [
            x for x in findings
            if x["status"] == "needs_login"
        ]

        unknown = [
            x for x in findings
            if x["status"] == "unknown"
        ]

        errors = [
            x for x in findings
            if x["status"] == "error"
        ]

        lines = [
            f"Checked: {now} Cairo time",
            "Cinema: VOX City Centre Almaza",
            "Format: IMAX",
            "Start date: 19 Aug 2026",
            "",
        ]

        if available:

            lines.append(
                "🚨 TICKETS / SEATS APPEAR AVAILABLE:"
            )

            for x in available:

                lines.append(
                    f"• {x['date']} — {x['time']}"
                )

            notify(
                "🚨 VOX IMAX — The Odyssey",
                "\n".join(lines),
                priority=1,
                url=available[0]["url"],
            )

        else:

            lines.append(
                "❌ No confirmed seat availability."
            )

            if login:

                lines.append(
                    f"⚠️ {len(login)} showtime(s) require VOX login."
                )

            if unknown:

                lines.append(
                    f"⚠️ {len(unknown)} showtime(s) "
                    "could not be confirmed."
                )

            if errors:

                lines.append(
                    f"⚠️ {len(errors)} booking page(s) "
                    "returned an error."
                )

            if dates_seen:

                lines.append(
                    "Dates currently exposed by VOX: "
                    + ", ".join(dates_seen)
                )

            else:

                lines.append(
                    "VOX has not exposed any monitored dates yet."
                )

            notify(
                "VOX IMAX — The Odyssey",
                "\n".join(lines),
                priority=0,
            )

    except Exception as e:

        notify(
            "⚠️ VOX monitor error",
            (
                "The hourly check failed: "
                f"{type(e).__name__}: {e}"
            ),
            priority=0,
        )

        raise


if __name__ == "__main__":
    main()
