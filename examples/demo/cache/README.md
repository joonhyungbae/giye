# Synthetic replay cache

These JSON files are **not** model output. They are hand-written raw responses that match the
CV extraction schema (`giye.extract.schema`) and the fictitious CVs in `../cvs/`. A run of
`giye extract --replay-only` reads them instead of calling a model.

Each file is keyed by the CV content hash, the prompt SHA-256 (`prompts/cv_extract_v1.txt`),
and the model id. The body stores that raw response plus `model`, `prompt_sha256`,
`content_sha256`, `created_at`, and `temperature`. `synthetic` is true on every record.
김하늘 and Haneul Kim are separate roster rows, so their CVs are two cache files. The old
file that hashed both documents together was removed when the English CV moved to the
workshop row.
