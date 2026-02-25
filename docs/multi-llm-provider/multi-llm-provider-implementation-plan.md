# Multi-LLM Provider — Kế hoạch triển khai

Tham chiếu thiết kế: `docs/multi-llm-provider-design.md`

---

## Tổng quan phân chia công việc

```
Phase 0: Foundation (types, interface, factory)     ██░░░░░░░░  ~1 ngày
Phase 1: OpenAI Adapter (refactor, không đổi behavior) ███░░░░░░░  ~1-2 ngày
Phase 2: Anthropic Adapter                          █████░░░░░  ~2-3 ngày
Phase 3: Gemini Adapter                             ████████░░  ~2-3 ngày
Phase 4: Integration & Config                       █████████░  ~1 ngày
Phase 5: Testing & Verification                     ██████████  ~1-2 ngày
                                                    ─────────────────────
                                                    Tổng: ~8-12 ngày
```

---

## Phase 0: Foundation — Types, Interface, Factory

**Mục tiêu:** Tạo các file nền tảng mà tất cả phases sau sẽ dùng. Chưa thay đổi code hiện tại.

### Task 0.1: Canonical Types

**File:** `src/agent/core/types.py` (NEW)

```python
# Tạo file mới chứa:
# - NormalizedStreamEvent dataclass
# - CanonicalResponse TypedDict (optional, vì response là dict)
# - Error types: LLMError, LLMRateLimitError, LLMAuthError, LLMBadRequestError, LLMServerError
# - NORMALIZED_EVENT_TYPES constant set
```

**Verify:** Import thành công từ các file khác.

### Task 0.2: LLMProvider Interface

**File:** `src/agent/core/llm_provider.py` (NEW)

```python
# Abstract base class LLMProvider với 3 abstract methods:
# - create() → CanonicalResponse (dict)
# - stream() → AsyncGenerator[NormalizedStreamEvent, None]
# - parse() → Any
#
# Signature dùng canonical param names (messages, tools, etc.)
```

**Verify:** Có thể subclass mà không lỗi.

### Task 0.3: Provider Factory

**File:** `src/agent/core/provider_factory.py` (NEW)

```python
# - ProviderType enum (OPENAI, ANTHROPIC, GEMINI)
# - MODEL_PROVIDER_MAP dict
# - create_provider() function
# - get_provider_type(model) helper
```

**Verify:** `create_provider("gpt-5-mini", openai_api_key="test")` trả về đúng type.

### Task 0.4: Provider package structure

```
src/agent/core/
├── llm_call.py          (giữ nguyên, deprecated dần)
├── llm_provider.py      (NEW — interface)
├── types.py             (NEW — canonical types)
├── provider_factory.py  (NEW — factory)
└── providers/
    ├── __init__.py      (NEW)
    ├── openai_provider.py    (NEW — Phase 1)
    ├── anthropic_provider.py (NEW — Phase 2)
    └── gemini_provider.py    (NEW — Phase 3)
```

### Definition of Done Phase 0:
- [ ] `types.py` import OK, NormalizedStreamEvent instantiate OK
- [ ] `llm_provider.py` import OK, LLMProvider là valid ABC
- [ ] `provider_factory.py` import OK, create_provider() chạy
- [ ] Folder `providers/` tạo xong với `__init__.py`
- [ ] **Không thay đổi bất kỳ file hiện tại nào**

---

## Phase 1: OpenAI Adapter — Refactor without Behavior Change

**Mục tiêu:** Wrap `LLM_call` hiện tại thành `OpenAIProvider` implement `LLMProvider` interface. Sau đó swap toàn bộ callers từ `LLM_call` → `LLMProvider`. Behavior PHẢI giữ y nguyên.

### Task 1.1: Implement OpenAIProvider

**File:** `src/agent/core/providers/openai_provider.py` (NEW)

Logic:
- `__init__`: Tạo `AsyncOpenAI` client (giống `LLM_call.__init__`)
- `create()`: Gọi `client.responses.create()`, return `response.model_dump(mode="json")`
  - `messages` param → rename thành `input` khi gọi API
- `stream()`: Gọi `client.responses.stream()`, yield `NormalizedStreamEvent`
  - Với OpenAI, mapping gần như 1:1 (event type giữ nguyên)
  - Event cuối = `response.completed` với canonical response dict
- `parse()`: Port logic từ `LLM_call.parse()` (Pydantic, JSON schema, no-format)

**KEY:** Phải giữ đúng behavior. Viết unit test so sánh output OpenAIProvider vs LLM_call.

### Task 1.2: Adapt LLMStreamHandler

**File:** `src/agent/general_agent/llm_stream_handler.py` (EDIT)

Thay đổi trong `stream()` method:
1. Gọi `run_config.llm_provider.stream()` thay `run_config.llm_call.stream()`
2. Nhận `NormalizedStreamEvent` — truy cập `event.type`, `event.item_id`, `event.delta`, `event.data`
3. Loại bỏ `isinstance(event, ParsedResponse)` check — thay bằng `event.type == "response.completed"`
4. Loại bỏ `event.model_dump()` calls — data đã nằm trong `event.data`

**Chi tiết changes:**

```python
# BEFORE:
async for event in run_config.llm_call.stream(**stream_params):
    if hasattr(event, "type"):
        event_type = getattr(event, "type", "")
        ...
        elif event_type == "response.output_item.added":
            event_dict = event.model_dump(mode="json") if hasattr(event, "model_dump") else {}
            ...
        elif event_type == "response.output_text.delta":
            event_dict = {
                "item_id": getattr(event, "item_id", None),
                "delta": getattr(event, "delta", ""),
            }
            ...
    if isinstance(event, ParsedResponse):
        final_response = event

# AFTER:
async for event in run_config.llm_provider.stream(**stream_params):
    event_type = event.type
    
    if event_type == "response.output_item.added":
        await self._handle_output_item_added(..., event.data or {}, ...)
        ...
    elif event_type == "response.output_text.delta":
        await self._handle_output_text_delta(
            ..., {"item_id": event.item_id, "delta": event.delta}, ...
        )
        ...
    elif event_type == "response.completed":
        final_response_dict = event.data["response"]
    ...

# Final response handling:
if final_response_dict is None:
    if stream_status == "failed":
        ...  # giữ nguyên fallback logic
    else:
        raise RuntimeError(...)
else:
    response_dict = final_response_dict  # Đã là dict, không cần model_dump()
```

**Quan trọng:** Các `_handle_*` methods giữ nguyên signature (nhận `event_dict: Dict`). Chỉ cách tạo `event_dict` thay đổi nhẹ.

### Task 1.3: Adapt RunConfig

**File:** `src/agent/general_agent/core/run_config.py` (EDIT)

```python
# Đổi field:
# llm_call: LLM_call → llm_provider: LLMProvider
# Giữ backward compat property nếu cần
```

### Task 1.4: Adapt AgentRunner

**File:** `src/agent/general_agent/core/agent_runner.py` (EDIT)

```python
# Trong _prepare_run_config():
# BEFORE: llm_call = LLM_call(api_key=api_key)
# AFTER:  llm_provider = create_provider(model=model, openai_api_key=api_key)
```

### Task 1.5: Adapt SubAgentRunner

**File:** `src/agent/general_agent/subagent/subagent_runner.py` (EDIT)

Tương tự — `LLM_call()` → `create_provider()`.

### Task 1.6: Adapt SuggestResponseStreamHandler

**File:** `src/agent/suggest_response/socket/stream_handler.py` (EDIT)

Tương tự LLMStreamHandler — nhận NormalizedStreamEvent.

### Task 1.7: Adapt SuggestResponseRunner

**File:** `src/agent/suggest_response/core/runner.py` (EDIT)

`LLM_call()` → `create_provider()`.

### Task 1.8: Adapt SummarizerService

**File:** `src/agent/general_agent/summarization/summarizer_service.py` (EDIT)

`LLM_call().parse()` → `create_provider().parse()`.

### Task 1.9: Adapt MediaDescriptionService

**File:** `src/services/media/media_description_service.py` (EDIT)

`LLM_call().create()` → `create_provider().create()`.

### Definition of Done Phase 1:
- [ ] Tất cả callers dùng `LLMProvider` interface thay `LLM_call`
- [ ] `LLM_call` class vẫn còn nhưng không ai import trực tiếp nữa
- [ ] **Toàn bộ existing behavior giữ nguyên** — test bằng cách chạy agent bình thường với OpenAI models
- [ ] Stream events hoạt động đúng — frontend hiển thị reasoning, text, tool calls
- [ ] Billing/usage tracking vẫn correct
- [ ] Summarizer, MediaDescription, SuggestResponse vẫn hoạt động

### Verify Phase 1:
```bash
# Chạy agent bình thường — phải hoạt động y hệt trước refactor
# Test: gpt-5-mini, gpt-5.2 (có reasoning), tool calls, streaming, vision
```

---

## Phase 2: Anthropic Adapter

**Mục tiêu:** Implement `AnthropicProvider`. Sau phase này, user có thể chọn Claude models.

### Task 2.1: Message Conversion Module

**File:** `src/agent/core/providers/anthropic_provider.py` (NEW, phần message conversion)

Implement:
- `_convert_messages(canonical_messages)` → `(system_prompt, anthropic_messages)`
  - Extract system/developer → `system` param (string)
  - Convert `input_text` → `text`, `input_image` → `image` blocks
  - Convert `output_text` → `text` blocks
  - Merge reasoning → thinking blocks in assistant content
  - Merge function_call → tool_use blocks in assistant content
  - function_call_output → user message with tool_result blocks
  - Skip web_search_call items

**Test cases cần verify:**
1. Simple user→assistant conversation
2. Conversation có tool calls (function_call + function_call_output)
3. Conversation có reasoning items
4. Conversation có images
5. Multi-turn tool loop (test_05 pattern)

### Task 2.2: Tool Conversion

Implement:
- `_convert_tools(canonical_tools)` → `anthropic_tools`
  - `parameters` → `input_schema`
  - Skip `type: "web_search"` tools
  - Remove `strict` field
- `_convert_tool_choice(canonical_choice)` → Anthropic format
  - `"auto"` → `{"type": "auto"}`
  - `"required"` → `{"type": "any"}`
  - `"none"` → None (omit tools)
  - `{"type":"function","name":"X"}` → `{"type": "tool", "name": "X"}`

### Task 2.3: Response Conversion

Implement:
- `_to_canonical_response(anthropic_response)` → CanonicalResponse dict
  - `content[].type == "thinking"` → `{"type": "reasoning", ...}`
  - `content[].type == "text"` → `{"type": "message", ...}`
  - `content[].type == "tool_use"` → `{"type": "function_call", call_id=id, arguments=json.dumps(input)}`
  - `stop_reason` → `status`
  - `usage` → normalize

### Task 2.4: Reasoning Config Conversion

Implement:
- `_convert_reasoning(canonical_reasoning)` → Anthropic thinking param
  - `{"effort": "low"}` → `{"type": "enabled", "budget_tokens": 2000}`
  - `{"effort": "medium"}` → `{"type": "enabled", "budget_tokens": 8000}`
  - `{"effort": "high"}` → `{"type": "enabled", "budget_tokens": 16000}`
  - `None` → omit thinking param
- `_get_max_tokens(model, reasoning)` → int
  - Anthropic requires `max_tokens`. Calculate: `budget_tokens + base_output_tokens`
  - Default: 4096 (no thinking), 16000 (with thinking)

### Task 2.5: Streaming — Event Mapping

Implement `stream()`:
- Open `client.messages.stream()` with converted params
- Map Anthropic events → NormalizedStreamEvent:

```
Anthropic Event               → Normalized Event(s)
─────────────────────────────────────────────────────
message_start                 → response.created
                                + response.output_item.added (if has model info)

content_block_start(thinking) → response.output_item.added(type=reasoning)
                                + response.reasoning_summary_part.added

content_block_delta(thinking) → response.reasoning_summary_text.delta

content_block_stop(thinking)  → response.reasoning_summary_part.done
                                + response.output_item.done(reasoning item)

content_block_start(text)     → response.output_item.added(type=message)
                                + response.content_part.added

content_block_delta(text)     → response.output_text.delta

content_block_stop(text)      → response.content_part.done
                                + response.output_item.done(message item)

content_block_start(tool_use) → (buffer tool_use info)

content_block_delta(input_json)→ (accumulate JSON string)

content_block_stop(tool_use)  → response.output_item.added(type=function_call)
                                + response.output_item.done(function_call item)

message_delta(stop=max_tokens)→ response.incomplete

message_stop                  → (nothing, wait for get_final_message)

get_final_message()           → response.completed(canonical response)
```

**Đây là task phức tạp nhất.** Cần state machine để track current block type.

### Task 2.6: create() và parse() Implementation

- `create()`: Gọi `client.messages.create()`, convert response → canonical
- `parse()`:
  - **Pydantic format:** Dùng tool_choice trick (tạo tool từ schema, force tool_use, extract input)
  - **JSON schema:** Tương tự tool trick
  - **No format:** Regular create

### Task 2.7: Error Handling

Map Anthropic exceptions → LLMError types:
- `anthropic.BadRequestError` → `LLMBadRequestError`
- `anthropic.AuthenticationError` → `LLMAuthError`
- `anthropic.RateLimitError` → `LLMRateLimitError`
- `anthropic.InternalServerError` → `LLMServerError`
- `anthropic.APITimeoutError` → `LLMError(retryable=True)`

### Task 2.8: Verbosity Injection

For Anthropic (no native verbosity param):
- Extract system prompt from messages
- Append verbosity directive based on `text.verbosity` value
- Pass modified system prompt to API

### Definition of Done Phase 2:
- [ ] `AnthropicProvider` implement đầy đủ LLMProvider interface
- [ ] Message conversion handles tất cả message types (chat, reasoning, function_call, function_call_output, web_search_call skip)
- [ ] Tool conversion: parameters → input_schema, tool_choice mapping
- [ ] Streaming: tất cả Anthropic events map đúng sang normalized events
- [ ] Response conversion: canonical format với call_id, arguments as JSON string
- [ ] Reasoning: effort → budget_tokens mapping hoạt động
- [ ] Error handling: exceptions → LLMError types
- [ ] **Test: Chạy agent end-to-end với claude-sonnet-4-5**
  - Basic conversation ✓
  - Tool calls (single + parallel) ✓
  - Multi-turn tool loop ✓
  - Streaming text + reasoning ✓
  - Streaming text + reasoning + tool call ✓

### Verify Phase 2:
```bash
# Đặt model = "claude-sonnet-4-5" trong conversation settings
# Chạy các scenario:
# 1. Hỏi câu đơn giản → text response stream correctly
# 2. Hỏi câu cần tool → tool call + tool result + final response
# 3. Hỏi câu cần reasoning → thinking stream + text stream
# 4. Complex query cần nhiều iterations → multi-turn loop works
```

---

## Phase 3: Gemini Adapter

**Mục tiêu:** Implement `GeminiProvider`. Tương tự Phase 2 nhưng cho Gemini.

### Task 3.1: Message Conversion

- Convert canonical messages → Gemini `Content` objects
- `role: "assistant"` → `role: "model"`
- `input_text` → text Part
- `input_image` → `Part.from_bytes()` hoặc `inline_data`
- `function_call` → `Part` with `function_call`
- `function_call_output` → `Part` with `function_response(name=..., response=...)`
- `reasoning` → skip (Gemini handles internally)
- `web_search_call` → skip
- Extract system → `system_instruction` in config

### Task 3.2: Tool Conversion

- Convert canonical tools → `FunctionDeclaration` objects
- `tool_choice` → `ToolConfig(function_calling_config=FunctionCallingConfig(mode=...))`
- Skip `type: "web_search"` tools

### Task 3.3: Response Conversion

- `candidates[0].content.parts[]` → canonical `output[]`
- Parts with `thought=True` → reasoning items
- Parts with `text` → message items
- Parts with `function_call` → function_call items (**generate call_id UUID**)
- `usage_metadata` → normalize to `usage`

### Task 3.4: Call ID Management

Gemini không có call_id. Adapter phải:
1. Generate UUID khi thấy function_call trong response
2. Lưu mapping `(function_name, generation_index) → call_id`
3. Khi tạo function_response cho next turn, match by name (Gemini requirement)
4. Trong canonical response, dùng generated call_id

```python
# Trong GeminiProvider:
def _assign_call_ids(self, parts):
    """Assign generated call_ids to function_call parts."""
    for part in parts:
        if hasattr(part, 'function_call') and part.function_call:
            call_id = f"gemini_{uuid.uuid4().hex[:12]}"
            # Store mapping for later function_response matching
            ...
```

### Task 3.5: Streaming — Chunk Processing

Gemini streaming khác OpenAI/Anthropic: chỉ trả chunks (GenerateContentChunk), không có event types chi tiết.

**State machine:**
```
chunk received
├── Has thought parts? → emit reasoning events
├── Has text parts? → emit content events  
├── Has function_call parts? → emit function_call events
└── Has finish_reason? → emit response.completed

State tracking:
- first_chunk_received: bool (for response.created)
- reasoning_started: bool (for output_item.added reasoning)
- text_started: bool (for output_item.added message)
- accumulated_response: dict (build canonical response incrementally)
```

### Task 3.6: create() và parse()

- `create()`: `client.models.generate_content()`, convert → canonical
- `parse()`:
  - **JSON schema:** `response_mime_type="application/json"` + `response_schema`
  - **Pydantic:** Convert Pydantic → JSON schema, use above
  - **No format:** Regular create

### Task 3.7: Reasoning Config

- `{"effort": "low"}` → `ThinkingConfig(include_thoughts=True, thinking_budget=1024)`
- `{"effort": "medium"}` → `ThinkingConfig(include_thoughts=True)`
- `{"effort": "high"}` → `ThinkingConfig(include_thoughts=True, thinking_budget=16384)`
- `None` → omit thinking_config

**Note:** Gemini SDK `google.genai` có thể không support `thinking_budget` param (evidence từ test 13b). Cần handle gracefully.

### Task 3.8: Error Handling

Map Gemini exceptions → LLMError types:
- `google.api_core.exceptions.InvalidArgument` → `LLMBadRequestError`
- `google.api_core.exceptions.PermissionDenied` → `LLMAuthError`
- `google.api_core.exceptions.ResourceExhausted` → `LLMRateLimitError`
- `google.api_core.exceptions.InternalServerError` → `LLMServerError`
- `ClientError` (genai SDK) → `LLMBadRequestError`

### Definition of Done Phase 3:
- [ ] `GeminiProvider` implement đầy đủ LLMProvider interface
- [ ] Call ID management: generated UUIDs cho Gemini function calls
- [ ] Streaming: chunks → normalized events với state tracking
- [ ] **Test: Chạy agent end-to-end với gemini-2.5-pro**
  - Basic conversation ✓
  - Tool calls (single + parallel) ✓
  - Multi-turn tool loop ✓
  - Streaming text + reasoning ✓

---

## Phase 4: Integration & Configuration

**Mục tiêu:** Kết nối tất cả với configuration system, API key management.

### Task 4.1: API Key Management

**File:** `src/agent/common/api_key_resolver_service.py` (EDIT)

```python
# Thêm functions:
def get_anthropic_api_key() -> str:
    """Get Anthropic API key from settings/env."""
    ...

def get_gemini_api_key() -> str:
    """Get Gemini API key from settings/env."""
    ...
```

### Task 4.2: Mở rộng SUPPORTED_MODELS

**File:** `src/agent/common/conversation_settings.py` (EDIT)

```python
SUPPORTED_MODELS = [
    "gpt-5-mini", "gpt-5-nano", "gpt-5", "gpt-5.2",
    "claude-sonnet-4-5", "claude-haiku-3-5",
    "gemini-2.5-pro", "gemini-2.0-flash",
]
```

### Task 4.3: Reasoning Validation Update

```python
# get_reasoning_param() cần update:
# - GPT-5.2 with reasoning=none → return None (giữ nguyên)
# - Anthropic models: return canonical format (adapter convert)
# - Gemini models: return canonical format (adapter convert)
# Logic giữ nguyên — adapter chịu trách nhiệm convert
```

### Task 4.4: Web Search Toggle

```python
# LLMStreamHandler._build_tools_with_web_search():
# Chỉ add {"type": "web_search"} khi provider là OpenAI
# Check: isinstance(run_config.llm_provider, OpenAIProvider) hoặc check model name
```

### Task 4.5: Frontend Model Selection

Nếu frontend có dropdown chọn model → cập nhật API endpoint để accept new model names.

### Definition of Done Phase 4:
- [ ] API keys resolve đúng cho mỗi provider
- [ ] SUPPORTED_MODELS chứa models của cả 3 providers
- [ ] Web search chỉ available cho OpenAI models
- [ ] User có thể chọn model từ bất kỳ provider nào trong conversation settings

---

## Phase 5: Testing & Verification

### Task 5.1: Unit Tests — Provider Adapters

```
tests/unit/providers/
├── test_openai_provider.py       # Verify OpenAI adapter = same behavior as LLM_call
├── test_anthropic_provider.py    # Verify message/tool/response conversion
├── test_gemini_provider.py       # Verify message/tool/response/call_id conversion
└── test_provider_factory.py      # Verify model → provider mapping
```

**Key test scenarios per provider:**
1. Message conversion: tất cả message types
2. Tool conversion: definitions + tool_choice
3. Response conversion: canonical format correct
4. Streaming events: normalized event sequence correct
5. Error handling: provider exceptions → LLMError

### Task 5.2: Integration Tests — End-to-End

Test mỗi provider với actual API calls (dùng test scripts tương tự `tests/llm_providers/`):

```
tests/integration/
├── test_openai_e2e.py      # Full agent run with OpenAI model
├── test_anthropic_e2e.py   # Full agent run with Anthropic model
└── test_gemini_e2e.py      # Full agent run with Gemini model
```

**Scenarios:**
1. Basic Q&A → text response streams correctly
2. Tool call → function_call detected, executed, result sent back
3. Multi-turn tool loop → 2+ iterations complete correctly
4. Reasoning → thinking/reasoning events stream, final text correct
5. Structured output → parse() returns correct Pydantic/dict
6. Vision → image input processed correctly
7. Error → max_tokens, refusal handled gracefully

### Task 5.3: Regression Testing

- Chạy agent bình thường với OpenAI models → behavior PHẢI giữ nguyên
- Check: streaming events, tool calls, reasoning, billing, frontend display
- So sánh response format trước/sau refactor

### Task 5.4: Performance Verification

- Stream latency: Normalized event overhead < 1ms per event
- Memory: No significant increase
- Connection pooling: Each provider manages its own connections

### Definition of Done Phase 5:
- [ ] Unit tests pass cho cả 3 providers
- [ ] Integration tests pass với actual API calls
- [ ] Regression: OpenAI behavior unchanged
- [ ] No performance degradation

---

## Dependency & Risk Assessment

### Dependencies

| Phase | Depends On | Risk |
|-------|-----------|------|
| Phase 0 | Nothing | LOW — chỉ tạo file mới |
| Phase 1 | Phase 0 | MEDIUM — refactor core code, nhưng behavior giữ nguyên |
| Phase 2 | Phase 0, Phase 1 | HIGH — Anthropic stream mapping phức tạp |
| Phase 3 | Phase 0, Phase 1 | HIGH — Gemini call_id management, chunk→event mapping |
| Phase 4 | Phase 1 | LOW — config changes |
| Phase 5 | Phase 1-4 | LOW — testing only |

### High Risk Areas

1. **LLMStreamHandler refactor (Phase 1.2):**
   - Đây là file 1138 dòng, handle streaming events real-time
   - Risk: Break streaming → user thấy blank/frozen
   - Mitigation: Giữ OpenAI event names → minimize changes

2. **Anthropic stream event mapping (Phase 2.5):**
   - Anthropic events khác hoàn toàn OpenAI (block-based vs item-based)
   - Risk: Event ordering sai → UI glitch
   - Mitigation: Có evidence JSON từ test_10, test_11 để verify

3. **Gemini call_id management (Phase 3.4):**
   - Gemini không có call_id native → phải generate + track
   - Risk: Tool result matching sai → agent loop bị stuck
   - Mitigation: Unit test riêng cho call_id mapping

4. **Concurrent provider connections:**
   - Mỗi provider có SDK client riêng
   - Risk: Connection leak, timeout mismatch
   - Mitigation: Mỗi provider quản lý connection lifecycle riêng

### Rollback Strategy

Mỗi phase có thể rollback independent:
- Phase 1: Revert `llm_provider` → `llm_call` trong RunConfig
- Phase 2/3: Remove adapter file, model khỏi SUPPORTED_MODELS
- `LLM_call` class giữ nguyên suốt quá trình (deprecate, không delete)

---

## File Change Summary (Quick Reference)

### New Files (8 files)
```
src/agent/core/types.py                           — Canonical types
src/agent/core/llm_provider.py                     — Interface
src/agent/core/provider_factory.py                 — Factory
src/agent/core/providers/__init__.py               — Package
src/agent/core/providers/openai_provider.py        — OpenAI adapter
src/agent/core/providers/anthropic_provider.py     — Anthropic adapter
src/agent/core/providers/gemini_provider.py        — Gemini adapter
src/agent/common/api_key_resolver_service.py       — Update for multi-key
```

### Modified Files (9 files)
```
src/agent/general_agent/core/run_config.py                — llm_call → llm_provider
src/agent/general_agent/core/agent_runner.py              — create_provider()
src/agent/general_agent/llm_stream_handler.py             — NormalizedEvent handling
src/agent/general_agent/subagent/subagent_runner.py       — create_provider()
src/agent/suggest_response/core/runner.py                 — create_provider()
src/agent/suggest_response/socket/stream_handler.py       — NormalizedEvent handling
src/agent/general_agent/summarization/summarizer_service.py — provider.parse()
src/services/media/media_description_service.py           — provider.create()
src/agent/common/conversation_settings.py                 — SUPPORTED_MODELS expand
```

### Unchanged Files (critical — verify NO changes)
```
src/agent/general_agent/core/iteration_runner.py
src/agent/general_agent/utils/response_analyzer.py
src/agent/general_agent/tool_executor.py
src/agent/general_agent/context/messages/message_converter.py
src/agent/general_agent/context/function_output_normalizer.py
src/agent/general_agent/context/messages/context_builder.py
src/agent/tools/base.py
All tool implementations (src/agent/tools/*.py)
```
