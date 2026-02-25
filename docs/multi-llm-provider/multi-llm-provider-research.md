# Multi-LLM Provider Research: Test Plan & Evidence Collection

## Objective

Trước khi thiết kế kiến trúc multi-provider (OpenAI + Anthropic + Google Gemini), cần thu thập bằng chứng thực nghiệm chi tiết về cách Anthropic và Google Gemini hoạt động. Document này mô tả kiến trúc hiện tại, các điểm coupling, và test plan chi tiết để thu thập evidence.

**Deliverables sau khi test xong:**
1. **Report file** (`tests/llm_providers/REPORT.md`) — Tổng hợp phân tích so sánh 3 providers
2. **JSON evidence files** (`tests/llm_providers/evidence/`) — Raw API input/output cho mỗi test case
3. **Test scripts** (`tests/llm_providers/`) — Runnable scripts có thể chạy lại bất kỳ lúc nào

---

## Part 1: Current Architecture — How OpenAI Is Used

### 1.1 Core LLM Client (`src/agent/core/llm_call.py`)

Hệ thống sử dụng **OpenAI Response API** (KHÔNG phải legacy chat/completions). Class `LLM_call` wrap `AsyncOpenAI` client:

```python
# LLM_call có 3 methods chính:
class LLM_call:
    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key)

    # 1. Non-streaming completion
    async def create(self, model, input, tools, tool_choice, reasoning, text, **kwargs):
        response = await self.client.responses.create(**params)
        return response.model_dump(mode="json")  # Returns dict

    # 2. Streaming
    async def stream(self, model, input, tools, reasoning, text, **kwargs):
        async with self.client.responses.stream(**params) as stream:
            async for event in stream:
                yield event          # Yields ResponseStreamEvent objects
            yield await stream.get_final_response()  # Yields ParsedResponse

    # 3. Structured output (parse)
    async def parse(self, model, input, text_format, reasoning, **kwargs):
        # text_format can be Pydantic BaseModel or JSON schema dict
        response = await self.client.responses.parse(**params)
        return response.output_parsed  # Returns Pydantic object or dict
```

**Key Parameters gửi tới OpenAI:**
- `model`: e.g., "gpt-5-mini", "gpt-5", "gpt-5.2", "gpt-5-nano"
- `input`: List of message items (NOT `messages` — this is Response API specific)
- `tools`: List of tool definitions
- `tool_choice`: "auto", "required", "none", or specific tool
- `parallel_tool_calls`: bool (default True)
- `reasoning`: `{"effort": "low"|"medium"|"high", "summary": "auto"}` or None
- `text`: `{"verbosity": "low"|"medium"|"high"}`
- `store`: bool (default True — stores on OpenAI's side)

### 1.2 Input Message Format (Context Building)

Messages được build bởi `ContextBuilder` → `MessageConverter` → OpenAI format.

**Message types trong hệ thống (file `message_converter.py`):**

```python
# TypedDict definitions — đây chính là format gửi vào OpenAI Response API

# 1. Standard message (user/assistant/system)
OpenAIChatMessage = {"role": "user"|"assistant"|"system"|"developer", "content": Any}

# 2. Reasoning item
OpenAIReasoning = {"type": "reasoning", "summary": [{"text": "...", "type": "summary_text"}]}

# 3. Function call (from previous LLM response, fed back as context)
OpenAIFunctionCall = {"type": "function_call", "call_id": "call_xxx", "name": "tool_name", "arguments": '{"key":"val"}'}
# NOTE: arguments is JSON STRING, not dict

# 4. Function call output (tool result)
OpenAIFunctionCallOutput = {"type": "function_call_output", "call_id": "call_xxx", "output": "string" | [{"type": "input_text", "text": "..."}]}

# 5. Web search call
OpenAIWebSearchCall = {"type": "web_search_call", "action": {...}}
```

**Content format cho messages:**

```python
# User messages — content là array of content blocks:
{"role": "user", "content": [
    {"type": "input_text", "text": "Hello"},
    {"type": "input_image", "image_url": "https://s3.amazonaws.com/..."},  # Vision
]}

# Assistant messages — content là array:
{"role": "assistant", "content": [
    {"type": "output_text", "text": "Here is my response..."},
]}
# NOTE: output_text for assistant, input_text for user/tool

# System messages:
{"role": "system", "content": [{"type": "input_text", "text": "You are..."}]}
```

**Function output normalization (`function_output_normalizer.py`):**

```python
# Tool results được convert sang format:
[{"type": "input_text", "text": "result string"}]
# Tất cả đều dùng "input_text" type, không phải "output_text"
```

### 1.3 Tool Definition Format

Mỗi tool implement `BaseTool.definition` property, trả về OpenAI function tool schema:

```python
{
    "type": "function",
    "name": "sql_query",
    "description": "Execute SQL queries...",
    "parameters": {
        "type": "object",
        "properties": {
            "sqls": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ["read", "write"]}
        },
        "required": ["sqls", "mode"],
        "additionalProperties": False
    },
    "strict": True  # Some tools have this
}
```

Additionally, OpenAI built-in `web_search` tool is added:
```python
tools.append({"type": "web_search"})
```

### 1.4 Stream Event Handling (`llm_stream_handler.py` — 1138 lines)

Đây là phần phức tạp nhất. Handler xử lý 15+ OpenAI-specific event types:

```
STREAM EVENTS (theo thứ tự typical):
├── response.created                         → Emit "response.created" to frontend
├── response.output_item.added               → Track new output item (message/reasoning/function_call)
│
├── [IF REASONING]:
│   ├── response.reasoning_summary_part.added  → Create reasoning temp message
│   ├── response.reasoning_summary_text.delta  → Append reasoning text delta (HOT PATH)
│   └── response.reasoning_summary_part.done   → Finalize reasoning part
│
├── [IF TEXT CONTENT]:
│   ├── response.content_part.added            → Create content temp message
│   ├── response.output_text.delta             → Append text delta (HOT PATH - most frequent)
│   └── response.content_part.done             → Finalize content part
│
├── [IF WEB SEARCH]:
│   ├── response.web_search_call.in_progress   → Create web search temp message
│   ├── response.web_search_call.searching     → Emit search query
│   └── response.web_search_call.completed     → Finalize web search
│
├── response.output_item.done                → Finalize item (function_call → store call details)
│
├── [ERROR EVENTS]:
│   ├── response.failed                        → Fatal error
│   ├── response.incomplete                    → Max tokens reached
│   ├── response.refusal.delta                 → Accumulate refusal text
│   ├── response.refusal.done                  → Model refused
│   └── error                                  → Stream error
│
└── ParsedResponse (final)                   → Complete response with all output items
```

**Final response structure (response_dict):**

```python
{
    "id": "resp_abc123",
    "created": 1234567890,
    "status": "completed",
    "output": [
        {"type": "reasoning", "id": "rs_1", "summary": [{"text": "...", "type": "summary_text"}]},
        {"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": "..."}]},
        {"type": "function_call", "id": "fc_1", "call_id": "call_xxx", "name": "sql_query", "arguments": "{\"sqls\": [...]}"},
        {"type": "web_search_call", "id": "ws_1", "action": {"query": "...", "type": "search"}},
    ],
    "usage": {
        "input_tokens": 1500,
        "output_tokens": 300,
        "total_tokens": 1800,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 100}
    }
}
```

### 1.5 Tool Call Processing (`tool_executor.py`)

```python
# Extract tool calls from response:
for item in response_dict.get("output", []):
    if item.get("type") != "function_call":
        continue
    call_id = item.get("call_id")        # "call_xxx" — unique ID
    name = item.get("name")               # "sql_query"
    arguments_str = item.get("arguments") # '{"sqls": [...]}' — JSON STRING
    arguments = json.loads(arguments_str)  # Parse to dict

# After execution, tool result is stored as:
MessageResponse(
    type="function_call_output",
    role="tool",
    call_id=call_id,
    function_output={"success": True, "data": ...},
)
```

### 1.6 Iteration Loop (`iteration_runner.py`)

```
ITERATION LOOP (simplified):
┌─────────────────────────────────────┐
│ 1. Get temp_context from Redis      │
│ 2. Call LLM stream(input=context)   │
│ 3. Process stream events            │
│ 4. Get response_dict                │
│ 5. Check is_final (no tool calls?)  │
│    ├── YES → Save & return          │
│    └── NO  → Execute tool calls     │
│             → Append results to ctx │
│             → Go to step 1          │
└─────────────────────────────────────┘
```

`ResponseAnalyzer.is_final()` checks:
```python
# Final = no function_call items in output
for item in response_dict.get("output", []):
    if item.get("type") == "function_call":
        return False
return True
```

### 1.7 Utility Agents

**SummarizerService** (`summarizer_service.py`):
- Uses `LLM_call.parse()` with Pydantic model `SummaryOutputSchema`
- Input: `[{role: "system", content: PROMPT}, {role: "user", content: JSON_DATA}]`

**MediaDescriptionService** (`media_description_service.py`):
- Uses `LLM_call.create()` for vision
- Input: `[{role: "system", content: PROMPT}, {role: "user", content: [{type: "input_image", image_url: URL}]}]`

---

## Part 2: Coupling Points Summary

| # | Coupling Point | Where | What OpenAI-specific thing is used |
|---|---------------|-------|-----------------------------------|
| C1 | **LLM Client** | `llm_call.py` | `AsyncOpenAI`, `responses.create/stream/parse`, `ParsedResponse`, `ResponseStreamEvent` |
| C2 | **Input param name** | `llm_call.py` | `input=` (not `messages=`) |
| C3 | **Message content types** | `message_converter.py`, `image_processor.py` | `input_text`, `input_image`, `output_text` type strings |
| C4 | **Function call format** | `message_converter.py` | `type: "function_call"`, `call_id`, `arguments` as JSON string |
| C5 | **Function output format** | `function_output_normalizer.py` | `type: "function_call_output"`, `call_id`, `output` as string/array |
| C6 | **Reasoning format** | `message_converter.py` | `type: "reasoning"`, `summary: [{text, type}]` |
| C7 | **Tool definition schema** | `base.py`, all tools | `{type: "function", name, parameters: {JSON Schema}}` |
| C8 | **Stream event types** | `llm_stream_handler.py` | 15+ event types: `response.output_text.delta`, etc. |
| C9 | **Response dict structure** | `response_analyzer.py`, `tool_executor.py` | `output[]` array with typed items |
| C10 | **Web search tool** | `llm_stream_handler.py` | `{type: "web_search"}` built-in tool |
| C11 | **Reasoning params** | `conversation_settings.py` | `reasoning: {effort, summary}`, `text: {verbosity}` |
| C12 | **Structured output** | `llm_call.py` | `responses.parse()`, `text_format` param |
| C13 | **Usage/billing format** | `iteration_runner.py` | `usage.input_tokens`, `usage.output_tokens` |

---

## Part 3: Test Plan — Evidence Collection

### Environment Setup

```
tests/llm_providers/
├── utils.py                     # Shared utilities (API clients, logging, JSON save)
│
│   # === Phase 1: Core behavior ===
├── test_01_basic_completion.py  # Non-streaming basic call
├── test_02_streaming.py         # Streaming events
├── test_03_single_tool_call.py  # Single tool call round-trip
├── test_04_parallel_tool_calls.py  # Multiple tool calls
├── test_05_tool_call_loop.py    # Multi-turn tool loop (THE critical test)
├── test_06_structured_output.py # JSON schema / structured output
├── test_07_vision.py            # Image input
├── test_08_reasoning.py         # Extended thinking / reasoning
├── test_09_streaming_tool_call.py # Streaming + tool calls combined
│
│   # === Phase 2: Complex + Error + Config ===
├── test_10_streaming_reasoning.py       # Streaming + reasoning combined
├── test_11_streaming_reasoning_tool.py  # Streaming + reasoning + tool call (triple combo)
├── test_12_error_handling.py            # Error types, refusal, max_tokens, streaming errors
├── test_13_config_params.py             # API config parameters mapping
│
├── evidence/                    # JSON evidence files (auto-generated)
│   ├── anthropic/
│   │   ├── 01_basic_completion.json
│   │   ├── ...
│   │   ├── 09_streaming_tool_call.json
│   │   ├── 10_streaming_reasoning.json
│   │   ├── 11_streaming_reasoning_tool.json
│   │   ├── 12a_max_tokens_exceeded.json
│   │   ├── 12b_model_refusal.json
│   │   ├── 12c_invalid_request.json
│   │   ├── 12d_rate_limit_errors.json
│   │   ├── 12e_streaming_error_events.json
│   │   ├── 13a_tool_choice.json
│   │   ├── 13b_reasoning_config.json
│   │   ├── 13c_verbosity.json
│   │   ├── 13d_max_tokens.json
│   │   ├── 13e_store.json
│   │   ├── 13f_parallel_tool_calls.json
│   │   └── 13g_sampling_params.json
│   └── gemini/
│       └── (same structure as anthropic/)
└── REPORT.md                    # Final analysis report
```

### API Keys (from .env)

```python
# Anthropic: Use ANTHROPIC_API_KEY (direct) hoặc ANTHROPIC_API_KEY_PROXY + SUPPER_API_BASE_URL
# Google: Use GOOGLE_API_KEY
# SDK: anthropic (already in pyproject.toml ^0.39.0), google-generativeai (cần install)
```

### JSON Evidence Format

Mỗi test case PHẢI save evidence JSON theo format:

```json
{
    "test_name": "01_basic_completion",
    "provider": "anthropic",
    "timestamp": "2026-02-11T...",
    "sdk_version": "0.39.0",
    "model_used": "claude-sonnet-4-20250514",

    "request": {
        "method": "messages.create",
        "params": {
            "model": "...",
            "max_tokens": 1024,
            "system": "...",
            "messages": [...],
            "tools": [...],
            "tool_choice": "..."
        }
    },

    "response": {
        "raw_json": { },
        "key_observations": {
            "response_structure": "description of top-level keys",
            "content_format": "how content is structured",
            "role_values": ["user", "assistant"],
            "stop_reason": "end_turn | tool_use | max_tokens",
            "usage_format": {"input_tokens": 0, "output_tokens": 0}
        }
    },

    "mapping_to_current_system": {
        "equivalent_openai_param": "what OpenAI param this maps to",
        "differences": ["list of differences"],
        "conversion_needed": "description of what conversion would be needed"
    }
}
```

---

### Test 01: Basic Completion (Non-streaming)

**Mục tiêu:** Hiểu response structure cơ bản, role values, content format, usage format.

**Cần test cho cả Anthropic và Gemini:**

```python
# === ANTHROPIC ===
# SDK: anthropic
# Docs: messages.create()

import anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

response = client.messages.create(
    model="claude-sonnet-4-20250514",  # hoặc model mới nhất available
    max_tokens=1024,
    system="You are a helpful assistant.",  # NOTE: system is SEPARATE param
    messages=[
        {"role": "user", "content": "What is 2+2? Reply in one sentence."}
    ]
)
# Save full response.__dict__ or response.model_dump() as JSON
```

```python
# === GEMINI ===
# SDK: google-generativeai
# Docs: GenerativeModel.generate_content()

import google.generativeai as genai
genai.configure(api_key=GOOGLE_API_KEY)

model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",  # hoặc model mới nhất
    system_instruction="You are a helpful assistant."  # NOTE: system is SEPARATE
)
response = model.generate_content("What is 2+2? Reply in one sentence.")
# Save response as JSON
```

**Evidence cần thu thập:**
1. Full response object (JSON serialized)
2. Content location path (e.g., `response.content[0].text` vs `response.candidates[0].content.parts[0].text`)
3. Role values used (Anthropic: "user"/"assistant", Gemini: "user"/"model")
4. Usage/token counting format
5. Stop reason values and what they mean

---

### Test 02: Streaming

**Mục tiêu:** Map ra đầy đủ stream event types, thứ tự events, cách lấy final response.

```python
# === ANTHROPIC STREAMING ===
# Key: Anthropic stream events are completely different from OpenAI

with client.messages.stream(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    system="You are a helpful assistant.",
    messages=[{"role": "user", "content": "Write a short poem about coding."}]
) as stream:
    events = []
    for event in stream:
        events.append({
            "type": type(event).__name__,
            "data": event.model_dump() if hasattr(event, "model_dump") else str(event)
        })
    final_message = stream.get_final_message()
    # Save: events list + final_message
```

```python
# === GEMINI STREAMING ===
response = model.generate_content(
    "Write a short poem about coding.",
    stream=True
)
chunks = []
for chunk in response:
    chunks.append({
        "text": chunk.text if hasattr(chunk, 'text') else None,
        "candidates": str(chunk.candidates) if hasattr(chunk, 'candidates') else None,
        # Capture full chunk structure
    })
# Save: chunks list
```

**Evidence cần thu thập:**
1. **Complete list of event types** với example data cho mỗi type
2. **Event ordering** — events xuất hiện theo thứ tự nào
3. **Delta format** — text delta nằm ở đâu trong event
4. **Final response** — cách lấy complete response sau stream
5. **So sánh với OpenAI events:** Map từng Anthropic/Gemini event sang equivalent OpenAI event

**Mapping table cần tạo:**

| OpenAI Event | Anthropic Equivalent | Gemini Equivalent |
|---|---|---|
| `response.created` | `message_start` | (first chunk) |
| `response.output_text.delta` | `content_block_delta` (text_delta) | chunk.text |
| `response.output_item.done` | `content_block_stop` + `message_stop` | (last chunk) |
| `response.reasoning_summary_text.delta` | ? (thinking delta?) | ? |
| `response.failed` | ? | ? |

---

### Test 03: Single Tool Call

**Mục tiêu:** Hiểu tool call format trong response và cách gửi tool result trả lại.

**Định nghĩa tool (dùng chung cho cả 3 provider):**

```python
# Conceptual tool — same logic, different format per provider
WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get current weather for a city",
    "parameters": {  # JSON Schema
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
        },
        "required": ["city"]
    }
}
```

```python
# === ANTHROPIC TOOL CALL ===
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    system="You are a helpful assistant.",
    tools=[{
        "name": "get_weather",
        "description": "Get current weather for a city",
        "input_schema": {  # NOTE: "input_schema" not "parameters"
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
            },
            "required": ["city"]
        }
    }],
    messages=[{"role": "user", "content": "What's the weather in Hanoi?"}]
)
# Expected response.content contains:
# [{"type": "tool_use", "id": "toolu_xxx", "name": "get_weather", "input": {"city": "Hanoi"}}]
# NOTE: "input" is DICT (not JSON string like OpenAI)
# NOTE: "id" (not "call_id")
# NOTE: stop_reason = "tool_use" (not checking output[] for function_call type)
```

```python
# === ANTHROPIC TOOL RESULT (sending back) ===
response2 = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    system="You are a helpful assistant.",
    tools=[...],  # Same tools
    messages=[
        {"role": "user", "content": "What's the weather in Hanoi?"},
        {"role": "assistant", "content": response.content},  # MUST include full content
        {"role": "user", "content": [  # NOTE: tool_result goes in USER message
            {
                "type": "tool_result",
                "tool_use_id": "toolu_xxx",  # Match the id from tool_use
                "content": "25°C, sunny"     # Can be string or content blocks
            }
        ]}
    ]
)
```

```python
# === GEMINI TOOL CALL ===
import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool

weather_func = FunctionDeclaration(
    name="get_weather",
    description="Get current weather for a city",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
        },
        "required": ["city"]
    }
)
tool = Tool(function_declarations=[weather_func])

model = genai.GenerativeModel("gemini-2.0-flash", tools=[tool])
chat = model.start_chat()  # Gemini uses chat session for multi-turn
response = chat.send_message("What's the weather in Hanoi?")
# Expected: response.candidates[0].content.parts[0].function_call
# function_call.name = "get_weather"
# function_call.args = {"city": "Hanoi"}  # DICT (not JSON string, no call_id)
```

```python
# === GEMINI TOOL RESULT ===
from google.protobuf.struct_pb2 import Struct

result = Struct()
result.update({"temperature": "25°C", "condition": "sunny"})

response2 = chat.send_message(
    genai.protos.Content(parts=[
        genai.protos.Part(function_response=genai.protos.FunctionResponse(
            name="get_weather",  # Match by NAME (no call_id!)
            response=result
        ))
    ])
)
```

**Evidence cần thu thập:**

| Aspect | OpenAI (current) | Anthropic (test) | Gemini (test) |
|--------|------------------|-------------------|---------------|
| Tool call location | `output[].type == "function_call"` | `content[].type == "tool_use"` | `parts[].function_call` |
| Tool call ID | `call_id: "call_xxx"` | `id: "toolu_xxx"` | **NO ID** (match by name) |
| Arguments format | JSON string | Dict | Dict |
| Arguments key | `arguments` | `input` | `args` |
| Stop reason | check output for function_call | `stop_reason == "tool_use"` | `finish_reason == "STOP"` with function_call in parts |
| Tool result role | `type: "function_call_output"` (flat item) | `role: "user"` with `type: "tool_result"` | `function_response` in content parts |
| Tool result matching | By `call_id` | By `tool_use_id` | By function `name` |
| Tool definition key | `parameters` | `input_schema` | `parameters` (same as OpenAI) |

---

### Test 04: Parallel Tool Calls

**Mục tiêu:** Xác nhận Anthropic/Gemini có hỗ trợ multiple tool calls trong 1 response không.

```python
# Define 2 tools: get_weather + get_time
# Prompt: "What's the weather in Hanoi and what time is it in Tokyo?"
# Check: Does response contain 2 tool calls?
```

**Evidence cần thu thập:**
1. Có bao nhiêu tool calls trong 1 response?
2. Format khi có nhiều tool calls (mỗi cái có ID riêng không?)
3. Cách gửi nhiều tool results cùng lúc
4. Có param tương đương `parallel_tool_calls` không?

---

### Test 05: Multi-Turn Tool Call Loop (THE CRITICAL TEST)

**Mục tiêu:** Mô phỏng đúng iteration loop hiện tại — đây là test quan trọng nhất.

**Scenario:** 
1. User hỏi "Tìm thời tiết ở Hà Nội, rồi dựa vào kết quả đó suggest outfit phù hợp"
2. LLM gọi `get_weather` → nhận kết quả → gọi `suggest_outfit` → nhận kết quả → trả lời final

```python
# === ANTHROPIC MULTI-TURN ===
messages = [{"role": "user", "content": "..."}]

# Turn 1: LLM responds with tool call
response1 = client.messages.create(model=MODEL, tools=TOOLS, messages=messages)
# Append assistant response + tool result
messages.append({"role": "assistant", "content": response1.content})
messages.append({"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "...", "content": "..."}
]})

# Turn 2: LLM responds with another tool call (or final text)
response2 = client.messages.create(model=MODEL, tools=TOOLS, messages=messages)
# Continue loop...
```

```python
# === GEMINI MULTI-TURN ===
chat = model.start_chat()

# Turn 1
response1 = chat.send_message("...")
# Extract function_call, execute, send function_response
response2 = chat.send_message(function_response_content)
# Continue loop...
```

**Evidence cần thu thập:**
1. **Full messages array** sau mỗi turn (xem cách context tích lũy)
2. **Khi nào LLM quyết định stop tool calling** và trả text (stop_reason khác nhau thế nào)
3. **Có mixed content không** — trong 1 response có cả text + tool_call? (OpenAI có thể output cả message + function_call)
4. **Context accumulation pattern** — messages array cần maintain thế nào giữa các turns

**Đây là evidence quan trọng nhất vì nó ảnh hưởng trực tiếp đến thiết kế IterationRunner mới.**

---

### Test 06: Structured Output

**Mục tiêu:** Hiểu cách mỗi provider hỗ trợ structured JSON output.

```python
# Target schema:
{
    "summary": "string",
    "key_points": ["string"],
    "sentiment": "positive|negative|neutral"
}
```

```python
# === ANTHROPIC ===
# Anthropic uses tool_use trick for structured output
# OR uses response_format (check if available in latest SDK)

# Method 1: Tool as structured output
response = client.messages.create(
    model=MODEL,
    max_tokens=1024,
    tools=[{
        "name": "structured_response",
        "description": "Return structured analysis",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]}
            },
            "required": ["summary", "key_points", "sentiment"]
        }
    }],
    tool_choice={"type": "tool", "name": "structured_response"},
    messages=[{"role": "user", "content": "Analyze: AI is transforming healthcare..."}]
)
```

```python
# === GEMINI ===
# Gemini supports response_schema directly

model = genai.GenerativeModel(
    "gemini-2.0-flash",
    generation_config=genai.GenerationConfig(
        response_mime_type="application/json",
        response_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]}
            },
            "required": ["summary", "key_points", "sentiment"]
        }
    )
)
response = model.generate_content("Analyze: AI is transforming healthcare...")
```

**Evidence cần thu thập:**
1. Có native structured output không? Hay phải dùng tool trick?
2. Output nằm ở đâu trong response? (content text vs tool input)
3. Validation: response có luôn match schema không?
4. So sánh với OpenAI `responses.parse()` behavior

---

### Test 07: Vision (Image Input)

**Mục tiêu:** Hiểu image input format.

```python
# Use a public image URL for testing
TEST_IMAGE_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/300px-PNG_transparency_demonstration_1.png"

# === ANTHROPIC ===
# Anthropic supports both URL and base64
response = client.messages.create(
    model=MODEL,
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this image."},
            {
                "type": "image",
                "source": {
                    "type": "url",  # or "base64"
                    "url": TEST_IMAGE_URL,
                    # For base64: "media_type": "image/png", "data": "base64string"
                }
            }
        ]
    }]
)

# === GEMINI ===
import PIL.Image
import requests
from io import BytesIO

img_data = requests.get(TEST_IMAGE_URL).content
img = PIL.Image.open(BytesIO(img_data))

response = model.generate_content([
    "Describe this image.",
    img  # Can pass PIL Image directly
])
# OR:
response = model.generate_content([
    "Describe this image.",
    {"mime_type": "image/png", "data": img_data}  # Raw bytes
])
```

**Evidence cần thu thập:**
1. **Image input format** — URL supported? Base64? File upload?
2. **Content block type** — OpenAI uses `input_image`, Anthropic uses `image`, Gemini uses inline
3. **Image URL restrictions** — do they fetch URLs directly? Size limits?
4. **Response format** — same as text response or different?

**So sánh table:**

| Aspect | OpenAI | Anthropic | Gemini |
|--------|--------|-----------|--------|
| Image in message | `{type: "input_image", image_url: URL}` | `{type: "image", source: {type: "url", url: URL}}` | PIL Image or `{mime_type, data}` |
| URL support | Yes (S3, public) | Yes (check restrictions) | Yes (check) |
| Base64 support | Yes | Yes | Yes |
| Max images | ? | ? | ? |

---

### Test 08: Reasoning / Extended Thinking

**Mục tiêu:** Hiểu thinking/reasoning output format.

```python
# === ANTHROPIC ===
# Extended thinking (available on Claude 3.5+)
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=16000,
    thinking={
        "type": "enabled",
        "budget_tokens": 10000  # Max tokens for thinking
    },
    messages=[{"role": "user", "content": "Solve: If a train leaves..."}]
)
# Expected: response.content contains:
# [{"type": "thinking", "thinking": "Let me work through this..."},
#  {"type": "text", "text": "The answer is..."}]

# === GEMINI ===
model = genai.GenerativeModel(
    "gemini-2.0-flash-thinking-exp",  # or appropriate thinking model
    # thinking_config might be available
)
response = model.generate_content("Solve: If a train leaves...")
# Check response structure for thinking output
```

**Evidence cần thu thập:**
1. **Thinking output format** — nằm ở đâu trong response?
2. **Streaming thinking** — thinking có stream từng delta không?
3. **Thinking in tool call context** — khi có thinking + tool call, output structure thế nào?
4. **Token counting** — thinking tokens counted separately?

**Mapping table:**

| Aspect | OpenAI | Anthropic | Gemini |
|--------|--------|-----------|--------|
| Enable param | `reasoning={effort, summary}` | `thinking={type, budget_tokens}` | `thinking_config={...}` |
| Output location | `output[].type == "reasoning"` | `content[].type == "thinking"` | `candidates[].content.parts[].thought` |
| Summary available | Yes (`summary: "auto"`) | No (full thinking text) | ? |
| Streaming | `response.reasoning_summary_text.delta` | `content_block_delta` with thinking | ? |

---

### Test 09: Streaming + Tool Calls Combined

**Mục tiêu:** Test streaming khi có tool calls — đây là scenario production thực tế.

```python
# === ANTHROPIC STREAMING + TOOL CALL ===
with client.messages.stream(
    model=MODEL,
    max_tokens=1024,
    tools=[WEATHER_TOOL],
    messages=[{"role": "user", "content": "What's the weather in Hanoi?"}]
) as stream:
    events = []
    for event in stream:
        events.append(serialize_event(event))
    final_message = stream.get_final_message()
```

**Evidence cần thu thập:**
1. **Event sequence khi có tool call** — events nào xuất hiện, theo thứ tự nào?
2. **Khi nào biết tool call complete** — event nào signal "tool call done, you can execute"?
3. **Mixed content streaming** — nếu LLM output text trước rồi mới tool call, events thế nào?
4. **Final message structure** — same as non-streaming?

---

### Test 10: Streaming + Reasoning Combined

**Mục tiêu:** Thu thập event sequence khi stream có reasoning/thinking — evidence quan trọng nhất cho việc thiết kế stream handler vì production phải handle cả reasoning events VÀ text events trong cùng 1 stream.

**Tại sao cần test riêng:** Test 02 chỉ test streaming text thuần. Test 08 chỉ test reasoning non-streaming. Stream handler (`llm_stream_handler.py` — 1138 dòng) phải handle cả reasoning + text interleaved. Thiếu evidence cho kết hợp này = thiếu cơ sở thiết kế normalized event set.

```python
# === ANTHROPIC STREAMING + REASONING ===
# Khi thinking enabled, stream events sẽ khác: có thinking blocks trước text blocks

with client.messages.stream(
    model="claude-sonnet-4-5",
    max_tokens=16000,
    thinking={"type": "enabled", "budget_tokens": 10000},
    messages=[{"role": "user", "content": "Solve step by step: What is the sum of all prime numbers less than 20?"}]
) as stream:
    events = []
    for event in stream:
        events.append(safe_serialize(event))  # Dùng _safe_event_data từ test_02
    final_message = stream.get_final_message()

# Expected event sequence (cần verify):
# message_start → 
# content_block_start (type=thinking) → content_block_delta (thinking_delta) [repeated] → content_block_stop →
# content_block_start (type=text) → content_block_delta (text_delta) [repeated] → content_block_stop →
# message_delta → message_stop
```

```python
# === GEMINI STREAMING + REASONING ===
# Với include_thoughts=True, chunks sẽ có parts với thought=True

config = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(include_thoughts=True),
)
for chunk in client.models.generate_content_stream(
    model="gemini-2.5-pro",
    contents="Solve step by step: What is the sum of all prime numbers less than 20?",
    config=config,
):
    # Capture: chunk structure, thought parts vs text parts, ordering
    pass

# Expected: chunks contain thought=True parts (thinking) before text parts (answer)
# Cần verify: thought parts stream riêng hay mix với text parts trong cùng chunk?
```

**Evidence cần thu thập:**
1. **Full event sequence** khi có thinking + text — events nào, thứ tự nào, interleaved hay sequential?
2. **Thinking delta format** — thinking text stream qua event type nào? (`content_block_delta` với `thinking_delta` type? hay event type riêng?)
3. **Boundary between thinking and text** — event nào signal "thinking done, text starts"?
4. **Final message structure** — thinking blocks + text blocks same as non-streaming test 08?
5. **So sánh event sequence:**

| Phase | OpenAI Event | Anthropic Expected | Gemini Expected |
|-------|-------------|-------------------|-----------------|
| Thinking start | `response.output_item.added` (reasoning) | `content_block_start` (type=thinking) | chunk with `thought=True` part |
| Thinking delta | `response.reasoning_summary_text.delta` | `content_block_delta` (thinking_delta) | chunk with `thought=True` + text |
| Thinking end | `response.output_item.done` (reasoning) | `content_block_stop` | ? (last thought chunk) |
| Text start | `response.output_item.added` (message) | `content_block_start` (type=text) | chunk with `thought=False` part |
| Text delta | `response.output_text.delta` | `content_block_delta` (text_delta) | chunk with text |
| Text end | `response.output_item.done` (message) | `content_block_stop` + `message_stop` | last chunk |

---

### Test 11: Streaming + Reasoning + Tool Call (Triple Combo)

**Mục tiêu:** Test scenario phức tạp nhất trong production — model vừa thinking, vừa gọi tool, vừa stream. Đây là cách `llm_stream_handler.py` + `iteration_runner.py` hoạt động thực tế.

**Scenario:** User hỏi câu phức tạp → model suy nghĩ → gọi tool → nhận result → tiếp tục suy nghĩ → trả lời.

```python
# === ANTHROPIC: STREAMING + THINKING + TOOL CALL ===
# Turn 1: Stream with thinking enabled + tools available

tools = [
    {"name": "get_weather", "description": "Get current weather", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}
]

with client.messages.stream(
    model="claude-sonnet-4-5",
    max_tokens=16000,
    thinking={"type": "enabled", "budget_tokens": 10000},
    system="You are a helpful assistant.",
    tools=tools,
    messages=[{"role": "user", "content": "I'm planning a trip to Hanoi tomorrow. What's the weather like and what should I pack?"}]
) as stream:
    events_turn1 = []
    for event in stream:
        events_turn1.append(safe_serialize(event))
    final_turn1 = stream.get_final_message()

# Expected Turn 1 event sequence:
# message_start → 
# [thinking block] content_block_start → content_block_delta (thinking) → content_block_stop →
# [text block?] maybe text before tool call →
# [tool_use block] content_block_start (tool_use) → content_block_delta (input_json_delta) → content_block_stop →
# message_delta (stop_reason=tool_use) → message_stop

# Extract tool call, send result
# ... (same as test_05 loop logic)

# Turn 2: Continue with tool result (streaming + thinking again)
messages = [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": final_turn1.content},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "25°C, sunny"}]}
]

with client.messages.stream(
    model="claude-sonnet-4-5",
    max_tokens=16000,
    thinking={"type": "enabled", "budget_tokens": 10000},
    system="You are a helpful assistant.",
    tools=tools,
    messages=messages,
) as stream:
    events_turn2 = []
    for event in stream:
        events_turn2.append(safe_serialize(event))
    final_turn2 = stream.get_final_message()

# Expected Turn 2: thinking about weather result → text response (no more tool calls)
```

```python
# === GEMINI: STREAMING + THINKING + TOOL CALL ===
# Same logic but with Gemini's streaming + thinking_config + tools

tool = types.Tool(function_declarations=[
    types.FunctionDeclaration(name="get_weather", description="Get current weather", parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]})
])
config = types.GenerateContentConfig(
    system_instruction="You are a helpful assistant.",
    tools=[tool],
    thinking_config=types.ThinkingConfig(include_thoughts=True),
)

# Turn 1: stream
chunks_turn1 = []
for chunk in client.models.generate_content_stream(
    model="gemini-2.5-pro",
    contents="I'm planning a trip to Hanoi tomorrow. What's the weather like and what should I pack?",
    config=config,
):
    chunks_turn1.append(serialize_chunk(chunk))

# Expected: chunks with thought parts, then function_call in final chunk(s)
# Extract function_call, send function_response, stream turn 2...
```

**Evidence cần thu thập:**
1. **Full event sequence cho cả 2 turns** — thinking + tool_use + text interleaved thế nào?
2. **Thinking trước tool call** — model có thinking trước khi quyết định gọi tool không? Events nào?
3. **Thinking sau tool result** — ở turn 2, model có thinking lại sau khi nhận tool result không?
4. **Mixed blocks order** — 1 response có thể chứa: thinking → text → tool_use? Hay thinking → tool_use?
5. **Final message structure mỗi turn** — content blocks ordering khi có cả thinking + text + tool_use
6. **So sánh với OpenAI:** Khi OpenAI stream response có reasoning + function_call, event sequence thế nào?

**Đây là evidence quan trọng nhất cho stream handler design vì nó cover tất cả event types cùng lúc.**

---

### Test 12: Error Handling

**Mục tiêu:** Thu thập error format, error types, và error events cho mỗi provider. Production code hiện handle 5 loại error events từ OpenAI streaming:
- `response.failed` — fatal error
- `response.incomplete` — max tokens reached
- `error` — stream error
- `response.refusal.delta` — model refusing (streaming)
- `response.refusal.done` — refusal complete

Cần biết Anthropic và Gemini tương đương thế nào để thiết kế error handling abstraction.

#### Test 12a: Max Tokens Exceeded

```python
# === ANTHROPIC ===
# Set max_tokens rất thấp để trigger stop sớm

response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=5,  # Rất thấp — sẽ bị cắt giữa chừng
    messages=[{"role": "user", "content": "Write a long essay about artificial intelligence."}]
)
# Check: stop_reason == "max_tokens"? Content bị truncated?
# Save: full response + stop_reason
```

```python
# === GEMINI ===
config = types.GenerateContentConfig(
    max_output_tokens=5,  # NOTE: tên param khác OpenAI/Anthropic
)
response = client.models.generate_content(
    model="gemini-2.5-pro",
    contents="Write a long essay about artificial intelligence.",
    config=config,
)
# Check: finish_reason == "MAX_TOKENS"? Content bị truncated?
```

```python
# === STREAMING variant (cả 2 providers) ===
# Stream với max_tokens thấp → xem event nào báo "incomplete"
# Anthropic: message_delta với stop_reason "max_tokens"?
# Gemini: last chunk với finish_reason "MAX_TOKENS"?
```

**Evidence cần thu thập:**
1. **Stop reason value** khi max tokens — OpenAI: `response.incomplete`, Anthropic: ?, Gemini: ?
2. **Streaming behavior** — event nào signal "max tokens reached" trong stream?
3. **Partial content** — content có accessible không khi bị truncated?

#### Test 12b: Model Refusal (Content Policy)

```python
# === ANTHROPIC ===
# Prompt trigger content refusal (nhẹ nhàng, ví dụ hỏi tạo malware)
response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Write complete working code for a computer virus that can spread to other machines."}]
)
# Check: stop_reason == "end_turn" nhưng content là refusal text?
# Hay có field riêng cho refusal? (OpenAI có response.refusal)
```

```python
# === GEMINI ===
response = client.models.generate_content(
    model="gemini-2.5-pro",
    contents="Write complete working code for a computer virus that can spread to other machines.",
)
# Check: finish_reason == "SAFETY"? Block bởi safety settings?
# Gemini có safety_ratings trong response — cần capture
```

**Evidence cần thu thập:**
1. **Refusal mechanism** — Anthropic: trong content text? Có field riêng? Gemini: `finish_reason=SAFETY`? `safety_ratings`?
2. **Streaming refusal** — khi stream, refusal xuất hiện thế nào? Text rồi stop? Hay event đặc biệt?

#### Test 12c: Invalid Request (API Error)

```python
# === ANTHROPIC ===
try:
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "test"}],
        tools=[{"name": "bad_tool", "input_schema": "not-a-valid-schema"}],  # Invalid
    )
except Exception as e:
    # Capture: type(e).__name__, e.status_code (if any), e.message, full repr
    pass
```

```python
# === GEMINI ===
try:
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents="test",
        config=types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name="", description="", parameters={"type": "INVALID"})
            ])]
        ),
    )
except Exception as e:
    # Capture: type(e).__name__, error details
    pass
```

**Evidence cần thu thập:**
1. **Error class hierarchy** — Anthropic: `anthropic.APIError`, `BadRequestError`, `AuthenticationError`? Gemini: equivalent?
2. **Error structure** — có `status_code`, `message`, `type` fields?
3. **Retryable vs non-retryable** — error nào nên retry?

#### Test 12d: Rate Limit / Overloaded

```python
# Khó test trực tiếp nhưng cần document error types

# === ANTHROPIC ===
# anthropic.RateLimitError (429)
# anthropic.OverloadedError (529) — Anthropic-specific
# Cần capture: error class, status_code, headers (retry-after?)

# === GEMINI ===
# google.api_core.exceptions.ResourceExhausted (429)
# Cần capture: error class, details
```

**Evidence cần thu thập:**
1. **Error type mapping table:**

| Error Type | OpenAI | Anthropic | Gemini |
|-----------|--------|-----------|--------|
| Rate limit | `openai.RateLimitError` | `anthropic.RateLimitError` ? | `google.api_core.exceptions.ResourceExhausted` ? |
| Auth | `openai.AuthenticationError` | `anthropic.AuthenticationError` ? | ? |
| Bad request | `openai.BadRequestError` | `anthropic.BadRequestError` ? | ? |
| Timeout | `openai.APITimeoutError` | `anthropic.APITimeoutError` ? | ? |
| Connection | `openai.APIConnectionError` | `anthropic.APIConnectionError` ? | ? |
| Server error | `openai.InternalServerError` | `anthropic.InternalServerError` ? | ? |
| Overloaded | N/A | `anthropic.OverloadedError` ? | ? |

2. **Retry-after header** — có header chỉ thời gian chờ không?
3. **Status codes** — mapping status codes giữa các providers

#### Test 12e: Streaming Error Events

```python
# === ANTHROPIC STREAMING ERROR ===
# Stream với max_tokens thấp hoặc invalid input → capture error events trong stream
with client.messages.stream(
    model="claude-sonnet-4-5",
    max_tokens=5,
    messages=[{"role": "user", "content": "Write a very long essay about everything."}]
) as stream:
    events = []
    for event in stream:
        events.append(safe_serialize(event))
    # Check: event nào là "error" event? Format thế nào?
    # Anthropic có `error` event type không? Hay chỉ message_delta với stop_reason?
    final = stream.get_final_message()
    # Check: final.stop_reason == "max_tokens"?
```

```python
# === GEMINI STREAMING ERROR ===
# Similar approach
config = types.GenerateContentConfig(max_output_tokens=5)
chunks = []
try:
    for chunk in client.models.generate_content_stream(
        model="gemini-2.5-pro",
        contents="Write a very long essay about everything.",
        config=config,
    ):
        chunks.append(serialize_chunk(chunk))
except Exception as e:
    # Capture: exception during streaming? Or just last chunk has finish_reason?
    pass
```

**Evidence cần thu thập:**
1. **Error event mapping:**

| OpenAI Stream Event | Anthropic Equivalent | Gemini Equivalent |
|--------------------|--------------------|------------------|
| `response.failed` | ? (error event type?) | ? (exception during iteration?) |
| `response.incomplete` | `message_delta` with `stop_reason=max_tokens`? | last chunk with `finish_reason=MAX_TOKENS`? |
| `error` (stream error) | ? | ? (exception?) |
| `response.refusal.delta` | ? (refusal in text content?) | ? (SAFETY finish_reason?) |
| `response.refusal.done` | ? | ? |

2. **Khi stream bị lỗi giữa chừng** — events nào đã nhận được có usable content không?

---

### Test 13: API Config Parameters Mapping

**Mục tiêu:** Thu thập evidence cho TỔNG THỂ các config parameters khi call API — đây là coupling point C11 và ảnh hưởng trực tiếp đến interface design của LLM client abstraction.

**Context từ production code (`llm_call.py`, `conversation_settings.py`):**

Hiện tại hệ thống dùng các params sau khi call OpenAI Response API:
- `model` — model name
- `input` — messages (Response API specific)
- `tools` — tool definitions
- `tool_choice` — "auto" / "required" / "none" / specific tool
- `parallel_tool_calls` — bool (default True)
- `reasoning` — `{"effort": "low"|"medium"|"high", "summary": "auto"}`
- `text` — `{"verbosity": "low"|"medium"|"high"}`
- `store` — bool (default True, stores on OpenAI's side)
- `text_format` — Pydantic BaseModel or JSON schema (for `parse()`)

**KHÔNG dùng** (nhưng cần biết có tương đương ở provider khác không):
- `temperature` — sampling temperature
- `top_p` — nucleus sampling
- `max_tokens` — chưa set explicitly (API tự quyết)
- `frequency_penalty` / `presence_penalty`

#### Test 13a: tool_choice Equivalent

```python
# === ANTHROPIC tool_choice ===
# OpenAI: tool_choice = "auto" | "required" | "none" | {"type": "function", "name": "xxx"}
# Anthropic: tool_choice = {"type": "auto"} | {"type": "any"} | {"type": "tool", "name": "xxx"} | không set (default auto)

# Test 1: tool_choice = auto (mặc định, model tự quyết)
response_auto = client.messages.create(
    model=MODEL, max_tokens=1024, system=SYSTEM, tools=tools,
    tool_choice={"type": "auto"},
    messages=[{"role": "user", "content": "What's 2+2?"}]  # Likely won't use tool
)

# Test 2: tool_choice = any (force model phải dùng tool — equivalent OpenAI "required")
response_any = client.messages.create(
    model=MODEL, max_tokens=1024, system=SYSTEM, tools=tools,
    tool_choice={"type": "any"},
    messages=[{"role": "user", "content": "What's 2+2?"}]  # Will be forced to use a tool
)

# Test 3: tool_choice = specific tool (force specific tool)
response_specific = client.messages.create(
    model=MODEL, max_tokens=1024, system=SYSTEM, tools=tools,
    tool_choice={"type": "tool", "name": "get_weather"},
    messages=[{"role": "user", "content": "Tell me about Hanoi"}]
)

# Save all 3 responses + whether tool was actually used
```

```python
# === GEMINI tool_choice ===
# Gemini: tool_config in GenerateContentConfig
# mode: "AUTO" | "ANY" | "NONE" | per-function filtering

# Test 1: AUTO (default)
config_auto = types.GenerateContentConfig(
    tools=[tool],
    tool_config=types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(mode="AUTO")
    ),
)

# Test 2: ANY (force tool use)
config_any = types.GenerateContentConfig(
    tools=[tool],
    tool_config=types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(mode="ANY")
    ),
)

# Test 3: NONE (disable tool use)
config_none = types.GenerateContentConfig(
    tools=[tool],
    tool_config=types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(mode="NONE")
    ),
)

# Test 4: Specific function (allowed_function_names)
config_specific = types.GenerateContentConfig(
    tools=[tool],
    tool_config=types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(
            mode="ANY",
            allowed_function_names=["get_weather"]
        )
    ),
)
```

**Evidence cần thu thập:**

| OpenAI tool_choice | Anthropic tool_choice | Gemini tool_config.mode | Notes |
|--------------------|-----------------------|------------------------|-------|
| `"auto"` | `{"type": "auto"}` | `"AUTO"` | Default behavior |
| `"required"` | `{"type": "any"}` | `"ANY"` | Force tool use |
| `"none"` | Omit tools? | `"NONE"` | Disable tool use |
| `{"type": "function", "name": "xxx"}` | `{"type": "tool", "name": "xxx"}` | `"ANY"` + `allowed_function_names=["xxx"]` | Force specific tool |

#### Test 13b: Reasoning/Thinking Config Mapping

```python
# === ANTHROPIC thinking config ===
# OpenAI: reasoning={"effort": "low"|"medium"|"high", "summary": "auto"}
# Anthropic: thinking={"type": "enabled", "budget_tokens": N} hoặc {"type": "disabled"}
#            Với Opus 4.6: thinking={"type": "adaptive"} (tự quyết budget)
# NOTE: "adaptive" chỉ hỗ trợ Opus 4.6, Sonnet 4.5 chỉ support "enabled"

# Test mapping effort -> budget_tokens:
# Cần test với các budget_tokens khác nhau và ghi nhận thinking output length

# Low effort equivalent:
response_low = client.messages.create(
    model=MODEL, max_tokens=8000,
    thinking={"type": "enabled", "budget_tokens": 2000},
    messages=[{"role": "user", "content": "What is 2+2?"}]
)

# Medium effort:
response_med = client.messages.create(
    model=MODEL, max_tokens=16000,
    thinking={"type": "enabled", "budget_tokens": 8000},
    messages=[{"role": "user", "content": "Explain quantum computing briefly."}]
)

# High effort:
response_high = client.messages.create(
    model=MODEL, max_tokens=32000,
    thinking={"type": "enabled", "budget_tokens": 16000},
    messages=[{"role": "user", "content": "Solve: Find all integer solutions to x^3 + y^3 = z^3 where x,y,z < 100"}]
)

# No thinking (equivalent "none"):
response_none = client.messages.create(
    model=MODEL, max_tokens=1024,
    # Omit thinking param entirely, or thinking={"type": "disabled"}
    messages=[{"role": "user", "content": "What is 2+2?"}]
)

# Save: thinking output length, total tokens, actual budget used for each
```

```python
# === GEMINI thinking config ===
# OpenAI: reasoning={"effort": "low"|"medium"|"high"}
# Gemini: thinking_config=ThinkingConfig(thinking_budget=N, include_thoughts=True/False)
#   thinking_budget: 0 (off), 128..N (range depends on model)
#   Default: model decides budget

# Low effort:
config_low = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=1024, include_thoughts=True),
)

# High effort:
config_high = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=16384, include_thoughts=True),
)

# Disabled:
config_off = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=0),
)

# Save: thinking output in parts, token counts
```

**Evidence cần thu thập:**

| OpenAI reasoning | Anthropic thinking | Gemini thinking_config | Notes |
|-----------------|-------------------|----------------------|-------|
| `{effort: "low", summary: "auto"}` | `{type: "enabled", budget_tokens: 2000}` | `ThinkingConfig(thinking_budget=1024)` | Approximate mapping; exact numbers cần tune |
| `{effort: "medium", summary: "auto"}` | `{type: "enabled", budget_tokens: 8000}` | `ThinkingConfig(thinking_budget=8192)` | |
| `{effort: "high", summary: "auto"}` | `{type: "enabled", budget_tokens: 16000}` | `ThinkingConfig(thinking_budget=16384)` | |
| `None` (no reasoning) | Omit / `{type: "disabled"}` | `ThinkingConfig(thinking_budget=0)` hoặc omit | |
| summary: "auto" | Không có summary — full thinking text | include_thoughts=True/False | Anthropic/Gemini không có summary mode |

#### Test 13c: text/verbosity Equivalent

```python
# === OpenAI hiện dùng: text={"verbosity": "low"|"medium"|"high"} ===
# Cần tìm equivalent cho Anthropic và Gemini

# === ANTHROPIC ===
# Anthropic KHÔNG có verbosity param native. 
# Cần test: có system prompt instruction nào control verbosity không?
# Hay phải dùng system prompt: "Be concise" / "Be detailed"?

# Test: gọi Anthropic với system prompt khác nhau để simulate verbosity
response_low = client.messages.create(
    model=MODEL, max_tokens=1024,
    system="Be extremely concise. Reply in as few words as possible.",
    messages=[{"role": "user", "content": "Explain machine learning."}]
)

response_high = client.messages.create(
    model=MODEL, max_tokens=4096,
    system="Be thorough and detailed in your response.",
    messages=[{"role": "user", "content": "Explain machine learning."}]
)

# Save: response length comparison
```

```python
# === GEMINI ===
# Gemini có response_modalities? Hay chỉ dùng system instruction?
# Check: GenerateContentConfig có param nào tương tự verbosity?

# Similar approach: system instruction as verbosity control
```

**Evidence cần thu thập:**
1. Anthropic/Gemini có native verbosity param không?
2. Nếu không, system prompt workaround có hiệu quả không?
3. Mapping table:

| OpenAI text param | Anthropic equivalent | Gemini equivalent |
|-------------------|---------------------|-------------------|
| `{verbosity: "low"}` | ? | ? |
| `{verbosity: "medium"}` | ? | ? |
| `{verbosity: "high"}` | ? | ? |

#### Test 13d: max_tokens / max_output_tokens

```python
# === ANTHROPIC ===
# Anthropic: max_tokens (required parameter — khác OpenAI nơi không bắt buộc)
# Cần biết: default behavior nếu không set? Có max limit nào?

# Test: model limits
# claude-sonnet-4-5: max output tokens? 
# claude-haiku-3-5: max output tokens?

response = client.messages.create(
    model=MODEL,
    max_tokens=8192,  # Anthropic REQUIRES max_tokens
    messages=[{"role": "user", "content": "Say hello."}]
)
# Save: actual tokens used vs max_tokens set
```

```python
# === GEMINI ===
# Gemini: max_output_tokens in GenerateContentConfig (optional)

config_with_limit = types.GenerateContentConfig(max_output_tokens=8192)
config_no_limit = types.GenerateContentConfig()  # No limit — model decides

response_limited = client.models.generate_content(model=MODEL, contents="Say hello.", config=config_with_limit)
response_unlimited = client.models.generate_content(model=MODEL, contents="Say hello.", config=config_no_limit)
```

**Evidence cần thu thập:**

| Aspect | OpenAI Response API | Anthropic | Gemini |
|--------|--------------------|-----------| -------|
| Param name | (auto / không set explicit) | `max_tokens` (REQUIRED) | `max_output_tokens` (optional) |
| Default | Model decides | Must specify | Model decides |
| Max limit | Depends on model | Depends on model | Depends on model |
| Với thinking | Phải set đủ cho reasoning + output | `max_tokens` phải >= `budget_tokens` + output | `max_output_tokens` includes thinking? |

#### Test 13e: store / Conversation Memory Equivalent

```python
# === OpenAI: store=True (default) ===
# Stores request/response on OpenAI's side for fine-tuning, evals, etc.
# Cần biết: Anthropic/Gemini có equivalent?

# === ANTHROPIC ===
# Anthropic: metadata param? Logging? 
# Check: client.messages.create(..., metadata={"user_id": "..."})? 
# Anthropic có "prompt caching" — khác store

# === GEMINI ===
# Gemini: cached_content? Tuned model? Context caching?
# Google AI Studio có history — nhưng đó là client-side
```

**Evidence cần thu thập:**
1. Anthropic có `store` equivalent? → Likely NO, just metadata
2. Gemini có `store` equivalent? → Likely NO
3. Nếu không: adapter có cần ignore/skip store param cho non-OpenAI providers không?

#### Test 13f: parallel_tool_calls Equivalent

```python
# === OpenAI: parallel_tool_calls=True/False ===
# Controls whether model can emit multiple tool calls in one response

# === ANTHROPIC ===
# Anthropic: Không có explicit param — model tự quyết
# Nhưng có param: tool_choice={"type": "auto", "disable_parallel_tool_use": True/False} ?
# Cần verify qua docs/SDK

# Test: gọi với prompt yêu cầu 2 tools, check response có 2 tool_use blocks?
# (Đã test ở test_04, nhưng cần confirm KHÔNG có param disable)

# === GEMINI ===  
# Gemini: Không có explicit param
# Model tự quyết gọi nhiều functions hay không
# (Đã verify ở test_04)
```

**Evidence cần thu thập:**

| Aspect | OpenAI | Anthropic | Gemini |
|--------|--------|-----------|--------|
| Param name | `parallel_tool_calls` | ? (`disable_parallel_tool_use` ?) | No param |
| Default | True | Always parallel if needed | Always parallel if needed |
| Can disable? | Yes (set False) | ? | Likely no |

#### Test 13g: Temperature / Sampling Params (Future-proofing)

```python
# Production code KHÔNG dùng temperature/top_p/etc., nhưng cần biết:
# 1. Mỗi provider hỗ trợ params nào?
# 2. Tên param giống hay khác?
# 3. Range/scale giống hay khác?

# === ANTHROPIC ===
response = client.messages.create(
    model=MODEL, max_tokens=1024,
    temperature=0.0,  # Deterministic
    # top_p=0.9,      # Nucleus sampling — check nếu support
    # top_k=40,        # Anthropic có top_k (OpenAI không có!)
    messages=[{"role": "user", "content": "What is 2+2?"}]
)

# === GEMINI ===
config = types.GenerateContentConfig(
    temperature=0.0,
    # top_p=0.9,
    # top_k=40,
)
response = client.models.generate_content(model=MODEL, contents="What is 2+2?", config=config)
```

**Evidence cần thu thập:**

| Param | OpenAI Response API | Anthropic | Gemini |
|-------|--------------------|-----------| -------|
| `temperature` | ? (check if supported) | Yes (0.0-1.0) | Yes (in config) |
| `top_p` | ? | Yes | Yes |
| `top_k` | No | Yes (Anthropic-specific) | Yes |
| `frequency_penalty` | ? | No (verify) | ? |
| `presence_penalty` | ? | No (verify) | ? |
| `stop_sequences` | ? | Yes | Yes (stop_sequences in config) |

---

## Part 3.5: Updated Evidence File Structure

Các test mới (10-13) sẽ save evidence vào:

```
tests/llm_providers/
├── test_10_streaming_reasoning.py
├── test_11_streaming_reasoning_tool.py
├── test_12_error_handling.py
├── test_13_config_params.py
├── evidence/
│   ├── anthropic/
│   │   ├── 10_streaming_reasoning.json
│   │   ├── 11_streaming_reasoning_tool.json
│   │   ├── 12a_max_tokens_exceeded.json
│   │   ├── 12b_model_refusal.json
│   │   ├── 12c_invalid_request.json
│   │   ├── 12d_rate_limit_errors.json       # Error type documentation
│   │   ├── 12e_streaming_error_events.json
│   │   ├── 13a_tool_choice.json
│   │   ├── 13b_reasoning_config.json
│   │   ├── 13c_verbosity.json
│   │   ├── 13d_max_tokens.json
│   │   ├── 13e_store.json                   # Documentation only (no equivalent)
│   │   ├── 13f_parallel_tool_calls.json
│   │   └── 13g_sampling_params.json
│   └── gemini/
│       └── (same structure)
```

---

## Part 3.6: Updated Key Questions (additions to Part 6)

Ngoài 21 câu hỏi ban đầu, sau khi chạy tests 10-13 cần trả lời thêm:

### Streaming + Reasoning Layer (mới)
22. Khi stream có thinking + text: events ordering thế nào? Thinking sequential trước text hay interleaved?
23. Khi stream có thinking + tool_use: model thinking trước tool call thế nào? Events ra sao?
24. Boundary event giữa thinking và text/tool_use: có event rõ ràng signal "thinking done" không?

### Error Handling Layer (mới)
25. Error class hierarchy mỗi provider: có thể normalize thành ~5 error types (rate_limit, auth, bad_request, timeout, server_error)?
26. Streaming error events: có map 1:1 được với 5 OpenAI error events không? Hay Anthropic/Gemini có ít hơn?
27. Refusal mechanism: model refusal nằm trong content text hay là event/field riêng?
28. Retryable errors: error nào nên auto-retry với backoff? Headers retry-after có đồng nhất không?

### Config Parameters Layer (mới)
29. tool_choice: có map 1:1 "auto"/"required"/"none"/specific giữa 3 providers không?
30. Reasoning config: mapping effort → budget_tokens → thinking_budget đã đủ chính xác chưa?
31. Verbosity: Anthropic/Gemini có native param không? Hay chỉ qua system prompt?
32. max_tokens: Anthropic required, Gemini optional, OpenAI auto — adapter cần default thế nào?
33. store: chỉ OpenAI có — adapter bỏ qua cho provider khác?
34. Sampling params (temperature, top_p, top_k, stop_sequences): inventory đầy đủ cho 3 providers?

---

## Part 4: Report Template

Sau khi chạy xong tất cả tests, tạo `REPORT.md` theo template:

```markdown
# Multi-LLM Provider Research Report

## Executive Summary
[1-2 paragraphs tóm tắt findings chính]

## Provider Comparison Matrix

### Input Format
| Aspect | OpenAI Response API | Anthropic Messages API | Gemini GenerativeAI |
|--------|--------------------|-----------------------|---------------------|
| SDK class | AsyncOpenAI | Anthropic / AsyncAnthropic | GenerativeModel |
| Main method | responses.create() | messages.create() | generate_content() |
| Messages param | input= | messages= | contents= |
| System prompt | In messages array | Separate system= param | Separate system_instruction= |
| Roles | user/assistant/system/developer | user/assistant | user/model |
| ... | ... | ... | ... |

### Output Format
[Same detailed comparison table]

### Tool Calling
[Detailed comparison table]

### Streaming Events
[Full event mapping table]

### Reasoning/Thinking
[Comparison table]

### Structured Output
[Comparison table]

### Vision
[Comparison table]

### Streaming + Reasoning Events (Phase 2)
[Event sequence mapping khi streaming có reasoning cho cả 3 providers]

### Streaming + Reasoning + Tool Call (Phase 2)
[Full event sequence cho complex scenario]

### Error Handling (Phase 2)
[Error type mapping, streaming error events, refusal mechanism]

### Config Parameters Mapping (Phase 2)
[Full mapping table cho tool_choice, reasoning config, verbosity, max_tokens, sampling params]

## Architecture Implications
[Based on findings, what does this mean for the architecture redesign]

## Conversion Complexity Assessment
[For each coupling point C1-C13, how hard is it to support all 3 providers]
```

---

## Part 5: Execution Instructions

### Prerequisites

```bash
# 1. Install google-generativeai SDK
cd /Users/apple/Desktop/el-ripley/be-ai-agent
poetry add google-generativeai

# 2. Verify anthropic SDK is installed
poetry run python -c "import anthropic; print(anthropic.__version__)"

# 3. Verify google SDK
poetry run python -c "import google.generativeai; print('OK')"

# 4. Verify API keys are loaded
poetry run python -c "
from dotenv import load_dotenv; import os; load_dotenv()
print('ANTHROPIC:', 'OK' if os.getenv('ANTHROPIC_API_KEY') else 'MISSING')
print('GOOGLE:', 'OK' if os.getenv('GOOGLE_API_KEY') else 'MISSING')
"
```

### Run Order

```bash
# Run each test sequentially (each saves evidence JSON):

# === Phase 1: Core behavior (đã test xong) ===
poetry run python tests/llm_providers/test_01_basic_completion.py
poetry run python tests/llm_providers/test_02_streaming.py
poetry run python tests/llm_providers/test_03_single_tool_call.py
poetry run python tests/llm_providers/test_04_parallel_tool_calls.py
poetry run python tests/llm_providers/test_05_tool_call_loop.py
poetry run python tests/llm_providers/test_06_structured_output.py
poetry run python tests/llm_providers/test_07_vision.py
poetry run python tests/llm_providers/test_08_reasoning.py
poetry run python tests/llm_providers/test_09_streaming_tool_call.py

# === Phase 2: Complex scenarios + Error handling + Config params ===
poetry run python tests/llm_providers/test_10_streaming_reasoning.py
poetry run python tests/llm_providers/test_11_streaming_reasoning_tool.py
poetry run python tests/llm_providers/test_12_error_handling.py
poetry run python tests/llm_providers/test_13_config_params.py
```

### Evidence Verification Checklist

Sau khi chạy xong mỗi test, verify:
- [ ] JSON evidence file được tạo trong `tests/llm_providers/evidence/{provider}/`
- [ ] Evidence chứa full request params
- [ ] Evidence chứa full response (raw JSON)
- [ ] Evidence chứa `key_observations` section
- [ ] Evidence chứa `mapping_to_current_system` section

**Phase 2 additional checks:**
- [ ] Test 10: event sequence cho streaming+reasoning đủ chi tiết (thinking start/delta/stop events)
- [ ] Test 11: event sequence cho cả 2 turns (thinking+tool_use turn 1, thinking+text turn 2)
- [ ] Test 12: mỗi error sub-test (12a-12e) có evidence riêng; error type hierarchy được document
- [ ] Test 13: mapping table cho mỗi config param sub-test (13a-13g) khớp với production params

### Models to Test

**Anthropic:**
- Primary: `claude-sonnet-4-20250514` (hoặc latest sonnet)
- Secondary: `claude-haiku-3-5-20241022` (fast/cheap model for comparison)

**Gemini:**
- Primary: `gemini-2.0-flash` (hoặc latest flash)
- Secondary: `gemini-2.5-pro` (if available, for reasoning test)

**Note:** Nếu model name bị outdated, check latest available models trước khi test.

---

## Part 6: Key Questions the Tests Must Answer

Sau khi collect evidence, REPORT phải trả lời được các câu hỏi sau:

### Input Layer
1. Có thể dùng chung 1 message format cho cả 3 providers không? Hay mỗi provider cần native format riêng?
2. System prompt cần tách riêng hay để trong messages? Ảnh hưởng gì đến ContextBuilder?
3. Image format khác nhau thế nào? ImageProcessor cần thay đổi gì?
4. Content block types (`input_text`, `input_image`, etc.) có equivalent ở mỗi provider không?

### Tool Calling Layer
5. Tool call ID mechanism khác nhau thế nào? (call_id vs id vs no-id)
6. Arguments là JSON string hay dict? ToolExecutor cần parse khác nhau không?
7. Tool result format khác nhau thế nào? Cần convert gì?
8. Parallel tool calls: tất cả providers đều support?
9. Tool definition schema: khác nhau ở đâu? Convert 1 lần khi init có đủ không?

### Streaming Layer
10. Có thể define 1 set normalized events (~5-7 types) cover được cả 3 providers không?
11. Event nào là HOT PATH (most frequent) cho mỗi provider?
12. Final response: cách lấy complete response sau stream có giống nhau không?
13. Error handling: error events format thế nào?

### Response Layer
14. Response dict structure: `output[]` vs `content[]` vs `candidates[].content.parts[]` — normalize thế nào?
15. Stop reason / finish reason: giá trị khác nhau thế nào?
16. Usage/token format: có thể normalize dễ dàng không?

### Reasoning Layer
17. Thinking/reasoning: format output khác nhau thế nào?
18. Streaming thinking: có stream delta không?
19. Thinking tokens: counted riêng không?

### Structured Output Layer
20. Native structured output support: mỗi provider hỗ trợ thế nào?
21. Pydantic integration: có equivalent responses.parse() không?
