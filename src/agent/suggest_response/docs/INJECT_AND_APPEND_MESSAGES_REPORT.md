# Báo cáo: Inject message & Append user message khi trigger Suggest Response

Tài liệu mô tả chi tiết các trường hợp **inject message** (hệ thống chèn nội dung vào message/context) và cách **user message được tạo/append** theo từng `conv_type` (**comments** và **messages**) khi trigger suggest response agent.

---

## 1. Luồng trigger và tham số liên quan

- **Entry**: `SuggestResponseOrchestrator.trigger()` nhận `conversation_type`, `trigger_source`, `hint`, v.v.
- **Trigger sources**: `api_manual`, `api_auto`, `webhook`, `general_agent`.
- **Trigger action** (dùng trong context, không đổi cách inject/append): `new_customer_message`, `operator_request`, `escalation_update`, `routine_check`.
- **Context build**: `SuggestResponseContextBuilder.build_context(conn, conversation_type, conversation_id, ..., trigger_action, hint)` → trả về `(input_messages, metadata)`.
- **Sau build**: Runner có thể **inject thêm** playbook vào `input_messages`; trong vòng lặp iteration có **inject** iteration warning vào tool output.

---

## 2. Conv_type: COMMENTS

### 2.1. Cấu trúc `input_messages` (comments)

- **Luôn 2 message**:
  1. **1 system message**: system prompt (page_memory, escalation_list, conversation_info, delivery_mode).
  2. **1 user message**: nội dung dạng array-of-blocks, gồm **các phần được ghép theo thứ tự** (không phải “append” nhiều user message riêng lẻ).

### 2.2. Các trường hợp INJECT vào user message (comments)

Tất cả đều nằm trong **một user message duy nhất**, thứ tự trong `user_parts`:

| Thứ tự | Nguồn | Điều kiện | Nội dung inject |
|--------|--------|-----------|------------------|
| 1 | Escalation context | Có open escalations cho conversation | `<system-reminder>` Escalation History (các escalation mở + tin nhắn nội bộ). |
| 2 | **Hint** | `hint` từ API / general_agent được truyền vào `build_context(..., hint=...)` | `<system-reminder>` Instruction Hint (raw text từ caller). |
| 3 | Conversation data | Luôn có (đã fetch comment thread) | `<conversation_data>...</conversation_data>` (fb_content + optional `<media_entries>`). |

- **Playbook (sau khi build context)**  
  Nếu playbook retrieval trả về `system_reminder`: runner gọi `_inject_playbook_into_messages(prepared.input_messages, playbook_block)`. Với comments, **message user duy nhất** có `content` là list → playbook được **append** vào cuối dưới dạng `{"type": "input_text", "text": playbook_block}`.

**Tóm tắt inject cho comments:**

- Trong **context_builder**: escalation context, hint, conversation_data (theo thứ tự đó) → **một user message**.
- Trong **runner**: playbook system-reminder → append vào **cùng user message đó** (cuối content).

### 2.3. Append “user message” (comments)

- **Không có** khái niệm “append thêm một user message riêng” sau khi build.
- Chỉ có **một user message** được tạo một lần trong `_build_comments_context`, gồm các phần trên; playbook chỉ **append block** vào content của message đó, không tạo message mới.

---

## 3. Conv_type: MESSAGES

### 3.1. Cấu trúc `input_messages` (messages)

- **1 system message** (đầu tiên): system prompt (page_memory, **user_memory**, conversation_info, escalation_list, delivery_mode).
- **Tiếp theo**: danh sách message **turn-based** (user/assistant) được **append lần lượt** từ `turn_data["turns"]`, cuối cùng có thể có **một user message “trailing”**.

### 3.2. Các trường hợp INJECT (messages)

#### A. Khi **không có turn** (empty conversation)

- **Một user message** được append với `content` = array blocks, thứ tự:
  1. Escalation context (nếu có).
  2. Ad context (nếu có).
  3. Hint (nếu có).
  4. Chuỗi `"[Empty conversation — no customer messages yet.]"`.

#### B. Khi **có turns**

- Các turn **trước turn cuối** được append nguyên bản (user/assistant), không inject thêm.
- **Turn cuối là user**:
  - **Một user message** được append, các phần (theo thứ tự):
    1. **Image-matching hint** (nếu: turn cuối có ảnh **và** page_memory có chứa ảnh/media_id).
    2. Escalation context.
    3. Ad context.
    4. Hint.
    5. Nội dung turn cuối (`last_turn["content_parts"]`).
- **Turn cuối là assistant**:
  - Append **một assistant message** (nội dung turn cuối).
  - Sau đó append **một user message “trailing”**, gồm:
    1. Escalation context (nếu có).
    2. Ad context (nếu có).
    3. Hint (nếu có).
    4. Nếu không có gì: `"[No new customer activity.]"`.

#### C. Playbook (sau build context, cả comments và messages)

- Runner gọi `_inject_playbook_into_messages(prepared.input_messages, playbook_block)`.
- Tìm **last user message** trong `input_messages` (duyệt từ cuối), rồi:
  - Nếu `content` là list: append `{"type": "input_text", "text": playbook_block}`.
  - Nếu `content` là string: nối `content + "\n\n" + playbook_block`.

#### D. Iteration warning (trong vòng lặp response_generation)

- **Không** nằm trong `input_messages` ban đầu.
- Trong `SuggestResponseIterationRunner.run`, sau mỗi iteration (khi chưa final), gọi `SuggestResponseIterationWarningInjector.inject_warning(temp_messages, current_iteration, max_iteration)`.
- Inject vào **message cuối cùng có type `function_call_output`** trong `temp_messages`: append thêm một block `{"type": "input_text", "text": warning}` vào `function_output` của message đó (80% / 90% iteration).
- Các `temp_messages` này sau đó được **append** vào Redis temp context (assistant + tool calls/outputs), không phải “user message” từ conversation.

### 3.3. Bảng tóm tắt inject (messages)

| Vị trí | Điều kiện | Nội dung inject |
|--------|-----------|------------------|
| User message (empty) | Không có turns | escalation, ad_context, hint, `"[Empty conversation — no customer messages yet.]"` |
| User message (last turn = user) | Có turns, last role = user | image_matching_hint (nếu user gửi ảnh + page memory có ảnh), escalation, ad_context, hint, content_parts |
| User message (trailing) | Có turns, last role = assistant | escalation, ad_context, hint, hoặc `"[No new customer activity.]"` |
| Runner (sau build) | Có playbook system_reminder | Playbook block → append vào **last user message** |
| Iteration runner | 80%/90% iterations | Warning → append vào **last function_call_output** (tool result) |

### 3.4. Append user message (messages)

- **Append** ở đây nghĩa là **thêm từng message vào list `input_messages`** trong `_build_messages_context`:
  1. **Empty**: append **1** user message (các block như trên).
  2. **Có turns**:
     - Append lần lượt từng turn (user hoặc assistant) **trừ turn cuối**.
     - Append **1** message cho turn cuối:
       - Nếu turn cuối là user → 1 user message (có inject image hint, escalation, ad, hint + content).
       - Nếu turn cuối là assistant → 1 assistant message, rồi append **1 user message trailing** (escalation, ad, hint hoặc "[No new customer activity.]").
- **Không có** luồng nào từ API/socket/trigger **append thêm một user message mới** (ví dụ tin nhắn mới từ client) sau khi `build_context` đã chạy; toàn bộ nội dung user/assistant đều lấy từ DB (conversation messages) và được format thành turns rồi append như trên.

---

## 4. Nguồn tham số ảnh hưởng inject/append

| Tham số | Nguồn | Ảnh hưởng |
|--------|--------|-----------|
| `conversation_type` | Caller (API, webhook, general_agent) | Chọn `_build_comments_context` hay `_build_messages_context` → khác hẳn cấu trúc và thứ tự inject/append. |
| `hint` | API (body), general_agent (trigger_suggest_response tool) | Luôn được wrap trong `<system-reminder>` Instruction Hint và đưa vào user message (comments: 1 block; messages: trong last user block hoặc trailing user message). |
| `trigger_action` | Resolver từ `trigger_source` / `trigger_reason` | Chỉ dùng cho logic/description (e.g. routine_check, new_customer_message); **không** thay đổi cấu trúc inject/append trong code hiện tại. |
| Playbook | PlaybookRetriever sau khi build context | Append vào **last user message** (cả comments và messages). |
| Iteration | IterationRunner khi gần hết số lần lặp | Inject warning vào **last function_call_output** trong temp context (không phải user message). |

---

## 5. Tóm tắt nhanh

- **Comments**: 1 system + 1 user message; inject theo thứ tự: escalation context → hint → conversation_data (trong context_builder), sau đó playbook append vào cùng user message.
- **Messages**: 1 system + N message (turns + có thể 1 trailing user); inject escalation/ad/hint/image_hint vào last hoặc trailing user message tùy last turn là user hay assistant; playbook append vào last user message; iteration warning inject vào last tool output trong temp context.
- **Append user message**: Chỉ xảy ra trong **messages** khi build context (append từng turn + 1 trailing user nếu last là assistant); **comments** không append thêm user message, chỉ có một user message duy nhất. Không có cơ chế “append thêm một user message từ bên ngoài” sau khi build.
