# yt.py

import asyncio
import re
import discord

from functools import partial
from googleapiclient.discovery import build
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

youtube = build('youtube', 'v3', developerKey=API_KEY)

async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

async def monitor_channel(bot):
    await asyncio.sleep(CHECK_INTERVAL)  # Give some breathing room at startup

    # Resolve channel ID
    if "/channel/" in CHANNEL_URL:
        channel_id = CHANNEL_URL.split("/channel/")[1].split("/")[0]
    else:
        username = CHANNEL_URL.split("/@")[1].split("/")[0]
        res = await run_blocking(youtube.search().list(q=f"@{username}", type="channel", part="snippet", maxResults=1).execute)
        channel_id = res['items'][0]['snippet']['channelId']

    last_video_id = None

    while True:
        try:
            up = await run_blocking(youtube.channels().list(part='contentDetails', id=channel_id).execute)
            uploads_pid = up['items'][0]['contentDetails']['relatedPlaylists']['uploads']

            pl = await run_blocking(youtube.playlistItems().list(
                part='snippet,contentDetails',
                playlistId=uploads_pid,
                maxResults=1
            ).execute)

            if not pl['items']:
                print("No videos found.")
            else:
                vid = pl['items'][0]['contentDetails']['videoId']
                if vid != last_video_id:
                    v = await run_blocking(youtube.videos().list(
                        part='snippet,liveStreamingDetails,status,contentDetails',
                        id=vid
                    ).execute)

                    v = v['items'][0]
                    ann = _summarize(v)
                    if ann:
                        ch = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
                        if ch:
                            new_link = f"https://www.youtube.com/watch?v={vid}"

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
                        else:
                            print(f"Channel {ANNOUNCEMENT_CHANNEL_ID} not found.")

                    last_video_id = vid
                else:
                    pass
        except Exception as e:
            print("YouTube monitor error:", e)

        await asyncio.sleep(CHECK_INTERVAL)

def _summarize(v):
    s = v['snippet']
    stat = v.get('status', {})
    live = v.get('liveStreamingDetails', {})
    cd = v.get('contentDetails', {})

    def F(tpl):
        prem = live.get('scheduledStartTime') or s.get('publishedAt', '')
        prem = prem.replace('T', ' ').replace('Z', ' UTC') if prem else ''
        return tpl.format(
            PING_everyone='@everyone',
            target_video_link=f"https://www.youtube.com/watch?v={v['id']}",
            title=s.get('title', ''),
            description=s.get('description', ''),
            premiere_date=prem
        )

    bc = s.get('liveBroadcastContent', 'none')

    if bc == 'none':
        m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', cd.get('duration', ''))
        hrs = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        secs = int(m.group(3) or 0)
        tot = hrs * 3600 + mins * 60 + secs

        thumb = next((s.get('thumbnails', {}).get(k) for k in ('maxres', 'standard', 'high', 'medium', 'default') if s.get('thumbnails', {}).get(k)), {})
        is_vertical = thumb.get('height', 0) > thumb.get('width', 0)

        if tot <= 180 and is_vertical:
            return None if IGNORE_SHORTS else F(ANNOUNCEMENTS['new_short'])
        else:
            return None if IGNORE_VIDEOS else F(ANNOUNCEMENTS['new_video'])

    if bc == 'upcoming':
        if 'scheduledStartTime' in live:
            if stat.get('uploadStatus') == 'processed':
                return F(ANNOUNCEMENTS['upcoming_premiere'])
            return None if IGNORE_STREAMS else F(ANNOUNCEMENTS['upcoming_stream'])

    if bc == 'live' and not IGNORE_STREAMS:
        if stat.get('uploadStatus') == 'processed':
            return F(ANNOUNCEMENTS['premiere'])
        return F(ANNOUNCEMENTS['stream'])

    return None
