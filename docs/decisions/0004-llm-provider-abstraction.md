# 0004. LLM provider abstraction, Anthropic only for MVP

- Status: accepted
- Date: 2026-10-13

## Context

Fields extraction uses an LLM. The author may want to run a local LLM later; the draft mentioned both Anthropic and OpenAI.

## Decision

LLM access goes through a provider abstraction selected by configuration. Only Anthropic (Python SDK) is implemented. Other providers (OpenAI, local) are stubs that return an explicit error when selected.

## Consequences

- Adding a provider means implementing one interface.
- No silent fallback between providers.
