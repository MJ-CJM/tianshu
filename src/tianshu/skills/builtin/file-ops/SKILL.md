---
name: file-ops
description: "File read/write operations guidance"
metadata:
  openclaw:
    always: true
  tianshu:
    tool_tier: "T0/T1"
---

# File Operations

Use `read_file` and `write_file` tools to manage files in the workspace.

## Guidelines

- All paths are relative to the workspace directory
- read_file returns up to 10,000 characters
- write_file creates parent directories automatically
- Always verify file content after writing when accuracy is critical
- Use UTF-8 encoding

## Common Patterns

- Read a config file before modifying it
- Write structured output (JSON, YAML, Markdown)
- Create new files for task results
