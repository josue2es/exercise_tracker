# Spanish exercise catalog

The catalog is imported in English (`scripts/import_catalog.py`). This folder
holds the Spanish layer on top of it; the app shows Spanish when an exercise
has it and falls back to English otherwise. Search matches both, without
accents ("jalon" finds "Jalón al pecho").

| File | What it is |
| --- | --- |
| `es.json` | Spanish names and instructions, keyed `source:source_id`. Applied by the importer. |
| `glossary_es.json` | Gym vocabulary and style rules. Sent to the model and used to check every name. Edit freely. |

## Where the Spanish comes from

Each entry records its origin (`name_origin`, `instructions_origin`):

- **`exercises-dataset`**: instructions for ~1,320 ExerciseDB exercises from
  [hasaneyldrm/exercises-dataset](https://github.com/hasaneyldrm/exercises-dataset)
  (published under MIT, © its contributors). Used only where its English is
  identical to ours, so the Spanish translates exactly the text we show.
- **`claude-opus-5`** (or the model used): translated by `scripts/translate_catalog.py`.
- **`manual`**: edited by a person through the CSV review; never overwritten.

## License of the translations

The translations are published so anyone can use them. What you may do with
an entry depends on where its English came from — the key prefix in `es.json`
(and in `names.csv`) tells you:

| Key prefix | Entries | Source | You may |
| --- | --- | --- | --- |
| `free_exercise_db:` | 876 | [free-exercise-db](https://github.com/yuhonas/free-exercise-db), public domain (Unlicense) | Use names and instructions for anything, commercial included. No attribution needed. |
| `exercisedb_v1:` | 1,500 | [ExerciseDB](https://exercisedb.dev) V1 free dataset: non-commercial use only, attribution required | **Instructions:** non-commercial use only, crediting ExerciseDB (exercisedb.dev). **Names:** short generic terms ("Press de banca"); generally not protected, but if in doubt treat them like the instructions. |

A translation is a derivative work, so it can't be more permissive than its
original. That is why the `exercisedb_v1:` instructions carry ExerciseDB's
terms even though the Spanish text is new. The same applies to those that came
from exercises-dataset: its English is ExerciseDB's text, so its MIT label
does not lift ExerciseDB's terms.

The glossary (`glossary_es.json`) and the translation tooling are part of this
project and are MIT like the rest of the code (see `LICENSE`).

Suggested credit when reusing `exercisedb_v1:` entries:

> Exercise instructions from ExerciseDB (exercisedb.dev), translated to Spanish
> by exercise_tracker (github.com/josue2es/exercise_tracker). Non-commercial use only.

This summarizes the sources' published terms and is not legal advice; for
commercial use of ExerciseDB content, check its terms or license it from
AscendAPI.

Each entry also stores the English it was made from (`en_name`,
`en_instructions_sha`). When upstream changes an exercise, `translate` redoes
just that one.

## Workflow

```bash
uv sync --group translate                                # once: installs the anthropic SDK
export ANTHROPIC_API_KEY=...                             # or `ant auth login`

uv run python -m scripts.translate_catalog status        # coverage
uv run python -m scripts.translate_catalog seed-instructions   # reuse exercises-dataset (free)
uv run python -m scripts.translate_catalog translate --limit 20 # small trial run
uv run python -m scripts.translate_catalog translate     # the rest (Message Batches API, ~1 h)
uv run python -m scripts.translate_catalog check         # validation report
uv run python -m scripts.translate_catalog export-csv names.csv  # review names, flagged first
uv run python -m scripts.translate_catalog import-csv names.csv  # apply edits, mark reviewed
./import_catalog --translations-only                     # load es.json into the database
```

`translate` is safe to interrupt: it saves the batch id and resumes. Results
are validated before merging (every key returned, same number of steps, no
English left, glossary terms used); anything that fails is left for the next
run. Names that don't use the glossary term are kept but flagged, and appear at
the top of the CSV.

Cost for the full catalog (all names, ~1,050 instruction sets) with
Claude Opus 5 through the Batches API is roughly $7–10.
