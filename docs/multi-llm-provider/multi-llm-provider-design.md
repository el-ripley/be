# Multi-LLM Provider Architecture Design

## Mục tiêu

Thiết kế lại kiến trúc LLM layer để hỗ trợ 3 providers (OpenAI, Anthropic, Gemini) mà:
1. **Không thay đổi business logic** — IterationRunner, ToolExecutor, ContextBuilder, AgentRunner giữ nguyên flow
2. **Swap provider dễ dàng** — Chỉ cần đổi config, không sửa code
3. **Provider-specific features vẫn hoạt động** — Web search (OpenAI), thinking budget (Anthropic), etc.
4. **Production-ready** — Streaming, error handling, billing tracking đều hoạt động đúng

---

## Tổng quan kiến trúc

### Hiện tại (tightly coupled to OpenAI)

```
AgentRunner → IterationRunner → LLMStreamHandler → LLM_call → AsyncOpenAI
     │                                                              │
     ├── ContextBuilder → MessageConverter → OpenAI format          │
     ├── ToolExecutor ← response_dict["output"] (OpenAI format)    │
     └── ResponseAnalyzer ← response_dict["output"]                │
                                                                    │
SummarizerService → LLM_call.parse() ─────────────────────────────→│
MediaDescriptionService → LLM_call.create() ──────────────────────→│
SuggestResponseRunner → LLM_call.stream() ────────────────────────→│
SubAgentRunner → LLM_call ────────────────────────────────────────→│
```

### Thiết kế mới (provider-agnostic)

```
AgentRunner → IterationRunner → LLMStreamHandler → LLMProvider (interface)
     │                               │                    │
     │                    Normalized Events          ┌────┴─────┐──────────┐
     │                               │               │          │          │
     ├── ContextBuilder              │          OpenAI     Anthropic    Gemini
     │     └─→ Canonical format      │          Provider   Provider    Provider
     │         (provider-agnostic)   │               │          │          │
     ├── ToolExecutor                │          ┌────┴──────────┴──────────┘
     │     └─← Canonical response    │          │
     └── ResponseAnalyzer            │     Each provider implements:
           └─← Canonical response    │     - convert_messages(canonical → native)
                                     │     - convert_tools(canonical → native)
                                     │     - stream() → yields NormalizedEvent
                                     │     - create() → returns CanonicalResponse
                                     │     - parse() → returns structured output
                                     │     - convert_response(native → canonical)
```

### Thiết kế core: Adapter Pattern + Normalized Events

**Tại sao chọn Adapter Pattern thay vì Abstract Factory hay Strategy:**
- Business logic layer (IterationRunner, ToolExecutor, etc.) đã hoạt động tốt — KHÔNG muốn sửa
- Chỉ cần "dịch" giữa canonical format ↔ provider-specific format
- Mỗi provider có SDK riêng, API riêng — adapter wrap SDK cụ thể

---

## Layer 1: Canonical Data Formats

### 1.1 Canonical Message Format

Đây là format duy nhất mà business logic layer sử dụng. MessageConverter hiện đang output OpenAI format — sẽ giữ OpenAI format làm canonical format vì:
- Code hiện tại đã dùng format này everywhere
- OpenAI Response API format đã khá structured và expressive
- Ít code phải sửa nhất

```python
# === Canonical Message Types (giữ nguyên từ message_converter.py) ===

# 1. Chat message
{"role": "user" | "assistant" | "system" | "developer", "content": Any}
# content có thể là string hoặc list of content blocks:
# [{"type": "input_text", "text": "..."}, {"type": "input_image", "image_url": "..."}]

# 2. Reasoning
{"type": "reasoning", "summary": [{"text": "...", "type": "summary_text"}]}

# 3. Function call
{"type": "function_call", "call_id": "call_xxx", "name": "tool_name", "arguments": '{"key":"val"}'}
# NOTE: arguments luôn là JSON string (canonical)

# 4. Function call output
{"type": "function_call_output", "call_id": "call_xxx", "output": "string" | [{"type": "input_text", "text": "..."}]}

# 5. Web search call (OpenAI-only, các provider khác bỏ qua)
{"type": "web_search_call", "action": {...}}
```

**Quyết định quan trọng:** Giữ nguyên OpenAI format làm canonical. Adapter's job = convert canonical ↔ native.

### 1.2 Canonical Response Format

Response dict từ LLM sẽ luôn có format này (dù provider nào):

```python
CanonicalResponse = {
    "id": "resp_xxx",                    # Provider-specific response ID
    "created": 1234567890,               # Timestamp
    "status": "completed" | "failed",    # Normalized status
    "output": [                          # Canonical output items
        # Text message
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hello..."}],
        },
        # Reasoning
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [{"text": "thinking...", "type": "summary_text"}],
        },
        # Function call
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_xxx",       # Unique identifier
            "name": "get_weather",
            "arguments": '{"city":"Hanoi"}',  # Always JSON string
        },
        # Web search (OpenAI only)
        {
            "type": "web_search_call",
            "id": "ws_1",
            "action": {"query": "...", "type": "search"},
        },
    ],
    "usage": {
        "input_tokens": 1500,
        "output_tokens": 300,
        "total_tokens": 1800,
    },
}
```

**Mapping từ mỗi provider:**

| Field | OpenAI | Anthropic | Gemini |
|-------|--------|-----------|--------|
| `output[]` | Giữ nguyên | Map từ `content[]` | Map từ `candidates[0].content.parts[]` |
| `output[].type == "function_call"` | Giữ nguyên | Map từ `tool_use` | Map từ `function_call` part |
| `call_id` | `call_id` | `id` (e.g. toolu_xxx) | **Generate UUID** (Gemini không có call_id) |
| `arguments` | Giữ nguyên (JSON string) | `json.dumps(input)` (input là dict) | `json.dumps(args)` (args là dict) |
| `status` | Giữ nguyên | Map từ `stop_reason` | Map từ `finish_reason` |
| `usage.input_tokens` | Giữ nguyên | Giữ nguyên | Map từ `usage_metadata.prompt_token_count` |
| `usage.output_tokens` | Giữ nguyên | Giữ nguyên | Map từ `usage_metadata.candidates_token_count` |

### 1.3 Canonical Tool Definition

```python
# Canonical = OpenAI format (giữ nguyên BaseTool.definition output)
{
    "type": "function",
    "name": "get_weather",
    "description": "Get weather info",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    "strict": True,  # Optional
}
```

**Adapter converts to native:**

| Canonical | Anthropic | Gemini |
|-----------|-----------|--------|
| `"type": "function"` | Bỏ (Anthropic không wrap) | Bỏ (dùng `FunctionDeclaration`) |
| `"parameters"` | → `"input_schema"` | → `"parameters"` (giữ nguyên) |
| `"strict": True` | Bỏ (không support) | Bỏ (không support) |

### 1.4 Normalized Stream Events

Hiện tại LLMStreamHandler handle 15+ OpenAI-specific event types. Thiết kế normalized event set:

```python
from dataclasses import dataclass
from typing import Any, Dict, Optional, Literal

@dataclass
class NormalizedStreamEvent:
    """Provider-agnostic stream event."""
    type: str                         # Event type (see below)
    item_id: Optional[str] = None     # ID of the output item
    delta: Optional[str] = None       # Text delta (for delta events)
    data: Optional[Dict[str, Any]] = None  # Extra data (event-specific)

# === Event Types ===
# Tên event giữ y hệt OpenAI format (vì LLMStreamHandler đã handle chúng)
# Adapter's job = map native events → normalized events cùng type

NORMALIZED_EVENT_TYPES = {
    # === Lifecycle ===
    "response.created",                        # Stream started
    
    # === Output Items ===
    "response.output_item.added",              # New item started (message/reasoning/function_call)
    #   data.item = {"id": "...", "type": "message"|"reasoning"|"function_call"}
    
    "response.output_item.done",               # Item completed
    #   data.item = full item dict (same as canonical output item)
    
    # === Reasoning ===
    "response.reasoning_summary_part.added",   # Reasoning part started
    #   data.summary_index = 0
    
    "response.reasoning_summary_text.delta",   # Reasoning text delta (HOT PATH)
    #   delta = "thinking text..."
    #   data.summary_index = 0
    
    "response.reasoning_summary_part.done",    # Reasoning part done
    
    # === Content ===
    "response.content_part.added",             # Content part started
    
    "response.output_text.delta",              # Text content delta (HOT PATH - most frequent)
    #   delta = "response text..."
    
    "response.content_part.done",              # Content part done
    
    # === Web Search (OpenAI only, others skip) ===
    "response.web_search_call.in_progress",
    "response.web_search_call.searching",
    "response.web_search_call.completed",
    
    # === Errors ===
    "response.failed",                         # Fatal error
    #   data = {"code": "...", "message": "..."}
    
    "response.incomplete",                     # Max tokens reached
    #   data = {"reason": "max_tokens", "response": {...}}
    
    "response.refusal.delta",                  # Refusal text delta
    "response.refusal.done",                   # Refusal complete
    
    "error",                                   # Stream-level error
    #   data = {"code": "...", "message": "..."}
    
    # === Final Response ===
    "response.completed",                      # Final canonical response
    #   data.response = CanonicalResponse dict
}
```

**Tại sao giữ nguyên OpenAI event names:**
- `LLMStreamHandler` đã handle chính xác các event types này → **KHÔNG cần sửa stream handler**
- Adapter chỉ cần map native events → normalized events với đúng type names
- Giảm risk, giảm code changes

**Event mapping từ mỗi provider:**

| Normalized Event | Anthropic Native | Gemini Native |
|---|---|---|
| `response.created` | `message_start` | First chunk received |
| `response.output_item.added` | `content_block_start` | Detect from chunk parts |
| `response.reasoning_summary_text.delta` | `content_block_delta` (thinking_delta) | Chunk part with `thought=true` |
| `response.output_text.delta` | `content_block_delta` (text_delta) | Chunk part text (thought=false) |
| `response.output_item.done` | `content_block_stop` + reconstruct item | Last chunk for item |
| `response.failed` | Exception caught | Exception caught |
| `response.incomplete` | `message_delta` with `stop_reason=max_tokens` | Chunk with `finish_reason=MAX_TOKENS` |
| `response.completed` | `message_stop` + `get_final_message()` | Last chunk + accumulate |

---

## Layer 2: LLM Provider Interface

### 2.1 Abstract Interface

```python
# src/agent/core/llm_provider.py

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional, Type, Union
from pydantic import BaseModel

from src.agent.core.types import NormalizedStreamEvent, CanonicalResponse


class LLMProvider(ABC):
    """Abstract interface for LLM providers."""
    
    @abstractmethod
    async def create(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str | Dict[str, Any]] = None,
        parallel_tool_calls: bool = True,
        reasoning: Optional[Dict[str, Any]] = None,
        text: Optional[Dict[str, Any]] = None,
        store: bool = True,
        **kwargs,
    ) -> CanonicalResponse:
        """Non-streaming completion. Returns canonical response dict."""
        ...
    
    @abstractmethod
    async def stream(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str | Dict[str, Any]] = None,
        parallel_tool_calls: bool = True,
        reasoning: Optional[Dict[str, Any]] = None,
        text: Optional[Dict[str, Any]] = None,
        store: bool = True,
        **kwargs,
    ) -> AsyncGenerator[NormalizedStreamEvent, None]:
        """Streaming completion. Yields normalized events.
        
        QUAN TRỌNG: Event cuối cùng phải là type "response.completed"
        với data.response = CanonicalResponse dict.
        """
        ...
    
    @abstractmethod
    async def parse(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        text_format: Union[Type[BaseModel], Dict[str, Any], None] = None,
        reasoning: Optional[Dict[str, Any]] = None,
        return_full_response: bool = False,
        **kwargs,
    ) -> Any:
        """Structured output parsing."""
        ...
```

**Signature giữ canonical param names:**
- `messages` (canonical) → adapter chuyển thành `input` (OpenAI) / `messages` (Anthropic) / `contents` (Gemini)
- `tools` (canonical, OpenAI format) → adapter chuyển thành native tool format
- `tool_choice` (canonical) → adapter chuyển thành native format
- `reasoning` (canonical, OpenAI format) → adapter chuyển thành `thinking` (Anthropic) / `thinking_config` (Gemini)
- `text` (canonical) → adapter handle verbosity per provider

### 2.2 Parameter Mapping Table (Reference cho adapter implementation)

| Canonical Param | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| `messages` | `input=messages` | `system=extract_system(messages)`, `messages=convert_messages(messages)` | `contents=convert_to_contents(messages)`, `system_instruction=extract_system(messages)` |
| `tools` | Giữ nguyên | Convert: `parameters` → `input_schema`, bỏ `type`, bỏ `strict` | Convert to `FunctionDeclaration` objects |
| `tool_choice="auto"` | `"auto"` | `{"type": "auto"}` | `ToolConfig(mode="AUTO")` |
| `tool_choice="required"` | `"required"` | `{"type": "any"}` | `ToolConfig(mode="ANY")` |
| `tool_choice="none"` | `"none"` | Omit tools | `ToolConfig(mode="NONE")` |
| `tool_choice={"type":"function","name":"X"}` | Giữ nguyên | `{"type": "tool", "name": "X"}` | `ToolConfig(mode="ANY", allowed_function_names=["X"])` |
| `reasoning={"effort":"low","summary":"auto"}` | Giữ nguyên | `thinking={"type":"enabled","budget_tokens":2000}` | `ThinkingConfig(include_thoughts=True, thinking_budget=1024)` |
| `reasoning={"effort":"medium","summary":"auto"}` | Giữ nguyên | `thinking={"type":"enabled","budget_tokens":8000}` | `ThinkingConfig(include_thoughts=True)` (default budget) |
| `reasoning={"effort":"high","summary":"auto"}` | Giữ nguyên | `thinking={"type":"enabled","budget_tokens":16000}` | `ThinkingConfig(include_thoughts=True, thinking_budget=16384)` |
| `reasoning=None` | Omit | Omit thinking (hoặc `{"type":"disabled"}`) | Omit thinking_config |
| `text={"verbosity":"low"}` | Giữ nguyên | System prompt: "Be concise." | System instruction append |
| `text={"verbosity":"high"}` | Giữ nguyên | System prompt: "Be thorough." | System instruction append |
| `parallel_tool_calls` | Giữ nguyên | Bỏ qua (không có param) | Bỏ qua (không có param) |
| `store` | Giữ nguyên | Bỏ qua (không support) | Bỏ qua (không support) |

### 2.3 Reasoning Effort → Budget Mapping

```python
# Anthropic: effort → budget_tokens mapping
ANTHROPIC_THINKING_BUDGET = {
    "low": 2000,
    "medium": 8000,
    "high": 16000,
}

# Gemini: effort → thinking_budget mapping  
GEMINI_THINKING_BUDGET = {
    "low": 1024,
    "medium": 8192,   # hoặc None (let model decide)
    "high": 16384,
}
```

### 2.4 Verbosity Handling (Non-OpenAI Providers)

Anthropic và Gemini không có native `text.verbosity` param. Adapter inject verbosity directive vào system prompt:

```python
VERBOSITY_SYSTEM_PROMPTS = {
    "low": "\n\n[Response style: Be concise. Use short, direct answers.]",
    "medium": "",  # Default — no injection needed
    "high": "\n\n[Response style: Be thorough and detailed in your response.]",
}
```

---

## Layer 3: Provider Implementations

### 3.1 OpenAI Provider

```python
# src/agent/core/providers/openai_provider.py

class OpenAIProvider(LLMProvider):
    """OpenAI Response API provider.
    
    Simplest adapter — canonical format IS OpenAI format.
    Hầu hết params pass-through trực tiếp.
    """
    
    def __init__(self, api_key: str, timeout: Timeout = DEFAULT_TIMEOUT):
        self.client = AsyncOpenAI(api_key=api_key, timeout=timeout)
    
    async def create(self, model, messages, tools, ...):
        # messages → input (rename only)
        # tools → pass-through
        # Everything else → pass-through
        response = await self.client.responses.create(
            model=model, input=messages, tools=tools, ...
        )
        return response.model_dump(mode="json")  # Already canonical
    
    async def stream(self, model, messages, tools, ...):
        async with self.client.responses.stream(
            model=model, input=messages, tools=tools, ...
        ) as stream:
            async for event in stream:
                yield self._to_normalized_event(event)
            
            final = await stream.get_final_response()
            yield NormalizedStreamEvent(
                type="response.completed",
                data={"response": final.model_dump(mode="json")}
            )
    
    def _to_normalized_event(self, event) -> NormalizedStreamEvent:
        # OpenAI events → Normalized events (mostly pass-through)
        # Vì canonical event names = OpenAI event names
        event_type = getattr(event, "type", "")
        return NormalizedStreamEvent(
            type=event_type,
            item_id=getattr(event, "item_id", None),
            delta=getattr(event, "delta", None),
            data=self._extract_event_data(event, event_type),
        )
```

### 3.2 Anthropic Provider

```python
# src/agent/core/providers/anthropic_provider.py

class AnthropicProvider(LLMProvider):
    """Anthropic Messages API provider.
    
    Key conversions:
    - messages: extract system, convert content blocks
    - tools: parameters → input_schema
    - response: content[] → output[]
    - streaming: Anthropic events → Normalized events
    - reasoning: effort → thinking budget_tokens
    - arguments: dict → JSON string
    - call_id: id (toolu_xxx) → call_id
    """
    
    def __init__(self, api_key: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
    
    # === Message Conversion ===
    
    def _convert_messages(self, canonical_messages):
        """Convert canonical messages → Anthropic format.
        
        Key differences:
        - Extract system messages → separate system param
        - reasoning items → thinking blocks in assistant content
        - function_call → assistant message with tool_use blocks  
        - function_call_output → user message with tool_result blocks
        - input_text/input_image → text/image content blocks
        - web_search_call → skip (OpenAI-only)
        """
        system_parts = []
        messages = []
        
        for msg in canonical_messages:
            msg_type = msg.get("type")
            
            if msg_type is None:  # Standard chat message
                role = msg.get("role")
                if role == "system" or role == "developer":
                    system_parts.append(self._extract_text(msg["content"]))
                    continue
                messages.append(self._convert_chat_message(msg))
                
            elif msg_type == "reasoning":
                # Anthropic: thinking blocks go into assistant message content
                # Merge with adjacent assistant message or create new one
                self._append_thinking_block(messages, msg)
                
            elif msg_type == "function_call":
                # Anthropic: tool_use goes into assistant message content
                self._append_tool_use_block(messages, msg)
                
            elif msg_type == "function_call_output":
                # Anthropic: tool_result goes into USER message
                self._append_tool_result(messages, msg)
                
            elif msg_type == "web_search_call":
                pass  # Skip — OpenAI-only feature
        
        return "\n".join(system_parts), messages
    
    def _convert_chat_message(self, msg):
        """Convert content blocks: input_text→text, input_image→image."""
        content = msg["content"]
        if isinstance(content, str):
            return {"role": msg["role"], "content": content}
        
        # Convert content block types
        converted = []
        for block in content:
            if block["type"] == "input_text":
                converted.append({"type": "text", "text": block["text"]})
            elif block["type"] == "input_image":
                converted.append({
                    "type": "image",
                    "source": {"type": "url", "url": block["image_url"]}
                })
            elif block["type"] == "output_text":
                converted.append({"type": "text", "text": block["text"]})
        
        return {"role": msg["role"], "content": converted}
    
    def _append_tool_use_block(self, messages, function_call_msg):
        """Anthropic: tool_use blocks go in assistant message content."""
        tool_use_block = {
            "type": "tool_use",
            "id": function_call_msg["call_id"],      # call_id → id
            "name": function_call_msg["name"],
            "input": json.loads(function_call_msg["arguments"]),  # JSON string → dict
        }
        # Merge into last assistant message or create new
        if messages and messages[-1]["role"] == "assistant":
            if isinstance(messages[-1]["content"], list):
                messages[-1]["content"].append(tool_use_block)
            else:
                messages[-1]["content"] = [
                    {"type": "text", "text": messages[-1]["content"]},
                    tool_use_block,
                ]
        else:
            messages.append({"role": "assistant", "content": [tool_use_block]})
    
    def _append_tool_result(self, messages, output_msg):
        """Anthropic: tool_result goes in user message."""
        tool_result_block = {
            "type": "tool_result",
            "tool_use_id": output_msg["call_id"],     # call_id → tool_use_id
            "content": self._extract_output_text(output_msg["output"]),
        }
        # Merge into user message or create new
        if messages and messages[-1]["role"] == "user":
            if isinstance(messages[-1]["content"], list):
                messages[-1]["content"].append(tool_result_block)
            else:
                messages[-1]["content"] = [
                    {"type": "text", "text": messages[-1]["content"]},
                    tool_result_block,
                ]
        else:
            messages.append({"role": "user", "content": [tool_result_block]})
    
    # === Tool Conversion ===
    
    def _convert_tools(self, canonical_tools):
        """Convert canonical tools → Anthropic format."""
        anthropic_tools = []
        for tool in canonical_tools:
            if tool.get("type") == "web_search":
                continue  # Skip web_search (OpenAI-only)
            if tool.get("type") == "function":
                anthropic_tools.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool["parameters"],  # parameters → input_schema
                })
        return anthropic_tools
    
    # === Response Conversion ===
    
    def _to_canonical_response(self, response) -> dict:
        """Convert Anthropic response → canonical format."""
        output = []
        for block in response.content:
            if block.type == "thinking":
                output.append({
                    "type": "reasoning",
                    "id": f"rs_{block.id if hasattr(block, 'id') else 'auto'}",
                    "summary": [{"text": block.thinking, "type": "summary_text"}],
                })
            elif block.type == "text":
                output.append({
                    "type": "message",
                    "id": response.id,
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": block.text}],
                })
            elif block.type == "tool_use":
                output.append({
                    "type": "function_call",
                    "id": f"fc_{block.id}",
                    "call_id": block.id,                           # id → call_id
                    "name": block.name,
                    "arguments": json.dumps(block.input),          # dict → JSON string
                })
        
        return {
            "id": response.id,
            "created": int(time.time() * 1000),
            "status": "completed" if response.stop_reason != "max_tokens" else "incomplete",
            "output": output,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            },
        }
    
    # === Streaming ===
    
    async def stream(self, model, messages, tools, ...):
        """Stream Anthropic events → Normalized events."""
        system_prompt, converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(tools) if tools else None
        thinking_param = self._convert_reasoning(reasoning)
        
        params = {
            "model": model,
            "max_tokens": self._get_max_tokens(model, reasoning),
            "system": system_prompt,
            "messages": converted_messages,
        }
        if converted_tools:
            params["tools"] = converted_tools
            params["tool_choice"] = self._convert_tool_choice(tool_choice)
        if thinking_param:
            params["thinking"] = thinking_param
        
        # State tracking for stream
        current_block_type = None  # "thinking" | "text" | "tool_use"
        current_item_id = None
        tool_input_json = ""
        
        async with self.client.messages.stream(**params) as stream:
            for event in stream:
                for normalized in self._map_anthropic_event(event, state):
                    yield normalized
            
            final_message = stream.get_final_message()
            canonical = self._to_canonical_response(final_message)
            yield NormalizedStreamEvent(
                type="response.completed",
                data={"response": canonical},
            )
    
    def _map_anthropic_event(self, event, state) -> list[NormalizedStreamEvent]:
        """Map single Anthropic event → 0 or more normalized events.
        
        Anthropic event flow:
            message_start → [content_block_start → content_block_delta* → content_block_stop]* → message_delta → message_stop
        
        Mapping:
            message_start → response.created
            content_block_start(thinking) → response.output_item.added(reasoning) + response.reasoning_summary_part.added
            content_block_delta(thinking_delta) → response.reasoning_summary_text.delta
            content_block_stop(thinking) → response.reasoning_summary_part.done + response.output_item.done(reasoning)
            content_block_start(text) → response.output_item.added(message) + response.content_part.added
            content_block_delta(text_delta) → response.output_text.delta
            content_block_stop(text) → response.content_part.done + response.output_item.done(message)
            content_block_start(tool_use) → (buffer, wait for stop)
            content_block_delta(input_json_delta) → (buffer arguments)
            content_block_stop(tool_use) → response.output_item.added(function_call) + response.output_item.done(function_call)
            message_delta(stop_reason=max_tokens) → response.incomplete
        """
        # (Implementation pseudocode — actual mapping logic)
        ...
```

### 3.3 Gemini Provider

```python
# src/agent/core/providers/gemini_provider.py

class GeminiProvider(LLMProvider):
    """Google Gemini provider via google.genai SDK.
    
    Key conversions:
    - messages: convert to Content/Part objects, system → system_instruction
    - tools: convert to FunctionDeclaration objects
    - response: candidates[0].content.parts[] → output[]
    - streaming: chunks → Normalized events (requires state tracking)
    - reasoning: effort → ThinkingConfig
    - call_id: Generate UUID (Gemini has no call_id)
    - arguments: dict → JSON string
    """
    
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self._call_id_map = {}  # name → generated call_id (for Gemini's no-ID tool calls)
    
    # === Message Conversion ===
    
    def _convert_messages(self, canonical_messages):
        """Convert canonical → Gemini Content objects.
        
        Key differences:
        - system → system_instruction (separate)
        - role "assistant" → "model"
        - function_call → Part with function_call
        - function_call_output → Part with function_response (match by NAME, not ID)
        - reasoning → skip (Gemini handles thinking internally)
        - web_search_call → skip
        - content blocks: input_text → text Part, input_image → inline_data Part
        """
        system_instruction = None
        contents = []
        
        for msg in canonical_messages:
            # ... conversion logic
            pass
        
        return system_instruction, contents
    
    # === Tool Call ID Management ===
    
    def _generate_call_id(self, function_name: str) -> str:
        """Generate stable call_id for Gemini (which has no native call_id).
        
        Uses: f"gemini_call_{uuid4().hex[:12]}"
        Stored in _call_id_map so tool_result can reference it.
        """
        call_id = f"gemini_call_{uuid.uuid4().hex[:12]}"
        return call_id
    
    # === Streaming ===
    
    async def stream(self, model, messages, tools, ...):
        """Stream Gemini chunks → Normalized events.
        
        Gemini streaming is simpler — chunks contain parts directly.
        Need to track state to emit proper normalized events.
        
        State machine:
        - First chunk → response.created
        - Chunk with thought=True part → reasoning events
        - Chunk with text part (thought=False) → content events
        - Chunk with function_call part → function_call events
        - Last chunk (finish_reason set) → response.completed
        """
        ...
```

---

## Layer 4: Provider Factory & Configuration

### 4.1 Provider Registry

```python
# src/agent/core/provider_factory.py

from enum import Enum
from typing import Optional
from src.agent.core.llm_provider import LLMProvider
from src.agent.core.providers.openai_provider import OpenAIProvider
from src.agent.core.providers.anthropic_provider import AnthropicProvider
from src.agent.core.providers.gemini_provider import GeminiProvider


class ProviderType(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


# Model → Provider mapping
MODEL_PROVIDER_MAP = {
    # OpenAI models
    "gpt-5-mini": ProviderType.OPENAI,
    "gpt-5-nano": ProviderType.OPENAI,
    "gpt-5": ProviderType.OPENAI,
    "gpt-5.2": ProviderType.OPENAI,
    
    # Anthropic models
    "claude-sonnet-4-5": ProviderType.ANTHROPIC,
    "claude-sonnet-4-20250514": ProviderType.ANTHROPIC,
    "claude-haiku-3-5": ProviderType.ANTHROPIC,
    
    # Gemini models
    "gemini-2.5-pro": ProviderType.GEMINI,
    "gemini-2.0-flash": ProviderType.GEMINI,
}


def create_provider(
    model: str,
    openai_api_key: Optional[str] = None,
    anthropic_api_key: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
) -> LLMProvider:
    """Create appropriate provider based on model name."""
    provider_type = MODEL_PROVIDER_MAP.get(model)
    
    if provider_type is None:
        # Default to OpenAI for unknown models
        provider_type = ProviderType.OPENAI
    
    if provider_type == ProviderType.OPENAI:
        return OpenAIProvider(api_key=openai_api_key)
    elif provider_type == ProviderType.ANTHROPIC:
        return AnthropicProvider(api_key=anthropic_api_key)
    elif provider_type == ProviderType.GEMINI:
        return GeminiProvider(api_key=gemini_api_key)
    
    raise ValueError(f"Unknown provider type: {provider_type}")
```

### 4.2 Integration với RunConfig

```python
# Thay đổi trong run_config.py

@dataclass
class RunConfig:
    user_id: str
    conversation_id: str
    api_key: str  # Giữ cho backward compat
    settings: Dict[str, Any]
    model: str
    llm_provider: LLMProvider  # ← THAY llm_call: LLM_call
    active_tab: Optional[Dict[str, Any]] = None
    context_token_limit: int = DEFAULT_CONTEXT_TOKEN_LIMIT
    context_buffer_percent: int = DEFAULT_CONTEXT_BUFFER_PERCENT
    current_context_tokens: int = 0
```

### 4.3 Integration với AgentRunner

```python
# Thay đổi trong agent_runner.py → _prepare_run_config()

async def _prepare_run_config(self, user_id, conversation_id, active_tab):
    # ... existing settings resolution code ...
    
    model = settings.get("model", self.default_model)
    
    # NEW: Create provider based on model
    llm_provider = create_provider(
        model=model,
        openai_api_key=get_system_api_key(),
        anthropic_api_key=get_anthropic_api_key(),
        gemini_api_key=get_gemini_api_key(),
    )
    
    return RunConfig(
        user_id=user_id,
        conversation_id=conversation_id,
        api_key=api_key,
        settings=settings,
        model=model,
        llm_provider=llm_provider,  # ← THAY llm_call
        ...
    )
```

---

## Layer 5: Integration Points (Minimal Changes)

### 5.1 LLMStreamHandler — Thay đổi nhỏ nhất

Hiện tại `LLMStreamHandler.stream()` gọi `run_config.llm_call.stream()` rồi switch trên `event.type`.

**Thay đổi:**
1. Gọi `run_config.llm_provider.stream()` thay vì `run_config.llm_call.stream()`
2. Nhận `NormalizedStreamEvent` thay vì OpenAI-specific events
3. Switch logic **giữ y nguyên** vì normalized event types = OpenAI event types

```python
# BEFORE (hiện tại):
async for event in run_config.llm_call.stream(**stream_params):
    if hasattr(event, "type"):
        event_type = getattr(event, "type", "")
        if event_type == "response.output_text.delta":
            ...
    if isinstance(event, ParsedResponse):
        final_response = event

# AFTER (mới):
async for event in run_config.llm_provider.stream(**stream_params):
    event_type = event.type
    
    if event_type == "response.output_text.delta":
        # Normalized event → truy cập trực tiếp event.delta, event.item_id
        await self._handle_output_text_delta(
            ..., {"item_id": event.item_id, "delta": event.delta}, ...
        )
    elif event_type == "response.completed":
        final_response = event.data["response"]  # CanonicalResponse dict
    # ... rest giữ nguyên ...
```

**Lợi ích:**
- 90% code trong LLMStreamHandler giữ nguyên
- Chỉ thay đổi cách tạo `event_dict` cho mỗi handler method
- Không cần `event.model_dump()` nữa (NormalizedStreamEvent đã có sẵn fields)

### 5.2 ResponseAnalyzer — Không thay đổi

```python
# response_dict luôn có canonical format:
# output[].type == "function_call" → is_final = False
# Giữ nguyên 100%
```

### 5.3 ToolExecutor — Không thay đổi

```python
# response_dict.output[] luôn có:
# - type: "function_call"
# - call_id: string (Gemini adapter generate fake call_id)
# - name: string
# - arguments: JSON string (adapters convert dict → string)
# Giữ nguyên 100%
```

### 5.4 MessageConverter — Không thay đổi

```python
# MessageConverter output canonical format (hiện tại = OpenAI format)
# Provider adapters convert canonical → native khi gọi API
# Giữ nguyên 100%
```

### 5.5 ContextBuilder — Không thay đổi

```python
# ContextBuilder → MessageConverter → canonical format
# Provider adapter convert canonical messages khi gọi API
# Giữ nguyên 100%
```

### 5.6 SummarizerService — Thay đổi nhỏ

```python
# BEFORE:
self.llm_call = LLM_call(api_key=api_key)
result = await self.llm_call.parse(model=model, input=messages, text_format=Schema)

# AFTER:
self.llm_provider = create_provider(model=model, ...)
result = await self.llm_provider.parse(model=model, messages=messages, text_format=Schema)
```

### 5.7 MediaDescriptionService — Thay đổi nhỏ

```python
# BEFORE:
self.llm_call = LLM_call(api_key=api_key)
result = await self.llm_call.create(model=model, input=messages)

# AFTER:
self.llm_provider = create_provider(model=model, ...)
result = await self.llm_provider.create(model=model, messages=messages)
```

### 5.8 SuggestResponseStreamHandler — Thay đổi tương tự LLMStreamHandler

Cùng pattern: nhận NormalizedStreamEvent thay vì OpenAI events.

### 5.9 SubAgentRunner — Thay đổi nhỏ

Tạo `LLMProvider` thay vì `LLM_call`.

---

## Layer 6: Model Configuration Update

### 6.1 conversation_settings.py

```python
# Mở rộng SUPPORTED_MODELS
SUPPORTED_MODELS = [
    # OpenAI
    "gpt-5-mini", "gpt-5-nano", "gpt-5", "gpt-5.2",
    # Anthropic
    "claude-sonnet-4-5", "claude-haiku-3-5",
    # Gemini  
    "gemini-2.5-pro", "gemini-2.0-flash",
]

# get_reasoning_param() vẫn return canonical format
# Provider adapter convert sang native format
```

### 6.2 Web Search Handling

Web search là OpenAI-only built-in tool. Cho Anthropic/Gemini:
- **Phase 1:** Bỏ qua web_search tool khi dùng non-OpenAI providers
- **Phase 2 (future):** Implement custom web search tool wrap API (nếu cần)

```python
# Trong LLMStreamHandler._build_tools_with_web_search():
if settings.get("web_search_enabled", True):
    if isinstance(run_config.llm_provider, OpenAIProvider):
        tools.append({"type": "web_search"})
    # Non-OpenAI: skip web_search (hoặc add custom tool)
```

---

## Error Handling Strategy

### Normalized Error Types

```python
class LLMError(Exception):
    """Base error for all LLM provider errors."""
    def __init__(self, code: str, message: str, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable

class LLMRateLimitError(LLMError):
    """Rate limit hit — should retry with backoff."""
    def __init__(self, message, retry_after=None):
        super().__init__("rate_limit", message, retryable=True)
        self.retry_after = retry_after

class LLMAuthError(LLMError):
    """Authentication failed — don't retry."""
    ...

class LLMBadRequestError(LLMError):
    """Invalid request — don't retry."""
    ...

class LLMServerError(LLMError):
    """Provider server error — may retry."""
    ...
```

**Adapter responsibility:** Catch provider-specific exceptions → raise normalized LLMError.

**LLMStreamHandler:** Existing error handling (try/except with `hasattr(e, "status_code")`) sẽ work với normalized errors.

---

## Tổng kết: Files cần thay đổi

| File | Thay đổi | Mức độ |
|------|----------|--------|
| **Mới tạo** | | |
| `src/agent/core/llm_provider.py` | Interface LLMProvider | NEW |
| `src/agent/core/types.py` | NormalizedStreamEvent, error types | NEW |
| `src/agent/core/providers/openai_provider.py` | OpenAI adapter | NEW |
| `src/agent/core/providers/anthropic_provider.py` | Anthropic adapter | NEW |
| `src/agent/core/providers/gemini_provider.py` | Gemini adapter | NEW |
| `src/agent/core/provider_factory.py` | Factory + model mapping | NEW |
| **Sửa nhẹ** | | |
| `src/agent/core/run_config.py` | `llm_call` → `llm_provider` | LOW |
| `src/agent/general_agent/core/agent_runner.py` | `LLM_call()` → `create_provider()` | LOW |
| `src/agent/general_agent/llm_stream_handler.py` | Event handling từ NormalizedEvent | MEDIUM |
| `src/agent/common/conversation_settings.py` | Mở rộng SUPPORTED_MODELS | LOW |
| `src/agent/general_agent/subagent/subagent_runner.py` | `LLM_call()` → `create_provider()` | LOW |
| `src/agent/suggest_response/core/runner.py` | `LLM_call()` → `create_provider()` | LOW |
| `src/agent/suggest_response/socket/stream_handler.py` | Event handling từ NormalizedEvent | MEDIUM |
| `src/agent/general_agent/summarization/summarizer_service.py` | `LLM_call()` → provider | LOW |
| `src/services/media/media_description_service.py` | `LLM_call()` → provider | LOW |
| **Không thay đổi** | | |
| `src/agent/general_agent/core/iteration_runner.py` | Giữ nguyên | NONE |
| `src/agent/general_agent/utils/response_analyzer.py` | Giữ nguyên | NONE |
| `src/agent/general_agent/tool_executor.py` | Giữ nguyên | NONE |
| `src/agent/general_agent/context/messages/message_converter.py` | Giữ nguyên | NONE |
| `src/agent/general_agent/context/function_output_normalizer.py` | Giữ nguyên | NONE |
| `src/agent/general_agent/context/messages/context_builder.py` | Giữ nguyên | NONE |
| `src/agent/tools/base.py` | Giữ nguyên | NONE |
| Tất cả tool implementations | Giữ nguyên | NONE |

---

## Câu hỏi thiết kế đã giải quyết

1. **Q: Dùng canonical format gì?** → OpenAI Response API format (ít sửa nhất)
2. **Q: Adapter convert ở đâu?** → Trong provider implementation, trước khi gọi SDK
3. **Q: Stream events normalize thế nào?** → Giữ OpenAI event names, adapter map native → normalized
4. **Q: Gemini không có call_id?** → Adapter generate UUID, lưu mapping
5. **Q: Verbosity không có native param?** → Inject vào system prompt
6. **Q: Web search?** → OpenAI-only phase 1, custom tool phase 2
7. **Q: Reasoning effort mapping?** → effort → budget_tokens table per provider
8. **Q: IterationRunner cần sửa?** → KHÔNG — canonical response format giữ nguyên
9. **Q: Tool definitions cần sửa?** → KHÔNG — adapter convert canonical → native
10. **Q: Structured output (parse)?** → Mỗi provider implement khác nhau trong adapter
