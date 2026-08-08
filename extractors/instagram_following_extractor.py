import random
import re
import time
from typing import Callable, Optional
from urllib.parse import quote, unquote

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


USERNAME_RGX = re.compile(r"^[a-z0-9._]{1,30}$")
ProgressCallback = Optional[Callable[[str, int], None]]


def normalize_username(value: str) -> str:
    username = (value or "").strip().lower().lstrip("@").strip("/").strip()
    return username.rstrip(".")


def is_valid_username(username: str) -> bool:
    return bool(USERNAME_RGX.fullmatch(username))


def parse_cookie_string(cookie_string: str) -> dict:
    cookies = {}
    for part in (cookie_string or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = unquote(value.strip())
        else:
            cookies[part] = ""
    return cookies


def _is_cooldown_text(text: str) -> bool:
    text = (text or "").lower()
    return "too many queries" in text or "need to wait" in text


def _make_session(cookies: dict) -> requests.Session:
    session = requests.Session()

    for key, value in cookies.items():
        session.cookies.set(key, value, domain=".instagram.com")

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Referer": "https://www.instagram.com/",
        "X-IG-App-ID": "936619743392459",
    })

    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
        respect_retry_after_header=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _get_user_id(session: requests.Session, username: str, req_timeout: int = 12):
    try:
        response = session.get(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={quote(username)}",
            timeout=req_timeout,
        )
        if response.status_code == 200:
            user = (response.json().get("data") or {}).get("user")
            if user and user.get("id"):
                return int(user["id"])
        if response.status_code in (400, 429) and _is_cooldown_text(response.text):
            return None
    except (requests.RequestException, ValueError, TypeError):
        pass

    try:
        response = session.get(
            f"https://i.instagram.com/api/v1/users/search/?q={quote(username)}&count=30",
            timeout=req_timeout,
            headers={"User-Agent": "Instagram 155.0.0.37.107"},
        )
        if response.status_code == 200:
            for user in response.json().get("users", []):
                if (user.get("username") or "").lower() == username and user.get("pk"):
                    return int(user["pk"])
    except (requests.RequestException, ValueError, TypeError):
        pass

    try:
        response = session.get(
            f"https://www.instagram.com/{quote(username)}/",
            timeout=req_timeout,
        )
        if response.status_code == 200 and response.text:
            match = re.search(r'"profile_id"\s*:\s*"(\d+)"', response.text)
            if match:
                return int(match.group(1))
    except requests.RequestException:
        pass

    return None


def _fetch_following(
    session: requests.Session,
    user_id: int,
    progress_callback: ProgressCallback = None,
    wait: float = 1.2,
    req_timeout: int = 10,
):
    url = f"https://i.instagram.com/api/v1/friendships/{user_id}/following/"
    headers = {
        "User-Agent": "Instagram 155.0.0.37.107",
        "Accept": "*/*",
        "X-IG-App-ID": "936619743392459",
        "Referer": "https://www.instagram.com/",
    }

    results = []
    max_id = None
    page = 0
    consecutive_failures = 0

    while True:
        page += 1
        params = {"count": 200}
        if max_id:
            params["max_id"] = max_id

        if progress_callback:
            progress_callback(
                f"Fetching following · {len(results):,} found",
                min(88, 24 + (page * 5)),
            )

        try:
            response = session.get(url, params=params, headers=headers, timeout=req_timeout)
        except requests.RequestException as error:
            consecutive_failures += 1
            if consecutive_failures >= 4:
                raise RuntimeError("Instagram could not be reached. Please try again.") from error
            time.sleep(wait + random.uniform(0, 0.5))
            continue

        if response.status_code in (400, 429) and _is_cooldown_text(response.text):
            raise RuntimeError("Instagram is limiting requests. Please try again later.")

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as error:
                raise RuntimeError("Instagram returned an unreadable response.") from error

            users = data.get("users") or []
            for user in users:
                username = user.get("username") or ""
                results.append({
                    "pk": str(user.get("pk") or ""),
                    "username": username,
                    "full_name": user.get("full_name") or "",
                    "profile_url": f"https://www.instagram.com/{username}/" if username else "",
                })

            max_id = data.get("next_max_id")
            consecutive_failures = 0
            if not max_id:
                break

            time.sleep(wait + random.uniform(0, 0.3))
            continue

        if response.status_code == 429 or response.status_code >= 500:
            consecutive_failures += 1
            if consecutive_failures >= 4:
                raise RuntimeError("Instagram is temporarily unavailable. Please try again later.")
            backoff = min(6, wait * (1.5 ** consecutive_failures)) + random.uniform(0, 0.5)
            time.sleep(backoff)
            continue

        if response.status_code in (401, 403):
            raise RuntimeError("Instagram cookie is expired or does not have access.")

        raise RuntimeError(f"Instagram stopped the request (HTTP {response.status_code}).")

    return results


def extract_instagram_following(
    target_username: str,
    cookie_string: str,
    progress_callback: ProgressCallback = None,
) -> pd.DataFrame:
    username = normalize_username(target_username)
    if not is_valid_username(username):
        raise ValueError("Please enter a valid Instagram username.")

    cookies = parse_cookie_string(cookie_string)
    if not cookies.get("sessionid"):
        raise RuntimeError("INSTAGRAM_COOKIE must contain a valid sessionid.")

    if progress_callback:
        progress_callback("Checking Instagram session...", 8)
    session = _make_session(cookies)

    if progress_callback:
        progress_callback("Finding account...", 16)
    user_id = _get_user_id(session, username)
    if not user_id:
        raise RuntimeError("Account not found. Check the username or Instagram cookie.")

    following = _fetch_following(
        session=session,
        user_id=user_id,
        progress_callback=progress_callback,
    )

    if progress_callback:
        progress_callback("Preparing overview...", 95)

    df = pd.DataFrame(
        following,
        columns=["pk", "username", "full_name", "profile_url"],
    )
    if not df.empty:
        df = df.drop_duplicates(subset=["pk", "username"]).reset_index(drop=True)
        df.insert(0, "index", range(1, len(df) + 1))

    if progress_callback:
        progress_callback("Done", 100)
    return df
