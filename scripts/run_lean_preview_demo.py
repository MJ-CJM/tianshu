#!/usr/bin/env python3
"""Delegate source-tree use to the installed Lean Preview runner."""

import runpy

if __name__ == "__main__":
    runpy.run_module("tianshu.lean_preview_demo", run_name="__main__")
