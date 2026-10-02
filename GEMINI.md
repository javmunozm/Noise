# Lottery Prediction System — Technical Reference for Gemini

> **DOCUMENTATION REFACTORED TO SAVE TOKENS**
> The full project documentation has been split into independent files.
> Start your context search by reading `docs/index.md`.

## Essential Rules & Guidelines

1. **Production Engine:** `V25` / `V26` (Pure Geometric / HDM Sentinel) is the current rational operational default for regime 3203+. The data is verified IID random.
2. **First Action:** On session start, ALWAYS run `python ml_models/pm_agent.py report` first.
3. **Keep Logs Updated:** Whenever you add data or run evaluations, add a row to `docs/activity_log.md`. Summarize pivotal user prompts/responses into `docs/conversation_log.md`.
4. **No Arbitrary Logic:** Filters like `is_legal_v2` have been removed because they excluded actual valid events (22.7%).
5. **Historical Systems:** `V12.3`, `V21`, and `V22` are deprecated/decayed. Do not resurrect.

Read `docs/index.md` for in-depth information about constraints, statistics, and system history.
