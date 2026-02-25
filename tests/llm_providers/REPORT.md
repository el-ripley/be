# Multi-LLM Provider Research Report

## Executive Summary

Báo cáo này tổng hợp evidence từ test suite trong `tests/llm_providers/` cho **Anthropic** (API direct) và **Google Gemini** (SDK `google.genai`), so với **OpenAI Response API** hiện dùng trong `src/agent/core/llm_call.py`. Mục tiêu: làm cơ sở thiết kế kiến trúc multi-provider.

**Setup hiện tại (từ evidence & code):**
- **Anthropic:** Gọi qua **direct API** (`get_anthropic_client_direct`, `ANTHROPIC_API_KEY`). Model mặc định **claude-sonnet-4-5** (override bằng `ANTHROPIC_MODEL`). SDK `anthropic` 0.79.0.
- **Gemini:** `gemini-2.5-pro`, SDK `google.genai` 1.2.0. Gọi qua AI Studio (`GOOGLE_API_KEY`) hoặc Vertex (service account JSON).

**Kết quả chính từ evidence:**
- **Basic completion:** Anthropic OK — `content[0].text`, `stop_reason` (end_turn). Gemini OK — `candidates[0].content.parts[0].text`, `finish_reason`: STOP, `usage_metadata`.
- **Streaming:** Anthropic: event types `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`; delta ở `content_block_delta.delta.text`; final qua `stream.get_final_message()`. Thu thập event dùng safe dict (không `event.model_dump()`) để tránh cảnh báo Pydantic. Gemini: stream trả từng chunk (GenerateContentChunk), cần accumulate text.
- **Tool calling:** Anthropic: `input_schema`; response `content[].type == "tool_use"` với `id`, `input` (dict). Tool result: message `role: user`, `type: "tool_result"`, `tool_use_id`. Gemini: `function_call` với `name`, `args` (dict), không call_id; tool result qua `Part.from_function_response(name=..., response=...)`.
- **Parallel tool calls:** Cả hai hỗ trợ nhiều tool call trong một response; Anthropic mỗi tool_use có `id` riêng; Gemini nhiều part `function_call`.
- **Multi-turn tool loop (test 05):** Cả hai thực hiện được vòng get_weather → suggest_outfit → final text.
- **Structured output:** Anthropic: tool_choice + tool, đọc `tool_use.input`. Gemini: `response_mime_type="application/json"` + `response_schema`, đọc JSON từ `response.text`.
- **Vision:** Anthropic: message content `{ type: "image", source: { type: "base64", media_type, data } }` (đã test với ảnh S3, fetch rồi base64). Gemini: `Part.from_bytes(data=..., mime_type=...)` hoặc inline_data.
- **Reasoning/thinking:**
  - **Anthropic (test 08):** Với **claude-sonnet-4-5**, **adaptive** thinking không hỗ trợ (chỉ Opus 4.6). Dùng **thinking type "enabled" + budget_tokens** thì OK — response có block `type: "thinking"` rồi `type: "text"`. Evidence ghi `model_in_response` từ API (e.g. `claude-sonnet-4-5-20250929`).
  - **Gemini (test 08):** Bật `thinking_config=ThinkingConfig(include_thoughts=True)`; response có part với `thought: true` (thought summary) và part text (answer).
- **Streaming + tool call (test 09):** Anthropic stream với tools ra event content_block_*; có block tool_use. Gemini stream với tools trả chunk có `function_call` trong parts.
- **Phase 2 — Streaming + Reasoning (test 10):** Anthropic: event sequence `message_start` → `content_block_start`(thinking) → `content_block_delta`(thinking_delta) → `content_block_stop` → `content_block_start`(text) → `content_block_delta`(text_delta) → `message_stop`. Gemini: chunks có parts với `thought=true` (thinking) rồi text.
- **Phase 2 — Triple combo (test 11):** Cả hai thực hiện được: stream + thinking + tool call, 2 turns (turn1: thinking → tool_use; turn2: thinking → text). Anthropic: 51 events turn1, 157 turn2. Gemini: 4 chunks turn1, 4 chunks turn2.
- **Phase 2 — Error handling (test 12):** Max tokens: Anthropic `stop_reason=max_tokens`; Gemini `finish_reason=MAX_TOKENS`. Refusal: Anthropic content text refusal; Gemini `finish_reason=STOP`. Invalid request: Anthropic `BadRequestError`; Gemini `ClientError`. Streaming incomplete: cả hai không raise exception; final message/chunk có stop_reason.
- **Phase 2 — Config params (test 13):** tool_choice: Anthropic `{"type":"auto"}`/`any`/`tool,name`; Gemini `ToolConfig(mode=AUTO/ANY/NONE)`. Reasoning: Anthropic `thinking={type:enabled, budget_tokens}`; Gemini `ThinkingConfig(include_thoughts=True)` — SDK không hỗ trợ thinking_budget. Verbosity: cả hai dùng system prompt. max_tokens: Anthropic required; Gemini optional. store: chỉ OpenAI có. parallel_tool_calls: cả hai support, không có param disable.

**Kết luận ngắn:** Có thể dùng một format nội bộ thống nhất (messages, tool_call, tool_result) và viết adapter từng provider. Chuẩn hóa stream events và response structure (`output[]` vs `content[]` vs `candidates[].content.parts[]`) là phần tốn công. Tool layer và iteration loop tương thích về logic; chỉ khác tên field và cách gửi tool result. Phase 2 evidence đủ để thiết kế stream handler, error handling, và config mapping.

---

## Test Run Summary (from evidence)

| Test | Anthropic (claude-sonnet-4-5, direct API) | Gemini (gemini-2.5-pro) |
|------|-------------------------------------------|-------------------------|
| 01 Basic completion | OK — content[0].text, stop_reason end_turn | OK — candidates[0].content.parts[0].text, finish_reason STOP |
| 02 Streaming | OK — event types (message_start, content_block_*, message_stop), get_final_message() | OK — chunks, accumulate text |
| 03 Single tool call | OK — tool_use id + input, tool_result by tool_use_id | OK — function_call name/args, Part.from_function_response |
| 04 Parallel tool calls | OK — multiple tool_use in one response | OK — multiple function_call parts |
| 05 Tool call loop | OK — get_weather → suggest_outfit → final | OK — same flow |
| 06 Structured output | OK — tool_choice + tool_use.input | OK — response_schema + response.text JSON |
| 07 Vision | OK — image source base64 (media_type + data) | OK — Part.from_bytes / inline_data |
| 08 Reasoning | OK — thinking type enabled + budget_tokens; adaptive chỉ Opus 4.6 | OK — include_thoughts=True → thought=true + text |
| 09 Streaming + tool | OK — events include tool_use blocks | OK — chunks contain function_call |
| **10 Streaming + reasoning** | OK — thinking_delta → text_delta event sequence | OK — chunks với thought parts rồi text |
| **11 Triple combo** | OK — turn1 51 events, turn2 157; thinking+tool_use→thinking+text | OK — turn1 4 chunks, turn2 4 chunks; same flow |
| **12a Max tokens** | OK — stop_reason=max_tokens | OK — finish_reason=MAX_TOKENS |
| **12b Refusal** | OK — content text refusal | OK — finish_reason=STOP |
| **12c Invalid request** | OK — BadRequestError | OK — ClientError |
| **12d Rate limit** | Doc only — error type mapping | Doc only |
| **12e Stream error** | OK — events + final stop_reason=max_tokens | OK — chunks + last finish_reason |
| **13a tool_choice** | OK — auto/any/tool(name); any→force tool | OK — AUTO/ANY/NONE; ANY→force tool |
| **13b Reasoning config** | OK — budget_tokens 2000/8000; omit=none | OK — include_thoughts True/False; SDK không có thinking_budget |
| **13c Verbosity** | OK — system prompt workaround | OK — system_instruction |
| **13d max_tokens** | OK — required | OK — optional |
| **13e store** | Doc — no equivalent | Doc — no equivalent |
| **13f parallel_tool_calls** | OK — 2 tool_use; no disable param | OK — 2 function_calls |
| **13g Sampling** | OK — temperature supported | OK — temperature supported |

**Ghi chú:** Cả hai provider đều chạy; evidence lưu tại `evidence/anthropic/` và `evidence/gemini/`.

---

## Provider Comparison Matrix

### Input Format

| Aspect | OpenAI Response API | Anthropic Messages API | Gemini (google.genai) |
|--------|--------------------|------------------------|------------------------|
| SDK / client | AsyncOpenAI | Anthropic (direct: api_key only) / proxy (base_url) | genai.Client(api_key=) or Vertex |
| Main method | responses.create / stream / parse | messages.create / messages.stream | models.generate_content / generate_content_stream |
| Messages param | input= | messages= | contents= (list of Content) |
| System prompt | In input as role system | Separate system= | system_instruction in GenerateContentConfig |
| Roles | user/assistant/system/developer | user/assistant | user/model (content.role) |
| Content block types | input_text, input_image, output_text | text, image, tool_use | parts: text, inline_data, function_call |

### Output Format

| Aspect | OpenAI Response API | Anthropic Messages API | Gemini |
|--------|--------------------|------------------------|--------|
| Response structure | output[] (reasoning, message, function_call, …) | content[] (text, tool_use, thinking) | candidates[].content.parts[] |
| Text location | output[].content[].text | content[].text | parts[].text |
| Stop reason | status / output types | stop_reason (end_turn, tool_use, max_tokens) | finish_reason (e.g. STOP) |
| Usage | usage.input_tokens, output_tokens | usage.input_tokens, output_tokens | usage_metadata.prompt_token_count, candidates_token_count |
| Model in response | — | message.model (evidence: model_in_response) | model_version / usage_metadata |

### Tool Calling

| Aspect | OpenAI | Anthropic | Gemini |
|--------|--------|-----------|--------|
| Tool call location | output[].type == "function_call" | content[].type == "tool_use" | parts[].function_call |
| Tool call ID | call_id | id (e.g. toolu_xxx) | No ID (match by name) |
| Arguments | arguments (JSON string) | input (dict) | args (dict) |
| Tool definition | parameters (JSON Schema) | input_schema | parameters (Schema OBJECT) |
| Tool result | function_call_output, call_id, output | user message, type tool_result, tool_use_id | Part.from_function_response(name, response) |

### Streaming Events

| OpenAI Event | Anthropic (observed) | Gemini (observed) |
|--------------|----------------------|--------------------|
| response.created | message_start | first chunk |
| response.output_text.delta | content_block_delta (delta.text) | chunk.candidates[0].content.parts[].text |
| response.output_item.done | content_block_stop, message_stop | last chunk (finish_reason set) |
| response.reasoning_summary_text.delta | content_block_delta (thinking) if supported | thought parts when include_thoughts=True |
| response.failed | error event | exception / error in chunk |

### Reasoning / Thinking

| Aspect | OpenAI | Anthropic | Gemini |
|--------|--------|-----------|--------|
| Enable param | reasoning={effort, summary} | thinking={type: **enabled**, budget_tokens} (Sonnet 4.5). **adaptive** chỉ Opus 4.6 | thinking_config=ThinkingConfig(include_thoughts=True) |
| Output location | output[].type == "reasoning" | content[].type == "thinking" rồi "text" | parts[].thought=true (thought summary) + parts[].text |

### Structured Output

| Aspect | OpenAI | Anthropic | Gemini |
|--------|--------|-----------|--------|
| Native support | responses.parse(text_format=) | Tool trick: tool_choice + tool với input_schema | response_mime_type=application/json + response_schema |
| Output location | response.output_parsed | tool_use.input | response.text (JSON string) |

### Vision

| Aspect | OpenAI | Anthropic | Gemini |
|--------|--------|-----------|--------|
| Image in message | input_image, image_url | type: image, source: { type: base64, media_type, data } hoặc url | Part.from_bytes(data, mime_type) / inline_data |
| URL support | Yes | Yes (fetch → base64 hoặc source.url) | Qua fetch bytes rồi truyền data |

### Streaming + Reasoning Events (Phase 2)

| Phase | OpenAI Event | Anthropic | Gemini |
|-------|-------------|-----------|--------|
| Thinking start | response.output_item.added (reasoning) | content_block_start (type=thinking) | chunk parts với thought=true |
| Thinking delta | response.reasoning_summary_text.delta | content_block_delta (delta.type=thinking_delta) | chunk parts thought=true + text |
| Thinking end | response.output_item.done | content_block_stop | (last thought chunk) |
| Text start | response.output_item.added (message) | content_block_start (type=text) | chunk parts text |
| Text delta | response.output_text.delta | content_block_delta (delta.text) | chunk parts text |

### Error Handling (Phase 2)

| Scenario | OpenAI | Anthropic | Gemini |
|----------|--------|-----------|--------|
| Max tokens exceeded | response.incomplete | stop_reason=max_tokens | finish_reason=MAX_TOKENS |
| Model refusal | response.refusal | content text refusal | finish_reason=STOP (content refusal) |
| Invalid request | BadRequestError | BadRequestError | ClientError |
| Streaming incomplete | response.incomplete event | final message stop_reason=max_tokens | last chunk finish_reason=MAX_TOKENS |

### Config Parameters (Phase 2)

| Param | OpenAI | Anthropic | Gemini |
|-------|--------|-----------|--------|
| tool_choice auto | "auto" | {"type":"auto"} | mode=AUTO |
| tool_choice required | "required" | {"type":"any"} | mode=ANY |
| tool_choice none | "none" | omit tools | mode=NONE |
| Reasoning enable | reasoning={effort, summary} | thinking={type:enabled, budget_tokens} | ThinkingConfig(include_thoughts=True) |
| max_tokens | optional | **required** | optional (max_output_tokens) |
| store | Yes | No equivalent | No equivalent |
| temperature | — | Yes | Yes |

---

## Architecture Implications

1. **Unified message format:** Một format nội bộ (role, content blocks, tool_call/tool_result); adapter map sang messages= / contents= và system / system_instruction.
2. **Tool layer:** ToolExecutor chuẩn hóa call_id (OpenAI/Anthropic) vs match-by-name (Gemini); arguments → dict; tool result map sang từng format.
3. **Stream handler:** Định nghĩa event chuẩn (delta_text, delta_reasoning, tool_call_done, done, error); mapper Anthropic/Gemini → chuẩn. Anthropic tránh model_dump() trực tiếp lên event để tránh cảnh báo Pydantic (union block types).
4. **Iteration loop:** Cùng vòng: response → tool call? → execute → append tool result → gọi lại. Khác nhau ở cách append (messages vs contents).
5. **Structured output:** Anthropic = tool_choice + tool_use.input; Gemini = JSON mode + response.text; OpenAI = responses.parse().
6. **Reasoning:** Anthropic Sonnet 4.5: dùng thinking type **enabled** + budget_tokens; Opus 4.6 mới dùng **adaptive**. Gemini: include_thoughts=True → parts với thought=true.
7. **Model verification:** Evidence lưu model_requested và model_in_response (từ API) để kiểm tra proxy/model thực tế.

---

## Conversion Complexity (Coupling C1–C13)

| # | Coupling Point | Complexity | Notes |
|---|----------------|------------|--------|
| C1 | LLM Client | Medium | Một interface chung; impl OpenAI, Anthropic (direct/proxy), Gemini. |
| C2 | Input param name | Low | Map messages → input= / messages= / contents=; system → system= / system_instruction. |
| C3 | Message content types | Medium | input_text/input_image/output_text ↔ text/image/tool_use ↔ parts (text, inline_data, function_call). |
| C4 | Function call format | Medium | call_id + arguments string ↔ id + input dict ↔ name + args dict (không call_id). |
| C5 | Function output format | Medium | function_call_output ↔ tool_result (tool_use_id) ↔ Part.from_function_response(name, response). |
| C6 | Reasoning format | Medium | reasoning block ↔ thinking block (enabled/adaptive tùy model) ↔ thought part + text. |
| C7 | Tool definition schema | Low | parameters ↔ input_schema (Anthropic); parameters Schema (Gemini). |
| C8 | Stream event types | High | Nhiều event OpenAI; chuẩn hóa event và map từng provider; Anthropic dùng safe serialization. |
| C9 | Response dict structure | Medium | output[] ↔ content[] ↔ candidates[].content.parts[]. |
| C10 | Web search tool | High | OpenAI built-in; Anthropic/Gemini cần custom tool hoặc bỏ qua. |
| C11 | Reasoning params | Low | reasoning / thinking (enabled vs adaptive) / thinking_config per provider. |
| C12 | Structured output | Medium | tool_choice + tool (Anthropic), JSON mode (Gemini), parse() (OpenAI). |
| C13 | Usage/billing | Low | Chuẩn hóa input_tokens, output_tokens từ usage / usage_metadata. |

---

## Evidence Files

Evidence lưu tại:

- `tests/llm_providers/evidence/anthropic/` — 01–09, 10_streaming_reasoning, 11_streaming_reasoning_tool, 12a–12e, 13a–13g
- `tests/llm_providers/evidence/gemini/` — cùng cấu trúc

Mỗi file gồm: test_name, provider, timestamp, sdk_version, model_requested, model_in_response, request, response (raw_json, key_observations), mapping_to_current_system.

---

## How to Run the Tests

**Phase 1 + Phase 2 (chạy cả Anthropic và Gemini):**

```bash
poetry run python tests/llm_providers/test_01_basic_completion.py
poetry run python tests/llm_providers/test_02_streaming.py
poetry run python tests/llm_providers/test_03_single_tool_call.py
poetry run python tests/llm_providers/test_04_parallel_tool_calls.py
poetry run python tests/llm_providers/test_05_tool_call_loop.py
poetry run python tests/llm_providers/test_06_structured_output.py
poetry run python tests/llm_providers/test_07_vision.py
poetry run python tests/llm_providers/test_08_reasoning.py
poetry run python tests/llm_providers/test_09_streaming_tool_call.py
poetry run python tests/llm_providers/test_10_streaming_reasoning.py
poetry run python tests/llm_providers/test_11_streaming_reasoning_tool.py
poetry run python tests/llm_providers/test_12_error_handling.py
poetry run python tests/llm_providers/test_13_config_params.py
```

**Env:** `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` (hoặc Vertex credentials). Cả hai provider đều chạy mặc định.
