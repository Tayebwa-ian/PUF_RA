# Ground-Truth Guidelines — Physical Attacks on PUFs Screening Study

This document defines how the **human-curated ground truth** must look so it
integrates *smoothly* with the evaluation pipeline. The dataset itself is built
by you and a second annotator (a human task) — this file only specifies the
**contract**: labels, format, and the exact file the ingest script expects.

> **Thesis framing:** we are *evaluating the tools* (LLM prompts / screening
> methods) used to build a Systematication-of-Knowledge (SoK) paper on physical
> attacks on PUFs. The ground truth is the gold standard those tools are
> measured against. The LLMs are screening aids under evaluation, not authors.

---

## 1. The three labels (locked)

Every paper is assigned exactly one of three classes:

| Label | Meaning | Inclusions | Exclusions |
|---|---|---|---|
| `in-scope` | A **physical attack** on a PUF | Side-channel analysis (power/DPA/SPA, EM, timing), fault injection (laser, voltage/clock glitch), invasive/semi-invasive (probing, FIB, depackaging, delayering), and papers whose **core contribution** is such an attack on a PUF. | Pure ML/modeling attacks (see below). |
| `out-of-scope` | **Not** a physical attack on a PUF | ML/modeling attacks (logistic regression, SVM, neural nets trained on CRPs), pure cryptography, non-PUF hardware, survey/position papers with no attack contribution. | Anything that *is* a physical attack. |
| `hybrid` | Combines a **physical side-channel** with **ML/modeling** | Works that use side-channel leakage **and** modeling (e.g. power/EM traces fed to an ML model to attack a PUF). Borderline by definition. | Pure physical-only or pure ML-only. |

**Critical scope rule:** *machine-learning / modeling attacks on PUFs are
explicitly OUT OF SCOPE*. They are logical, non-invasive CRP-based attacks, not
physical. A paper that only models a PUF from CRPs is `out-of-scope`, even if it
"breaks" the PUF. Only when side-channel measurement is involved does it become
`in-scope` or `hybrid`.

---

## 2. Stratification

Aim for **30–50 papers** covering all three classes, with deliberate
stratification:

- ~40% `in-scope` (clear physical attacks)
- ~40% `out-of-scope` (clear ML/modeling or unrelated)
- ~20% `hybrid` (the hard, borderline cases — these stress-test the tools)

Include a **second annotator** (you + one other person). Each annotator labels
the *same* set independently. The pipeline computes inter-rater agreement
(**Cohen's κ** for 2 annotators, **Fleiss' κ** for >2) and a consensus label.

---

## 3. Exact file format (the integration contract)

The ingest script reads a single CSV at
`data/ground_truth/ground_truth.csv` with **this exact header**:

```csv
paper_id,annotator_id,label,confidence,rationale
```

| Column | Type | Notes |
|---|---|---|
| `paper_id` | integer | The `id` of the paper **already in the database** after import. (See §5 for how to get these.) |
| `annotator_id` | string | A stable id per human, e.g. `A` and `B`. |
| `label` | enum | One of `in-scope`, `out-of-scope`, `hybrid` (case-sensitive, lowercase). |
| `confidence` | float 0–1 | Annotator's confidence in the label. |
| `rationale` | string | One-line justification (helps the second annotator and future review). |

One row = one annotator's label for one paper. The same `paper_id` appears once
per `annotator_id`. Example:

```csv
paper_id,annotator_id,label,confidence,rationale
12,A,in-scope,0.95,"Power-analysis attack on arbiter PUF via EM traces."
13,A,out-of-scope,0.9,"LR model trained on 10k CRPs; no physical measurement."
14,A,hybrid,0.8,"EM leakage features fed to an MLP to recover PUF responses."
12,B,in-scope,0.9,"Clear side-channel attack."
13,B,out-of-scope,0.85,"Pure modeling attack, CRP-based."
14,B,hybrid,0.75,"Side-channel + ML combination."
```

Optional columns (ignored if absent): `notes`.

**Validation the script enforces:** `paper_id` must exist; `label` must be one
of the three enums; `confidence` in [0,1]; duplicate `(paper_id, annotator_id)`
is upserted (last write wins).

---

## 4. Where the file lives

```
data/ground_truth/
├── ground_truth.csv        # <-- the file you fill in (this contract)
└── ground_truth_template.csv  # starter with header + examples
```

Ingest with:

```bash
puf eval groundtruth data/ground_truth/ground_truth.csv
```

This writes `ground_truth` (per-annotator rows) and `ground_truth_consensus`
(agreed labels + agreement statistics) into the database.

---

## 5. Getting the `paper_id`s to label

The papers must first be imported (see README quick start). To produce a
fill-in sheet with the right IDs and titles, run:

```bash
puf eval export-papers data/ground_truth/ground_truth_template.csv
```

This writes one row per paper (with `paper_id`, `doi`, `title`) and empty
`annotator_id`/`label`/`confidence`/`rationale` columns for you to complete.
Then copy/split into `ground_truth.csv` for each annotator.

---

## 5b. The curated working set (`ground_truth_template.csv`)

`data/ground_truth/ground_truth_template.csv` is the **curated working set** used for
human labeling. Unlike the strict ingest contract `ground_truth.csv` (sec. 3:
`paper_id,annotator_id,label,confidence,rationale`), this file is a **SUPERSET**: it
carries the human-friendly, metadata-enriched columns

```csv
paper_id,DOI,Title,Abstract,annotator_id,label,confidence,rationale
```

so a second rater can read each paper's DOI, Title and full Abstract inline while
labeling. It currently holds **50 papers** (each appearing twice -- once per
annotator), giving a balanced, three-class distribution:

* **17 `in-scope`** (physical attack on a PUF)
* **17 `out-of-scope`** (e.g. ML / modeling attacks)
* **16 `hybrid`** (side-channel + ML)

**Annotator A is pre-filled** (label, confidence, rationale already entered for all 50
papers); **annotator B is left entirely blank** for a second rater to complete
independently (do not discuss labels beforehand -- see sec. 6). To ingest, split the
two annotators into the strict `ground_truth.csv` contract (keep only
`paper_id,annotator_id,label,confidence,rationale`), or label B and then run
`puf eval groundtruth` on the resulting file (see sec. 4). The extra `DOI` / `Title` /
`Abstract` columns are ignored by the ingest script (it only reads the sec. 3 columns).

---

## 6. Do NOT

- Do not change the three label names.
- Do not label on title alone when the abstract is available — read the abstract.
- Do not discuss labels with the co-annotator before both have submitted
  (to keep inter-rater agreement meaningful).
- Do not include papers you cannot access the abstract for.
