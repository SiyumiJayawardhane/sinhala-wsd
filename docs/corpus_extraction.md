# Corpus sentence extraction

Notebook: `Extraction_pipeline.ipynb` (Google Colab, seed 42)

## Source

`sinhala-nlp/sinhala-1.5B-corpus` (Ranasinghe et al., 2025), Hugging Face revision `352f711af658137be6acceea60063c9e711bf4a4`: one TSV file with `text` and `source` fields.

## Pipeline

1. **Target words:** 65 words from `WSD_Polysemy_Words.xlsx` (sheet `target_words`), base form only; a cell with several spellings is one word, and any spelling counts.
2. **Cleaning:** NFC; URLs, zero-width characters and date/time lines removed; split at line breaks and `. ! ? ।`; a sentence is kept if it has 15–400 characters, at least 50 % Sinhala characters and at least 4 Sinhala tokens.
3. **Matching:** whole tokens, ZWJ/ZWNJ-insensitive.
4. **Word-level filters:** a sentence is dropped for a word only if every occurrence of the word is inside a pattern.
   - Noise (the token is not the target word): රැස, සල්, සී, මී, දන
   - Grammatical-only uses: කර, කලි, වස්
   - Patterns and rejection counts are recorded in the run metadata.
5. **Deduplication:** exact duplicates per word (hash of Sinhala letters and digits). Near-duplicates are not removed.
6. **Reservoir sampling** (Algorithm R): an unbiased random sample of up to 1,000 sentences per word in one pass over the corpus. Checkpoints every 100,000 rows allow resuming.
7. **Annotation sample:** 200 random sentences per word from the pool; sentences also in the textbook set are removed first. Each sentence keeps its sub-source and row id.

## Results

| | Count |
|---|---:|
| Rows scanned | 7,074,641 |
| Sentences in pool (maximum 65 × 1,000) | 63,401 |
| Words with no sentence | 0 |
| Overlap with textbook removed | 2 |
| Annotation sample | 13,000 (200 for each of the 65 words) |

Rows per `source` label:

| hplt_2 | CulturaX | Nsina | sinmin | FacebookDecadeCorpora | semisold | no label |
|---:|---:|---:|---:|---:|---:|---:|
| 1,152,703 | 753,655 | 486,932 | 313,910 | 364,402 | 107,210 | 3,895,829 |

## Notes and limitations

- Sub-sources come from the `source` field of the released data, not the dataset card. CulturaX appears in the data but not on the card; 3,895,829 rows have no source label and are reported as `unlabelled`.
- The filter patterns used in this run allow zero spaces (`\s*`), so a pattern can also match inside a longer word (e.g. මීළඟ). This affects only sentences containing both forms; later runs should require whole tokens.
- Near-duplicates across web sub-sources are not removed.

## Outputs

Saved in Google Drive (not committed): pool CSV (with sub-source and row id), per-word summary, 200-per-word CSV and TXT (annotation-tool format), checkpoints. Run metadata: `metadata/corpus15b_v4_meta.json`.
