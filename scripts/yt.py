import asyncio
import re
import discord
import random
import time

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
ANNOUNCEMENTS            = get_value("youtube", "announcements")
ANNOUNCEMENT_CHANNEL_ID  = int(get_value("youtube", "flags", "ANNOUNCEMENT_CHANNEL_ID"))


def build_client():
    return build("youtube", "v3", developerKey=API_KEY)


async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


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
    if "/channel/" in CHANNEL_URL:
        channel_id = CHANNEL_URL.split("/channel/")[1].split("/")[0]
    else:
        username = CHANNEL_URL.split("/@")[1].split("/")[0]
        try:
            youtube_tmp = build_client()
            res = await run_blocking(
                youtube_tmp.search().list(
                    q=f"@{username}",
                    type="channel",
                    part="snippet",
                    maxResults=1
                ).execute
            )
            channel_id = res.get("items", [{}])[0].get("snippet", {}).get("channelId")
            if not channel_id:
                print("Could not resolve channel ID for", username)
                return
        except Exception as e:
            print("YouTube monitor error while resolving channel:", e)
            return

    last_video_id = None
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
            if not pl:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            items = pl.get("items", [])
            if not items:
                print("No videos found in uploads playlist.")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            vid = items[0].get("contentDetails", {}).get("videoId")
            if not vid:
                print("No videoId found in playlist item.")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            if vid != last_video_id:
                v = await _safe_api_call(
                    youtube.videos().list(
                        part="snippet,liveStreamingDetails,status,contentDetails",
                        id=vid
                    )
                )
                if not v:
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                v_items = v.get("items", [])
                if not v_items:
                    print("No video details found for", vid)
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                v = v_items[0]
                ann = _summarize(v)
                if ann:
                    ch = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
                    if ch:
                        new_link = f"https://www.youtube.com/watch?v={vid}"
                        await _safe_announce(bot, ch, ann, new_link)
                    else:
                        print(f"Channel {ANNOUNCEMENT_CHANNEL_ID} not found.")

                last_video_id = vid

        except Exception as e:
            print("[ERROR] YouTube monitor inner loop error:", e)
            youtube = build_client()

        await asyncio.sleep(CHECK_INTERVAL)


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


async def _safe_announce(bot, ch, ann, new_link):
    try:
        pinned = await ch.pins()
        if not any(new_link in msg.content for msg in pinned):
            sent = await ch.send(ann)

            for msg in pinned:
                try:
                    await msg.unpin()
                except Exception as e:
                    print("Failed to unpin:", e)

            try:
                await sent.pin()
                async for msg in ch.history(limit=5):
                    if msg.type == discord.MessageType.pins_add and msg.author == bot.user:
                        await msg.delete()
                        break
            except Exception as e:
                print("Failed pin cleanup:", e)
        else:
            print("Already announced.")
    except Exception as e:
        print("Pinned message handling failed:", e)


def _summarize(v):
    s = v.get("snippet", {})
    stat = v.get("status", {})
    live = v.get("liveStreamingDetails", {})
    cd = v.get("contentDetails", {})

    def F(tpl):
        prem = live.get("scheduledStartTime") or s.get("publishedAt", "")
        prem = prem.replace("T", " ").replace("Z", " UTC") if prem else ""
        return tpl.format(
            PING_everyone="@everyone",
            target_video_link=f"https://www.youtube.com/watch?v={v.get('id')}",
            title=s.get("title", ""),
            description=s.get("description", ""),
            premiere_date=prem
        )

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
            return None if IGNORE_SHORTS else F(ANNOUNCEMENTS["new_short"])
        else:
            return None if IGNORE_VIDEOS else F(ANNOUNCEMENTS["new_video"])

    if bc == "upcoming":
        if "scheduledStartTime" in live:
            if stat.get("uploadStatus") == "processed":
                return F(ANNOUNCEMENTS["upcoming_premiere"])
            return None if IGNORE_STREAMS else F(ANNOUNCEMENTS["upcoming_stream"])

    if bc == "live" and not IGNORE_STREAMS:
        if stat.get("uploadStatus") == "processed":
            return F(ANNOUNCEMENTS["premiere"])
        return F(ANNOUNCEMENTS["stream"])

    return None