"""Field-level accuracy benchmark.

Takes references with known-correct ground truth, applies systematic
perturbations (the error classes real bibliographies contain), runs the full
pipeline, and scores whether each perturbed field was corrected back to truth.

Run (live: network + configured LLM):

    REFASSIST_RUN_LIVE=1 IEEE_REF_LLM=azure PYTHONPATH=src \
        python3 tests/benchmark_accuracy.py

The score to watch is per-field recovery rate. Gate changes in select_best /
apply_corrections should be judged by their effect on this number, not
anecdotes.
"""
import asyncio
import os
import re
import sys
import time

# Ground truth: (correct reference string, {field: expected value after correction})
GROUND_TRUTH = [
    {
        "name": "nature-journal",
        "ref": 'Y. LeCun, Y. Bengio, and G. Hinton, "Deep learning," Nature, vol. 521, no. 7553, pp. 436-444, 2015.',
        "truth": {"year": "2015", "volume": "521", "pages": "436-444", "doi": "10.1038/nature14539"},
    },
    {
        "name": "ijcv-journal",
        "ref": 'O. Russakovsky, J. Deng, H. Su, J. Krause, S. Satheesh, S. Ma, Z. Huang, A. Karpathy, A. Khosla, M. Bernstein, A. C. Berg, and L. Fei-Fei, "ImageNet Large Scale Visual Recognition Challenge," International Journal of Computer Vision, vol. 115, no. 3, pp. 211-252, 2015.',
        "truth": {"year": "2015", "volume": "115", "pages": "211-252", "doi": "10.1007/s11263-015-0816-y"},
    },
    {
        "name": "cvpr-conference",
        "ref": 'K. He, X. Zhang, S. Ren, and J. Sun, "Deep residual learning for image recognition," in Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition, 2016, pp. 770-778.',
        "truth": {"year": "2016", "pages": "770-778", "doi": "10.1109/cvpr.2016.90"},
    },
    {
        "name": "neurips-conference",
        "ref": 'A. Vaswani, N. Shazeer, N. Parmar, J. Uszkoreit, L. Jones, A. N. Gomez, L. Kaiser, and I. Polosukhin, "Attention is all you need," in Advances in Neural Information Processing Systems, 2017, pp. 5998-6008.',
        "truth": {"year": "2017"},
    },
    {
        "name": "mit-press-book",
        "ref": "I. Goodfellow, Y. Bengio, and A. Courville, Deep Learning. Cambridge, MA: MIT Press, 2016.",
        "truth": {"year": "2016"},
    },
    {
        "name": "naacl-conference",
        "ref": 'J. Devlin, M.-W. Chang, K. Lee, and K. Toutanova, "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding," in Proceedings of NAACL-HLT, 2019, pp. 4171-4186.',
        "truth": {"year": "2019", "doi": "10.18653/v1/n19-1423"},
    },
    {
        "name": "arxiv-preprint",
        "ref": 'D. P. Kingma and J. Ba, "Adam: A Method for Stochastic Optimization," arXiv:1412.6980, 2014.',
        "truth": {"year": "2014"},
    },
    {
        "name": "lancet-retracted",
        "ref": 'A. J. Wakefield et al., "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children," The Lancet, vol. 351, no. 9103, pp. 637-641, 1998.',
        "truth": {"year": "1998", "volume": "351", "doi": "10.1016/s0140-6736(97)11096-0", "retracted": True},
    },
]


# ---------------------------------------------------------------------------
# Perturbations — each returns (perturbed_ref, perturbed_field) or None if
# not applicable to this reference.
# ---------------------------------------------------------------------------

def perturb_year(ref, truth):
    y = truth.get("year")
    if not y or y not in ref:
        return None
    wrong = str(int(y) - 2)
    return ref.replace(y, wrong, 1), "year"

def perturb_volume(ref, truth):
    v = truth.get("volume")
    if not v:
        return None
    m = re.search(rf"vol\.\s*{re.escape(v)}", ref)
    if not m:
        return None
    return ref[:m.start()] + f"vol. {int(v) + 7}" + ref[m.end():], "volume"

def perturb_pages(ref, truth):
    p = truth.get("pages")
    if not p or f"pp. {p}" not in ref:
        return None
    start = p.split("-")[0]
    return ref.replace(f"pp. {p}", f"p. {start}", 1), "pages"

def perturb_title_typo(ref, truth):
    m = re.search(r'"([^"]{10,})"', ref)
    if not m:
        return None
    title = m.group(1)
    words = title.split()
    for i, w in enumerate(words):
        if len(w) >= 8 and w.isalpha():
            words[i] = w[: len(w) // 2] + w[len(w) // 2 + 1:]  # drop a middle letter
            return ref.replace(title, " ".join(words), 1), "title"
    return None

PERTURBATIONS = [perturb_year, perturb_volume, perturb_pages, perturb_title_typo]


def _norm(v):
    return str(v or "").replace("–", "-").replace("—", "-").strip().lower()


async def run_benchmark():
    from refassist.graphs import run_one

    cases = []
    for gt in GROUND_TRUTH:
        for p in PERTURBATIONS:
            out = p(gt["ref"], gt["truth"])
            if out:
                cases.append({"name": f"{gt['name']}/{out[1]}", "ref": out[0],
                              "truth": gt["truth"], "field": out[1]})

    print(f"Running {len(cases)} perturbed cases…\n")
    field_stats: dict = {}
    t0 = time.time()

    for case in cases:
        try:
            out = await run_one(case["ref"])
            ex = out.get("extracted") or {}
        except Exception as e:
            print(f"  ERROR {case['name']}: {e!r}")
            ex, out = {}, {}

        for fld, want in case["truth"].items():
            if fld == "retracted":
                got_ok = bool(out.get("retracted")) == bool(want)
            elif fld == "title":
                got_ok = _norm(want) in _norm(ex.get("title"))
            elif fld == "doi":
                got_ok = _norm(want) in _norm(ex.get("doi"))
            else:
                got_ok = _norm(ex.get(fld)) == _norm(want)
            key = f"{fld}{' (perturbed)' if fld == case['field'] else ''}"
            ok_n, tot = field_stats.get(key, (0, 0))
            field_stats[key] = (ok_n + int(got_ok), tot + 1)
            if fld == case["field"] and not got_ok:
                print(f"  MISS  {case['name']}: {fld} = {ex.get(fld)!r}, expected {want!r}")

    print(f"\n{'FIELD':28} {'RECOVERED':>10}")
    print("-" * 40)
    total_ok = total_n = 0
    for key in sorted(field_stats):
        ok_n, tot = field_stats[key]
        total_ok += ok_n
        total_n += tot
        print(f"{key:28} {ok_n:>5}/{tot:<4} {100 * ok_n / tot:5.1f}%")
    print("-" * 40)
    print(f"{'OVERALL':28} {total_ok:>5}/{total_n:<4} {100 * total_ok / total_n:5.1f}%")
    print(f"\n({len(cases)} pipeline runs in {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    if os.getenv("REFASSIST_RUN_LIVE", "").lower() not in ("1", "true", "yes"):
        sys.exit("Set REFASSIST_RUN_LIVE=1 (needs network + a configured LLM).")
    asyncio.run(run_benchmark())
