from typing import Optional, Dict, Any, List, TypedDict
from src.common.clients.http_client import HttpClient
from src.utils import get_logger
from urllib.parse import urlencode


class FacebookAPIError(Exception):
    """Custom exception for Facebook API errors with user-friendly messages."""

    def __init__(
        self,
        message: str,
        error_code: Optional[int] = None,
        error_subcode: Optional[int] = None,
        error_type: Optional[str] = None,
        user_message: Optional[str] = None,
    ):
        self.message = message
        self.error_code = error_code
        self.error_subcode = error_subcode
        self.error_type = error_type
        # User-friendly message for frontend
        self.user_message = user_message or message
        super().__init__(self.user_message)


# ========================================================================
# FACEBOOK POST TYPE DEFINITIONS
# ========================================================================


class FacebookImageData(TypedDict):
    height: int
    src: str
    width: int


class FacebookMediaData(TypedDict):
    image: FacebookImageData
    source: Optional[str]  # Video source URL, only present for videos


class FacebookAttachmentTarget(TypedDict):
    id: str
    url: str


class FacebookAttachmentData(TypedDict):
    media: FacebookMediaData
    target: FacebookAttachmentTarget
    type: str  # "video_direct_response", "photo", etc.
    url: str
    description: Optional[str]  # Only present for some attachment types


class FacebookAttachments(TypedDict):
    data: List[FacebookAttachmentData]


class FacebookSummary(TypedDict):
    total_count: int


class FacebookLikes(TypedDict):
    summary: FacebookSummary


class FacebookComments(TypedDict):
    summary: FacebookSummary


class FacebookPostData(TypedDict):
    id: str
    message: Optional[str]
    created_time: str
    permalink_url: str
    attachments: Optional[FacebookAttachments]
    likes: Optional[FacebookLikes]
    comments: Optional[FacebookComments]


logger = get_logger()


class FacebookGraphPageClient:
    def __init__(self, page_access_token: str, api_version: str = "v23.0"):
        """
        Initialize the FacebookGraphClient.

        Args:
            page_access_token: The Facebook Graph API access token
            api_version: Facebook Graph API version to use
        """
        self.page_access_token = page_access_token
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.http = HttpClient()
        self.http_video = HttpClient(timeout=90)  # 90 seconds for video uploads

    async def get_user_info(self, psid: str) -> Optional[dict]:
        """Get user info from Facebook Graph.

        Args:
            psid: User ID on the platform

        Returns:
            User info or None if not found
        """
        url = f"{self.base_url}/{psid}"
        params = {
            "fields": "first_name,last_name,name,profile_pic",
            "access_token": self.page_access_token,
        }
        response = await self.http.get(url, params=params)

        if response.status_code == 200:
            user_data = response.json()
            return user_data
        else:
            code = None
            subcode = None
            try:
                payload = response.json()
                err = (payload or {}).get("error") or {}
                code = err.get("code")
                subcode = err.get("error_subcode")
            except Exception:
                pass

            if response.status_code == 400 and code == 100 and subcode == 33:
                # Typical "object not found / missing permissions" for PSID that cannot be resolved
                logger.warning(
                    f"⚠️ FB API: User info not accessible | PSID: {psid} | code: {code} | subcode: {subcode}"
                )
            else:
                logger.error(
                    f"❌ FB API: User info failed | PSID: {psid} | Status: {response.status_code} | code: {code} | subcode: {subcode}"
                )
            return None

    async def get_page_info(self, page_id: str) -> Optional[dict]:
        """Get page info from Facebook Graph.

        Args:
            page_id: Page ID on the platform

        Returns:
            Page info or None if not found
        """
        url = f"{self.base_url}/{page_id}"
        params = {"fields": "name,picture", "access_token": self.page_access_token}
        r = await self.http.get(url, params=params)

        if r.status_code == 200:
            page_data = r.json()
            return page_data
        else:
            logger.error(
                f"❌ FB API: Page info failed | Page ID: {page_id} | Status: {r.status_code}"
            )
            return None

    async def send_message(
        self,
        user_id: str,
        message: str,
        metadata: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to a user on Facebook.

        Args:
            user_id: ID of the user to send message to
            message: Message content to send
            metadata: Additional metadata for the message
            reply_to_message_id: Optional Facebook message id (mid) to reply to; message will appear as reply in Messenger

        Returns:
            API response data
        """
        url = f"{self.base_url}/me/messages"

        headers = {
            "Authorization": f"Bearer {self.page_access_token}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {"recipient": {"id": user_id}, "message": {"text": message}}

        # Add any metadata if provided
        if metadata:
            payload["message"]["metadata"] = metadata
        if reply_to_message_id:
            payload["reply_to"] = {"mid": reply_to_message_id}

        response = await self.http.post(url, json=payload, headers=headers)

        # Handle error response with user-friendly messages
        if response.status_code != 200:
            error_code = None
            error_subcode = None
            error_message = None
            error_type = None

            try:
                error_payload = response.json()
                error_obj = error_payload.get("error", {})
                error_code = error_obj.get("code")
                error_subcode = error_obj.get("error_subcode")
                error_message = error_obj.get("message", "")
                error_type = error_obj.get("type")
            except Exception:
                error_message = (
                    response.text[:500] if response.text else "Unknown error"
                )

            # Generate user-friendly message based on error code
            user_message = self._get_user_friendly_error_message(
                error_code, error_subcode, error_message
            )

            logger.error(
                f"❌ FB API: Send message failed | User ID: {user_id} | "
                f"Status: {response.status_code} | Code: {error_code} | Subcode: {error_subcode} | "
                f"Message: {error_message}"
            )

            raise FacebookAPIError(
                message=error_message or "Facebook API error",
                error_code=error_code,
                error_subcode=error_subcode,
                error_type=error_type,
                user_message=user_message,
            )

        return response.json()

    async def take_thread_control(
        self,
        recipient_id: str,
        metadata: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Take control of a conversation from another app (Handover Protocol).
        Use when Facebook returns error 10/2018300 (another app controlling this thread).
        The app must be set as Primary Receiver in Meta App Dashboard for this to work.

        Args:
            recipient_id: Page-scoped user ID (PSID) of the conversation participant
            metadata: Optional metadata string to pass to the handover

        Returns:
            API response with success boolean
        """
        url = f"{self.base_url}/me/take_thread_control"
        headers = {
            "Authorization": f"Bearer {self.page_access_token}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {"recipient": {"id": recipient_id}}
        if metadata is not None:
            payload["metadata"] = metadata
        response = await self.http.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            err: Dict[str, Any] = {}
            try:
                err = (response.json() or {}).get("error", {})
            except Exception:
                pass
            msg = err.get("message", response.text or "Unknown error")
            logger.warning(
                f"⚠️ FB API: take_thread_control failed | PSID: {recipient_id} | {msg}"
            )
            raise FacebookAPIError(
                message=msg,
                error_code=err.get("code"),
                error_subcode=err.get("error_subcode"),
                user_message=f"Take thread control failed: {msg}",
            )
        return response.json()

    def _get_user_friendly_error_message(
        self,
        error_code: Optional[int],
        error_subcode: Optional[int],
        error_message: str,
    ) -> str:
        """Convert Facebook API error codes to user-friendly messages."""
        # Error code 10, subcode 2018278 = Outside 24-hour messaging window
        if error_code == 10 and error_subcode == 2018278:
            return (
                "Cannot send message: More than 24 hours have passed since the customer's last message. "
                "Please wait for the customer to send a new message to reopen the messaging window."
            )

        # Error code 10, subcode 2018300 = Another app (e.g. Messenger AI) is controlling this thread
        if error_code == 10 and error_subcode == 2018300:
            return (
                "Cannot send message: Another app (e.g. Messenger AI) is controlling this conversation. "
                "Try again in a moment or wait for the customer to send a new message."
            )

        # Error code 2303 = User blocked or messaging not allowed
        if error_code == 2303:
            return "Cannot send message: The customer has blocked or does not allow messages from this page."

        # Error code 368 = Rate limit exceeded
        if error_code == 368:
            return (
                "Cannot send message: Message rate limit exceeded. "
                "Please try again in a few minutes."
            )

        # Error code 613 = API rate limit
        if error_code == 613:
            return (
                "Cannot send message: API rate limit exceeded. Please try again later."
            )

        # Error code 200 = Permission error
        if error_code == 200:
            return (
                "Cannot send message: Missing access permissions. "
                "Please check the Facebook page permissions."
            )

        # Default: return original message or generic error
        if error_message and "outside of allowed window" in error_message.lower():
            return "Cannot send message: More than 24 hours have passed since the customer's last message."

        return (
            f"Cannot send message: {error_message or 'Unknown error from Facebook API'}"
        )

    async def send_image_message(
        self,
        user_id: str,
        image_url: str,
        is_reusable: bool = True,
        metadata: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send an image message to a user on Facebook.

        Args:
            user_id: ID of the user to send message to
            image_url: URL of the image to send (must be publicly accessible)
            is_reusable: Whether Facebook should cache the image for reuse
            metadata: Additional metadata for the message
            reply_to_message_id: Optional Facebook message id (mid) to reply to
        Returns:
            API response data

        Raises:
            Exception: If the image sending fails
        """
        url = f"{self.base_url}/me/messages"

        headers = {
            "Authorization": f"Bearer {self.page_access_token}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "recipient": {"id": user_id},
            "message": {
                "attachment": {
                    "type": "image",
                    "payload": {"url": image_url, "is_reusable": is_reusable},
                }
            },
        }

        if metadata:
            payload["message"]["metadata"] = metadata
        if reply_to_message_id:
            payload["reply_to"] = {"mid": reply_to_message_id}

        response = await self.http_video.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    async def send_video_message(
        self,
        user_id: str,
        video_url: str,
        is_reusable: bool = True,
        metadata: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a video message to a user on Facebook.

        Args:
            user_id: ID of the user to send message to
            video_url: URL of the video to send (must be publicly accessible)
            is_reusable: Whether Facebook should cache the video for reuse
            metadata: Additional metadata for the message
            reply_to_message_id: Optional Facebook message id (mid) to reply to

        Returns:
            API response data

        Raises:
            Exception: If the video sending fails

        Note:
            - Maximum video file size: 25MB
            - Upload timeout: 75 seconds for videos
            - Supported video formats: MP4, MOV, AVI, WMV, FLV, WebM
        """
        url = f"{self.base_url}/me/messages"

        headers = {
            "Authorization": f"Bearer {self.page_access_token}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "recipient": {"id": user_id},
            "message": {
                "attachment": {
                    "type": "video",
                    "payload": {"url": video_url, "is_reusable": is_reusable},
                }
            },
        }

        if metadata:
            payload["message"]["metadata"] = metadata
        if reply_to_message_id:
            payload["reply_to"] = {"mid": reply_to_message_id}

        response = await self.http_video.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    async def send_button_template(
        self,
        user_id: str,
        text: str,
        buttons: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Send a button template message to a user.

        Args:
            user_id: ID of the user to send message to
            text: Main text of the button template
            buttons: List of button objects

        Returns:
            API response data
        """
        template_data = {"template_type": "button", "text": text, "buttons": buttons}
        return await self._send_templated_message(user_id, "button", template_data)

    async def send_generic_template(
        self,
        user_id: str,
        elements: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Send a generic (carousel) template message.

        Args:
            user_id: ID of the user to send message to
            elements: List of carousel elements

        Returns:
            API response data
        """
        template_data = {"template_type": "generic", "elements": elements}
        return await self._send_templated_message(user_id, "generic", template_data)

    async def _send_templated_message(
        self,
        user_id: str,
        template_id: str,
        template_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Send a Facebook template message to a user.

        This method allows sending structured message templates supported by Facebook Messenger,
        such as generic templates, button templates, media templates, etc.

        Example template_data for a button template:
        {
            "template_type": "button",
            "text": "What do you want to do next?",
            "buttons": [
                {
                    "type": "web_url",
                    "url": "https://example.com",
                    "title": "Visit Website"
                },
                {
                    "type": "postback",
                    "title": "Start Chatting",
                    "payload": "DEVELOPER_DEFINED_PAYLOAD"
                }
            ]
        }

        Args:
            user_id: ID of the user to send message to
            template_id: The type of template (e.g., "generic", "button", "media")
            template_data: Data specific to the template type

        Returns:
            API response data
        """
        url = f"{self.base_url}/me/messages"

        headers = {
            "Authorization": f"Bearer {self.page_access_token}",
            "Content-Type": "application/json",
        }

        # Prepare the template payload
        template_payload = template_data.copy()
        if "template_type" not in template_payload:
            template_payload["template_type"] = template_id

        payload = {
            "recipient": {"id": user_id},
            "message": {
                "attachment": {"type": "template", "payload": template_payload}
            },
        }

        response = await self.http.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # CONVERSATION MANAGEMENT
    # ========================================================================

    async def get_conversations(
        self,
        page_id: str,
        folder: str = "inbox",
        limit: int = 25,
        after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get conversations from a page's inbox.

        Args:
            page_id: Facebook Page ID
            folder: Folder to fetch from (inbox, spam, pending)
            limit: Number of conversations to fetch

        Returns:
            Raw Graph API response including paging info
        """
        url = f"{self.base_url}/{page_id}/conversations"
        params = {
            "fields": "participants,senders,updated_time,unread_count,messages{message,from,created_time}",
            "folder": folder,
            "limit": limit,
            "access_token": self.page_access_token,
        }

        if after:
            params["after"] = after

        response = await self.http.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def get_conversation_messages(
        self,
        conversation_id: str | None = None,
        limit: int = 50,
        user_psid: str | None = None,
        page_id: str | None = None,
    ) -> dict:
        page_token = self.page_access_token

        # Nếu chỉ có PSID -> tìm conversation chứa PSID này
        if user_psid and not conversation_id:
            # 1) liệt kê conversation của Page
            url = f"{self.base_url}/{page_id}/conversations"
            params = {
                "platform": "messenger",
                "fields": "id,participants.limit(10){id,name},updated_time",
                "access_token": page_token,
            }

            r = await self.http.get(url, params=params)
            r.raise_for_status()
            data = r.json()

            conv_id = None
            for conv in data.get("data", []):
                parts = (conv.get("participants") or {}).get("data", [])
                if any(p.get("id") == user_psid for p in parts):
                    conv_id = conv["id"]
                    break
            if not conv_id:
                return {"data": [], "paging": {}, "note": "no_conversation_for_psid"}
            conversation_id = conv_id

        # 2) lấy messages từ conversation-id
        url = f"{self.base_url}/{conversation_id}"
        params = {
            "fields": f"messages.limit({limit}){{id,message,from,created_time,attachments}}",
            "access_token": page_token,
        }
        r = await self.http.get(url, params=params)
        r.raise_for_status()

        return r.json()

    # ========================================================================
    # CONVERSATION HISTORY UTILITIES
    # ========================================================================

    async def get_conversation_for_user(
        self,
        page_id: str,
        user_psid: str,
        folder: str = "inbox",
        page_size: int = 25,
        max_pages: int = 40,
    ) -> Optional[Dict[str, Any]]:
        """
        Locate a conversation that involves the given PSID.

        This method iterates through the page inbox (with pagination) until it finds
        a conversation whose participants include the provided PSID.
        """
        url = f"{self.base_url}/{page_id}/conversations"
        params: Optional[Dict[str, Any]] = {
            "platform": "messenger",
            "folder": folder,
            "limit": min(max(page_size, 1), 50),
            "fields": "id,participants.limit(25){id,name},updated_time,unread_count",
            "access_token": self.page_access_token,
        }

        pages_checked = 0

        while url and pages_checked < max_pages:
            response = await self.http.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            pages_checked += 1

            for conversation in payload.get("data", []):
                participants = (conversation.get("participants") or {}).get("data", [])
                if any(p.get("id") == user_psid for p in participants):
                    return conversation

            paging = payload.get("paging") or {}
            next_url = paging.get("next")
            if not next_url:
                break
            url = next_url
            params = None  # next already includes all query params

        return None

    async def get_full_conversation_history(
        self,
        conversation_id: str,
        messages_page_size: int = 50,
        max_messages: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Fetch the entire message history for a conversation.

        Args:
            conversation_id: Graph API conversation ID.
            messages_page_size: Number of messages per page (Graph cap: 100).
            max_messages: Optional cap to stop early.
        """
        page_size = min(max(messages_page_size, 1), 100)
        url = f"{self.base_url}/{conversation_id}"
        params = {
            "fields": (
                f"updated_time,"
                f"participants.limit(50){{id,name}},"
                f"messages.limit({page_size})"
                "{id,message,from,created_time,attachments,sticker,is_echo,shares}"
            ),
            "access_token": self.page_access_token,
        }

        response = await self.http.get(url, params=params)
        response.raise_for_status()
        payload = response.json()

        participants = (payload.get("participants") or {}).get("data", [])
        updated_time = payload.get("updated_time")
        messages_container = payload.get("messages") or {}
        messages: List[Dict[str, Any]] = list(messages_container.get("data", []))

        next_url = (messages_container.get("paging") or {}).get("next")

        while next_url and (max_messages is None or len(messages) < max_messages):
            next_response = await self.http.get(next_url)
            next_response.raise_for_status()
            next_payload = next_response.json()
            batch = next_payload.get("data", [])
            messages.extend(batch)

            if max_messages is not None and len(messages) >= max_messages:
                break

            next_url = (next_payload.get("paging") or {}).get("next")

        if max_messages is not None and len(messages) > max_messages:
            messages = messages[:max_messages]

        # Enrich messages with attachments if missing
        # Facebook may not return attachments in conversation query, need to fetch per message
        # Fetch attachments for all messages that don't have attachments field
        messages_without_attachments = [
            msg for msg in messages if not msg.get("attachments") and msg.get("id")
        ]

        if messages_without_attachments:
            # Fetch attachments concurrently with rate limiting
            import asyncio

            semaphore = asyncio.Semaphore(
                10
            )  # Max 10 concurrent requests to avoid rate limits

            async def fetch_with_semaphore(msg: Dict[str, Any]) -> None:
                async with semaphore:
                    try:
                        msg_attachments = await self._fetch_message_attachments(
                            msg.get("id")
                        )
                        if msg_attachments:
                            msg["attachments"] = msg_attachments
                    except Exception:
                        pass

            # Execute all fetches concurrently
            await asyncio.gather(
                *[fetch_with_semaphore(msg) for msg in messages_without_attachments]
            )

        return {
            "conversation_id": conversation_id,
            "updated_time": updated_time,
            "participants": participants,
            "messages": messages,
            "message_count": len(messages),
            "has_more": bool(next_url),
        }

    async def _fetch_message_attachments(
        self, message_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch attachments for a specific message ID.

        Args:
            message_id: Facebook message ID

        Returns:
            Attachments data or None
        """
        url = f"{self.base_url}/{message_id}"
        params = {
            "fields": "attachments{type,url,media{image{src}},payload{url},target{url}}",
            "access_token": self.page_access_token,
        }

        try:
            response = await self.http.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            return payload.get("attachments")
        except Exception:
            return None

    # ========================================================================
    # POSTS & CONTENT
    # ========================================================================

    async def get_post_content(
        self,
        post_id: str,
        fields: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get post content from Facebook Graph API.

        Args:
            post_id: Facebook post ID (format: {page_id}_{post_id})
            fields: Comma-separated fields to retrieve (defaults to id, permalink_url, message,
                   created_time, from, limited attachments with details, and comments summary)

        Returns:
            Post data dictionary or None if not found:
            {
                "id": "post_id",
                "permalink_url": "https://...",
                "message": "Post content",
                "created_time": "timestamp",
                "from": {"name": "Page Name", "id": "page_id"},
                "attachments": {
                    "data": [{"type": "photo", "url": "...", "media": {...}, "target": {...}}]
                },
                "comments": {
                    "data": [],
                    "summary": {"total_count": 2, "can_comment": True}
                }
            }
        """
        if fields is None:
            fields = (
                "id,permalink_url,message,created_time,from,"
                "attachments.limit(1){type,url,media,target},"
                "comments.summary(true).limit(0),"
                "reactions.summary(true).limit(100),"
                "shares,"
                "full_picture,"
                "status_type,"
                "is_published"
            )

        url = f"{self.base_url}/{post_id}"
        params = {
            "fields": fields,
            "access_token": self.page_access_token,
        }

        response = await self.http.get(url, params=params)

        if response.status_code == 200:
            post_data = response.json()
            return post_data
        else:
            logger.error(
                f"❌ FB API: Post content failed | Post ID: {post_id} | Status: {response.status_code}"
            )
            return None

    async def list_page_posts(
        self,
        page_id: str,
        limit: int = 25,
        after: Optional[str] = None,
        fields: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        List posts from a Facebook page with pagination.

        Args:
            page_id: Facebook Page ID
            limit: Max posts per page (default: 25, capped at 100)
            after: Pagination cursor
            fields: Optional fields override

        Returns:
            Raw Graph API response with data and paging, or None on failure.
            Response format:
            {
                "data": [
                    {
                        "id": "page_id_post_id",
                        "message": "Post content",
                        "created_time": "2024-01-01T00:00:00+0000",
                        "permalink_url": "https://...",
                        ...
                    }
                ],
                "paging": {
                    "cursors": {"before": "...", "after": "..."},
                    "next": "https://..."
                }
            }
        """
        if fields is None:
            fields = (
                "id,message,created_time,permalink_url,full_picture,"
                "status_type,is_published,"
                "attachments.limit(5){type,url,media,target},"
                "reactions.summary(true).limit(0),"
                "comments.summary(true).limit(0),"
                "shares"
            )

        url = f"{self.base_url}/{page_id}/posts"
        params = {
            "fields": fields,
            "limit": min(limit, 100),
            "access_token": self.page_access_token,
        }

        if after:
            params["after"] = after

        response = await self.http.get(url, params=params)

        if response.status_code == 200:
            return response.json()

        logger.error(
            f"❌ FB API: list_page_posts failed | Page: {page_id} | Status: {response.status_code}"
        )
        return None

    # ========================================================================
    # COMMENTS & ENGAGEMENT
    # ========================================================================

    async def list_comments(
        self,
        object_id: str,
        limit: int = 25,
        after: Optional[str] = None,
        order: Optional[str] = "chronological",
        fields: Optional[str] = None,
        filter_stream: bool = True,
        since: Optional[int] = None,
    ):
        """
        List direct comments for any Graph object (post or comment) using the /comments edge.

        Args:
            object_id: Post ID or comment ID whose direct comments should be listed
            limit: Max number of comments per page (default: 25, capped at 100)
            after: Graph cursor for pagination
            order: chronological | reverse_chronological ordering (default: chronological)
            fields: Optional override of returned fields
            filter_stream: Whether to set filter=stream for full fidelity (default: True)
            since: Optional UNIX timestamp to fetch comments created after this time

        Returns:
            Raw response dict from Graph API or None on failure.
        """
        if fields is None:
            fields = (
                "id,from{id,name},message,created_time,like_count,comment_count,"
                "parent{id},attachment{type,url,media,target}"
            )

        url = f"{self.base_url}/{object_id}/comments"
        params = {
            "fields": fields,
            "limit": min(limit, 100),
            "access_token": self.page_access_token,
        }

        if filter_stream:
            params["filter"] = "stream"

        if order:
            params["order"] = order

        if after:
            params["after"] = after

        if since:
            params["since"] = since

        response = await self.http.get(url, params=params)

        if response.status_code == 200:
            return response.json()

        logger.error(
            f"❌ FB API: list_comments failed | Object ID: {object_id} | Status: {response.status_code}"
        )
        return None

    async def fetch_comment_with_parent(self, comment_id: str):
        """
        Fetch a single comment with its parent information.
        Useful for processing webhook events to understand comment hierarchy.

        Args:
            comment_id: Facebook comment ID

        Returns:
            Comment data with parent info or None if not found:
            {
                "id": "comment_id",
                "from": {"name": "User Name", "id": "user_id"},
                "message": "Comment text",
                "created_time": "timestamp",
                "like_count": 0,
                "attachment": {"type": "photo", "url": "...", "media": {...}, "target": {...}},
                "parent": {"id": "parent_id", "from": {...}, "message": "...", "created_time": "..."}
            }
        """
        url = f"{self.base_url}/{comment_id}"
        params = {
            "fields": "id,from,message,created_time,like_count,attachment{type,url,media,target},parent{id,from,message,created_time}",
            "access_token": self.page_access_token,
        }

        response = await self.http.get(url, params=params)

        if response.status_code == 200:
            comment_data = response.json()
            return comment_data
        else:
            logger.warning(
                f"❌ FB API: Comment fetch failed | Comment ID: {comment_id} | Status: {response.status_code}"
            )
            return None

    async def reply_to_comment(
        self,
        comment_id: str,
        message: str,
        attachment_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Reply to a comment.

        Args:
            comment_id: ID of the comment to reply to
            message: Reply message text
            attachment_url: Optional image URL to attach

        Returns:
            API response data
        """
        url = f"{self.base_url}/{comment_id}/comments"
        data = {
            "message": message,
            "access_token": self.page_access_token,
        }
        if attachment_url:
            data["attachment_url"] = attachment_url

        response = await self.http.post(url, data=data)
        response.raise_for_status()
        return response.json()

    async def hide_comment(
        self,
        comment_id: str,
        is_hidden: bool = True,
    ) -> Dict[str, Any]:
        """
        Hide or unhide a comment.

        Args:
            comment_id: ID of the comment
            is_hidden: Whether to hide (True) or unhide (False)

        Returns:
            API response data
        """
        url = f"{self.base_url}/{comment_id}"
        data = {
            "is_hidden": is_hidden,
            "access_token": self.page_access_token,
        }
        response = await self.http.post(url, data=data)
        response.raise_for_status()
        return response.json()

    async def unhide_comment(
        self,
        comment_id: str,
    ) -> Dict[str, Any]:
        """
        Unhide a previously hidden comment.

        Args:
            comment_id: ID of the comment to unhide

        Returns:
            API response data
        """
        return await self.hide_comment(comment_id, is_hidden=False)

    async def delete_comment(
        self,
        comment_id: str,
    ) -> Dict[str, Any]:
        """
        Delete a comment.

        Args:
            comment_id: ID of the comment to delete

        Returns:
            API response data
        """
        url = f"{self.base_url}/{comment_id}"
        qs = urlencode({"access_token": self.page_access_token})
        response = await self.http.delete(f"{url}?{qs}")
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # ENGAGEMENT REFETCH (FOR AGENT USE)
    # ========================================================================

    async def get_post_engagement(
        self,
        post_id: str,
        reactions_limit: int = 100,
    ) -> Optional[Dict[str, Any]]:
        """
        FOR AGENT USE: Refetch fresh engagement data for a post.

        Use case: Agent decides data is stale and needs refresh.
        Side effect: Caller should update DB with returned data.

        Args:
            post_id: Facebook post ID
            reactions_limit: Maximum number of reactions to fetch (default: 100)

        Returns:
            Post engagement data dictionary or None if not found:
            {
                "id": "post_id",
                "reactions": {
                    "summary": {"total_count": 150},
                    "data": [
                        {"id": "user_id", "name": "User", "type": "LOVE"},
                        ...
                    ]
                },
                "shares": {"count": 25},
                "comments": {"summary": {"total_count": 50}},
                "full_picture": "https://...",
                "permalink_url": "https://...",
                "status_type": "added_photos",
                "is_published": true
            }
        """
        fields = (
            f"id,reactions.summary(true).limit({reactions_limit}),"
            "shares,"
            "comments.summary(true).limit(0),"
            "full_picture,"
            "permalink_url,"
            "status_type,"
            "is_published"
        )

        url = f"{self.base_url}/{post_id}"
        params = {
            "fields": fields,
            "access_token": self.page_access_token,
        }

        response = await self.http.get(url, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(
                f"❌ FB API: Post engagement failed | Post ID: {post_id} | Status: {response.status_code}"
            )
            return None

    async def get_comment_reactions(
        self,
        comment_id: str,
        limit: int = 100,
    ) -> Optional[Dict[str, Any]]:
        """
        FOR AGENT USE: Refetch fresh reactions for a comment.

        Args:
            comment_id: Facebook comment ID
            limit: Maximum number of reactions to fetch (default: 100)

        Returns:
            Comment reactions data dictionary or None if not found:
            {
                "id": "comment_id",
                "reactions": {
                    "summary": {"total_count": 10},
                    "data": [
                        {"id": "user_id", "name": "User", "type": "LIKE"},
                        ...
                    ]
                },
                "like_count": 10
            }
        """
        fields = f"id,reactions.summary(true).limit({limit})," "like_count"

        url = f"{self.base_url}/{comment_id}"
        params = {
            "fields": fields,
            "access_token": self.page_access_token,
        }

        response = await self.http.get(url, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            logger.warning(
                f"❌ FB API: Comment reactions failed | Comment ID: {comment_id} | Status: {response.status_code}"
            )
            return None

    # ========================================================================
    # BATCH OPERATIONS (HIGH PERFORMANCE)
    # ========================================================================

    async def batch_request(
        self,
        requests: List[Dict[str, Any]],
    ) -> List[Optional[Dict[str, Any]]]:
        """
        Execute multiple Graph API requests in a single batch call.

        Facebook allows up to 50 requests per batch, dramatically reducing API calls.

        Args:
            requests: List of request dicts with keys:
                - method: HTTP method (GET, POST, DELETE)
                - relative_url: URL path relative to graph.facebook.com/{version}/
                  Example: "123_456/reactions?limit=100"
                - body: Optional request body for POST requests (as dict)
                - headers: Optional headers (as list of dicts)
                - name: Optional name to reference this request in dependencies
                - depends_on: Optional name of request that must complete first

        Returns:
            List of responses in same order as requests.
            Each response is either:
            - Dict with "code", "headers", "body" if successful
            - None if request failed

        Example:
            requests = [
                {"method": "GET", "relative_url": "post1/reactions?limit=100"},
                {"method": "GET", "relative_url": "post2/reactions?limit=100"},
            ]
            results = await client.batch_request(requests)
            # = [{"code": 200, "body": {...}}, {"code": 200, "body": {...}}]

        Performance:
            50 individual API calls → 1 batch call = 50× faster!
        """
        if not requests:
            return []

        # Facebook batch limit is 50 requests per call
        if len(requests) > 50:
            logger.warning(
                f"⚠️ Batch request has {len(requests)} requests, but Facebook limit is 50. "
                f"Splitting into multiple batches."
            )
            # Split into chunks of 50
            all_results = []
            for i in range(0, len(requests), 50):
                chunk = requests[i : i + 50]
                chunk_results = await self.batch_request(chunk)
                all_results.extend(chunk_results)
            return all_results

        url = f"{self.base_url}/"
        headers = {
            "Authorization": f"Bearer {self.page_access_token}",
            "Content-Type": "application/json",
        }

        # Build batch payload
        batch_payload = []
        for req in requests:
            batch_item = {
                "method": req.get("method", "GET"),
                "relative_url": req["relative_url"],
            }
            if req.get("body"):
                import json

                batch_item["body"] = (
                    json.dumps(req["body"])
                    if isinstance(req["body"], dict)
                    else req["body"]
                )
            if req.get("name"):
                batch_item["name"] = req["name"]
            if req.get("depends_on"):
                batch_item["depends_on"] = req["depends_on"]
            batch_payload.append(batch_item)

        # Send batch request
        payload = {"batch": batch_payload}

        try:
            response = await self.http.post(url, json=payload, headers=headers)
            response.raise_for_status()
            batch_responses = response.json()

            # Parse responses
            results = []
            for batch_resp in batch_responses:
                if batch_resp is None:
                    results.append(None)
                    continue

                code = batch_resp.get("code")
                if code != 200:
                    logger.debug(
                        f"⚠️ Batch request item failed with code {code}: {batch_resp.get('body')}"
                    )
                    results.append(None)
                    continue

                # Parse body JSON
                body_str = batch_resp.get("body")
                if body_str:
                    try:
                        import json

                        body_data = json.loads(body_str)
                        results.append(body_data)
                    except json.JSONDecodeError:
                        logger.warning(
                            f"⚠️ Failed to parse batch response body: {body_str}"
                        )
                        results.append(None)
                else:
                    results.append(None)

            return results

        except Exception as e:
            logger.error(f"❌ Batch request failed: {e}")
            # Return None for all requests
            return [None] * len(requests)
