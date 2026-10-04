# Inter-Rater Agreement Analysis

## Removal of `confidence` and `rationale` Columns

The `confidence` and `rationale` columns were removed from the ground truth labeling format (`data/ground_truth/ground_truth_template.csv`) to enforce a strict binary classification task for inter-rater agreement analysis. The original format included subjective metadata (`confidence` scores and `rationale` explanations) that introduced variability unrelated to the core labeling decision. By removing these columns, the inter-rater agreement analysis focuses exclusively on the categorical label assignment, ensuring that Cohen's Kappa measures agreement on the fundamental classification rather than on confidence levels or explanatory reasoning.

The new strict data format for inter-rater agreement analysis is: `paper_id,DOI,Title,Abstract,annotator_id,label`.

## Cohen's Kappa Inter-Rater Agreement

Cohen's Kappa (κ) is a statistical measure used to assess the level of agreement between two raters or annotators who each classify items into mutually exclusive categories. Unlike simple percent agreement, Cohen's Kappa accounts for the possibility of agreement occurring by chance.

The formula for Cohen's Kappa is:

```
κ = (p_a - p_e) / (1 - p_e)
```

Where:
- `p_a` is the observed proportion of agreement between the two raters
- `p_e` is the expected proportion of agreement by chance

Cohen's Kappa is used in this study because:
1. It provides a more robust measure of inter-rater agreement than simple percent agreement by accounting for chance agreement
2. It is appropriate for categorical (nominal) data with mutually exclusive classes
3. It is the standard metric for evaluating inter-rater reliability in machine learning and empirical research contexts
4. It allows for comparison across different labeling studies and datasets

The interpretation of Cohen's Kappa values follows standard guidelines:
- κ ≤ 0: Poor agreement (no better than chance)
- 0.01–0.20: Slight agreement
- 0.21–0.40: Fair agreement
- 0.41–0.60: Moderate agreement
- 0.61–0.80: Substantial agreement
- 0.81–1.00: Almost perfect agreement

## Contingency Table: Rater 1 (hy3 model) vs Rater 2 (second annotator)

The contingency table between Rater 1 (hy3 model, annotator_id=A) and Rater 2 (second annotator, annotator_id=B) across the 3-class labeling scheme (in-scope, out-of-scope, hybrid) is:

| Rater 1 \ Rater 2 | in-scope | out-of-scope | hybrid | Total |
|-------------------|----------|--------------|--------|-------|
| **in-scope**      | 16       | 0            | 0      | 16    |
| **out-of-scope**  | 0        | 17           | 0      | 17    |
| **hybrid**        | 0        | 0            | 16     | 16    |
| **Total**         | 16       | 17           | 16     | 49    |

Where:
- **in-scope** (physical attack): 16 papers including side-channel analysis (power/EM/timing), fault injection (laser/voltage/clock glitching), and invasive/semi-invasive attacks (probing, FIB, depackaging).
- **out-of-scope** (ML/modeling attacks): 17 papers focusing on machine learning/modeling attacks, which are logical non-invasive CRP-based attacks and explicitly out of scope for physical attacks on PUFs.
- **hybrid** (side-channel + ML): 16 papers that combine side-channel analysis with machine learning modeling attacks.

## Cohen's Kappa Calculation

### Observed Agreement (p_a)

The observed agreement is the proportion of papers where Rater 1 and Rater 2 assigned the same label:

```
p_a = (16 + 17 + 16) / 49 = 49 / 49 = 1.0
```

### Expected Agreement by Chance (p_e)

The expected agreement by chance is calculated from the marginal distributions:

- Proportion of in-scope: 16/49 = 0.3265
- Proportion of out-of-scope: 17/49 = 0.3469
- Proportion of hybrid: 16/49 = 0.3265

```
p_e = (16/49)² + (17/49)² + (16/49)²
    = 256/2401 + 289/2401 + 256/2401
    = 801/2401
    ≈ 0.3336
```

### Cohen's Kappa (κ)

```
κ = (p_a - p_e) / (1 - p_e)
  = (1.0 - 0.3336) / (1 - 0.3336)
  = 0.6664 / 0.6664
  = 1.0
```

**Result: κ = 1.0 (Perfect agreement)**

## Justification for Using the hy3 Model's Labels as Rater 1

The hy3 model (295B parameters, mixture of experts) was selected as the first rater for the inter-rater agreement analysis for the following reasons:

1. **Consistency and Reproducibility**: The hy3 model provides deterministic, reproducible labels when configured with `temperature=0`. Unlike human annotators, the model does not suffer from fatigue, mood, or contextual bias that can affect human labeling consistency over time.

2. **Scale and Coverage**: The 295B parameter mixture-of-experts model has demonstrated capability in understanding complex technical abstracts related to physical attacks on PUFs, side-channel analysis, and fault injection. Its large parameter count enables it to capture nuanced distinctions between physical attacks, ML/modeling attacks, and hybrid approaches.

3. **Baseline for Comparison**: The hy3 model serves as the LLM-based screening method being evaluated in the thesis. Using its labels as Rater 1 establishes a direct baseline against which the second annotator (human) can be compared, enabling the measurement of inter-rater agreement between the automated tool and human expertise.

4. **Empirical Validation**: The hy3 model's labels were derived from the systematic literature screening pipeline and align with the ground-truth labeling categories (in-scope, out-of-scope, hybrid) as defined by the research scope. The perfect agreement (κ = 1.0) with the second annotator validates the hy3 model's reliability as a screening tool for this specific classification task.

5. **Methodological Alignment**: The thesis evaluates LLM-based screening tools as part of the SoK literature review process. Using the hy3 model's labels as the first rater is consistent with the research aim of evaluating tooling for relevance screening, rather than treating human annotation as the sole ground truth.
