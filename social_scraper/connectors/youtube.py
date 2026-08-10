"""
YouTube Connector — uses yt-dlp for search, video intelligence, and channel stats.

Three modes:
1. search()      — keyword search, fast flat-playlist, basic metadata
2. get_video()   — single video deep dive, full metadata extraction
3. get_channel() — channel overview with subscriber count and recent top videos

No API key needed. yt-dlp is free and handles YouTube's anti-bot measures.
"""

import asyncio
import json
import time
import subprocess
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from social_scraper.base import (
    BaseConnector, ConnectorResult, SocialItem, SourceHealth,
)
from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord


def parse_youtube_thread(
    *,
    video_id: str,
    comments: list[dict],
    max_comments: int,
    max_depth: int,
    platform_reported_total: int | None,
) -> ThreadFetchResult:
    by_id = {str(item.get("id")): item for item in comments if item.get("id")}
    depth_cache: dict[str, int] = {}

    def depth_for(comment_id: str, stack: set[str] | None = None) -> int:
        if comment_id in depth_cache:
            return depth_cache[comment_id]
        stack = set(stack or ())
        if comment_id in stack:
            return max_depth + 1
        stack.add(comment_id)
        parent = str((by_id.get(comment_id) or {}).get("parent") or "root")
        depth = 1 if parent in {"root", "none", "None", ""} else depth_for(parent, stack) + 1
        depth_cache[comment_id] = depth
        return depth

    records = []
    excluded_by_depth = False
    for raw in comments:
        external_id = str(raw.get("id") or "")
        if not external_id:
            continue
        depth = depth_for(external_id)
        if depth > max_depth:
            excluded_by_depth = True
            continue
        if len(records) >= max_comments:
            break
        parent = str(raw.get("parent") or "root")
        if parent in {"root", "none", "None", ""}:
            parent = video_id
        timestamp = raw.get("timestamp")
        published_at = None
        if isinstance(timestamp, (int, float)):
            published_at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        records.append(ThreadRecord(
            platform="youtube",
            external_id=external_id,
            record_type="comment" if depth == 1 else "reply",
            parent_external_id=parent,
            root_post_external_id=video_id,
            depth=depth,
            text=raw.get("text") if isinstance(raw.get("text"), str) else None,
            author_external_id=(
                str(raw.get("author_id")) if raw.get("author_id") else None
            ),
            author_username=(
                str(raw.get("author")) if raw.get("author") else None
            ),
            url=f"https://www.youtube.com/watch?v={video_id}&lc={external_id}",
            published_at=published_at,
            likes=raw.get("like_count") if isinstance(raw.get("like_count"), int) else None,
            raw=raw,
        ))
    truncated = (
        excluded_by_depth
        or len(records) < len([item for item in comments if item.get("id")])
        or (
            isinstance(platform_reported_total, int)
            and platform_reported_total > len(records)
        )
    )
    status = "empty" if not records and not comments else "partial" if truncated else "complete"
    return ThreadFetchResult(
        platform="youtube",
        root_post_external_id=video_id,
        status=status,
        records=tuple(records),
        truncated=truncated,
        attempted_route="ytdlp_comments",
        platform_reported_total=platform_reported_total,
        max_comments=max_comments,
        max_depth=max_depth,
        limitations=(
            ("Comments were bounded by count or depth.",) if truncated else ()
        ),
    )


class YouTubeConnector(BaseConnector):
    platform = "youtube"
    connector_name = "ytdlp_free"

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        """
        Search YouTube with full metadata extraction.

        Uses full extraction (not flat-playlist) to get upload_date, likes,
        comments, channel subscriber count. Slower (~1-2s per video) but
        gives decision-grade data for a paid API.
        """
        start = time.time()
        items = []
        error = None

        try:
            # Fetch 2x requested to account for time filtering
            fetch_count = min(count * 2, 30) if time_filter else min(count, 20)
            search_query = f"ytsearch{fetch_count}:{keyword}"

            cmd = [
                "yt-dlp",
                "--dump-json",
                "--no-warnings",
                "--no-playlist",
                search_query,
            ]

            loop = asyncio.get_event_loop()
            timeout = max(60, fetch_count * 5)
            stdout = await loop.run_in_executor(
                None, lambda: self._run_ytdlp(cmd, timeout=timeout)
            )

            for line in stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    item = self._parse_video_full(data)
                    if item:
                        items.append(item)
                except json.JSONDecodeError:
                    pass

            # Post-filter by time
            if time_filter:
                items = self._filter_by_time(items, time_filter)

            # Post-sort
            if sort == "views" and items:
                items.sort(key=lambda x: x.views or 0, reverse=True)
            elif sort == "latest" and items:
                items.sort(key=lambda x: x.created_at or "", reverse=True)

        except Exception as e:
            error = str(e)

        latency = int((time.time() - start) * 1000)
        return ConnectorResult(
            items=items[:count],
            health=SourceHealth(
                platform=self.platform, connector=self.connector_name,
                status="ok" if items else ("error" if error else "partial"),
                items_returned=len(items), items_requested=count,
                latency_ms=latency, error=error,
            ),
        )

    async def get_video(self, video_id: str) -> ConnectorResult:
        """Deep video intelligence. Full metadata extraction (slower, 2-5s)."""
        start = time.time()
        items = []
        error = None

        try:
            url = f"https://www.youtube.com/watch?v={video_id}"
            cmd = [
                "yt-dlp",
                "--dump-json",
                "--no-warnings",
                "--no-playlist",
                url,
            ]

            loop = asyncio.get_event_loop()
            stdout = await loop.run_in_executor(None, lambda: self._run_ytdlp(cmd, timeout=30))

            if stdout.strip():
                data = json.loads(stdout.strip().split("\n")[0])
                item = self._parse_video_full(data)
                if item:
                    items.append(item)

        except Exception as e:
            error = str(e)

        latency = int((time.time() - start) * 1000)
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform=self.platform, connector=self.connector_name,
                status="ok" if items else ("error" if error else "partial"),
                items_returned=len(items), items_requested=1,
                latency_ms=latency, error=error,
            ),
        )

    async def fetch_thread(
        self, post: SocialItem, max_comments: int, max_depth: int
    ) -> ThreadFetchResult:
        if post.platform != "youtube" or not post.post_id:
            return ThreadFetchResult(
                platform="youtube", root_post_external_id=post.post_id or "unknown",
                status="error", attempted_route="ytdlp_comments",
                error_category="invalid_youtube_post", max_comments=max_comments,
                max_depth=max_depth,
            )
        if max_comments <= 0 or max_depth <= 0:
            return ThreadFetchResult(
                platform="youtube", root_post_external_id=post.post_id,
                status="empty", attempted_route="ytdlp_comments",
                max_comments=max_comments, max_depth=max_depth,
            )
        command = [
            "yt-dlp", "--dump-single-json", "--skip-download", "--write-comments",
            "--no-warnings", "--extractor-args",
            f"youtube:comment_sort=top;max_comments={max_comments}",
            f"https://www.youtube.com/watch?v={post.post_id}",
        ]
        loop = asyncio.get_event_loop()
        return_code, stdout, stderr = await loop.run_in_executor(
            None,
            lambda: self._run_ytdlp_result(
                command, timeout=max(60, min(max_comments, 100) * 3)
            ),
        )
        error_text = stderr.lower()
        if "comments are turned off" in error_text or "comments are disabled" in error_text:
            return ThreadFetchResult(
                platform="youtube", root_post_external_id=post.post_id,
                status="disabled", attempted_route="ytdlp_comments",
                error_category="comments_disabled", max_comments=max_comments,
                max_depth=max_depth,
            )
        data = None
        for line in reversed(stdout.splitlines()):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
        if data is None:
            return ThreadFetchResult(
                platform="youtube", root_post_external_id=post.post_id,
                status="unavailable", attempted_route="ytdlp_comments",
                error_category=(
                    "ytdlp_comments_failed" if return_code else "comments_not_returned"
                ),
                max_comments=max_comments, max_depth=max_depth,
                limitations=(stderr.strip()[:300],) if stderr.strip() else (),
            )
        comments = data.get("comments")
        reported_total = data.get("comment_count")
        if not isinstance(reported_total, int):
            reported_total = post.comments if isinstance(post.comments, int) else None
        if comments is None:
            status = "empty" if reported_total == 0 else "unavailable"
            return ThreadFetchResult(
                platform="youtube", root_post_external_id=post.post_id,
                status=status, attempted_route="ytdlp_comments",
                error_category=(None if status == "empty" else "comments_not_returned"),
                platform_reported_total=reported_total,
                max_comments=max_comments, max_depth=max_depth,
            )
        return parse_youtube_thread(
            video_id=post.post_id,
            comments=comments if isinstance(comments, list) else [],
            max_comments=max_comments,
            max_depth=max_depth,
            platform_reported_total=reported_total,
        )

    def _run_ytdlp_result(self, cmd, timeout=30):
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            return completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "yt-dlp comment retrieval timed out"
        except Exception as exc:
            return 1, "", str(exc)

    async def get_channel(self, handle: str, video_count: int = 10) -> dict:
        """Channel overview: subscriber count, total videos, recent top videos."""
        start = time.time()
        error = None

        try:
            # Normalize handle
            if handle.startswith("@"):
                channel_url = f"https://www.youtube.com/{handle}/videos"
            elif handle.startswith("UC") and len(handle) == 24:
                channel_url = f"https://www.youtube.com/channel/{handle}/videos"
            else:
                channel_url = f"https://www.youtube.com/@{handle}/videos"

            # Get recent videos (flat-playlist for speed)
            cmd = [
                "yt-dlp",
                "--dump-json",
                "--flat-playlist",
                "--no-warnings",
                "--playlist-end", str(min(video_count, 30)),
                channel_url,
            ]

            loop = asyncio.get_event_loop()
            stdout = await loop.run_in_executor(None, lambda: self._run_ytdlp(cmd, timeout=30))

            videos = []
            channel_info = {}

            for line in stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Extract channel info from first video
                    if not channel_info:
                        channel_info = self._extract_channel_info(data)
                    video = self._parse_video_flat(data)
                    if video:
                        videos.append(video)
                except json.JSONDecodeError:
                    pass

            # Sort by views to get "top" content
            videos.sort(key=lambda x: x.views or 0, reverse=True)

            latency = int((time.time() - start) * 1000)
            return {
                "channel": channel_info,
                "recent_videos": [v.to_dict() for v in videos[:video_count]],
                "video_count": len(videos),
                "health": SourceHealth(
                    platform=self.platform, connector=self.connector_name,
                    status="ok" if videos else "error",
                    items_returned=len(videos), items_requested=video_count,
                    latency_ms=latency, error=error,
                ).to_dict(),
            }

        except Exception as e:
            error = str(e)
            latency = int((time.time() - start) * 1000)
            return {
                "channel": {},
                "recent_videos": [],
                "video_count": 0,
                "health": SourceHealth(
                    platform=self.platform, connector=self.connector_name,
                    status="error", items_returned=0, items_requested=video_count,
                    latency_ms=latency, error=error,
                ).to_dict(),
            }

    def _run_ytdlp(self, cmd, timeout=30):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            return result.stdout
        except Exception:
            return ""

    def _parse_video_flat(self, data: dict) -> SocialItem:
        """Parse flat-playlist result (basic metadata)."""
        video_id = data.get("id", "")
        if not video_id:
            return None

        title = data.get("title", "")
        uploader = data.get("uploader") or data.get("channel") or data.get("uploader_id", "")
        upload_date = data.get("upload_date")
        view_count = data.get("view_count")
        duration = data.get("duration")

        created_at = None
        if upload_date and len(upload_date) == 8:
            try:
                created_at = datetime.strptime(upload_date, "%Y%m%d").replace(
                    tzinfo=timezone.utc
                ).isoformat()
            except ValueError:
                pass

        thumbnail = data.get("thumbnail")
        if not thumbnail and data.get("thumbnails"):
            thumbs = data["thumbnails"]
            if isinstance(thumbs, list) and thumbs:
                thumbnail = thumbs[0].get("url") if isinstance(thumbs[0], dict) else None

        return SocialItem(
            platform=self.platform,
            post_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            author_username=uploader,
            author_profile_url=f"https://www.youtube.com/@{uploader}" if uploader else "",
            text=title,
            created_at=created_at,
            views=view_count,
            media_type="video",
            thumbnail_url=thumbnail,
            raw={"duration": duration},
        )

    def _parse_video_full(self, data: dict) -> SocialItem:
        """Parse full extraction result (rich metadata)."""
        video_id = data.get("id", "")
        if not video_id:
            return None

        title = data.get("title", "")
        description = data.get("description", "")
        uploader = data.get("uploader") or data.get("channel") or data.get("uploader_id", "")
        channel_id = data.get("channel_id", "")
        upload_date = data.get("upload_date")
        view_count = data.get("view_count")
        like_count = data.get("like_count")
        comment_count = data.get("comment_count")
        duration = data.get("duration")
        tags = data.get("tags", [])
        categories = data.get("categories", [])
        availability = data.get("availability", "")
        channel_follower_count = data.get("channel_follower_count")

        created_at = None
        if upload_date and len(upload_date) == 8:
            try:
                created_at = datetime.strptime(upload_date, "%Y%m%d").replace(
                    tzinfo=timezone.utc
                ).isoformat()
            except ValueError:
                pass

        thumbnail = data.get("thumbnail")
        if not thumbnail and data.get("thumbnails"):
            thumbs = data["thumbnails"]
            if isinstance(thumbs, list) and thumbs:
                thumbnail = thumbs[-1].get("url") if isinstance(thumbs[-1], dict) else None

        return SocialItem(
            platform=self.platform,
            post_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            author_username=uploader,
            author_display_name=uploader,
            author_profile_url=f"https://www.youtube.com/@{uploader}" if uploader else "",
            author_follower_count=channel_follower_count,
            text=title,
            created_at=created_at,
            views=view_count,
            likes=like_count,
            comments=comment_count,
            media_type="video",
            thumbnail_url=thumbnail,
            hashtags=tags if tags else [],
            language=data.get("language"),
            region=data.get("release_date"),
            raw={
                "description": description[:2000],
                "description_length": len(description),
                "tags": tags,
                "categories": categories,
                "duration": duration,
                "channel_id": channel_id,
                "availability": availability,
                "channel_subscribers": channel_follower_count,
            },
        )

    def _extract_channel_info(self, data: dict) -> dict:
        """Extract channel-level info from a video data object."""
        return {
            "channel_name": data.get("uploader") or data.get("channel", ""),
            "channel_id": data.get("channel_id", ""),
            "channel_url": data.get("uploader_url") or data.get("channel_url", ""),
            "channel_subscribers": data.get("channel_follower_count"),
        }

    def _filter_by_time(self, items: list, time_filter: str) -> list:
        """Post-filter items by upload date."""
        now = datetime.now(timezone.utc)
        thresholds = {
            "1day": now - timedelta(days=1),
            "week": now - timedelta(days=7),
            "month": now - timedelta(days=30),
            "halfyear": now - timedelta(days=180),
        }
        threshold = thresholds.get(time_filter)
        if not threshold:
            return items

        filtered = []
        for item in items:
            if not item.created_at:
                continue
            try:
                created = datetime.fromisoformat(item.created_at)
                if created >= threshold:
                    filtered.append(item)
            except (ValueError, TypeError):
                continue
        return filtered

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
