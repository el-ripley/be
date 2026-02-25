# Hệ thống In-App Notification

Tài liệu mô tả thiết kế và implementation của hệ thống **in-app notification**: lưu thông báo trong DB, gửi real-time qua Socket, và REST API cho FE. Hiện tại dùng cho thông báo khi **suggest_response_agent** thay đổi escalation (tạo mới, thêm message, đóng thread).

- **API/Socket cho FE:** [`docs/NOTIFICATION_API_FE.md`](NOTIFICATION_API_FE.md)

---

## 1. Mục tiêu

- **Lưu trữ**: Notification được lưu DB để user offline vẫn xem lại, có trạng thái đọc/chưa đọc.
- **Real-time**: Khi tạo notification, emit qua Socket (`notification.new`) để FE cập nhật ngay (toast, badge).
- **Tách biệt**: `NotificationService` không biết escalation hay domain cụ thể; logic “khi nào tạo notification” nằm ở **trigger** (ví dụ `EscalationNotificationTrigger`).
- **Mở rộng**: Sau này thêm loại thông báo khác (billing, system, …) chỉ cần thêm trigger mới và gọi `NotificationService.create()`.

---

## 2. Kiến trúc tổng quan

```
┌─────────────────────────────────────────────────────────────────┐
│  TRIGGER (domain-specific)                                      │
│  EscalationNotificationTrigger: phát hiện suggest_response_agent │
│  ghi vào agent_escalations / agent_escalation_messages          │
│  → gọi NotificationService.create(...)                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  NotificationService (generic)                                  │
│  - create() → INSERT DB + emit notification.new                 │
│  - get_notifications(), get_unread_count(), mark_read(), ...    │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                     ▼
   notifications         SocketService        REST API
   (table)               notification.new     /api/notifications
```

- **DB**: Bảng `notifications` (application layer, không RLS).
- **Socket**: Event `notification.new` gửi tới room `user_{user_id}`.
- **REST**: List, unread count, mark read (single / all).

---

## 3. Schema: bảng `notifications`

File: `src/database/postgres/sql/09_schema_notifications.sql`

| Cột | Kiểu | Mô tả |
|-----|------|--------|
| `id` | UUID | PK |
| `owner_user_id` | VARCHAR(36) | FK → users, người nhận |
| `type` | VARCHAR(100) | Loại: `escalation.created`, `escalation.new_message`, `escalation.closed`, … |
| `title` | VARCHAR(500) | Tiêu đề hiển thị |
| `body` | TEXT | Nội dung chi tiết (optional) |
| `reference_type` | VARCHAR(100) | Entity nguồn: `agent_escalation`, … |
| `reference_id` | UUID | ID entity (để FE navigate) |
| `metadata` | JSONB | Dữ liệu bổ sung: fan_page_id, conversation_type, conversation_id, subject, priority, … |
| `is_read` | BOOLEAN | Đã đọc / chưa đọc |
| `read_at` | BIGINT | Thời điểm đánh dấu đọc (ms), null nếu chưa đọc |
| `created_at` | BIGINT | Thời điểm tạo (ms) |

- **RLS**: Không dùng. Chỉ application (el-ripley-user) truy cập; agent không đọc/ghi bảng này.
- **Index**: `(owner_user_id, is_read, created_at DESC)` cho list + unread count; `(reference_type, reference_id)` cho tra cứu theo entity.

---

## 4. NotificationService (generic)

File: `src/services/notifications/notification_service.py`

- **Phụ thuộc**: `SocketService` (để emit sau khi tạo).
- **Không phụ thuộc**: Escalation hay bất kỳ domain nào.

**Method chính:**

| Method | Mô tả |
|--------|--------|
| `create(owner_user_id, type, title, body=..., reference_type=..., reference_id=..., metadata=...)` | INSERT notification, emit `notification.new` tới user, trả về record vừa tạo |
| `get_notifications(owner_user_id, is_read=..., limit, offset)` | List phân trang, trả về `{ items, total_unread }` |
| `get_unread_count(owner_user_id)` | Số notification chưa đọc |
| `mark_read(notification_id, owner_user_id)` | Đánh dấu một bản ghi đã đọc |
| `mark_all_read(owner_user_id)` | Đánh dấu tất cả đã đọc |

Tạo notification ở đâu cũng được, chỉ cần gọi `NotificationService.create()` với đủ tham số.

---

## 5. EscalationNotificationTrigger (bridge cho escalation)

File: `src/services/notifications/escalation_trigger.py`

- **Phụ thuộc**: `NotificationService`.
- **Nhiệm vụ**: Sau khi **suggest_response_agent** thực thi SQL write thành công, nếu có thao tác lên `agent_escalations` hoặc `agent_escalation_messages` thì tạo notification tương ứng.

**Cách phát hiện:** So khớp chuỗi SQL (case-insensitive):

- `INSERT INTO agent_escalations` → notification type `escalation.created`
- `UPDATE agent_escalations` và có `closed` → `escalation.closed`
- `INSERT INTO agent_escalation_messages` → `escalation.new_message`

Sau đó query DB (main pool) để lấy thông tin escalation (subject, priority, id) rồi gọi `notification_service.create(...)` với `owner_user_id` = user đang chạy suggest_response.

**Luồng gọi:** Trong `SuggestResponseToolExecutor`, sau khi `sql_query` (mode write) chạy thành công → gọi `escalation_trigger.check_and_notify(...)`. Lỗi trong trigger được log, không làm fail tool.

---

## 6. Socket event

- **Tên:** `notification.new`
- **Khi nào:** Mỗi lần `NotificationService.create()` chạy xong.
- **Room:** `user_{owner_user_id}` (cùng pattern với các event khác).
- **Payload:** Một object notification (giống item trong REST list): `id`, `type`, `title`, `body`, `reference_type`, `reference_id`, `metadata`, `is_read`, `read_at`, `created_at`.

FE subscribe `notification.new` để cập nhật list, badge, (optional) toast.

---

## 7. REST API

Prefix: `/api/notifications`. Auth: JWT (giống API khác).

| Method | Path | Mô tả |
|--------|------|--------|
| GET | `/api/notifications` | List (query: `is_read`, `limit`, `offset`), kèm `total_unread` |
| GET | `/api/notifications/unread-count` | Số chưa đọc (badge) |
| PATCH | `/api/notifications/{id}/read` | Đánh dấu một notification đã đọc |
| PATCH | `/api/notifications/read-all` | Đánh dấu tất cả đã đọc |

Chi tiết request/response và ví dụ: xem [`NOTIFICATION_API_FE.md`](NOTIFICATION_API_FE.md).

---

## 8. Notification types (hiện tại)

**Escalation** (từ suggest_response_agent):

| type | Khi nào |
|------|--------|
| `escalation.created` | Agent tạo escalation mới (INSERT agent_escalations). *Khi cùng lúc tạo escalation + message đầu, chỉ gửi một notification này (không gửi thêm `escalation.new_message`).* |
| `escalation.new_message` | Agent gửi message trong thread đã tồn tại (INSERT agent_escalation_messages) |
| `escalation.closed` | Agent đóng escalation (UPDATE status = 'closed') |

`reference_type` = `agent_escalation`, `reference_id` = ID escalation.

**Payment** (từ webhook Stripe/SePay sau khi cộng credits):

| type | Khi nào |
|------|--------|
| `payment.credits_added` | User đã thanh toán thành công (Stripe hoặc SePay), credits đã được cộng vào tài khoản |

`reference_type` = `credit_transaction`, `reference_id` = null. `metadata`: `amount_usd`, `source_type` (`stripe_payment` \| `sepay_payment`). FE có thể deep link sang màn Billing/Transaction.

---

## 9. Mở rộng sau này

- **Thêm loại notification khác**: Định nghĩa `type` mới, ở chỗ xử lý nghiệp vụ (webhook, job, API, …) gọi `NotificationService.create(...)` với `type`, `title`, `reference_*`, `metadata` phù hợp.
- **Không cần sửa** NotificationService hay schema (trừ khi muốn thêm cột chung). Chỉ thêm trigger/handler mới và có thể bổ sung index nếu query theo `type`.

---

## 10. File liên quan

| Vai trò | File |
|--------|------|
| Schema | `src/database/postgres/sql/09_schema_notifications.sql` |
| Index | `src/database/postgres/sql/indexes.sql` (notifications) |
| Repository | `src/database/postgres/repositories/notification_queries.py` |
| Service (generic) | `src/services/notifications/notification_service.py` |
| Trigger (escalation) | `src/services/notifications/escalation_trigger.py` |
| Trigger (payment) | `src/services/notifications/payment_trigger.py` |
| Hook vào agent | `src/agent/suggest_response/tools/tool_executor.py`, `src/agent/suggest_response/core/runner.py` |
| Hook vào billing | `src/api/billing/handler.py` (sau Stripe/SePay webhook) |
| Socket emit | `src/socket_service/emitters.py` (`emit_notification`), `src/socket_service/socket_service.py` |
| REST API | `src/api/notifications/` (router, handler, schemas) |
| Wiring | `src/main.py` (NotificationService, EscalationNotificationTrigger, BillingHandler, router, suggest_response_runner) |

---

## 11. Tóm tắt một dòng

**Notification** = bảng `notifications` (generic) + **NotificationService** (CRUD + emit `notification.new`) + **EscalationNotificationTrigger** (phát hiện write escalation từ suggest_response_agent và gọi service). FE dùng REST để list/đếm/đánh dấu đọc và Socket để nhận thông báo mới real-time.
