---
name: analyze-context
description: Analyzes the current context and estimates the token size per topic. Use when the user says "analyze context", "what's in context?" or wants to understand how many tokens where used in the context.
disable-model-invocation: true
license: MIT License, Copyright (c) 2026 ti&m AG
---
Analyze the current context and summarize it concisely by topic.
Estimate the token size of each topic.
Categorize the topics by layers: system prompt, tools, persistent instructions (CLAUDE.md, ...), project context (file contents, directories, ...), conversation history, ... 
Output in a table with columns: Layer, Topic, Description, Token Count

Give a short summary of the biggest token consumers.
