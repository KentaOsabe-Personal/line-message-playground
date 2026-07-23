from uuid import UUID

from linewebhooks.types import FrozenJsonObject, VerifiedWebhookEvent


CHANNEL_ID = UUID("12345678-1234-4234-9234-123456789abc")
EVENT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SUBJECT = "U" + "a" * 32
REPLY_TOKEN = "reply-token-canary"


def interaction_event(
    *,
    event_type: str = "message",
    source: object | None = None,
    reply_token: object = REPLY_TOKEN,
    message: object | None = None,
    postback: object | None = None,
    extra: dict[str, object] | None = None,
) -> VerifiedWebhookEvent:
    data: dict[str, object] = {
        "source": source if source is not None else {"type": "user", "userId": SUBJECT},
        "replyToken": reply_token,
    }
    if message is not None:
        data["message"] = message
    elif event_type == "message":
        data["message"] = {"type": "text", "text": "/ping"}
    if postback is not None:
        data["postback"] = postback
    elif event_type == "postback":
        data["postback"] = {"data": "v1:confirm:opaque"}
    if extra:
        data.update(extra)
    return VerifiedWebhookEvent(
        channel_public_id=CHANNEL_ID,
        webhook_event_id=EVENT_ID,
        event_type=event_type,
        occurred_at_ms=1,
        is_redelivery=False,
        data=FrozenJsonObject(data),
    )
