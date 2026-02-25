# Page Playbooks: Design, Qdrant & Implementation

Tài liệu thiết kế và triển khai Playbooks với Qdrant + OpenAI Embedding.

---

## 1. Playbooks vs Page Memory

| | page_memory | page_playbooks |
|---|---|---|
| Nature | Static knowledge (facts, rules) | Situational coaching (strategies, examples) |
| When loaded | Always in system prompt | Only when situation matches |
| Structure | Container + blocks (all shown) | Independent entries (selectively matched) |
| Created by | User/agent, set once | User teaches agent via general_agent |

**Example:** title "Xử lý khách hỏi giá", situation "Khi khách hỏi giá chưa nêu rõ sản phẩm", content = guidance + examples (đừng vội báo giá, hỏi lại sản phẩm...). Injected as `<system-reminder>` into latest user message when matched.

---

## 2. Database Schema

### 2.1. `page_playbooks`

id, owner_user_id, title, situation, content, tags[], **embedding_model**, created_by_type, created_at, updated_at, deleted_at (NULL = active).

- Content is owner-scoped, not tied to any page.
- **embedding_model**: e.g. `text-embedding-3-large`, NULL = chưa embed — dùng để biết model embed và re-embed khi đổi model.

### 2.2. `page_playbook_assignments`

playbook_id, **page_admin_id** (FK facebook_page_admins — per admin, not per page), conversation_type ('messages' | 'comments'), created_at, deleted_at. UNIQUE (playbook_id, page_admin_id, conversation_type).

One playbook can be assigned to many (page_admin, conversation_type). No 'both' — use two assignment rows.

---

## 3. Qdrant

### 3.1. Collection `page_playbooks`

- **Single vector:** Một vector mặc định (dim 3072, COSINE), embed từ **title + "\n" + situation**. Content không embed, chỉ lưu trong payload để retrieve.
- **Point id** = Postgres `page_playbooks.id`.
- **Payload:** title, situation, content, tags, owner_user_id, embedding_model.
- **Sync:** Postgres là source of truth. Create/update → embed(title+situation) → upsert point; soft delete → delete point. Assignments không lưu trong Qdrant; filter theo playbook IDs từ Postgres lúc query.

### 3.2. Search

- Search theo vector similarity (query embed → so sánh với vector title+situation).
- **score_threshold** (mặc định 0.5): chỉ trả về kết quả có score ≥ threshold; tránh inject playbook không liên quan.

---

## 4. Retrieval Flow (Channel 1 — auto)

PlaybookRetriever chạy dưới dạng **mini-agent** với vòng lặp tool (không còn one-shot):

1. **Postgres:** Lấy danh sách playbook được assign qua [get_assigned_playbook_ids](src/database/postgres/repositories/playbook_queries.py).
2. **Agent loop (max 10 iterations, max 3 lần search):**
   - LLM được cấp 2 tools: **search_playbooks** (query) và **select_playbooks** (selected_ids, reason optional).
   - Mỗi iteration: LLM stream → có thể gọi search (embed + Qdrant) hoặc select (kết thúc).
   - Sau 3 lần search, chỉ còn tool select; LLM phải gọi select_playbooks để kết thúc.
3. **Kết quả:** Lấy các playbook được chọn (từ cache kết quả search) → format `<system-reminder>## Matched Playbooks` và inject vào last user message.

---

## 5. Injection Channels

- **Channel 1 (auto):** Pre-analysis LLM mô tả situation → embed → Qdrant search → inject top playbooks. — **đã implement** (xem § 5.1).
- **Channel 2 (test):** Manual hint qua **trigger_suggest_response** (tool) hoặc **POST /suggest-response/generate** (API): tham số `hint` (raw text) được inject vào context suggest_response_agent dưới dạng `<system-reminder>## Instruction Hint`. Dùng để thử nội dung hướng dẫn trước khi tạo playbook. — **đã implement**
- **Channel 3 (runtime):** suggest_response_agent query playbooks qua **manage_playbook** (mode search) — đã implement.

### 5.1. Channel 1 (auto) — PlaybookRetriever

- **Vị trí:** [src/agent/suggest_response/playbook/playbook_retriever.py](src/agent/suggest_response/playbook/playbook_retriever.py)
- **Luồng trong `SuggestResponseRunner.run()`:** Sau hash check, tạo `agent_response` → emit **run.started** → **step.started(playbook_retrieval)** → gọi `PlaybookRetriever.retrieve()` → **step.completed(playbook_retrieval)** → **step.started(response_generation)** → iteration loop (suggest_response agent) → **step.completed(response_generation)** → emit **run.completed**.
- **PlaybookRetriever.retrieve():**
  1. Resolve `page_admin_id` từ `(user_id, fan_page_id)`; lấy assigned playbook IDs từ Postgres. Nếu không có assignment → return None.
  2. Build context: system prompt (hướng dẫn search tối đa 3 lần rồi phải select) + conversation messages.
  3. **Agent loop (max 10 iterations):**
     - Gọi `LLM_call.stream()` với tools `search_playbooks`, `select_playbooks`, `tool_choice="required"`.
     - Stream reasoning → emit **playbook.reasoning.delta** / **playbook.reasoning.done** (nếu có socket_emitter).
     - Từ response final: nếu **search_playbooks** → chạy `playbook_sync_service.search_playbooks(...)`, append kết quả vào context, emit **playbook.search**; nếu **select_playbooks** → lưu selected_ids, emit **playbook.selected**, thoát loop.
     - Mỗi LLM call log → `insert_openai_response_with_agent(agent_response_id=...)`.
     - Sau 3 lần search, tools chỉ còn `select_playbooks`.
  4. Format các playbook đã chọn (từ cache) thành `<system-reminder>## Matched Playbooks` và return; nếu không chọn gì thì return None.
- **Lỗi:** Nếu retrieval lỗi → log warning và return None (suggest_response chạy tiếp không playbook).

---

## 6. Implementation — Architecture

```
ManagePlaybookTool (general_agent + suggest_response_agent)
         │
         ├── create / update / delete ──► PlaybookSyncService
         │                                      │
         └── search ───────────────────────────►├── openai_embedding_client (embed texts)
                                               ├── Qdrant (upsert/delete/search vectors)
                                               ├── Postgres (page_playbooks CRUD)
                                               └── openai_response (log usage → billing)
```

---

## 7. Implementation — Embedding

### 7.1. Pricing ([constants.py](src/agent/common/constants.py), [pricing.py](src/database/postgres/repositories/agent_queries/pricing.py))

- `text-embedding-3-large`: $0.13 / 1M tokens (input only).
- `calculate_embedding_cost(model, input_tokens)` — wrapper cho `calculate_cost(model, input_tokens, 0)`.

### 7.2. Client ([openai_embedding_client.py](src/common/clients/openai_embedding_client.py))

- `EmbeddingResult(vectors, model, total_tokens)`
- `async embed_texts(texts, model="text-embedding-3-large") -> EmbeddingResult`
- Singleton `AsyncOpenAI` (`_get_client()`) — một instance dùng chung, tránh tạo connection pool mới mỗi lần gọi.
- Dùng `OPENAI_API_KEY` (system-wide).

---

## 8. Implementation — Qdrant Layer

### 8.1. [src/database/qdrant/connection.py](src/database/qdrant/connection.py)

- `get_qdrant_client()` — singleton **AsyncQdrantClient** (async I/O, không block event loop).
- `ensure_playbooks_collection()` — tạo collection `page_playbooks` với **một vector** (3072 dim, COSINE). Được gọi **một lần lúc app startup** trong [main.py](src/main.py) lifespan; không gọi trong mỗi service call.

### 8.2. [src/database/qdrant/playbook_repository.py](src/database/qdrant/playbook_repository.py)

- `upsert_playbook(playbook_id, situation_vec, payload)` — upsert một point với một vector.
- `delete_playbook(playbook_id)`
- `search_playbooks(query_vec, playbook_ids?, limit?, score_threshold?)` — search theo vector, filter theo `score_threshold` (mặc định 0.5).

---

## 9. Implementation — Playbook Sync Service

### 9.1. [src/services/playbook/playbook_sync_service.py](src/services/playbook/playbook_sync_service.py)

**create_playbook:** INSERT Postgres → embed(title+situation) → log usage → upsert Qdrant → UPDATE embedding_model. Nếu Qdrant upsert fail: log, set `embedding_model = NULL`, re-raise.

**update_playbook:** UPDATE Postgres → nếu title/situation/content đổi: re-embed(title+situation) → log usage → upsert Qdrant. Nếu Qdrant fail: log, set `embedding_model = NULL`, re-raise.

**delete_playbook:** Soft-delete Postgres + delete point Qdrant. Nếu Qdrant delete fail: log, re-raise.

**search_playbooks:** embed(query) → log usage → search Qdrant (với score_threshold mặc định) → return results. Nếu Qdrant search fail: log, re-raise.

---

## 10. Implementation — Manage Playbook Tool

### 10.1. [src/agent/tools/manage_playbook/](src/agent/tools/manage_playbook/)

- **Modes:** create, update, delete, search.
- **Schema:** mode, playbook_id, title, situation, content, tags, query, limit, playbook_ids, description (không còn search_mode).
- **Phân quyền:** general_agent — tất cả modes; suggest_response_agent — chỉ search.
- **DB:** Tất cả modes dùng **async_db_transaction** (system connection). Conn của agent (agent_writer) chỉ dùng cho **sql_query** tool; manage_playbook luôn dùng system connection để tránh permission denied trên `openai_response` (billing) và thống nhất một nguồn quyền.

### 10.2. Đăng ký

- **general_agent:** [registry.py](src/agent/tools/registry.py) — `ManagePlaybookTool()`.
- **suggest_response_agent:** [tool_registry.py](src/agent/suggest_response/tools/tool_registry.py) — `ManagePlaybookTool(description_override=SR_MANAGE_PLAYBOOK_DESCRIPTION)` trong messages + comments.

---

## 11. Implementation — Billing

- Mỗi embedding call → `insert_openai_response_with_agent` (model=`text-embedding-3-large`).
- `update_agent_response_aggregates` gộp vào `agent_response.total_cost`.
- `deduct_credits_after_agent` trừ credits khi agent kết thúc.

**Suggest response:** `agent_response` được tạo **ngay sau hash check** (trước PlaybookRetriever và iteration). Mọi cost trong một lần chạy (LLM playbook agent loop, embedding search, LLM iteration loop) đều gắn vào cùng `agent_response_id`; `persistence.save_result(..., agent_response_id=...)` dùng lại ID này và chỉ thêm openai_response của iteration, rồi finalize và deduct credits một lần.

---

## 12. Socket events (suggest_response)

FE có thể dựa vào các event sau để biết luồng 2 bước: **chuẩn bị playbook** → **xử lý hội thoại & generate responses**.

**Thứ tự phát sinh:**

1. **run.started** — Phát ngay sau khi tạo `agent_response` (trước bước playbook). Data: `run_id`.
2. **step.started** — Bắt đầu một bước. Data: `run_id`, `step` = `"playbook_retrieval"` | `"response_generation"`.
3. Trong bước **playbook_retrieval** (nếu có): **playbook.reasoning.delta**, **playbook.reasoning.done**, **playbook.search** (query, results_count), **playbook.selected** (selected_count, selected_ids).
4. **step.completed** — Kết thúc bước. Data: `run_id`, `step`.
5. Trong bước **response_generation**: **iteration.started**, **reasoning.started** / **reasoning.delta** / **reasoning.done**, **tool_call.started**, **tool_result**, **iteration.done** (lặp theo từng iteration).
6. **run.completed** — Thành công. Data: `run_id`, `history_id`, `suggestions`, `suggestion_count`.
7. **run.error** — Lỗi. Data: `run_id`, `error`, `code` (optional).

Emitter: [src/agent/suggest_response/socket/emitter.py](src/agent/suggest_response/socket/emitter.py).

---

## 13. Config & Env

- **Qdrant:** `QDRANT_HOST`, `QDRANT_PORT_REST` (6333), `QDRANT_PORT_GRPC` (6334). Docker: `docker-compose.infra.yml` (image `qdrant/qdrant:v1.12.4`).
- **OpenAI:** `OPENAI_API_KEY` cho embedding.

---

## 14. Kiểm tra

- **Qdrant smoke test:** `poetry run python scripts/test_qdrant_named_vectors.py` (single vector: create collection, upsert, search, ID filter).
- **Import:** `from src.agent.tools.manage_playbook import ManagePlaybookTool`

**Lưu ý nâng cấp:** Nếu đã có collection cũ (2 vectors), xóa collection trong Qdrant để app startup tạo lại với 1 vector. Playbooks trong Postgres cần re-embed (re-save hoặc script re-embed) để có vector trong collection mới.

---

## 15. Các bước tiếp theo

1. ~~**Channel 1 (auto):** Situation Analyzer — LLM mô tả situation → embed → Qdrant search → inject playbooks.~~ — **Đã xong** (PlaybookRetriever, § 5.1).
2. ~~**Channel 2 (test):** Tham số hint cho `trigger_suggest_response`.~~ — **Đã xong** (hint trên API + tool, inject system-reminder).
3. ~~**Suggest response billing:** Tạo `agent_response` trước iteration để gắn cost embedding (nếu cần charge).~~ — **Đã xong** (tạo sớm, share với PlaybookRetriever + iteration).
4. **Re-embed batch:** Script re-embed playbooks khi đổi embedding model.
