# Subagent Implementation - Context Isolation Architecture

> **Last Updated**: 2025-01-23

## Overview

Subagent system cho phép main agent **isolate context** khi thực hiện các task phức tạp, giúp:
- Giảm context length của main agent
- Tách biệt nhiệm vụ phức tạp thành các task nhỏ hơn
- Real-time feedback: User thấy subagent hoạt động real-time qua streaming

## Architecture Flow

```
Main Agent → task tool → SubAgentRunner → SubAgentIterationRunner
                                    ↓
                            (Streams to FE with metadata)
                                    ↓
                            Returns result to Main Agent
```

### Key Design Decisions

| Feature | Implementation | Notes |
|---------|----------------|-------|
| **Streaming** | ✅ Implemented | Real-time feedback qua socket events với subagent metadata |
| **Resume** | ✅ Implemented | Main agent có thể resume subagent conversation với prompt mới |
| **max_turns** | ✅ Implemented | Default: 20, Max: 60 |
| **Parent Stop Signal** | ✅ Implemented | Subagent tự động stop nếu parent agent bị stopped |
| **Recursive subagent** | ❌ Not supported | Subagent không có task tool |

## Database Schema

### Subagent Fields in `openai_conversation`

```sql
-- Subagent support columns
parent_conversation_id UUID REFERENCES openai_conversation(id) ON DELETE CASCADE,
parent_agent_response_id UUID REFERENCES agent_response(id) ON DELETE SET NULL,
subagent_type VARCHAR(64),      -- 'explore'
is_subagent BOOLEAN DEFAULT FALSE,
task_call_id VARCHAR(255),       -- call_id của task function_call

-- Indexes
CREATE INDEX idx_openai_conversation_parent ON openai_conversation(parent_conversation_id) 
WHERE parent_conversation_id IS NOT NULL;
CREATE INDEX idx_openai_conversation_subagent ON openai_conversation(is_subagent) 
WHERE is_subagent = TRUE;
CREATE INDEX idx_openai_conversation_task_call_id ON openai_conversation(task_call_id)
WHERE task_call_id IS NOT NULL;
```

### Data Hierarchy

```
openai_conversation (main, is_subagent=false)
├── agent_response (main agent trigger)
│   └── openai_response[] (main agent's API calls)
│
└── openai_conversation (subagent)
    ├── parent_conversation_id = main.id
    ├── task_call_id = "call_abc123"
    ├── is_subagent = true
    ├── openai_message[] (subagent's ISOLATED context)
    └── agent_response (parent_agent_response_id = main_agent.id)
        └── openai_response[] (subagent's API calls)
```

## Socket Events

### Event Structure

Subagent events include metadata để FE biết render ở đâu:

```python
{
    "event": "agent.event",
    "data": {
        # Standard fields
        "conversation_id": "subagent-conv-uuid",
        "branch_id": "subagent-branch-uuid",
        "agent_response_id": "subagent-response-uuid",
        "msg_type": "message" | "function_call" | "function_call_output",
        "msg_item": { ... },
        
        # Subagent metadata
        "is_subagent": true,
        "parent_conversation_id": "main-conv-uuid",
        "task_call_id": "call_abc123",
    }
}
```

## Code Architecture

### Directory Structure

```
src/agent/
├── agent_runner/
│   ├── agent_runner.py
│   ├── iteration_runner.py
│   ├── response_analyzer.py      # Shared response analysis
│   ├── llm_stream_handler.py     # Reused for subagent
│   └── tool_executor.py           # Reused for subagent
│
├── subagent/
│   ├── subagent_runner.py         # Main orchestrator
│   ├── subagent_iteration_runner.py  # Iteration logic
│   ├── registry.py                # create_explore_registry()
│   └── prompts.py                 # EXPLORE_SYSTEM_PROMPT
│
└── tools/
    └── task/
        └── task.py                # TaskTool class
```

### Core Components

#### 1. SubAgentRunner

Main orchestrator cho subagent execution (cấu trúc tương tự AgentRunner):

```python
class SubAgentRunner:
    def __init__(self, socket_service, context_manager, sync_job_manager=None):
        self.iteration_runner = SubAgentIterationRunner(...)
        self.registry = create_explore_registry(...)
    
    async def run(ctx: SubAgentContext, prompt: str, resume_conversation_id: Optional[str]):
        # Create/resume conversation
        # Create temp context via context_manager
        # Run _iterate_agent_responses()
        # Finalize and deduct credits
        # Return SubAgentResult
    
    async def _iterate_agent_responses(...) -> Tuple[turns_used, total_tokens, final_content]:
        # Parent stop signal check BEFORE iteration
        # Run iteration_runner.run()
        # Parent stop signal check AFTER iteration
        # Return aggregated results
    
    async def _check_parent_stopped(...) -> bool:
        # Check if parent agent was stopped
    
    async def _finalize_and_deduct(agent_response_id):
        # finalize_agent_response() + deduct_credits_after_agent()
```

**Key methods:**
- `run()` - Main entry point with try/except for error handling
- `_iterate_agent_responses()` - Vòng lặp chính (giống AgentRunner)
- `_check_parent_stopped()` - Check parent stop signal at safe points
- `_finalize_and_deduct()` - Finalize và deduct credits khi hoàn thành
- `_create_conversation()` - Uses `create_subagent_conversation()` from repositories
- `_resume_conversation()` - Loads existing conversation and appends new prompt

#### 2. SubAgentIterationRunner

Handles single iteration execution (simplified - no stop signal checking):

```python
class SubAgentIterationRunner:
    async def run(ctx, conversation_id, branch_id, ...) -> SubAgentIterationResult:
        # Stream LLM response
        # Execute tool calls if needed
        # Save to DB via context_manager
        # Return result
```

**Features:**
- Reuses `LLMStreamHandler` and `ToolExecutor` from main agent
- Uses `ResponseAnalyzer` for response analysis
- Parent stop signal is checked in SubAgentRunner (not here)

#### 3. ResponseAnalyzer

Shared utility for analyzing LLM responses:

```python
class ResponseAnalyzer:
    @staticmethod
    def is_final(response_dict) -> bool
    
    @staticmethod
    def has_ask_user_question(response_dict) -> bool
    
    @staticmethod
    def extract_final_content(response_dict) -> Optional[str]
```

Used by both `IterationRunner` and `SubAgentIterationRunner`.

#### 4. Context Manager Methods

**For Subagent:**
```python
async def create_temp_context_for_subagent(
    conn, user_id, conversation_id, agent_response_id,
    system_prompt, user_prompt
) -> bool
```

Creates temp context in Redis với fixed system prompt (EXPLORE_SYSTEM_PROMPT).

**For Main Agent:**
```python
async def create_temp_context_for_current_branch(...)
```

Builds dynamic system prompt based on iteration count, model, etc.

#### 5. Repository Functions

**create_subagent_conversation()** in `conversations.py`:
- Creates conversation với subagent fields
- Creates master branch
- Returns (conversation_id, branch_id)

All SQL logic moved from `SubAgentRunner` to repository layer.

### Data Classes

```python
@dataclass
class SubAgentContext:
    user_id: str
    parent_conversation_id: str
    parent_agent_response_id: str
    task_call_id: str
    model: str
    max_turns: int

@dataclass
class SubAgentResult:
    result: str
    conversation_id: str
    turns_used: int
    total_tokens: int

@dataclass
class SubAgentMetadata:
    is_subagent: bool = True
    parent_conversation_id: str = ""
    task_call_id: str = ""
```

### Explore Registry

Limited tool set for subagent:

```python
def create_explore_registry(sync_job_manager=None) -> ToolRegistry:
    # Facebook Query Tools (read-only)
    # Sync Tools (fetch from Facebook)
    # Excludes: task, ask_user_question, todo_write, Memory tools
```

### task tool

```python
class TaskTool(BaseTool):
    async def execute(context, arguments) -> SubAgentResult:
        # Build SubAgentContext
        # Call subagent_runner.run()
        # Return formatted result
    
    def process_result(context, raw_result) -> ToolResult:
        # Format output with metadata
```

## API Endpoints

### Get Subagent Messages

```python
GET /conversations/{conversation_id}/subagents/{subagent_conversation_id}/messages

# Returns messages for subagent conversation
# Authorization: Verifies subagent belongs to parent conversation
```

## Resume Feature

Main agent có thể resume subagent conversation:

```python
# First call
task(prompt="Find unread conversations")
# Returns: subagent_id="abc123"

# Resume call
task(resume="abc123", prompt="Get details of conversation with John")
# Loads previous context + appends new prompt
```

Implementation:
- `_resume_conversation()` loads existing messages from branch
- Appends new user prompt
- Continues iteration loop

## Cost Tracking & Billing

### Finalize & Deduct

Mỗi agent_response (main hoặc subagent) được finalize và deduct riêng:

```python
async def _finalize_and_deduct(agent_response_id: str) -> None:
    async with async_db_transaction() as conn:
        await finalize_agent_response(conn, agent_response_id)  # Set status
        await deduct_credits_after_agent(conn, agent_response_id)  # Deduct credits
```

**CRITICAL**: Cả main agent và subagent đều phải gọi finalize_and_deduct khi hoàn thành.
Nếu không, agent_response sẽ mãi ở status='in_progress' và credits không bị trừ.

### Cost Aggregation

Cost được track qua `agent_response.parent_agent_response_id`:

```sql
-- Total cost of main agent + all subagents
SELECT SUM(total_cost) as total_cost
FROM agent_response
WHERE id = :main_agent_response_id
   OR parent_agent_response_id = :main_agent_response_id;
```

Per-subagent cost trong task tool output metadata.

## Refactoring Improvements

### Phase 1 (2025-01-23)

#### 1. SQL to Repositories
- Moved all SQL from `SubAgentRunner._create_conversation()` to `create_subagent_conversation()` in `conversations.py`
- Better separation of concerns

#### 2. Context Manager Method
- Added `create_temp_context_for_subagent()` method
- Replaces inline temp context creation code
- Cleaner separation between main agent and subagent flows

#### 3. ResponseAnalyzer Extraction
- Extracted `ResponseAnalyzer` to separate file `response_analyzer.py`
- Added `extract_final_content()` method
- Shared between main agent and subagent

#### 4. SubAgentIterationRunner
- Extracted iteration logic from `SubAgentRunner._run_iteration()` (~138 lines)
- Cleaner code organization

#### 5. Code Reduction
- `SubAgentRunner` reduced from ~565 lines to ~294 lines (48% reduction)
- Better maintainability and testability

### Phase 2 (2025-01-23) - Structure Alignment & Billing Fix

#### 1. SubAgentRunner Structure Aligned with AgentRunner
- Added `_iterate_agent_responses()` method to contain the main loop
- Matches AgentRunner's structure for consistency
- Better separation of concerns

#### 2. Parent Stop Signal Moved to SubAgentRunner
- Moved `_check_parent_stopped()` from SubAgentIterationRunner to SubAgentRunner
- Check at 2 safe points: before iteration (no tokens consumed) and after iteration (all tokens billed)
- Matches AgentRunner's stop signal checking pattern

#### 3. CRITICAL: Added Finalize & Deduct
- Added `_finalize_and_deduct()` method to SubAgentRunner
- Calls `finalize_agent_response()` to set status='completed'
- Calls `deduct_credits_after_agent()` to deduct user credits
- Handles error cases: finalize is called even if subagent errors

**Before (BUG):**
- SubAgentRunner only called `update_agent_response_aggregates()`
- agent_response.status stayed 'in_progress' forever
- User credits were NOT deducted for subagent usage

**After (FIXED):**
- SubAgentRunner calls `finalize_agent_response()` + `deduct_credits_after_agent()`
- agent_response.status correctly set to 'completed'
- User credits are properly deducted

## System Prompt

```python
EXPLORE_SYSTEM_PROMPT = """You are an Explore subagent specialized in gathering information from Facebook pages.

## Your Role
You work autonomously to fulfill the given task and return a comprehensive report.

## Available Tools
Facebook query and sync tools (read-only operations).

## Guidelines
1. Be thorough but efficient
2. Use sync tools when needed
3. Organize findings clearly
4. No questions - work with what you have
5. Summarize well in final report
"""
```

---

*Last updated: 2025-01-23*
