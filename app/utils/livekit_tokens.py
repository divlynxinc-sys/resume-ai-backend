"""
LiveKit join tokens for AI Interviews.

The browser never sees the LiveKit API secret: this module signs a short-lived
JWT that lets ONE participant join ONE room, and embeds an explicit agent
dispatch so the `interview_agent` worker (registered under
`livekit_settings.agent_name`) is started for that room with the session id as
its job metadata. Verified against livekit-api 1.2.x.
"""

from __future__ import annotations

import json
from datetime import timedelta

from livekit import api

from app.core.config import livekit_settings


def interview_room_name(session_id: str) -> str:
    return f"interview-{session_id}"


def participant_identity(user_id: int) -> str:
    return f"user-{user_id}"


def mint_interview_token(*, session_id: str, user_id: int, display_name: str) -> str:
    if not livekit_settings.configured:
        raise RuntimeError("LiveKit is not configured (LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET).")

    room = interview_room_name(session_id)
    dispatch = api.RoomAgentDispatch(
        agent_name=livekit_settings.agent_name,
        metadata=json.dumps({"session_id": session_id}),
    )
    token = (
        api.AccessToken(livekit_settings.api_key, livekit_settings.api_secret)
        .with_identity(participant_identity(user_id))
        .with_name(display_name[:80] if display_name else "Candidate")
        .with_ttl(timedelta(minutes=livekit_settings.token_ttl_minutes))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
                # Audio only — the product never uses the camera.
                can_publish_sources=["microphone"],
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                name=room,
                # Tear the room down quickly once everyone leaves / never joins,
                # so a stray browser tab doesn't keep an agent process alive.
                empty_timeout=60,
                departure_timeout=20,
                max_participants=2,
                agents=[dispatch],
            )
        )
    )
    return token.to_jwt()
