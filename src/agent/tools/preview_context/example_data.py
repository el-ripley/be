"""
Example data for preview tool.
Hardcoded conversation_data for previewing suggest_response context.
Includes ad_context, message images, and comment images as encountered in real flows.
"""

EXAMPLE_MESSAGES_CONVERSATION = """=== Conversation Info ===
Page: Example Shop (Retail)
Page avatar: Shop avatar
User: Khach Hang
User avatar: User avatar

Ad context: User replied to ad "Mẫu giày Diablo Hunter III sale 260k"
Ad image: Giày đá bóng màu đen trắng, kiểu dáng thể thao

=== Messages ===
[2026-01-17 10:00] User: Cho em hoi gia giay nay

[2026-01-17 10:01] Page: Du anh/chi, gia la 500k...

[2026-01-17 10:02] User: Mẫu này còn size 43 không shop?
[Attachment: message_image - Ảnh sản phẩm giày khách gửi để tham khảo]

[2026-01-17 10:03] Page: Size 43 shop còn ạ. Bạn đặt để shop xác nhận nha.
"""

EXAMPLE_COMMENTS_CONVERSATION = """=== Comment Thread Info ===
Page: Example Shop (Retail)
Page avatar: Shop avatar
Post: "Check out our new collection!" (2026-01-17 09:00)
Post image: Bộ sưu tập giày mới

=== Comments ===
[2026-01-17 10:00] User (Khach Hang) (comment: 123_456): Hay qua!
[Attachment: message_image - Ảnh minh họa khách gửi kèm comment]

  [2026-01-17 10:05] Page (reply to 123_456): Cảm ơn bạn!

[2026-01-17 10:10] User (Minh) (comment: 789_012): Mẫu này có size 40 không?
[Attachment: message_image - Ảnh sản phẩm trong comment]
"""
