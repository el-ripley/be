# Escalation System Redesign — Tổng hợp thay đổi

Tài liệu này tổng hợp toàn bộ thiết kế và thay đổi cho hệ thống escalation (giao tiếp giữa suggest_response_agent và user/general_agent). **DB + application layer đã implement xong.**

API doc cho FE: [`docs/ESCALATION_API_FE.md`](ESCALATION_API_FE.md)

---

## 1. Mục tiêu thiết kế

- **Tách bảng** thay vì trigger: RLS enforce field-level access tự nhiên, không dùng trigger.
- **Escalation = cuộc hội thoại hai chiều**: messages qua lại giữa agent nhỏ và general_agent/user.
- **Status đơn giản**: `open` / `closed` — “quyển sổ nóng mở trên bàn” vs “cất tủ”.
- **Context thông minh**: chỉ load escalation **open** vào context; **closed** không load (tránh bloating). Nếu cần tham khảo cũ có thể query riêng.
- **Quyền rõ ràng**: agent nhỏ tạo escalation + gửi message + close; general_agent chỉ đọc, gửi message phản hồi, close — **không** tạo escalation mới.

---

## 2. Schema mới

### 2.1 Bảng `agent_escalations` (thread header)

| Cột | Mô tả |
|-----|--------|
| `id` | UUID PK |
| `conversation_type` | `'messages'` \| `'comments'` |
| `facebook_conversation_messages_id` / `facebook_conversation_comments_id` | Link tới conversation FB |
| `fan_page_id`, `owner_user_id` | Scope |
| `created_by` | `'suggest_response_agent'` \| `'general_agent'` \| `'user'` (mặc định suggest_response_agent) |
| `subject` | Tóm tắt ngắn cho dashboard (thay cho request_summary cũ) |
| `priority` | `'low'` \| `'normal'` \| `'high'` \| `'urgent'` |
| **`status`** | **`'open'` \| `'closed'`** — open = hot (trên bàn), closed = cold (cất tủ) |
| `suggest_response_history_id` | Optional link audit |
| `created_at`, `updated_at` | BIGINT ms |

**Đã bỏ**: `request_summary`, `request_detail`, `request_context`, `response_content`, `responded_by`, `responded_at`, status cũ (`pending`/`resolved`/`dismissed`).

### 2.2 Bảng `agent_escalation_messages` (messages trong thread)

| Cột | Mô tả |
|-----|--------|
| `id` | UUID PK |
| `escalation_id` | FK → agent_escalations, ON DELETE CASCADE |
| **`sender_type`** | **`'suggest_response_agent'` \| `'general_agent'` \| `'user'`** — RLS enforce: mỗi role chỉ INSERT được giá trị của mình |
| `content` | TEXT nội dung message |
| `context_snapshot` | JSONB optional |
| `created_at` | BIGINT ms |

---

## 3. Quyền (GRANT) và RLS

### 3.1 suggest_response_agent (suggest_response_reader / suggest_response_writer)

| Bảng | SELECT | INSERT | UPDATE | Ghi chú |
|------|--------|--------|--------|---------|
| `agent_escalations` | ✅ | ✅ | ✅ | Tạo thread, đọc, **close** (status → closed) |
| `agent_escalation_messages` | ✅ | ✅ | — | Chỉ INSERT với `sender_type = 'suggest_response_agent'` (RLS) |

- Default `sender_type = 'suggest_response_agent'` trên `agent_escalation_messages` (trong 99_rls_suggest_response.sql).
- RLS scope: conversation hiện tại (session vars: fan_page_id, conversation_type, conversation_id, owner_user_id).

### 3.2 general_agent / user (agent_reader / agent_writer)

| Bảng | SELECT | INSERT | UPDATE | Ghi chú |
|------|--------|--------|--------|---------|
| `agent_escalations` | ✅ | **Không** | ✅ | Chỉ đọc và **close** (update status); **không** tạo escalation mới |
| `agent_escalation_messages` | ✅ | ✅ | — | Chỉ INSERT với `sender_type IN ('general_agent', 'user')` (RLS) |

- Không có INSERT policy cho `agent_escalations` trong 99_rls_policies.sql.

---

## 4. Luồng status và “ai làm gì”

- **open**  
  - Thread đang “nóng”, cần xử lý.  
  - Load vào `<escalation_history>` (hot context).

- **closed**  
  - Đã xong hoặc không cần nữa.  
  - **Không** load vào context (tránh bloating). Cần thì query riêng.

**Ai đóng (close)?**

- **General agent/user**: trả lời xong, thấy không cần agent nhỏ làm gì thêm → close; hoặc add message nhưng vẫn để open nếu cần agent nhỏ xử lý tiếp.
- **Agent nhỏ**: đọc được message từ bên kia, xử lý xong (ví dụ đã trả lời khách) → tự close; hoặc escalation quá cũ (mấy tuần chưa đụng) → có thể close (trong prompt hướng dẫn “căng thì query xem lại”).

**Không** còn status `acknowledged` hay auto-mark bởi runner — mô hình đơn giản: chỉ open/closed, cả hai bên đều có thể đóng.

---

## 5. Files đã sửa

### DB (schema, RLS, indexes)

| File | Nội dung thay đổi |
|------|--------------------|
| **04b_schema_agent_comm.sql** | Viết lại `agent_escalations` (bỏ request/response, thêm created_by, subject, status open/closed); thêm bảng `agent_escalation_messages`. |
| **elripley.sql** | GRANT: agent_escalations — agent_writer chỉ SELECT + UPDATE (bỏ INSERT); thêm GRANT cho agent_escalation_messages; suggest_response_writer có INSERT+UPDATE trên agent_escalations, INSERT trên agent_escalation_messages. Comment 6→7 tables. |
| **99_rls_policies.sql** | Bật RLS cho agent_escalation_messages; thêm SELECT/INSERT (sender_type general_agent/user); **xóa** INSERT policy cho agent_escalations. |
| **99_rls_suggest_response.sql** | Thêm UPDATE policy cho agent_escalations; thêm SELECT/INSERT cho agent_escalation_messages (sender_type = suggest_response_agent); default sender_type = 'suggest_response_agent'. |
| **indexes.sql** | Index cho agent_escalation_messages (escalation_id, created_at; escalation_id, sender_type); đổi tên index owner_pending → owner_status. |

### Application layer (đã implement)

| File | Nội dung |
|------|----------|
| **agent_comm_queries.py** | Cập nhật SELECT/UPDATE cho schema mới; thêm `get_escalation_messages`, `insert_escalation_message`, `get_open_escalations_with_messages`; đổi `update_escalation_response` → `update_escalation_status`. |
| **context_builder.py** | `_build_escalation_context_prompt()` — query open escalations + messages, inject `<escalation_history>` giữa user_memory và conversation_data. |
| **system_prompt_messages.md**, **system_prompt_comments.md** | Section `<escalation_history>`; Core Capabilities — Escalation System: architecture (thread + messages, open/closed), hướng dẫn đọc response / tạo thread / follow-up / close, guidelines. **SQL examples nằm ở tool description**, system prompt chỉ reference "see tool for SQL pattern". |
| **sql_query_messages.md**, **sql_query_comments.md** | Schema `agent_escalations` + `agent_escalation_messages`; COMMON PATTERNS: create thread (2 queries trong 1 WRITE call), create với `context_snapshot` (JSONB), add follow-up message, close (UPDATE status), query closed escalations; note open pre-loaded trong `<escalation_history>`. (messages còn có Customer Memory patterns: attach/detach media, update memory.) |
| **api/escalations/** (schemas, router, handler, service) | API cho FE: GET list, GET detail (kèm messages), PATCH status, POST message. Schemas: EscalationItem (subject, created_by, status open/closed), EscalationMessageItem, EscalationDetailResponse. |

---

## 6. Việc còn chưa làm (deferred)

1. **general_agent tool_description.md** — Schema agent_escalations trong tool của general_agent vẫn dùng cột cũ; dự kiến cập nhật phiên sau.

2. **Migration** — Dev mode bỏ qua. Khi lên production: script migrate từ bảng cũ (request/response) sang schema mới (1 row escalation + N rows messages) nếu có data cũ.

---

## 7. Tóm tắt một dòng

- **DB**: Escalation = 1 bảng thread (`agent_escalations`, status open/closed) + 1 bảng messages (`agent_escalation_messages`, sender_type). RLS phân quyền rõ: agent nhỏ tạo + gửi message + close; general chỉ đọc, gửi message, close.
- **App**: Đã inject escalation **open** vào `<escalation_history>`, cập nhật prompts và sql_query descriptions, API FE (list/detail/update/post message). Agent tự close khi xong hoặc khi quá cũ.
