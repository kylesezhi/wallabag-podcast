# Spec: chunked-audio-generation

Scope: feature

# Spec: chunked-audio-generation

Scope: feature

# Chunked TTS Generation with Streamed Writes + Granular Progress

## Problem
`generate_all()` synthesizes each episode with ONE Kokoro POST containing the full
TTS text and buffers the entire MP3 response in RAM before writing to disk.
Long articles exhaust memory (Kokoro server synthesizing a huge input at once;
app process holding all audio bytes), aborting generation.

## Contract

### Text chunking (`app/textclean.py`)
- `split_tts_text(text, max_chars) -> list[str]`: split on sentence boundaries
  (`.`, `!`, `?` followed by whitespace/end). Accumulate sentences into chunks of
  at most `max_chars`. A single sentence longer than `max_chars` is hard-split at
  word boundaries. Never split inside `[pause:...]` tokens (they contain no
  sentence punctuation; a boundary may place a token at a chunk start, which is valid).
- Chunk size default: `Settings.KOKORO_MAX_CHUNK_CHARS = 2000` (env-configurable).
- Input is the existing `build_tts_input()` string (`[pause:0.5s] title [pause:1s] body`),
  unchanged in format.

### Synthesis loop (`app/pipeline.generate_all`)
1. Split text into N chunks.
2. Open `DATA_DIR/audio/{id}.mp3.part` mode `"wb"` (fresh each attempt — never append to stale data).
3. Per chunk, in order:
   - persist progress (`progress_done=i, progress_total=N`) before the request,
   - `await kokoro_client.synthesize(chunk, voice)` — on `KokoroError`/connection failure retry ONCE before giving up,
   - measure the chunk's MP3 duration via mutagen over `BytesIO` (`measure_duration` accepts `bytes | Path`),
   - append bytes to the `.part` file,
   - persist `progress_done=i+1`.
4. Atomically `os.replace(.part -> {id}.mp3)`.
5. Episode duration = SUM of per-chunk durations; if any chunk measurement is None,
   fall back to `est_minutes * 60` exactly as today.

### Guarantees / preserved semantics
- Constant memory: only one chunk's bytes are ever held in RAM.
- Any failure or cancellation best-effort unlinks the `.part` file; no partial file is ever served.
- `asyncio.CancelledError`: in-flight episode marked `failed` ("Cancelled by user"), run halts, remaining episodes stay `staged`.
- `SkipArticle` / `KokoroError` / `WallabagError` isolation per episode unchanged.
- Mid-run staged-delete skip check unchanged; `reset_failed_to_staged()` clears progress columns.
- Final `{id}.mp3` appears on disk only when complete (atomic rename).

### Progress persistence & UI
- `episodes` table gains nullable INTEGER columns `progress_done`, `progress_total`;
  `init_db()` performs a guarded `ALTER TABLE` migration for existing databases.
- `db.set_episode_progress(conn, id, done, total)`; `get_queue_episodes()` returns both fields;
  `/queue/status` exposes them automatically.
- UI (2s poller in `static/js/app.js`): generating queue row badge shows "generating 4/12";
  the drive progress card also shows the current episode's chunk progress.