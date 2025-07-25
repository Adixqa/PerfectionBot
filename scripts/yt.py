import asyncio
import re
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

async def monitor_channel(bot):
    """Async loop: check every CHECK_INTERVAL seconds and post to Discord."""
    if "/channel/" in CHANNEL_URL:
        channel_id = CHANNEL_URL.split("/channel/")[1].split("/")[0]
    else:
        username = CHANNEL_URL.split("/@")[1].split("/")[0]
        res = youtube.search().list(q=f"@{username}", type="channel", part="snippet", maxResults=1).execute()
        channel_id = res['items'][0]['snippet']['channelId']

    last_video_id = None
    await asyncio.sleep(CHECK_INTERVAL)

    while True:
        try:
            up = youtube.channels().list(part='contentDetails', id=channel_id).execute()
            uploads_pid = up['items'][0]['contentDetails']['relatedPlaylists']['uploads']
            pl = youtube.playlistItems().list(
                part='snippet,contentDetails',
                playlistId=uploads_pid,
                maxResults=1
            ).execute()

            if not pl['items']:
                print("No videos found.")
            else:
                vid = pl['items'][0]['contentDetails']['videoId']
                if vid != last_video_id:
                    v = youtube.videos().list(
                        part='snippet,liveStreamingDetails,status,contentDetails',
                        id=vid
                    ).execute()['items'][0]

                    ann = _summarize(v)
                    if ann:
                        ch = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
                        if ch:
                            await ch.send(ann)
                        else:
                            print(f"Channel {ANNOUNCEMENT_CHANNEL_ID} not found.")
                    last_video_id = vid
                else:
                    print("No new video.")
        except Exception as e:
            print("YouTube monitor error:", e)

        await asyncio.sleep(CHECK_INTERVAL)

def _summarize(v):
    s    = v['snippet']
    stat = v.get('status', {})
    live = v.get('liveStreamingDetails', {})
    cd   = v.get('contentDetails', {})

    def F(tpl):
        prem = live.get('scheduledStartTime') or s.get('publishedAt','')
        prem = prem.replace('T',' ').replace('Z',' UTC') if prem else ''
        return tpl.format(
            PING_everyone='@everyone',
            target_video_link=f"https://www.youtube.com/watch?v={v['id']}",
            title=s.get('title',''),
            description=s.get('description',''),
            premiere_date=prem
        )

    bc = s.get('liveBroadcastContent','none')

    if bc == 'none':
        # Parse ISO 8601 duration string (PT#H#M#S)
        m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', cd.get('duration',''))
        hrs = int(m.group(1) or 0)
        mins= int(m.group(2) or 0)
        secs= int(m.group(3) or 0)
        tot = hrs * 3600 + mins * 60 + secs

        # Check aspect ratio from thumbnail
        thumbs = s.get('thumbnails', {})
        thumb = thumbs.get('maxres') or thumbs.get('standard') or thumbs.get('high') or thumbs.get('medium') or thumbs.get('default')
        is_vertical = False
        if thumb:
            width = thumb.get('width', 0)
            height = thumb.get('height', 0)
            is_vertical = height > width

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