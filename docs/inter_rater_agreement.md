# Inter-Rater Agreement Analysis for Ground Truth Labeling

## Approach Explanation

The ground truth labeling process for this study involves human annotators (the primary researcher and a co-annotator) classifying a stratified set of 50 papers. The papers are distributed across three mutually exclusive categories:

- `in-scope`: Papers describing physical attacks on Physical Unclonable Functions (PUFs), including side-channel attacks (power/EM/timing analysis), fault injection (laser/voltage/clock glitching), and invasive/semi-invasive attacks (probing, FIB, depackaging, delayering).
- `out-of-scope`: Papers describing non-physical attacks, such as machine learning/modeling attacks, which are logical, non-invasive CRP-based attacks and explicitly outside the scope of physical attacks.
- `hybrid`: Papers describing hybrid attacks that combine side-channel techniques with machine learning.

To assess the reliability of the labeling process, inter-rater agreement is measured using Cohen's Kappa (κ), a statistical metric that evaluates the level of agreement between two raters classifying items into mutually exclusive categories.

## Justifications for Using Cohen's Kappa

Cohen's Kappa is the selected metric for evaluating inter-rater agreement in this study for the following reasons:

1. **Accounts for Chance Agreement**: Cohen's Kappa provides a more robust measure of inter-rater agreement than simple percent agreement by accounting for the probability of chance agreement. This ensures that the reported agreement reflects true consensus rather than random alignment.

2. **Appropriate for Categorical Data**: The metric is specifically designed for categorical (nominal) data with mutually exclusive classes, making it ideal for the `in-scope`, `out-of-scope`, and `hybrid` classification scheme.

3. **Standard Metric in Research**: Cohen's Kappa is the standard metric for evaluating inter-rater reliability in machine learning and empirical research contexts, particularly for classification tasks with two raters assigning items to discrete categories.

4. **Facilitates Comparison**: Using Cohen's Kappa allows for meaningful comparison of labeling reliability across different labeling studies, datasets, and methodological approaches in the literature.

## Results and Consensus Distribution

The final ground truth dataset consists of 50 papers with the following consensus distribution:

- `in-scope`: 16 papers
- `out-of-scope`: 17 papers
- `hybrid`: 17 papers

**Total**: 50 papers

The aggregated labels are stored in the `ground_truth_consensus` database table. This table contains the `consensus_label` column, which records the final classification as 'in-scope', 'out-of-scope', or 'hybrid' when the annotators agree, or 'disagree' when the annotators do not reach a consensus. Papers marked as 'disagree' are subsequently resolved through discussion to ensure a definitive ground truth label for each paper in the evaluation pipeline.
