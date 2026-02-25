# Facebook Sync Architecture

## 📋 Overview

Unified architecture for Facebook sync operations that prevents **race conditions** and **server blocking** by centralizing lock management and job queue operations.

## 🎯 Problems Solved

### Problem 1: Race Conditions
**Before:** Individual sync services (PostSyncService, CommentSyncService, InboxSyncService) had no lock protection.
- FE calls async API → Worker executes sync
- Agent calls tool → Executes sync directly
- **Result:** Both can sync same resource simultaneously → conflicts

**After:** All sync operations protected by Redis locks via SyncJobManager.

### Problem 2: Server Blocking
**Before:** 
- FE calls → Job queue → Worker (non-blocking ✅)
- Agent calls → Direct execution (blocking ❌)

**After:** Both FE and Agent use job queue → All operations non-blocking ✅

---

## 🏗️ Architecture Components

### 1. **SyncLocks** (`src/redis_client/full_sync_locks.py`)

Generic Redis-based locking for all sync types:

```python
# Lock key patterns:
sync:full:{page_id}      # Full sync
sync:posts:{page_id}     # Posts sync
sync:comments:{post_id}  # Comments sync
sync:inbox:{page_id}     # Inbox sync
```

**Key methods:**
- `acquire_lock(lock_key, ttl_seconds)` - Atomic lock acquisition
- `release_lock(lock_key)` - Lock release

### 2. **SyncJobManager** (`src/services/facebook/sync_job_manager.py`)

Unified coordinator for all sync operations. Single entry point for both API and Agent.

**Responsibilities:**
1. ✅ Acquire lock BEFORE enqueuing
2. ✅ Enqueue job with `_lock_key` in payload
3. ✅ Support ASYNC mode (API) and SYNC mode (Agent)
4. ✅ Handle lock acquisition failures

**Key methods:**
```python
await sync_job_manager.submit_sync(
    sync_type=SyncType.POSTS,  # FULL, POSTS, COMMENTS, INBOX
    payload={"page_id": "123", "limit": 25},
    user_id="user_id",
    mode=SyncMode.ASYNC,  # or SYNC for agents
    timeout_seconds=300,
)
```

**Modes:**
- `SyncMode.ASYNC` - Return job_id immediately (for FE API)
- `SyncMode.SYNC` - Wait for completion (for Agent tools)

### 3. **SyncWorker** (`src/workers/sync_worker.py`)

Background worker that processes sync jobs.

**Updated behavior:**
```python
async def process_job(self, job_id: str):
    lock_key = None
    try:
        job = await self.job_queue.get_job(job_id)
        lock_key = job["payload"].get("_lock_key")
        
        # Execute sync logic
        result = await self._handle_*_sync(job_id, payload)
        await self.job_queue.mark_completed(job_id, result=result)
        
    except Exception as e:
        await self.job_queue.mark_failed(job_id, error=str(e))
        
    finally:
        # ALWAYS release lock (success, failure, or cancellation)
        if lock_key:
            await sync_locks.release_lock(lock_key)
```

---

## 🔄 Lock Lifecycle Flow

### Complete Flow Example (Posts Sync):

```
1. CALLER (API or Agent)
   └─> SyncJobManager.submit_sync(SyncType.POSTS, ...)

2. SYNCJOBMANAGER
   ├─> acquire_lock("sync:posts:123")  ← ACQUIRE HERE
   ├─> if locked → return error
   ├─> if acquired → enqueue job with _lock_key
   └─> mode == ASYNC → return job_id
       mode == SYNC → wait for completion

3. REDIS QUEUE
   └─> Job queued with payload: {page_id, limit, _lock_key}

4. SYNCWORKER (separate process)
   ├─> dequeue job
   ├─> extract _lock_key from payload
   ├─> try:
   │      PostSyncService.sync_posts()  ← NO LOCK HERE
   │   finally:
   │      release_lock(_lock_key)  ← RELEASE HERE
   └─> Job completed

5. RESULT
   └─> Lock released, next job can proceed
```

**Key Points:**
- ✅ Lock acquired at submission (prevents duplicate enqueue)
- ✅ Lock held during execution
- ✅ Lock released in finally (guaranteed cleanup)
- ✅ Services focus on business logic only

---

## 📦 Updated Services

### Services NO LONGER Handle Locks

All sync services now focus purely on business logic:

**FullSyncService** (`src/services/facebook/full_sync_service.py`)
- ❌ Removed: `acquire_full_sync_lock()` at start
- ❌ Removed: `release_full_sync_lock()` in finally
- ✅ Now: Pure business logic

**PostSyncService, CommentSyncService, InboxSyncService**
- Never had locks, continue as-is
- Called by Worker within lock protection

---

## 🔌 API Integration

### Async Router (`src/api/facebook/sync/async_router.py`)

**Before:**
```python
@router.post("/posts")
async def async_posts_sync(payload, job_queue):
    job_id = await job_queue.enqueue("post_sync", {...})
    return {"job_id": job_id}
```

**After:**
```python
@router.post("/posts")
async def async_posts_sync(payload, sync_job_manager):
    result = await sync_job_manager.submit_sync(
        sync_type=SyncType.POSTS,
        payload={...},
        mode=SyncMode.ASYNC,  # Return immediately
    )
    if not result["success"]:
        raise HTTPException(409, detail=result["error"])
    return {"job_id": result["job_id"]}
```

---

## 🤖 Agent Tools Integration

### Agent Tools (`src/agent/tools/sync/*.py`)

**Before:**
```python
class ManagePagePostsSyncTool:
    def __init__(self, post_sync_service):
        self._sync_service = post_sync_service
    
    async def execute(self, conn, context, arguments):
        # Direct execution - BLOCKS SERVER
        result = await self._sync_service.sync_posts(...)
        return result
```

**After:**
```python
class ManagePagePostsSyncTool:
    def __init__(self, sync_job_manager, post_sync_service):
        self._sync_job_manager = sync_job_manager
        self._sync_service = post_sync_service  # For status checks
    
    async def execute(self, conn, context, arguments):
        if action == "sync":
            # Submit via job queue - NON-BLOCKING
            result = await self._sync_job_manager.submit_sync(
                sync_type=SyncType.POSTS,
                payload={...},
                user_id=context.user_id,
                mode=SyncMode.SYNC,  # Wait for result
                timeout_seconds=300,
            )
            return result
        
        elif action == "status":
            # Status checks still use service directly
            return await self._sync_service.get_sync_status(...)
```

---

## 🚀 Initialization (`src/main.py`)

```python
# 1. Initialize sync infrastructure
sync_locks = SyncLocks(redis_client=redis_client)

# 2. Initialize sync job manager
sync_job_manager = SyncJobManager(
    job_queue=job_queue,
    sync_locks=sync_locks,
    default_lock_ttl=3600,
)
app.state.sync_job_manager = sync_job_manager

# 3. Initialize agent runner with sync_job_manager
agent_runner = AgentRunner(
    socket_service, 
    context_manager,
    sync_job_manager,  # Injected here
)

# 4. Tools registry gets sync_job_manager
registry = create_default_registry(sync_job_manager=sync_job_manager)
```

---

## ✅ Benefits

### 1. **Prevents Race Conditions**
- All sync operations locked at submission
- Duplicate requests rejected immediately
- Clean error messages: `"sync_already_in_progress"`

### 2. **Prevents Server Blocking**
- All operations use job queue
- Worker handles heavy lifting
- Server stays responsive

### 3. **Consistent Behavior**
- API and Agent use same code path
- Same lock protection
- Same job tracking

### 4. **Better Monitoring**
- All jobs trackable via job_id
- Progress tracking
- Job cancellation support

### 5. **Clean Architecture**
- SyncJobManager: Lock + queue management
- Services: Business logic only
- Worker: Execution + cleanup
- Clear separation of concerns

---

## 🔍 Lock Key Reference

| Sync Type | Lock Key Pattern | Scope |
|-----------|------------------|-------|
| Full Sync | `sync:full:{page_id}` | Entire page |
| Posts Sync | `sync:posts:{page_id}` | Page posts |
| Comments Sync | `sync:comments:{post_id}` | Single post |
| Inbox Sync | `sync:inbox:{page_id}` | Page inbox |

**Lock TTL:** 1 hour (3600 seconds) by default

**Lock Release:**
- ✅ On success: Worker finally block
- ✅ On failure: Worker finally block
- ✅ On cancellation: Worker finally block
- ✅ On worker crash: Redis TTL expires (1 hour)

---

## 🧪 Testing

### Test Race Condition Protection:

```bash
# Start 2 concurrent sync requests for same page
curl -X POST /sync/async/posts -d '{"page_id": "123"}' &
curl -X POST /sync/async/posts -d '{"page_id": "123"}' &

# Expected: First succeeds, second gets 409 Conflict
```

### Test Non-Blocking Agent Sync:

```python
# Agent calls sync tool
result = await tool.execute(
    action="sync",
    page_id="123",
    limit=25,
)

# Server remains responsive during sync
# Result returned after job completes
```

---

## 📝 Migration Notes

### Breaking Changes: None
- All existing API endpoints continue to work
- Agent tools continue to work
- Backward compatible

### New Behavior:
1. ✅ Duplicate sync requests now rejected (before: both executed)
2. ✅ Agent sync no longer blocks server (before: blocked)
3. ✅ All sync operations trackable via job_id

---

## 🐛 Troubleshooting

### "sync_already_in_progress" Error

**Cause:** Another sync is running for same resource

**Solution:** 
1. Check job status: `GET /sync/async/jobs`
2. Wait for current sync to complete
3. If stuck, check Redis: `redis-cli GET sync:posts:{page_id}`
4. If needed, manually release: `redis-cli DEL sync:posts:{page_id}`

### Lock Not Released

**Cause:** Worker crashed before finally block

**Solution:** Lock auto-expires after 1 hour (TTL)

**Manual fix:**
```bash
redis-cli DEL sync:posts:{page_id}
```

---

## 📚 Related Files

**Core:**
- `src/services/facebook/sync_job_manager.py` - Main coordinator
- `src/redis_client/full_sync_locks.py` - Lock management
- `src/workers/sync_worker.py` - Job execution

**API:**
- `src/api/facebook/sync/async_router.py` - Async endpoints

**Agent:**
- `src/agent/tools/sync/manage_page_posts_sync.py`
- `src/agent/tools/sync/manage_post_comments_sync.py`
- `src/agent/tools/sync/manage_page_inbox_sync.py`

**Services:**
- `src/services/facebook/full_sync_service.py`
- `src/services/facebook/posts/post_sync_service.py`
- `src/services/facebook/comments/sync/comment_sync_service.py`
- `src/services/facebook/messages/sync/inbox_sync_service.py`

---

**Last Updated:** 2026-01-15  
**Version:** 2.0 (Unified Sync Architecture)
