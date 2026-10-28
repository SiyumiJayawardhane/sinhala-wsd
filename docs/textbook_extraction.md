# Textbook sentence extraction

Notebook: `textbook_extraction.ipynb` (Google Colab, seed 42)

## Source

Educational Publications Department. *සිංහල සාහිත්‍ය සංග්‍රහය* (Grade 10–11), 7th printing 2020 (first printed 2014), ISBN 978-955-25-0381-8.
[PDF](http://edupub.gov.lk/Administrator/Sinhala/10/sin%20lit%20g-10-11/sinhala%20lit%20g-10-11.pdf). The PDF is all rights reserved and is not redistributed.

## Pipeline

1. **Read the PDF:** every text span with its font and position (PyMuPDF). pdftotext was rejected because it reads two-column verse column by column and cannot see fonts.
2. **Convert FM-Abhaya to Unicode** (pandukabhaya). English text is kept unchanged; fake-bold duplicates are removed, verse lines and two-column blocks are rebuilt; NFC-normalised.
3. **Check the conversion:** pages with more than 2 % Latin letters are flagged for manual review (6 flagged: 2, 4, 5, 8, 53, 54).
4. **Exclude pages:** front matter (PDF pages 1–10) and pages 53–54 (badly converted).
5. **Split and tag units:** prose is split at `. ? !`; long verse is cut into 2-line units. Each unit is tagged:
   - `text` (prose, verse, drama dialogue): used
   - `glossary` ("word – meaning" rows): excluded, as definitions rather than usage
   - `study_note` (අන්වය / අර්ථය / විස්තර): excluded, as paraphrase
   - `grammar_note` (sandhi breakdowns such as "සර + අග + ඒ = සරාගේ"): excluded
6. **Spot-check:** 100 random `text` units checked by hand.
7. **Extract sentences with the target words:** 65 words, base form only; any listed spelling counts as the word (e.g. සිළ / සිළි / සිළු); whole tokens, ZWJ/ZWNJ-insensitive; exact duplicates and units under 4 Sinhala tokens removed; one row per (word, unit). No grammatical-use patterns were applied in this run.

## Results

| | Count |
|---|---:|
| Units: text / glossary / study note / grammar note | 3,082: 2,532 / 473 / 69 / 8 |
| Text units used (after duplicate and short-unit removal) | 2,393 |
| (word, sentence) rows | 730, from 564 distinct units |
| Words with no textbook sentence | 7: වණ, රැස, කලාපය, නිදන, සමර, දීප, දල |

## Outputs

Saved in Google Drive (not committed): CSV master with book and page for every sentence, TXT in annotation-tool format, per-word summary, spot-check sheet, and `meta.json` (source, tool versions, excluded pages, filter counts).
