# Documentation Index

Design and operations documentation for El Ripley AI Agent.

## Architecture

- [System overview (white paper)](EL_RIPLEY_WHITE_PAPER%20copy.md) — product and system high-level overview
- [Subagent design](SUBAGENT_DESIGN.md) — context-isolated subagent system
- [Sync architecture](SYNC_ARCHITECTURE.md) — Facebook sync, Redis locks, job queue
- [Multi-LLM provider](multi-llm-provider/) — OpenAI / Anthropic / Gemini provider-agnostic design

## Features

- [Suggest Response Agent](SUGGEST_RESPONSE_AGENT.md) — suggest-response pipeline and tools
- [Escalation system](ESCALATION_SYSTEM_REDESIGN.md) — escalations and messages
- [Notification system](NOTIFICATION_SYSTEM.md) — in-app notifications and triggers
- [Billing system](BILLING_SYSTEM.md) — credits, Stripe, Polar, SePay
- [Playbooks and Qdrant](playbooks-design-and-qdrant-setup.md) — vector search and setup

## Operations

- [Agent SQL RLS setup](AGENT_SQL_RLS_SETUP.md) — PostgreSQL RLS roles and policies for the agent
