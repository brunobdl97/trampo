# Deterministic pipeline, not an agent loop

Despite the "agent" name, a Run is a fixed pipeline written in code (Discovery → collect Postings → pre-filter → dedupe into Jobs → judge → Digest), and Claude is called only at specific steps: Discovery, judging Jobs, writing Tailored resumes and cover letters. We rejected a model-driven tool loop because its cost per Run is unpredictable under a hard monthly spend cap, it cannot use the Batch API (50% cheaper), and a fixed pipeline is easier to test.
