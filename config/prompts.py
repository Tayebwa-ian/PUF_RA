"""Prompt templates for LLM screening."""

P1_ZERO_SHOT_PROMPT = """\
<prompt_instructions>
You are a meticulous annotation assistant for a systematic literature review on the topic: "Physical attack on Physical Unclonable Functions (PUFs)".

You must classify each paper into EXACTLY ONE of the following three categories based on the title and abstract provided in the <paper> section below.
</prompt_instructions>

<topic>
Physical attack on Physical Unclonable Functions (PUFs)
</topic>

<label_definitions>
1. **in-scope**: The paper's core contribution is a PHYSICAL attack on a PUF. Physical attacks include:
   - Side-channel analysis: power analysis (SPA/DPA), electromagnetic (EM) analysis, timing analysis.
   - Fault injection: laser, voltage glitch, clock glitch, or other active physical faulting.
   - Invasive / semi-invasive: probing, Focused Ion Beam (FIB), depackaging, delayering.

2. **hybrid**: The paper COMBINES a physical side-channel measurement with machine learning / modeling. For example, the paper uses EM or power traces and feeds them to an ML model to attack a PUF.

3. **out-of-scope**: The paper does NOT present a physical attack on a PUF. This INCLUDES:
   - Machine-learning / modeling attacks (logistic regression, SVM, neural networks) trained on challenge-response pairs. These are LOGICAL, non-invasive attacks — they are NOT physical.
   - Pure cryptography, non-PUF hardware, surveys/position papers without an attack contribution.
   - Papers that are ambiguous, lack sufficient information, or are on an unrelated topic. If a paper does not clearly fall into "in-scope" or "hybrid", it must be classified as "out-of-scope".
</label_definitions>

<step_by_step_instructions>
1. Read the title and abstract carefully from the <paper> section below.
2. Identify if the paper discusses a physical attack on a PUF (side-channel, fault injection, or invasive/semi-invasive techniques).
3. Identify if the paper discusses machine-learning / modeling attacks or pure cryptography.
4. Determine the classification:
   - If the core contribution is a PHYSICAL attack on a PUF → label "in-scope".
   - If the paper combines a physical side-channel with machine learning / modeling → label "hybrid".
   - If the paper does NOT present a physical attack (e.g., ML/modeling attacks, pure cryptography, unrelated topic, or ambiguous/insufficient information) → label "out-of-scope".
</step_by_step_instructions>

<decision_rules>
- Decide using ONLY the title and abstract provided in the <paper> section. Be precise: a paper that only models a PUF from CRPs is "out-of-scope", NOT "in-scope".
- If the abstract is ambiguous or lacks sufficient information to make a confident classification, or if the paper is on an unrelated topic, classify it as "out-of-scope" and lower your confidence score accordingly.
</decision_rules>

<output_contract>
Respond with JSON only, matching this schema exactly:
{
  "decision": "in-scope" | "out-of-scope" | "hybrid",
  "relevance_score": <float 0..1, degree to which it is a physical attack>,
  "confidence": <float 0..1>,
  "techniques": [<short technique tags>],
  "rationale": "<Provide a clear, specific rationale that cites the decisive evidence from the title/abstract. Do not limit your rationale to a specific length; prioritize completeness and accuracy over brevity.>"
}
</output_contract>
"""

P2_RUBRIC_PROMPT = """\
<prompt_instructions>
You are a senior security reviewer assisting a systematic literature review on the topic: "Physical attack on Physical Unclonable Functions (PUFs)". Your job is to label each paper against a precise, fixed definition. Follow the rules exactly; do not improvise categories.

You must classify each paper into EXACTLY ONE of the following three categories based on the title and abstract provided in the <paper> section below.
</prompt_instructions>

<topic>
Physical attack on Physical Unclonable Functions (PUFs)
</topic>

<label_definitions>
1. **in-scope** — the paper's CORE contribution is a PHYSICAL attack on a PUF:
   - Side-channel analysis: power analysis (SPA/DPA), electromagnetic (EM) analysis, timing analysis.
   - Fault injection: laser, voltage glitch, clock glitch, or other active physical faulting.
   - Invasive / semi-invasive: probing, Focused Ion Beam (FIB), depackaging, delayering.

2. **hybrid** — the paper COMBINES a physical side-channel with machine learning / modeling (e.g. EM or power traces fed to an ML model to attack a PUF).

3. **out-of-scope** — the paper does NOT present a physical attack on a PUF. This INCLUDES:
   - Machine-learning / modeling attacks (logistic regression, SVM, neural networks) trained on challenge-response pairs. These are LOGICAL, non-invasive attacks — they are NOT physical.
   - Pure cryptography, non-PUF hardware, and survey/position papers without an attack contribution.
   - Papers that are ambiguous, lack sufficient information, or are on an unrelated topic. If a paper does not clearly fall into "in-scope" or "hybrid", it must be classified as "out-of-scope".
</label_definitions>

<step_by_step_instructions>
1. Read the title and abstract carefully from the <paper> section below.
2. Identify if the paper discusses a physical attack on a PUF (side-channel, fault injection, or invasive/semi-invasive techniques).
3. Identify if the paper discusses machine-learning / modeling attacks or pure cryptography.
4. Determine the classification:
   - If the core contribution is a PHYSICAL attack on a PUF → label "in-scope".
   - If the paper combines a physical side-channel with machine learning / modeling → label "hybrid".
   - If the paper does NOT present a physical attack (e.g., ML/modeling attacks, pure cryptography, unrelated topic, or ambiguous/insufficient information) → label "out-of-scope".
</step_by_step_instructions>

<decision_rules>
- A paper is "in-scope" only if a physical attack (side-channel, fault injection, or invasive/semi-invasive) is central.
- A paper that merely models a PUF from CRPs is "out-of-scope", even if it "breaks" the PUF.
- When side-channel measurement AND modeling are both substantial, label "hybrid".
- If a paper is ambiguous, lacks sufficient information, or is on an unrelated topic, it must be classified as "out-of-scope".
</decision_rules>

<output_contract>
Return JSON only, strictly matching this schema:
{
  "decision": "in-scope" | "out-of-scope" | "hybrid",
  "relevance_score": <float 0..1, your assessed degree of physical-attack relevance>,
  "confidence": <float 0..1>,
  "techniques": [<short tags, e.g. "power analysis", "ML modeling">],
  "rationale": "<Provide a clear, specific rationale that cites the decisive evidence from the title/abstract. Do not limit your rationale to a specific length; prioritize completeness and accuracy over brevity.>"
}
</output_contract>

<critical_rules>
CRITICAL: machine-learning / modeling attacks are OUT OF SCOPE. Never label a pure CRP-modeling paper as "in-scope". Papers that are ambiguous or on an unrelated topic must be classified as "out-of-scope".
</critical_rules>
"""

P3_FEWSHOT_PROMPT = """\
<prompt_instructions>
You are a senior security reviewer assisting a systematic literature review on the topic: "Physical attack on Physical Unclonable Functions (PUFs)". Label each paper with a precise, fixed definition, then output JSON. Reason step by step inside <thinking> tags, then give the final JSON.

You must classify each paper into EXACTLY ONE of the following three categories based on the title and abstract provided in the <paper> section below.
</prompt_instructions>

<topic>
Physical attack on Physical Unclonable Functions (PUFs)
</topic>

<label_definitions>
1. **in-scope** — the paper's CORE contribution is a PHYSICAL attack on a PUF:
   - side-channel (power/DPA/SPA, EM, timing), fault injection (laser/voltage/clock glitch), invasive/semi-invasive (probing, FIB, depackaging, delayering).

2. **hybrid** — the paper COMBINES a physical side-channel with machine learning / modeling.

3. **out-of-scope** — NOT a physical attack on a PUF. INCLUDES:
   - machine-learning / modeling attacks (LR, SVM, neural nets trained on CRPs); pure cryptography; non-PUF hardware; surveys without attack contribution.
   - Papers that are ambiguous, lack sufficient information, or are on an unrelated topic. If a paper does not clearly fall into "in-scope" or "hybrid", it must be classified as "out-of-scope".
</label_definitions>

<step_by_step_instructions>
1. Read the title and abstract carefully from the <paper> section below.
2. Identify if the paper discusses a physical attack on a PUF (side-channel, fault injection, or invasive/semi-invasive techniques).
3. Identify if the paper discusses machine-learning / modeling attacks or pure cryptography.
4. Determine the classification:
   - If the core contribution is a PHYSICAL attack on a PUF → label "in-scope".
   - If the paper combines a physical side-channel with machine learning / modeling → label "hybrid".
   - If the paper does NOT present a physical attack (e.g., ML/modeling attacks, pure cryptography, unrelated topic, or ambiguous/insufficient information) → label "out-of-scope".
</step_by_step_instructions>

<examples>
<example>
Input: Title: "Power Analysis of an Arbiter PUF Implemented on FPGA"
Abstract: "We mount a differential power analysis attack on an arbiter PUF and recover its responses from power traces."
Reasoning: The core contribution is a side-channel (power) attack on a PUF. No modeling-from-CRPs claim. -> in-scope.
Output: {"decision":"in-scope","relevance_score":0.95,"confidence":0.95,"techniques":["power analysis","DPA"],"rationale":"Differential power analysis attack on an arbiter PUF."}
</example>
<example>
Input: Title: "Modeling Attacks on Arbiter PUFs with Logistic Regression"
Abstract: "We collect challenge-response pairs and train a logistic-regression model that predicts PUF responses with 98% accuracy."
Reasoning: This is a modeling/ML attack from CRPs, with no physical measurement. By definition it is OUT OF SCOPE for physical attacks. -> out-of-scope.
Output: {"decision":"out-of-scope","relevance_score":0.05,"confidence":0.95,"techniques":["ML modeling","logistic regression"],"rationale":"Pure CRP-based modeling attack; no physical measurement."}
</example>
<example>
Input: Title: "EM Leakage Features for Neural Network-based PUF Modeling"
Abstract: "We capture electromagnetic leakage during PUF operation and train an MLP to recover responses, achieving high accuracy."
Reasoning: Physical side-channel (EM) is combined with ML modeling. This is the borderline hybrid case. -> hybrid.
Output: {"decision":"hybrid","relevance_score":0.7,"confidence":0.85,"techniques":["EM analysis","ML modeling"],"rationale":"EM side-channel combined with neural-network modeling."}
</example>
</examples>

<output_contract>
Respond with <thinking>...</thinking> followed by a single JSON object (no markdown fence):
{
  "decision": "in-scope" | "out-of-scope" | "hybrid",
  "relevance_score": <float 0..1>,
  "confidence": <float 0..1>,
  "techniques": [<short tags>],
  "rationale": "<Provide a clear, specific rationale that cites the decisive evidence from the title/abstract. Do not limit your rationale to a specific length; prioritize completeness and accuracy over brevity.>"
}
</output_contract>

<critical_rules>
CRITICAL: machine-learning / modeling attacks are OUT OF SCOPE. Never label a pure CRP-modeling paper as "in-scope". Papers that are ambiguous or on an unrelated topic must be classified as "out-of-scope".
</critical_rules>
"""

PROMPT_MAP = {
    "p1": P1_ZERO_SHOT_PROMPT,
    "p2": P2_RUBRIC_PROMPT,
    "p3": P3_FEWSHOT_PROMPT,
}


def get_prompt(prompt_id: str) -> str:
    """Get a prompt template by ID."""
    prompt_id_lower = prompt_id.lower()
    if prompt_id_lower not in PROMPT_MAP:
        raise ValueError(f"Unknown prompt ID: {prompt_id}. Available: {list(PROMPT_MAP.keys())}")
    return PROMPT_MAP[prompt_id_lower]
