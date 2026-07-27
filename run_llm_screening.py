#!/usr/bin/env python3
"""DEPRECATED: Use `puf screen` from the CLI instead.

This script is retained for backward compatibility only.
It will be removed in a future release.
See cli/screen.py for the replacement.
"""

import warnings
warnings.warn(
    "run_llm_screening.py is deprecated. Use 'puf screen' from the CLI.",
    DeprecationWarning,
    stacklevel=2,
)

# ... rest of original code ...
from typing import Any

import openai
import json
import sqlite3

import textwrap

import re
import html

########################################################3
## DATABASE FUNCTIONS
#

def get_human_labled_papers(conn: sqlite3.Connection) -> list[Any]:
    query = textwrap.dedent(f"""
        SELECT id, title, abstract, human_decision 
        FROM papers 
        WHERE human_decision IS NOT NULL ORDER BY id""")
    return conn.execute(query).fetchall()


def get_papers(conn: sqlite3.Connection, source_query_ids: list[int]):
    query = textwrap.dedent(f"""
        SELECT id, title, abstract 
        FROM papers 
        WHERE query_id IN ({','.join(map(str, source_query_ids))}) 
        ORDER BY id""")
    for paper_id, title, abstract in conn.execute(query):
        # Some abstracts in the database have html characters for ... some reason
        # We decided to do clean up at read, not at database write
        yield paper_id, html.unescape(title), html.unescape(abstract)


def insert_run(conn: sqlite3.Connection, model:str, prompt:str, misc:str) -> int:
    query = textwrap.dedent(f"""
        INSERT INTO runs (model, prompt_text, misc) 
        VALUES (?, ?, ?) 
        RETURNING id""")
    (run_id, ) = conn.execute(query, (model, prompt, misc)).fetchone()
    return run_id


def insert_decision(conn: sqlite3.Connection, run_id:int, paper_id:int, decision:str, criterion:str, justification:str, excerpt:str, excerpt_verified:bool, tokens_used:int) -> int:
    # Do not introduce any variables above this.
    # All parameters are taken into param_dict to generate the SQL query.
    param_dict = locals()
    param_dict.pop("conn")
    columns = ", ".join(param_dict.keys())
    placeholders = ", ".join("?" for _ in param_dict) 
    values = tuple(param_dict.values())  # Extract values in order
    query = textwrap.dedent(f"""
        INSERT INTO decisions ({columns})
        VALUES ({placeholders})
        RETURNING id
    """)
    (decision_id, ) = conn.execute(query, values).fetchone()
    return decision_id

#
##
########################################################3
## LLM Functions
#

def query_llm(client:openai.OpenAI, model_name:str, system_prompt:str, title:str, abstract:str):
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Title: '{title}'\nAbstract: '{abstract}'"}
        ],
        response_format={"type": "json_object"},
        timeout=30.0,
    )
    result = json.loads(response.choices[0].message.content)
    token_usage = response.usage.total_tokens
    return result, token_usage
#
##
########################################################3
## Verification Functions
#

ELISION = re.compile(r'\s*(?:\[\.\.\.\]|\.{3,}|…)\s*') # splits at [...] or ...
TRAILING_ELLIPSIS = re.compile(r'[\s.…]+$') # removes trailing ...
QUOTE_MAP = str.maketrans({
    '\u201c': '"', '\u201d': '"',  # " " left/right double
    '\u2018': "'", '\u2019': "'",  # ' ' left/right single
    '\u201e': '"', '\u201a': "'",  # „ ‚ low quotation marks (German)
    '\u00ab': '"', '\u00bb': '"',  # « » guillemets
}) # translates quotation marks

def normalize(s: str) -> str:
    s = s.replace('\u00a0', ' ').translate(QUOTE_MAP)
    return re.sub(r'\s+', ' ', s).strip()

def verify_excerpt(excerpt: str, abstract: str) -> bool:
    excerpt = TRAILING_ELLIPSIS.sub('', excerpt)
    norm_abstract = normalize(abstract).lower()
    pos = 0
    for frag in ELISION.split(excerpt):
        frag = normalize(frag).lower()
        if not frag:
            continue
        idx = norm_abstract.find(frag, pos)
        if idx == -1:
            return False
        pos = idx + len(frag)
    return True

#
##
########################################################3
## Main
#

if __name__ == "__main__":
    DB_PATH = "results.db"
    API_KEY_FILE = "api_key_innkube.txt"
    SYSTEM_PROMPT_FILE = "system_prompt_v2.txt"
    MODELS = [
        "qwen35-397b", # reasoning -> overthinks stuff
        "qwen3-next-80b-a3b-instruct" # non reasoning -> more instruction-based
    ]
    QUERY_IDS = [3,4]
    MAX_RETRIES_PER_PAPER = 3

    model = MODELS[1]
    api_key = Path(API_KEY_FILE).read_text(encoding="utf-8").strip()
    system_prompt = Path(SYSTEM_PROMPT_FILE).read_text(encoding="utf-8").strip()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        with openai.OpenAI(
            api_key=api_key,
            base_url="https://llms.innkube.fim.uni-passau.de" 
        ) as client:
            ## Evaluation set:
            #mismatched_responses = []
            #for paper in get_human_labled_papers(conn):
            #  id, title, abstract, human_decision = (paper[0], paper[1], paper[2], paper[3])
            #  res = query_llm(client, model, system_prompt, title, abstract)
            #  if EVALUATE:
            #    if res['decision'] != human_decision.lower():
            #      print(f"Paper id: {id} - Mismatch!")
            #      print(f"Response: {res}")
            #      mismatched_responses.append(res)
            #    else:
            #      print(f"Paper id: {id} - Match!")
            #print(f"Mismatches: {mismatched_responses}")
            #
            ## Run through papers
            run_id = insert_run(conn, model, system_prompt, "")
            print(f"Started run {run_id}.")
            for paper_id, title, abstract in get_papers(conn, QUERY_IDS):
                for attempt in range(MAX_RETRIES_PER_PAPER):
                    try:
                        res, tokens_used = query_llm(client, model, system_prompt, title, abstract)
                        print(f"Processed paper {paper_id} -- {title}")
                        excerpt = res['excerpt']
                        excerpt_verified = verify_excerpt(excerpt, abstract)
                        print(f"  - Decision: {res['decision']}, Excerpt Verified: {excerpt_verified}")
                        print(f"  - Tokens used: {tokens_used}")
                        decision_id = insert_decision(conn, run_id, paper_id, res['decision'], res['criterion'], res['justification'], excerpt, excerpt_verified, tokens_used)
                        break
                    except Exception as e:
                        print(f"  attempt {attempt+1}/{MAX_RETRIES_PER_PAPER} failed: {e}")
                else:
                    raise RuntimeError(f"paper {paper_id} failed after {MAX_RETRIES_PER_PAPER} attempts")
                conn.commit()
            print(f"Finished!")
                
    