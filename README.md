# sinhala-wsd

A sense-annotated, multi-annotator Word Sense Disambiguation (WSD) dataset and evaluation pipeline for Sinhala
(final-year Individual Research Project, IIT Sri Lanka / Robert Gordon University).

- **Target words:** 65 candidate polysemous Sinhala words, base form only. After annotation, words with fewer than two attested senses are reported and removed.
- **Sense inventory:** glosses from an unpublished Sinhala sense structure shared by D. Sumanathilaka, checked against Sinhala resource books.
- **Sentence sources:** the Sinhala-1.5B corpus (Ranasinghe et al., 2025) and a Grade 10–11 Sinhala literature textbook (Educational Publications Department).
- **Sampling:** sentences are drawn at random, so each sense keeps its natural frequency in real text.
- **Annotation:** multiple annotators; agreement measured with Fleiss' kappa on a shared overlap set.
- **Evaluation:** WSD, plus Word-in-Context (WiC) built from the same gold labels.

## Components

| Component | Code | Details | Status |
|---|---|---|---|
| Textbook sentence extraction | `textbook_extraction.ipynb` | [docs/textbook_extraction.md](docs/textbook_extraction.md) | complete |
| Corpus sentence extraction | `Extraction_pipeline.ipynb` | [docs/corpus_extraction.md](docs/corpus_extraction.md) | complete |
| Annotation tool | `app/`, `scripts/` | [docs/annotation_tool.md](docs/annotation_tool.md) | complete |

## Repository conventions

- Branches: `main` (stable), `dev` (integration), `feature/*`, `docs/*`, `experiment/*`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/).
- Notebooks run in Google Colab; data lives in Google Drive and is not committed.

## Data and licences

- The textbook PDF is © Educational Publications Department (all rights reserved) and is not redistributed.
- The Sinhala-1.5B corpus combines sources under different licences (e.g. NSina CC BY-NC-SA 4.0). Extracted sentences are not committed.
- This repository contains code, notebooks and run metadata only.

## References

- Ranasinghe, T. et al. (2025). Sinhala Encoder-only Language Models and Evaluation. *ACL 2025*. https://aclanthology.org/2025.acl-long.422/
