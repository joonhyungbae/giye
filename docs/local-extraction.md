# Local extraction agreement

Each section is one local model against the synthetic demo replay cache
(`examples/demo/cache`, model id `claude-opus-5`). The cache is what `giye demo`
replays. It is hand-written (`synthetic: true`), not a fresh hosted call.

A row matches when year, activity type, and title agree. The title is Unicode
NFC, then casefold, then whitespace collapsed. Recall is matched/hosted.
Precision is matched/local. Both are recomputed from the totals, not averaged
across CVs. An invented row names a year that does not occur in the CV text,
or a non-empty venue that does not occur once whitespace is collapsed. Invalid
counts a response that is not JSON matching the activity schema, or a provider
error that returned no text. Temperature is 0, the value stored on the cache
records.

<!-- begin model gemma4:26b -->
## gemma4:26b

- Date (UTC): 2026-10-05
- Ollama: ollama version is 0.33.3
- Base URL: `http://localhost:11434/v1`

| CV | hosted | local | matched | recall | precision | invented | invalid |
|---|---:|---:|---:|---:|---:|---:|---:|
| CV-DEMO-HANEUL-ko | 10 | 10 | 9 | 0.900 | 0.900 | 2 | 0 |
| CV-DEMO-HANEUL-en | 4 | 4 | 2 | 0.500 | 0.500 | 1 | 0 |
| CV-DEMO-MINSOO-mixed | 3 | 3 | 2 | 0.667 | 0.667 | 0 | 0 |
| overall | 17 | 17 | 13 | 0.765 | 0.765 | 3 | 0 |
<!-- end model gemma4:26b -->

<!-- begin model qwen2.5:14b -->
## qwen2.5:14b

- Date (UTC): 2026-10-04
- Ollama: ollama version is 0.33.3
- Base URL: `http://localhost:11434/v1`

| CV | hosted | local | matched | recall | precision | invented | invalid |
|---|---:|---:|---:|---:|---:|---:|---:|
| CV-DEMO-HANEUL-ko | 10 | 10 | 10 | 1.000 | 1.000 | 0 | 0 |
| CV-DEMO-HANEUL-en | 4 | 4 | 3 | 0.750 | 0.750 | 0 | 0 |
| CV-DEMO-MINSOO-mixed | 3 | 2 | 2 | 0.667 | 1.000 | 0 | 0 |
| overall | 17 | 16 | 15 | 0.882 | 0.938 | 0 | 0 |
<!-- end model qwen2.5:14b -->

<!-- begin model qwen2.5:7b -->
## qwen2.5:7b

- Date (UTC): 2026-10-04
- Ollama: ollama version is 0.33.3
- Base URL: `http://localhost:11434/v1`

| CV | hosted | local | matched | recall | precision | invented | invalid |
|---|---:|---:|---:|---:|---:|---:|---:|
| CV-DEMO-HANEUL-ko | 10 | 10 | 6 | 0.600 | 0.600 | 0 | 0 |
| CV-DEMO-HANEUL-en | 4 | 4 | 2 | 0.500 | 0.500 | 0 | 0 |
| CV-DEMO-MINSOO-mixed | 3 | 3 | 2 | 0.667 | 0.667 | 0 | 0 |
| overall | 17 | 17 | 10 | 0.588 | 0.588 | 0 | 0 |
<!-- end model qwen2.5:7b -->

<!-- begin model qwen3.5:27b -->
## qwen3.5:27b

- Date (UTC): 2026-10-05
- Ollama: ollama version is 0.33.3
- Base URL: `http://localhost:11434/v1`

| CV | hosted | local | matched | recall | precision | invented | invalid |
|---|---:|---:|---:|---:|---:|---:|---:|
| CV-DEMO-HANEUL-ko | 10 | 10 | 9 | 0.900 | 0.900 | 0 | 0 |
| CV-DEMO-HANEUL-en | 4 | 4 | 3 | 0.750 | 0.750 | 0 | 0 |
| CV-DEMO-MINSOO-mixed | 3 | 3 | 2 | 0.667 | 0.667 | 0 | 0 |
| overall | 17 | 17 | 14 | 0.824 | 0.824 | 0 | 0 |
<!-- end model qwen3.5:27b -->
