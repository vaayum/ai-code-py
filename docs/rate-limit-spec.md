# Design Spec: Rate Limiting for FileTools

## Overview
Add rate limiting to the `FileTools` class to prevent agents from making too many
file reads in a short time window. This is a safety feature to avoid runaway agents.

## Requirements

### FR-1: Read Rate Limit
- Track the number of `read_file` calls per 60-second rolling window
- Default limit: **50 reads/minute**
- Configurable via constructor: `max_reads_per_minute: int = 50`

### FR-2: Write Rate Limit
- Track `create_file`, `update_file`, `delete_file` calls per 60-second window
- Default limit: **10 writes/minute**
- Configurable via constructor: `max_writes_per_minute: int = 10`

### FR-3: Limit Response
- When a rate limit is hit, return an error string:
  `"❌ Rate limit exceeded: max {N} reads/writes per minute. Wait {S} seconds."`
- Do NOT raise an exception — return as a tool error string so the agent can handle

### FR-4: State Reset
- Exposed method `reset_rate_limits()` for testing purposes
- Rate counters should use `collections.deque` with `maxlen` for efficiency

## Implementation Location
- File: `aicoder/tools/file_tools.py`
- Class: `FileTools`
- No new files needed

## Tests Required
- Test that read limit blocks after N reads
- Test that write limit blocks after N writes
- Test that `reset_rate_limits()` restores access
- Test that limits are configurable
