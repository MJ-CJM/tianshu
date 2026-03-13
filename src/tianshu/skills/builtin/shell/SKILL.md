---
name: shell
description: "Shell command execution guidance"
metadata:
  openclaw:
    always: true
  tianshu:
    tool_tier: "T2"
---

# Shell Execution

Use the `shell_exec` tool to run shell commands in the workspace directory.

## Guidelines

- Always specify the exact command to run
- Use relative paths within the workspace
- Check command output for errors before proceeding
- For long-running commands, consider timeouts
- Avoid destructive operations (rm -rf, etc.) unless explicitly requested
- Chain commands with && for sequential execution

## Common Patterns

- List files: `ls -la`
- Search content: `grep -r "pattern" .`
- Check disk usage: `du -sh *`
- Process management: `ps aux | grep process_name`
