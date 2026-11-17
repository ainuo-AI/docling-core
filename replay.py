"""Replay the public-API checks in an already prepared Docling environment.

This portable helper was prepared after the review. It is not the original
frozen measurement harness and does not reproduce a claimed speedup.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
import time
import warnings
from pathlib import Path


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def qualified_name(kind: type) -> str:
    return f"{kind.__module__}.{kind.__name__}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="The core checkout to execute")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path, help="Replay constructed-cases.json, outside any performance comparison")
    parser.add_argument("--document", type=Path, help="An existing DoclingDocument JSON for the original attachment")
    parser.add_argument("--expected", type=Path, help="Expected chunk-equivalence.json for document mode")
    args = parser.parse_args()
    if (args.cases is None) == (args.document is None):
        parser.error("Select exactly one of --cases and --document")
    if args.document is not None and os.environ.get("PYTHONHASHSEED") != "0":
        parser.error("Set PYTHONHASHSEED=0 before launching Python for document measurements")
    source = args.source.resolve(strict=True)
    sys.path.insert(0, str(source))

    # Imports are intentionally deferred until the requested checkout is on
    # sys.path. All imports and setup are outside the chunk timing interval.
    import tiktoken

    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
    from docling_core.transforms.chunker.line_chunker import LineBasedTokenChunker
    from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
    from docling_core.types.doc import DoclingDocument
    import docling_core.transforms.chunker.line_chunker as line_module

    actual_module = Path(line_module.__file__).resolve()
    expected_module = source / "docling_core/transforms/chunker/line_chunker.py"
    if actual_module != expected_module:
        raise RuntimeError("Python imported a different core checkout")
    source_hash = hashlib.sha256(actual_module.read_bytes()).hexdigest()

    if args.cases is not None:
        from transformers import AutoTokenizer

        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

        original = json.loads(args.cases.read_text(encoding="utf-8"))
        hf = AutoTokenizer.from_pretrained(
            "sentence-transformers/all-MiniLM-L6-v2",
            revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
            trust_remote_code=False,
        )
        encodings = {
            "openai-cl100k": tiktoken.get_encoding("cl100k_base"),
            "openai-o200k": tiktoken.get_encoding("o200k_base"),
        }
        records = []
        mismatches = []
        for expected in original["cases"]:
            spec = expected["case"]
            backend = spec["id"].split("/")[0]
            tokenizer = (HuggingFaceTokenizer(tokenizer=hf, max_tokens=8192)
                         if backend == "hf-minilm" else
                         OpenAITokenizer(tokenizer=encodings[backend], max_tokens=8192))
            chunks = tokens = error = None
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                try:
                    chunker = LineBasedTokenChunker(
                        tokenizer=tokenizer, prefix=spec["prefix"], max_tokens_override=spec["limit"],
                        omit_prefix_on_overflow=spec["omit"]
                    )
                    chunks = chunker.chunk_text(spec["lines"])
                    tokens = [tokenizer.count_tokens(text) for text in chunks]
                except Exception as caught:
                    # This is a test-result boundary, including expected backend
                    # errors. Exceptions are preserved, not treated as success.
                    error = {"type": qualified_name(type(caught)), "message": str(caught)}
            record = {"event": "case-result", "case": spec, "chunks": chunks, "tokens": tokens,
                      "error": error, "warnings": [{"type": qualified_name(w.category), "message": str(w.message)} for w in captured]}
            records.append(record)
            if record != expected:
                mismatches.append(spec["id"])
        result = {"mode": "constructed-cases", "source_sha256": source_hash,
                  "cases": len(records), "records": records, "mismatch_ids": mismatches,
                  "passed": not mismatches, "performance_claim": False}
    else:
        doc = DoclingDocument.load_from_json(args.document)
        tokenizer = OpenAITokenizer(tokenizer=tiktoken.encoding_for_model("gpt-4o"), max_tokens=256)
        chunker = HybridChunker(tokenizer=tokenizer)
        gc.collect()
        started_cpu = time.process_time()
        started_wall = time.perf_counter()
        chunks = list(chunker.chunk(doc))
        wall = time.perf_counter() - started_wall
        cpu = time.process_time() - started_cpu
        # Output serialization and all token checks are deliberately untimed.
        chunk_hash = hashlib.sha256()
        context_hash = hashlib.sha256()
        token_counts = []
        for chunk in chunks:
            chunk_hash.update(canonical(chunk.model_dump(mode="json")))
            context = chunker.contextualize(chunk)
            context_hash.update((json.dumps(context, ensure_ascii=False) + "\n").encode("utf-8"))
            token_counts.append(tokenizer.count_tokens(context))
        result = {"mode": "document", "source_sha256": source_hash,
                  "input_sha256": hashlib.sha256(args.document.read_bytes()).hexdigest(),
                  "hash_seed": os.environ.get("PYTHONHASHSEED"), "gc_enabled": gc.isenabled(),
                  "wall_seconds": wall, "cpu_seconds": cpu, "chunks": len(chunks),
                  "canonical_sha256": chunk_hash.hexdigest(), "contextualized_sha256": context_hash.hexdigest(),
                  "token_counts": token_counts, "max_token_count": max(token_counts, default=0),
                  "over_budget_count": sum(value > 256 for value in token_counts)}
        if args.expected is not None:
            expected = json.loads(args.expected.read_text(encoding="utf-8"))
            fields = ("chunks", "canonical_sha256", "contextualized_sha256", "token_counts", "max_token_count", "over_budget_count")
            result["mismatch_fields"] = [key for key in fields if result[key] != expected[key]]
            result["passed"] = not result["mismatch_fields"]
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in ("records", "token_counts")}, ensure_ascii=False))
    if result.get("passed") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
