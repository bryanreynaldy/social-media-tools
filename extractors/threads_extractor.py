import json
import os
import re
import sys
import tempfile
import subprocess
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.preview import fetch_generic_preview


CLEAN_COLS = ["index", "date", "author", "type", "comment", "like"]
PROGRESS_PREFIX = "__THREADS_PROGRESS__"


RUNNER_CODE = r'''
import sys
import re
import json
import time
import asyncio
import subprocess
from datetime import datetime
from http.cookies import SimpleCookie

from parsel import Selector
from nested_lookup import nested_lookup
from playwright.async_api import async_playwright

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

PROGRESS_PREFIX = "__THREADS_PROGRESS__"


def emit_progress(message, progress):
    try:
        print(
            PROGRESS_PREFIX + json.dumps(
                {
                    "message": message,
                    "progress": int(progress)
                },
                ensure_ascii=False
            ),
            flush=True
        )
    except Exception:
        pass


def clean_text(x):
    if x is None:
        return None

    x = str(x).replace("\n", " ").replace("\r", " ")
    x = re.sub(r"\s+", " ", x).strip()

    return x if x else None


def format_date(x):
    if not x:
        return None

    try:
        n = int(x)

        if n > 10_000_000_000:
            n = n // 1000

        if 1_000_000_000 <= n <= 2_100_000_000:
            return datetime.fromtimestamp(n).strftime("%b %d, %Y")
    except Exception:
        pass

    try:
        dt = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except Exception:
        return None


def normalize_threads_url(url):
    url = (url or "").strip()

    if url.startswith("threads.net") or url.startswith("threads.com"):
        url = "https://www." + url

    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]

    url = url.replace("https://threads.net/", "https://www.threads.net/")
    url = url.replace("https://threads.com/", "https://www.threads.com/")

    return url.rstrip("/")


def extract_threads_code(url):
    match = re.search(r"threads\.(?:net|com)/@[^/]+/post/([^/?#]+)", url)
    return match.group(1) if match else None


def manual_cookie_parse(cookie_header):
    cookies = {}

    for part in cookie_header.split(";"):
        part = part.strip()

        if not part or "=" not in part:
            continue

        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()

        if not name:
            continue

        if name.lower() in {
            "path",
            "domain",
            "expires",
            "max-age",
            "secure",
            "httponly",
            "samesite",
        }:
            continue

        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]

        cookies[name] = value

    return cookies


def cookie_header_to_playwright_cookies(cookie_header):
    cookie_header = (cookie_header or "").strip()

    if not cookie_header or "=" not in cookie_header:
        return []

    parsed = {}

    try:
        simple_cookie = SimpleCookie()
        simple_cookie.load(cookie_header)

        for name, morsel in simple_cookie.items():
            value = morsel.value
            if value is not None:
                parsed[name] = value
    except Exception:
        pass

    if not parsed:
        parsed = manual_cookie_parse(cookie_header)

    domains = [
        ".threads.com",
        ".threads.net",
        ".instagram.com",
        "threads.com",
        "threads.net",
        "www.threads.com",
        "www.threads.net",
        "www.instagram.com",
    ]

    skip_names = {
        "path",
        "domain",
        "expires",
        "max-age",
        "secure",
        "httponly",
        "samesite",
    }

    cookies = []
    seen = set()

    for name, value in parsed.items():
        if not name or value is None:
            continue

        if name.lower() in skip_names:
            continue

        value = str(value)

        for domain in domains:
            key = (name, domain)

            if key in seen:
                continue

            seen.add(key)

            cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "Lax",
            })

    return cookies


def parse_post(post_data, current_url=None, depth=0, root_code=None):
    if not isinstance(post_data, dict):
        return None

    caption = post_data.get("caption") or {}
    text = caption.get("text")

    user = post_data.get("user") or {}
    author = user.get("username")

    if not text or not author:
        return None

    code = post_data.get("code")

    timestamp = (
        post_data.get("taken_at")
        or post_data.get("created_at")
        or post_data.get("publish_time")
        or post_data.get("timestamp")
    )

    is_root = bool(root_code and code == root_code)

    return {
        "id": post_data.get("id"),
        "code": code,
        "text": clean_text(text),
        "author": author,
        "likes": post_data.get("like_count", 0) or 0,
        "reply_count": (
            post_data.get("text_post_app_info", {})
            .get("direct_reply_count", 0)
            or 0
        ),
        "date": format_date(timestamp),
        "_source_url": current_url,
        "_depth": depth,
        "_is_root": is_root,
        "_type": "parent" if depth == 0 else "reply",
    }


def extract_json_objects_from_text(text):
    text = (text or "").strip()

    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):].strip()

    objects = []

    try:
        objects.append(json.loads(text))
        return objects
    except Exception:
        pass

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith("for (;;);"):
            line = line[len("for (;;);"):].strip()

        try:
            objects.append(json.loads(line))
        except Exception:
            pass

    return objects


def add_posts_from_json(
    json_data,
    extracted,
    urls_to_visit,
    queued_urls,
    visited_urls,
    current_url,
    depth,
    root_code
):
    all_posts = nested_lookup("post", json_data)

    for post_obj in all_posts:
        parsed = parse_post(
            post_obj,
            current_url=current_url,
            depth=depth,
            root_code=root_code
        )

        if not parsed or not parsed.get("id"):
            continue

        post_id = parsed["id"]

        if post_id not in extracted:
            extracted[post_id] = parsed
        else:
            old_depth = extracted[post_id].get("_depth", 999)
            if parsed.get("_depth", 999) < old_depth:
                extracted[post_id] = parsed

        if parsed.get("reply_count", 0) > 0 and parsed.get("code"):
            new_url = f"https://www.threads.net/@{parsed['author']}/post/{parsed['code']}"

            if (
                new_url not in visited_urls
                and new_url not in queued_urls
                and parsed.get("code") != root_code
            ):
                urls_to_visit.append((new_url, depth + 1))
                queued_urls.add(new_url)


async def get_scroll_height(page):
    return await page.evaluate("document.body.scrollHeight")


async def scroll_and_expand(page, extracted, max_stale_rounds=12, progress_base=45, progress_span=25):
    button_selectors = [
        "text=/View more repli/i",
        "text=/Show hidden repli/i",
        "text=/View all repli/i",
        "text=/more repli/i",
        "text=/Load more/i",
        "text=/See more/i",
        "text=/Show replies/i",
        "text=/View replies/i",
        "text=/Weitere Antworten/i",
        "text=/Mehr anzeigen/i",
        "text=/Antworten anzeigen/i",
    ]

    previous_count = len(extracted)
    previous_height = await get_scroll_height(page)
    stale_rounds = 0
    round_no = 0

    while stale_rounds < max_stale_rounds:
        round_no += 1

        progress = min(
            progress_base + progress_span,
            progress_base + int((round_no / max(max_stale_rounds, 1)) * progress_span)
        )

        emit_progress(
            f"Scrolling and expanding replies... found {len(extracted)} items",
            progress
        )

        await page.mouse.wheel(0, 4000)
        await page.wait_for_timeout(1500)

        for selector in button_selectors:
            try:
                buttons = await page.locator(selector).all()

                for button in buttons:
                    try:
                        if await button.is_visible(timeout=500):
                            await button.click(timeout=2000)
                            await page.wait_for_timeout(1500)
                            stale_rounds = 0
                    except Exception:
                        pass
            except Exception:
                pass

        current_count = len(extracted)
        current_height = await get_scroll_height(page)

        if current_count > previous_count or current_height > previous_height:
            previous_count = current_count
            previous_height = current_height
            stale_rounds = 0
        else:
            stale_rounds += 1


async def launch_chromium_with_auto_install(pw, headless=True):
    try:
        return await pw.chromium.launch(headless=headless)

    except Exception as error:
        error_text = repr(error)

        if (
            "Executable doesn't exist" not in error_text
            and "playwright install" not in error_text
            and "Looks like Playwright was just installed or updated" not in error_text
        ):
            raise

        emit_progress("Installing Playwright Chromium on server...", 18)

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "playwright",
                "install",
                "chromium"
            ],
            capture_output=True,
            text=True,
            timeout=600
        )

        if completed.returncode != 0:
            print("Playwright Chromium install failed.", file=sys.stderr)
            print(completed.stdout or "", file=sys.stderr)
            print(completed.stderr or "", file=sys.stderr)
            raise RuntimeError("Failed to install Playwright Chromium.")

        emit_progress("Chromium installed. Launching browser...", 22)

        return await pw.chromium.launch(headless=headless)


async def scrape_threads_one(start_url, cookie_header, max_pages=100, headless=True, max_stale_rounds=12, link_index=1, total_links=1):
    start_url = normalize_threads_url(start_url)
    root_code = extract_threads_code(start_url)

    extracted = {}
    urls_to_visit = [(start_url, 0)]
    queued_urls = {start_url}
    visited_urls = set()

    current_state = {
        "url": start_url,
        "depth": 0,
    }

    cookie_list = cookie_header_to_playwright_cookies(cookie_header)

    emit_progress(f"Starting Threads link {link_index}/{total_links}...", 15)

    async with async_playwright() as pw:
        emit_progress("Launching browser for Threads...", 20)

        browser = await launch_chromium_with_auto_install(pw, headless=headless)
        context = await browser.new_context(locale="en-US")

        if cookie_list:
            valid_cookies = []

            for c in cookie_list:
                if (
                    c.get("name")
                    and c.get("value") is not None
                    and c.get("domain")
                    and c.get("path")
                ):
                    valid_cookies.append(c)

            if valid_cookies:
                await context.add_cookies(valid_cookies)

        page = await context.new_page()

        async def handle_response(response):
            response_url = response.url

            if not any(key in response_url for key in ["graphql", "api/v1", "/ajax/"]):
                return

            current_url = current_state["url"]
            depth = current_state["depth"]

            try:
                try:
                    json_data = await response.json()

                    add_posts_from_json(
                        json_data,
                        extracted,
                        urls_to_visit,
                        queued_urls,
                        visited_urls,
                        current_url,
                        depth,
                        root_code
                    )

                except Exception:
                    text = await response.text()

                    for obj in extract_json_objects_from_text(text):
                        add_posts_from_json(
                            obj,
                            extracted,
                            urls_to_visit,
                            queued_urls,
                            visited_urls,
                            current_url,
                            depth,
                            root_code
                        )

            except Exception:
                pass

        page.on("response", handle_response)

        page_count = 0

        while urls_to_visit and page_count < max_pages:
            current_url, depth = urls_to_visit.pop(0)

            if current_url in visited_urls:
                continue

            visited_urls.add(current_url)
            current_state["url"] = current_url
            current_state["depth"] = depth

            page_count += 1

            page_progress = min(40, 25 + int((page_count / max(max_pages, 1)) * 15))

            if depth == 0:
                emit_progress(f"Opening main Threads post... page {page_count}/{max_pages}", page_progress)
            else:
                emit_progress(f"Opening reply thread... page {page_count}/{max_pages}", page_progress)

            try:
                await page.goto(
                    current_url,
                    wait_until="networkidle",
                    timeout=45000
                )

                emit_progress(f"Reading Threads page data... found {len(extracted)} items", 42)

                html = await page.content()
                selector = Selector(text=html)

                for script in selector.xpath("//script/text()").getall():
                    try:
                        start_index = script.find("{")
                        end_index = script.rfind("}") + 1

                        if start_index == -1 or end_index <= 0:
                            continue

                        json_data = json.loads(script[start_index:end_index])

                        add_posts_from_json(
                            json_data,
                            extracted,
                            urls_to_visit,
                            queued_urls,
                            visited_urls,
                            current_url,
                            depth,
                            root_code
                        )

                    except Exception:
                        continue

                await scroll_and_expand(
                    page,
                    extracted,
                    max_stale_rounds=max_stale_rounds,
                    progress_base=45,
                    progress_span=30
                )

            except Exception as error:
                print(f"Error on {current_url}: {repr(error)}", file=sys.stderr)

        emit_progress(f"Finished browser scan. Found {len(extracted)} raw items.", 80)

        await browser.close()

    emit_progress("Converting Threads items to table...", 85)

    rows = []

    for item in extracted.values():
        if item.get("_is_root"):
            continue

        rows.append({
            "platform": "threads",
            "url": start_url,
            "source_url": item.get("_source_url"),
            "source_id": item.get("id"),
            "code": item.get("code"),
            "date": item.get("date"),
            "author": item.get("author"),
            "type": item.get("_type"),
            "comment": item.get("text"),
            "like": item.get("likes", 0),
        })

    emit_progress(f"Threads extraction produced {len(rows)} comments.", 90)

    return rows


async def main():
    input_file = sys.argv[1]
    output_file = sys.argv[2]

    with open(input_file, "r", encoding="utf-8") as f:
        payload = json.load(f)

    links = payload.get("links", [])
    cookie_header = payload.get("cookie_header", "")
    max_pages = int(payload.get("max_pages", 100))
    headless = bool(payload.get("headless", True))
    max_stale_rounds = int(payload.get("max_stale_rounds", 12))

    all_rows = []
    errors = []

    emit_progress("Preparing Threads input...", 10)

    total_links = len(links)

    for i, link in enumerate(links, start=1):
        try:
            rows = await scrape_threads_one(
                link,
                cookie_header=cookie_header,
                max_pages=max_pages,
                headless=headless,
                max_stale_rounds=max_stale_rounds,
                link_index=i,
                total_links=total_links
            )

            all_rows.extend(rows)

        except Exception as error:
            errors.append({
                "url": link,
                "error": repr(error)
            })

    emit_progress("Saving Threads extraction result...", 92)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "rows": all_rows,
                "errors": errors
            },
            f,
            ensure_ascii=False,
            indent=2
        )

    emit_progress("Threads runner finished.", 95)


if __name__ == "__main__":
    asyncio.run(main())
'''


def _get_secret(*keys, default=""):
    for key in keys:
        try:
            if key in st.secrets and st.secrets[key]:
                return str(st.secrets[key]).strip()
        except Exception:
            pass

    for key in keys:
        value = os.getenv(key)
        if value:
            return str(value).strip()

    return default


def _make_preview(url, with_preview=False):
    preview = fetch_generic_preview(url) if with_preview else {}

    if preview:
        title = (preview.get("title") or "").strip()
        description = (preview.get("description") or "").strip()

        caption = description if description and description != title else title

        preview = {
            "title": caption,
            "description": None,
            "image": preview.get("image"),
            "author": preview.get("author") or "Threads",
            "engagement": None,
            "mode": "threads",
        }

    return preview


def _empty_df():
    return pd.DataFrame(columns=CLEAN_COLS)


def _normalize_output(rows):
    df_full = pd.DataFrame(rows)

    if df_full.empty:
        return _empty_df()

    for col in CLEAN_COLS:
        if col not in df_full.columns:
            df_full[col] = None

    df = df_full[["date", "author", "type", "comment", "like"]].copy()
    df = df.dropna(subset=["comment"])
    df = df.drop_duplicates(subset=["date", "author", "type", "comment", "like"])
    df = df.reset_index(drop=True)
    df.insert(0, "index", range(1, len(df) + 1))

    return df[CLEAN_COLS]


def _handle_progress_line(line, progress_callback):
    if not progress_callback:
        return False

    if not line.startswith(PROGRESS_PREFIX):
        return False

    raw = line[len(PROGRESS_PREFIX):].strip()

    try:
        payload = json.loads(raw)
    except Exception:
        return True

    message = payload.get("message") or "Processing Threads..."
    progress = int(payload.get("progress") or 0)

    progress = max(0, min(100, progress))

    progress_callback(message, progress)

    return True


def extract_threads_comments(url: str, progress_callback=None, with_preview=False):
    preview = _make_preview(url, with_preview=with_preview)

    cookie_header = _get_secret(
        "THREADS_COOKIE_HEADER",
        "THREADS_COOKIE",
        "INSTAGRAM_COOKIE",
        default=""
    )

    max_pages = int(_get_secret("THREADS_MAX_PAGES", default="100"))
    max_stale_rounds = int(_get_secret("THREADS_MAX_STALE_ROUNDS", default="12"))

    headless_raw = _get_secret("THREADS_HEADLESS", default="1")
    headless = str(headless_raw).strip().lower() not in {"0", "false", "no"}

    if progress_callback:
        progress_callback("Preparing Threads extractor...", 5)

    if not url:
        return _empty_df(), preview

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        input_path = tmpdir / "threads_input.json"
        output_path = tmpdir / "threads_output.json"
        runner_path = tmpdir / "threads_runner.py"

        payload = {
            "links": [url],
            "cookie_header": cookie_header,
            "max_pages": max_pages,
            "headless": headless,
            "max_stale_rounds": max_stale_rounds
        }

        input_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        runner_path.write_text(RUNNER_CODE, encoding="utf-8")

        if progress_callback:
            progress_callback("Starting Threads browser process...", 8)

        cmd = [
            sys.executable,
            str(runner_path),
            str(input_path),
            str(output_path)
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1
        )

        stdout_lines = []

        while True:
            line = process.stdout.readline()

            if line:
                line = line.rstrip("\n")
                stdout_lines.append(line)

                handled = _handle_progress_line(line, progress_callback)

                if not handled:
                    pass

            if process.poll() is not None:
                break

        remaining_stdout, stderr_text = process.communicate()

        if remaining_stdout:
            for line in remaining_stdout.splitlines():
                stdout_lines.append(line)
                _handle_progress_line(line, progress_callback)

        if process.returncode != 0:
            try:
                with st.expander("Threads extractor error", expanded=True):
                    st.code(stderr_text or "\n".join(stdout_lines) or "Unknown error.")
            except Exception:
                pass

            return _empty_df(), preview

        if not output_path.exists():
            return _empty_df(), preview

        if progress_callback:
            progress_callback("Parsing Threads comments...", 96)

        try:
            output = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            return _empty_df(), preview

    rows = output.get("rows", []) or []
    errors = output.get("errors", []) or []

    if errors:
        try:
            with st.expander("Threads extractor warnings", expanded=False):
                st.dataframe(pd.DataFrame(errors), use_container_width=True)
        except Exception:
            pass

    df = _normalize_output(rows)

    if progress_callback:
        progress_callback("Done", 100)

    return df, preview
