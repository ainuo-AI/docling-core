# Validation evidence: fewer redundant line-splitter token counts

This accompanies the local optimization for [Docling #2510](https://github.com/docling-project/docling/issues/2510).
It supports a narrow result: fewer redundant tokenization calls, with equivalent output in the checked cases.
**A stable end-to-end speedup is not established, and this change should not close #2510.**

The reviewed fix is `7274b61450d995b759b9ed02d240c70403e7cd2e`, based on core
`a8aa9dce1a80bee78bed991eb18ecbaa11bd6947`. It changes only `line_chunker.py`.
The final license-header addition has the same AST as the candidate used for the earlier measurements.

## What was checked

- 78 existing line/hybrid chunker tests passed. `make check` and `make validate` exited successfully.
  The full type check reported 493 warnings; the same warnings were present, unchanged, on the baseline.
- 59 **constructed** cases used actual cl100k, o200k and Hugging Face MiniLM tokenizers through public APIs.
  Complete chunk text, per-chunk token counts, warning categories/messages and exception categories/messages matched.
  These are boundary checks, not real user reports or performance samples.
- The original public Hacker News attachment produced 2,674 chunks. Complete text, metadata, order,
  contextualized output and every token count matched across the checked baseline/candidate runs.
  The maximum was 256 tokens, with no over-budget chunks.
- The recorded profiles made 69,092 versus 41,235 calls to `count_tokens`: 27,857 fewer calls.
  All of that decrease came from the modified `LineBasedTokenChunker.chunk_text` caller
  (45,795 to 17,938); the other token-count callers made the same number of calls.
- An earlier isolated combination with the production hunk from core #932 also preserved all output
  relative to #932 alone. That hunk is not in this fix.

## Timing limitations, including the adverse results

The earlier profiles took **40.631 seconds for baseline and 54.431 seconds for the candidate**.
The slower profile performed an additional full-document scan in the unchanged serializer exclusion-cache path,
before the modified line splitter ran. This is an observed workload difference, not a proven explanation
for the whole timing increase. The old runs did not record identical harness bytes, a hash seed or a complete
dependency inventory. We do not discard the reversal as "noise" or attribute it to OS load without evidence.

A later comparison froze the harness, set `PYTHONHASHSEED=0`, and planned four separate processes in A-B-B-A order.
Imports, document loading, tokenizer setup and output validation were outside both timing intervals.
GC remained enabled, with `gc.collect()` before the timed chunking operation. The completed observations were:

| Run | Wall seconds | Process CPU seconds |
| --- | ---: | ---: |
| Baseline A1 | 27.722 | 27.344 |
| Candidate B1 | 24.433 | 24.188 |
| Candidate B2 | 30.851 | 29.797 |

The last baseline process never reached chunking. It was terminated in `import_chunker` after a long heartbeat gap.
The controller recorded about 935 seconds in that phase despite a configured 90-second limit; the cause is unknown.
This is **not a completed ABBA benchmark**, nor evidence of a chunking timeout. B2 was slower than A1 in both clocks.
The completed three runs have identical output/token vectors and matching recorded dependency inventories.
No additional runs were selected to replace the slower or failed observations.

The earlier two-per-version unprofiled medians happened to differ by 18.8%; that historical observation has been
withdrawn as a headline speedup claim. Neither fewer calls nor this incomplete sequence proves a stable latency reduction.

## Files

- [Input provenance](input-provenance.json): original attachment URL/hash, clean conversion source revisions and result hash.
- [Checks](checks.txt) and [warning comparison](check-comparison.json): captured check output and baseline equality.
- [Constructed cases](constructed-cases.json): all 59 case inputs and matched observations.
- [Chunk equivalence](chunk-equivalence.json): complete-output hashes, the full contextualized token-count vector and #932 combination evidence.
- [Profile comparison](profile-comparison.json) and [extra traversal](extra-traversal.json): normalized raw pstats/caller extracts, including the adverse timing.
- [Controlled attempt](controlled-attempt.json): all completed records, the failed fourth run, actual versus configured timeout and completeness status.
- [Manifest](manifest.json): exact fix/base identities and selected runtime versions. Installed core distribution metadata was 2.107.0;
  executed source modules were explicitly verified from the 2.107.1 checkout plus the patch.
- [Portable public-API replay](replay.py): a post-review convenience helper. It is not the original frozen measurement harness.

## Replaying the public API checks

Use an environment prepared for the corresponding core checkout with its `chunking` and `chunking-openai` extras.
The selected versions from the recorded Windows/Python 3.12.10 environment are in the manifest.
No model weights, credential files or machine-specific environment paths are included here.

Run the helper separately against baseline and fix checkouts:

```sh
python replay.py --source /path/to/core-checkout --cases constructed-cases.json --output cases-result.json
```

For the real case, download the [original attachment](https://github.com/user-attachments/files/23058884/hacker-news-discussion.html)
and first convert it through public `DocumentConverter().convert(...).document.save_as_json(...)`.
The source revisions and hashes used for conversion are recorded in `input-provenance.json`; keep the converted JSON
fixed between variants. Conversion or JSON serialization can differ on other versions, so check provenance rather
than silently treating a different input as the same experiment.

Then set `PYTHONHASHSEED=0` **before** launching Python and run:

```sh
PYTHONHASHSEED=0 python replay.py --source /path/to/core-checkout --document converted.json --expected chunk-equivalence.json --output chunk-result.json
```

In PowerShell, set `$env:PYTHONHASHSEED = '0'` before that Python command.
The document mode measures only `list(chunker.chunk(doc))`; its full output and token checks run afterward.
Do not use these few local observations as a statistical or cross-machine performance guarantee.

The evidence does not cover Python 3.10 runtime execution, Linux/macOS, the full Haiku RAG indexing/embedding flow,
all third-party tokenizer implementations or upstream CI. Python 3.10 syntax was checked. The actual code change
does not assume token counts are additive and does not cache counts across different strings.
