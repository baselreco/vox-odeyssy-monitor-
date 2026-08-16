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

    response = requests.post(
        "https://api.pushover.net/1/messages.json",
        data=data,
        timeout=30,
    )
    response.raise_for_status()


def parse_date_from_text(text):
    match = re.search(
        r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})\s+([A-Za-z]{3})\b",
        text,
    )

    if not match:
        return None

    day = int(match.group(1))
    month = match.group(2)
    year = datetime.now(TZ).year

    try:
        return datetime.strptime(
            f"{day} {month} {year}",
            "%d %b %Y",
        ).date()
    except ValueError:
        return None


def text_has_any(text, phrases):
    low = text.lower()
    return any(phrase in low for phrase in phrases)


def inspect_booking_page(page, href):
    try:
        page.goto(
            href,
            wait_until="domcontentloaded",
            timeout=45000,
        )
        page.wait_for_timeout(2500)

    except Exception as error:
        return (
            "error",
            f"Could not open booking page: {type(error).__name__}",
        )

    try:
        body = page.locator("body").inner_text(timeout=10000)
    except Exception:
        return (
            "error",
            "Could not read booking page.",
        )

    unavailable = [
        "sold out",
        "soldout",
        "house full",
        "no seats available",
        "no seats",
        "unavailable",
        "not available",
        "session unavailable",
    ]

    if text_has_any(body, unavailable):
        return (
            "unavailable",
            "VOX reports the session as unavailable/sold out.",
        )

    seat_select_markers = [
        "select your seats",
        "select seats",
        "choose your seats",
        "seat selection",
        "available seats",
        "screen",
    ]

    seat_dom = page.locator(
        "[class*='seat'], "
        "[id*='seat'], "
        "[data-seat], "
        "button[aria-label*='seat' i]"
    ).count()

    if text_has_any(body, seat_select_markers) and seat_dom > 0:
        return (
            "available",
            "Seat-selection page loaded.",
        )

    login_markers = [
        "sign in",
        "log in",
        "login",
        "register",
    ]

    if text_has_any(body, login_markers):
        return (
            "needs_login",
            "VOX appears to require login before seat selection.",
        )

    if seat_dom > 0:
        return (
            "available",
            "Seat elements were detected.",
        )

    return (
        "unknown",
        "Booking page opened, but a seat map could not be confirmed.",
    )


def collect_dates(page):
    result = []

    links = page.locator("a").all()

    for link in links:
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


def scan():
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")

    findings = []
    dates_seen = []

    with sync_playwright() as playwright:

        # IMPORTANT:
        # Disable HTTP/2 because VOX was returning
        # net::ERR_HTTP2_PROTOCOL_ERROR on GitHub Actions.
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-http2",
            ],
        )

        context = browser.new_context(
            locale="en-US",
            timezone_id="Africa/Cairo",
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/18.0 Mobile/15E148 Safari/604.1"
            ),
        )

        page = context.new_page()

        page.goto(
            VOX_URL,
            wait_until="domcontentloaded",
            timeout=45000,
        )

        page.wait_for_timeout(2500)

        date_links = collect_dates(page)

        for current_date, href in date_links:

            dates_seen.append(
                current_date.isoformat()
            )

            if href.startswith("/"):
                full_href = (
                    "https://egy.voxcinemas.com" + href
                )
            else:
                full_href = href

            date_page = context.new_page()

            try:
                date_page.goto(
                    full_href,
                    wait_until="domcontentloaded",
                    timeout=45000,
                )

                date_page.wait_for_timeout(1800)

                headings = date_page.get_by_text(
                    "The Odyssey",
                    exact=True,
                )

                if headings.count() == 0:
                    continue

                body_text = date_page.locator(
                    "body"
                ).inner_text()

                if "City Centre Almaza" not in body_text:
                    continue

                imax_locator = date_page.get_by_text(
                    "IMAX",
                    exact=True,
                )

                if imax_locator.count() == 0:
                    continue

                show_links = []

                for index in range(
                    imax_locator.count()
                ):

                    node = imax_locator.nth(index)

                    for _ in range(5):
                        try:
                            parent = node.locator(
                                "xpath=.."
                            )

                            if parent.count() == 0:
                                break

                            node = parent

                        except Exception:
                            break

                    links = node.locator("a").all()

                    for link in links:
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

                # Fallback if the VOX page layout changes.
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

                seen_show = set()
                unique_show_links = []

                for item in show_links:
                    if item not in seen_show:
                        seen_show.add(item)
                        unique_show_links.append(item)

                for showtime, show_href in unique_show_links:

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
            item
            for item in findings
            if item["status"] == "available"
        ]

        login = [
            item
            for item in findings
            if item["status"] == "needs_login"
        ]

        unknown = [
            item
            for item in findings
            if item["status"] == "unknown"
        ]

        errors = [
            item
            for item in findings
            if item["status"] == "error"
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

            for item in available:
                lines.append(
                    f"• {item['date']} — {item['time']}"
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
                    f"⚠️ {len(login)} showtime(s) "
                    "require VOX login."
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

    except Exception as error:

        notify(
            "⚠️ VOX monitor error",
            (
                f"The hourly check failed: "
                f"{type(error).__name__}: {error}"
            ),
            priority=0,
        )

        raise


if __name__ == "__main__":
    main()
