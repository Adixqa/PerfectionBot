import asyncio
import re
import discord
import random
import time
import json
import io
import requests

from functools import partial
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from PerfectionBot.config.yamlHandler import get_value


def to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == 'true'
    return False


API_KEY                  = get_value("tokens", "yt")
CHANNEL_URL              = get_value("youtube", "target")
CHECK_INTERVAL           = int(get_value("youtube", "flags", "CHECK_INTERVAL"))
IGNORE_SHORTS            = to_bool(get_value("youtube", "flags", "IGNORE_SHORTS"))
IGNORE_VIDEOS            = to_bool(get_value("youtube", "flags", "IGNORE_VIDEOS"))
IGNORE_STREAMS           = to_bool(get_value("youtube", "flags", "IGNORE_STREAMS"))
IGNORE_POSTS             = to_bool(get_value("youtube", "flags", "IGNORE_POSTS"))
ANNOUNCEMENTS            = get_value("youtube", "announcements")
ANNOUNCEMENT_CHANNEL_ID  = int(get_value("youtube", "flags", "ANNOUNCEMENT_CHANNEL_ID"))


def build_client():
    return build("youtube", "v3", developerKey=API_KEY)


async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

_MARKER_START = '\u2063'
_ZW_0 = '\u200b'
_ZW_1 = '\u200c'
_VALID_ZW = (_ZW_0, _ZW_1)


def _encode_marker(t: str) -> str:
    if not t:
        return ""
    try:
        b = t.encode('utf-8')
    except Exception:
        b = str(t).encode('utf-8')
    bits = ''.join(f'{byte:08b}' for byte in b)
    zw = ''.join(_ZW_0 if bit == '0' else _ZW_1 for bit in bits)
    return _MARKER_START + zw


def _decode_marker(content: str):
    if not content or _MARKER_START not in content:
        return None
    try:
        tail = content.split(_MARKER_START, 1)[1]
        zw_only = ''.join(ch for ch in tail if ch in _VALID_ZW)
        if not zw_only:
            return None
        bits = ''.join('0' if ch == _ZW_0 else '1' for ch in zw_only)
        if len(bits) % 8 != 0:
            return None
        bytes_list = [int(bits[i:i+8], 2) for i in range(0, len(bits), 8)]
        return bytes(bytes_list).decode('utf-8', errors='strict')
    except Exception:
        return None


def _dump_snippet(s, idx, radius=400):
    start = max(0, idx - radius)
    end = min(len(s), idx + radius)
    return s[start:end].replace("\n", " ").replace("\r", " ")[:2000]


def _find_key_paths(obj, target_key, path=None):
    if path is None:
        path = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == target_key:
                return path + [k]
            p = _find_key_paths(v, target_key, path + [k])
            if p:
                return p
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            p = _find_key_paths(item, target_key, path + [f"[{i}]"])
            if p:
                return p
    return None

def _fetch_community_html(channel_id=None, channel_username=None):
    """
    Fetch raw community HTML (used only for debugging / fallback).
    """
    try:
        if channel_username:
            url = f"https://www.youtube.com/@{channel_username}/community"
        else:
            url = f"https://www.youtube.com/channel/{channel_id}/community"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.youtube.com/",
            "Cookie": "CONSENT=YES+1"
        }

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[DEBUG] HTTP {resp.status_code} while fetching {url}")
            return None
        return resp.text
    except Exception as e:
        print("Failed to fetch community HTML:", e)
        return None


def _parse_yt_initialdata(html):
    try:
        match = re.search(r"var\s+ytInitialData\s*=\s*(\{.*?\})\s*;</script>", html, re.DOTALL)
        if not match:
            match = re.search(r"ytInitialData\"\s*:\s*(\{.*?\})\s*[,<]", html, re.DOTALL)
        if not match:
            return None
        json_text = match.group(1)
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        print(f"[DEBUG] JSON decode error in ytInitialData: {e}")
        snippet = _dump_snippet(html, html.find("ytInitialData"))
        print("[DEBUG] snippet:", snippet[:1000])
        return None
    except Exception as e:
        print("[DEBUG] Failed to parse ytInitialData:", e)
        return None


def _extract_latest_post_from_initialdata(initial):
    try:
        if not isinstance(initial, dict):
            return None

        tabs = initial.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs")
        if not tabs:
            tabs = initial.get("contents", {}).get("singleColumnBrowseResultsRenderer", {}).get("tabs")

        if not tabs:
            return None

        contents = None
        for tab in tabs:
            tab_renderer = tab.get("tabRenderer", {})
            if "content" in tab_renderer:
                contents = tab_renderer["content"]
                break

        if not contents:
            return None

        threads = []
        if "sectionListRenderer" in contents:
            threads = contents["sectionListRenderer"].get("contents", [])
        elif "richGridRenderer" in contents:
            threads = contents["richGridRenderer"].get("contents", [])

        if not threads:
            return None

        for thread in threads:
            post_thread = (
                thread.get("backstagePostThreadRenderer")
                or thread.get("richItemRenderer", {}).get("content", {}).get("backstagePostThreadRenderer")
            )
            if not post_thread:
                continue

            post = post_thread.get("post", {}).get("backstagePostRenderer")
            if not post:
                continue

            post_id = post.get("postId", "")
            content_runs = post.get("contentText", {}).get("runs", [])
            title = "".join(r.get("text", "") for r in content_runs)
            image_url = None

            attachments = post.get("backstageAttachment", []) or []
            if isinstance(attachments, dict):
                attachments = [attachments]

            for att in attachments:
                img_thumbs = att.get("backstageImageRenderer", {}).get("image", {}).get("thumbnails", []) \
                             or att.get("imageRenderer", {}).get("thumbnails", [])
                if img_thumbs:
                    image_url = img_thumbs[-1].get("url")
                    break

            published = post.get("publishedTimeText", {}).get("runs", [{}])[0].get("text", "")

            return {
                "postId": post_id,
                "title": title,
                "image_url": image_url,
                "publishedAt": published
            }

        return None
    except Exception as e:
        print("Failed to extract post from initialData:", e)
        return None


def _fetch_community_json_browse_ajax(channel_id=None, channel_username=None):
    try:
        html = _fetch_community_html(channel_id=channel_id, channel_username=channel_username)
        if not html:
            return None

        try:
            with open("/tmp/yt_community.html", "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass

        m = re.search(r'"continuationCommand"\s*:\s*\{\s*"token"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'"continuation"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'continuation":"([^"]+)"', html)
        if not m:
            return None

        continuation = m.group(1)
        ajax_url = f"https://www.youtube.com/browse_ajax?continuation={continuation}"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://www.youtube.com/@{channel_username}/community" if channel_username else f"https://www.youtube.com/channel/{channel_id}/community",
            "Cookie": "CONSENT=YES+1"
        }

        resp = requests.get(ajax_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[DEBUG] browse_ajax HTTP {resp.status_code}")
            return None

        return resp.json()
    except Exception as e:
        print("[DEBUG] _fetch_community_json_browse_ajax failed:", e)
        return None


def _fetch_community_json_youtubei(channel_id=None, channel_username=None):
    try:
        html = _fetch_community_html(channel_id=channel_id, channel_username=channel_username)
        if not html:
            return None

        try:
            with open("/tmp/yt_community.html", "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass

        key_match = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', html)
        client_match = re.search(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([^"]+)"', html)
        if not key_match:
            return None

        api_key = key_match.group(1)
        client_ver = client_match.group(1) if client_match else "2.20240712.00.00"

        api_url = f"https://www.youtube.com/youtubei/v1/browse?key={api_key}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.youtube.com",
            "Referer": f"https://www.youtube.com/@{channel_username}/community" if channel_username else f"https://www.youtube.com/channel/{channel_id}/community",
            "Cookie": "CONSENT=YES+1"
        }

        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": client_ver
                }
            },
            "browseId": channel_id or (f"@{channel_username}" if channel_username else ""),
            "params": "EgZjb21tdW5pdHk%3D"
        }

        resp = requests.post(api_url, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[DEBUG] youtubei API error {resp.status_code}")
            return None

        return resp.json()
    except Exception as e:
        print("[DEBUG] _fetch_community_json_youtubei failed:", e)
        return None


def _extract_latest_post_from_browse_ajax(data):
    try:
        if not data:
            return None

        if isinstance(data, list) and data:
            data = data[0]

        endpoints = data.get("onResponseReceivedEndpoints", []) or data.get("onResponseReceivedActions", [])
        for ep in endpoints:
            cont_items = (ep.get("appendContinuationItemsAction") or {}).get("continuationItems", []) \
                         or (ep.get("reloadContinuationItemsCommand") or {}).get("continuationItems", []) \
                         or ep.get("continuationItems", [])
            for item in cont_items:
                post = item.get("backstagePostThreadRenderer", {}).get("post", {}).get("backstagePostRenderer")
                if not post:
                    post = item.get("richItemRenderer", {}).get("content", {}).get("backstagePostThreadRenderer", {}).get("post", {}).get("backstagePostRenderer")
                if not post:
                    continue

                post_id = post.get("postId", "")
                content_runs = post.get("contentText", {}).get("runs", [])
                title = "".join(r.get("text", "") for r in content_runs)
                image_url = None
                att = post.get("backstageAttachment") or {}
                if isinstance(att, dict) and att.get("backstageImageRenderer"):
                    thumbs = att["backstageImageRenderer"].get("image", {}).get("thumbnails", [])
                    if thumbs:
                        image_url = thumbs[-1].get("url")
                elif isinstance(att, list):
                    for a in att:
                        thumbs = a.get("backstageImageRenderer", {}).get("image", {}).get("thumbnails", []) or a.get("imageRenderer", {}).get("thumbnails", [])
                        if thumbs:
                            image_url = thumbs[-1].get("url")
                            break

                published = post.get("publishedTimeText", {}).get("runs", [{}])[0].get("text", "")
                return {
                    "postId": post_id,
                    "title": title,
                    "image_url": image_url,
                    "publishedAt": published
                }

        post_node = _find_in_structure(data, "backstagePostRenderer")
        if post_node and isinstance(post_node, dict):
            post = post_node
            post_id = post.get("postId", "")
            content_runs = post.get("contentText", {}).get("runs", [])
            title = "".join(r.get("text", "") for r in content_runs)
            image_url = None
            attachments = post.get("backstageAttachment") or []
            if isinstance(attachments, dict):
                attachments = [attachments]
            for att in attachments:
                thumbs = att.get("backstageImageRenderer", {}).get("image", {}).get("thumbnails", []) or []
                if thumbs:
                    image_url = thumbs[-1].get("url")
                    break
            published = post.get("publishedTimeText", {}).get("runs", [{}])[0].get("text", "")
            return {
                "postId": post_id,
                "title": title,
                "image_url": image_url,
                "publishedAt": published
            }

        return None
    except Exception as e:
        print("Failed to extract from browse_ajax JSON:", e)
        return None


def _find_in_structure(obj, keyname):
    if isinstance(obj, dict):
        if keyname in obj:
            return obj[keyname]
        for v in obj.values():
            res = _find_in_structure(v, keyname)
            if res is not None:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = _find_in_structure(item, keyname)
            if res is not None:
                return res
    return None

def _safe_check_latest_post(channel_id, channel_username=None):
    try:
        if IGNORE_POSTS:
            return None

        data = _fetch_community_json_youtubei(channel_id, channel_username)
        if data:
            post = _extract_latest_post_from_initialdata(data) or _extract_latest_post_from_browse_ajax(data)
            if post:
                return post

        data = _fetch_community_json_browse_ajax(channel_id, channel_username)
        if data:
            post = _extract_latest_post_from_browse_ajax(data)
            if post:
                return post

        html = _fetch_community_html(channel_id=channel_id, channel_username=channel_username)
        if not html:
            return None

        try:
            with open("/tmp/yt_community.html", "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass

        initial = _parse_yt_initialdata(html)
        if initial:
            post = _extract_latest_post_from_initialdata(initial)
            return post

        return None
    except Exception as e:
        print("safe_check_latest_post failed:", e)
        return None

async def _safe_announce(bot, ch, ann, new_link, pin_type="video", file_tuple=None):
    try:
        pinned = await ch.pins()
        for msg in pinned:
            ptype = _decode_marker(msg.content or "") or "unknown"
            if ptype == pin_type and new_link in (msg.content or ""):
                print("Already announced (same-type pin exists).")
                return

        send_content = ann + _encode_marker(pin_type)

        if file_tuple:
            bts, fname = file_tuple
            file_obj = discord.File(io.BytesIO(bts), filename=fname)
            sent = await ch.send(content=send_content, file=file_obj)
        else:
            sent = await ch.send(send_content)

        for msg in pinned:
            try:
                ptype = _decode_marker(msg.content or "") or "unknown"
                if ptype == pin_type:
                    await msg.unpin()
            except Exception as e:
                print("Failed to unpin (type-filtered):", e)

        try:
            await sent.pin()
            async for msg in ch.history(limit=5):
                if msg.type == discord.MessageType.pins_add and msg.author == bot.user:
                    await msg.delete()
                    break
        except Exception as e:
            print("Failed pin cleanup:", e)
    except Exception as e:
        print("Pinned message handling failed:", e)


async def _safe_api_call(request, retries=5):
    delay = 2
    for attempt in range(retries):
        try:
            return await run_blocking(request.execute)
        except HttpError as e:
            if e.resp.status in (403, 500, 503):
                print(f"[WARN] YouTube API error {e.resp.status}, retrying...")
                await asyncio.sleep(delay + random.random())
                delay = min(delay * 2, 60)
            else:
                print(f"[ERROR] Unrecoverable YouTube error: {e}")
                return None
        except Exception as e:
            print(f"[WARN] API call failed: {e}, retrying...")
            await asyncio.sleep(delay + random.random())
            delay = min(delay * 2, 60)
    print("[ERROR] API call failed too many times.")
    return None

def _summarize(v):
    s = v.get("snippet", {})
    stat = v.get("status", {})
    live = v.get("liveStreamingDetails", {})
    cd = v.get("contentDetails", {})

    def F(tpl, pin_type="video"):
        prem = live.get("scheduledStartTime") or s.get("publishedAt", "")
        prem = prem.replace("T", " ").replace("Z", " UTC") if prem else ""
        return tpl.format(
            PING_everyone="@everyone",
            target_video_link=f"https://www.youtube.com/watch?v={v.get('id')}",
            title=s.get("title", ""),
            description=s.get("description", ""),
            premiere_date=prem
        ), pin_type

    bc = s.get("liveBroadcastContent", "none")

    if bc == "none":
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", cd.get("duration", ""))
        hrs = int(m.group(1) or 0) if m else 0
        mins = int(m.group(2) or 0) if m else 0
        secs = int(m.group(3) or 0) if m else 0
        tot = hrs * 3600 + mins * 60 + secs

        thumb = next(
            (s.get("thumbnails", {}).get(k) for k in ("maxres", "standard", "high", "medium", "default")
             if s.get("thumbnails", {}).get(k)), {}
        )
        is_vertical = thumb.get("height", 0) > thumb.get("width", 0)

        if tot <= 180 and is_vertical:
            return (None, None) if IGNORE_SHORTS else F(ANNOUNCEMENTS["new_short"], pin_type="video")
        else:
            return (None, None) if IGNORE_VIDEOS else F(ANNOUNCEMENTS["new_video"], pin_type="video")

    if bc == "upcoming":
        if "scheduledStartTime" in live:
            if stat.get("uploadStatus") == "processed":
                return F(ANNOUNCEMENTS["upcoming_premiere"], pin_type="video")
            return (None, None) if IGNORE_STREAMS else F(ANNOUNCEMENTS["upcoming_stream"], pin_type="video")

    if bc == "live" and not IGNORE_STREAMS:
        if stat.get("uploadStatus") == "processed":
            return F(ANNOUNCEMENTS["premiere"], pin_type="video")
        return F(ANNOUNCEMENTS["stream"], pin_type="video")

    return (None, None)


def _download_image_bytes(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.content
    except Exception as e:
        print("Failed to download image:", e)
    return None

async def monitor_channel(bot):
    while True:
        try:
            await _monitor_channel(bot)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[FATAL] YouTube monitor crashed: {e}")
            await asyncio.sleep(10)


async def _monitor_channel(bot):
    await asyncio.sleep(CHECK_INTERVAL)

    channel_id = None
    channel_username = None
    if "/channel/" in CHANNEL_URL:
        channel_id = CHANNEL_URL.split("/channel/")[1].split("/")[0]
    else:
        if "/@" in CHANNEL_URL:
            channel_username = CHANNEL_URL.split("/@")[1].split("/")[0]
        else:
            channel_username = CHANNEL_URL.rstrip("/").split("/")[-1]

        try:
            youtube_tmp = build_client()
            res = await run_blocking(
                youtube_tmp.search().list(
                    q=f"@{channel_username}",
                    type="channel",
                    part="snippet",
                    maxResults=1
                ).execute
            )
            channel_id = res.get("items", [{}])[0].get("snippet", {}).get("channelId")
            if not channel_id:
                print("Could not resolve channel ID for", channel_username)
                return
        except Exception as e:
            print("YouTube monitor error while resolving channel:", e)
            return

    last_video_id = None
    last_post_id = None
    youtube = build_client()

    while True:
        try:
            up = await _safe_api_call(
                youtube.channels().list(part="contentDetails", id=channel_id)
            )
            if not up:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            uploads = up.get("items", [])
            if not uploads:
                print("No channel items found.")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            content_details = uploads[0].get("contentDetails", {})
            related = content_details.get("relatedPlaylists", {})
            uploads_pid = related.get("uploads")
            if not uploads_pid:
                print("No uploads playlist found.")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            pl = await _safe_api_call(
                youtube.playlistItems().list(
                    part="snippet,contentDetails",
                    playlistId=uploads_pid,
                    maxResults=1
                )
            )
            if pl:
                items = pl.get("items", [])
            else:
                items = []

            if items:
                vid = items[0].get("contentDetails", {}).get("videoId")
                if vid:
                    if vid != last_video_id:
                        v = await _safe_api_call(
                            youtube.videos().list(
                                part="snippet,liveStreamingDetails,status,contentDetails",
                                id=vid
                            )
                        )
                        if v:
                            v_items = v.get("items", [])
                            if v_items:
                                v = v_items[0]
                                ann_tuple = _summarize(v)
                                if ann_tuple:
                                    ann_text, pin_type = ann_tuple
                                    if ann_text:
                                        ch = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
                                        if ch:
                                            new_link = f"https://www.youtube.com/watch?v={vid}"
                                            await _safe_announce(bot, ch, ann_text, new_link, pin_type)
                                        else:
                                            print(f"Channel {ANNOUNCEMENT_CHANNEL_ID} not found.")
                        last_video_id = vid

            if not IGNORE_POSTS:
                post = await run_blocking(_safe_check_latest_post, channel_id, channel_username)
                print(f"[DEBUG] Post check result: {post}")
                if post:
                    post_id = post.get("postId")
                    if post_id and post_id != last_post_id:
                        title = post.get("title", "")
                        post_link = f"https://www.youtube.com/post/{post_id}"
                        ann_template = ANNOUNCEMENTS.get("new_post", "{PING_everyone} ... has made a new [post]({target_video_link})!\n**# {title}**\n{image}")
                        prem = post.get("publishedAt", "")
                        ann_text = ann_template.format(
                            PING_everyone="@everyone",
                            target_video_link=post_link,
                            title=title,
                            description=post.get("description", ""),
                            premiere_date=prem,
                            image=""
                        )

                        ch = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
                        if ch:
                            image_url = post.get("image_url")
                            file_tuple = None
                            if image_url:
                                img_bytes = await run_blocking(_download_image_bytes, image_url)
                                if img_bytes:
                                    file_tuple = (img_bytes, "post.jpg")

                            await _safe_announce(bot, ch, ann_text, post_link, "post", file_tuple=file_tuple)
                        else:
                            print(f"Channel {ANNOUNCEMENT_CHANNEL_ID} not found (post).")

                        last_post_id = post_id

        except Exception as e:
            print("[ERROR] YouTube monitor inner loop error:", e)
            youtube = build_client()

        await asyncio.sleep(CHECK_INTERVAL)