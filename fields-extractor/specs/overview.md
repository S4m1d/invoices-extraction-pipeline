# Fields Extractor: Overview

## Purpose

Takes markdown with extracted layout and text and extracts concrete pieces of information into a JSON schema, then passes the JSON to the next pipeline stage (`integrator`).

## Stack

Python, Pydantic, LLM via a provider abstraction (see [`llm-providers.md`](llm-providers.md)).

## Responsibilities

- Consume messages from the `documents.text_extracted` queue.
- Read markdown from object storage.
- Call the configured LLM provider and obtain structured output.
- Validate output with Pydantic against the extraction schema.
- Write the validated JSON to object storage (`extracted.json`) and publish a message for `integrator`.

## Interfaces

- Input: queue message referencing markdown (see `/specs/pipeline-contracts.md`).
- Output: extracted JSON, see [`extraction-schema.md`](extraction-schema.md).
- LLM providers: see [`llm-providers.md`](llm-providers.md).

## Open questions

- OPEN: behaviour when the LLM output fails schema validation (retry count, fail the document).
- OPEN: prompt storage and versioning.
