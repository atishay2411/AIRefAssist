"""Live end-to-end corruption tests.

Feeds references with intentionally planted errors through the full pipeline
(real metadata lookups + configured LLM) and asserts the errors get corrected.

Opt-in — requires network and a configured LLM provider:

    REFASSIST_RUN_LIVE=1 IEEE_REF_LLM=azure pytest tests/test_live_corrections.py -v
"""
import asyncio
import os

import pytest

RUN_LIVE = os.getenv("REFASSIST_RUN_LIVE", "").lower() in ("1", "true", "yes")
pytestmark = pytest.mark.skipif(
    not RUN_LIVE, reason="live test: set REFASSIST_RUN_LIVE=1 (needs network + LLM)"
)


def _norm(s):
    return str(s or "").replace("–", "-").replace("—", "-").strip().lower()


def _run(ref: str) -> dict:
    from refassist.graphs import run_one
    return asyncio.run(run_one(ref))


def test_wrong_year_conference_corrected():
    out = _run('A. Vaswani, N. Shazeer, N. Parmar, J. Uszkoreit, L. Jones, '
               'A. N. Gomez, L. Kaiser, and I. Polosukhin, "Attention is all you '
               'need," in Advances in Neural Information Processing Systems, 2015, pp. 5998-6008.')
    assert out["extracted"].get("year") == "2017"


def test_misspelled_title_corrected_and_doi_found():
    out = _run('Y. LeCun, Y. Bengio, and G. Hinton, "Deep learnng," Nature, '
               'vol. 521, no. 7553, pp. 436-444, 2015.')
    assert "deep learning" in _norm(out["extracted"].get("title"))
    assert "10.1038/nature14539" in _norm(out["extracted"].get("doi"))


def test_wrong_volume_corrected():
    out = _run('O. Russakovsky, J. Deng, H. Su, J. Krause, S. Satheesh, S. Ma, '
               'Z. Huang, A. Karpathy, A. Khosla, M. Bernstein, A. C. Berg, and '
               'L. Fei-Fei, "ImageNet Large Scale Visual Recognition Challenge," '
               'International Journal of Computer Vision, vol. 99, no. 3, pp. 211-252, 2015.')
    assert out["extracted"].get("volume") == "115"


def test_book_wrong_year_corrected():
    out = _run("I. Goodfellow, Y. Bengio, and A. Courville, Deep Learning. "
               "Cambridge, MA: MIT Press, 2018.")
    assert out["extracted"].get("year") == "2016"
    assert (out.get("type") or "").lower() == "book"


def test_single_page_expanded_to_range():
    out = _run('K. He, X. Zhang, S. Ren, and J. Sun, "Deep residual learning '
               'for image recognition," in Proceedings of the IEEE Conference on '
               'Computer Vision and Pattern Recognition, 2016, p. 770.')
    assert _norm(out["extracted"].get("pages")) == "770-778"


def test_non_reference_rejected():
    out = _run("What is the weather like in Paris today?")
    assert out["report_data"]["status"] == "rejected"
    assert not (out.get("formatted") or "").strip()


def test_retracted_article_flagged():
    # Wakefield 1998 (The Lancet) — retracted in 2010.
    out = _run('A. J. Wakefield et al., "Ileal-lymphoid-nodular hyperplasia, '
               'non-specific colitis, and pervasive developmental disorder in '
               'children," The Lancet, vol. 351, no. 9103, pp. 637-641, 1998.')
    assert out.get("retracted") is True
    assert out["report_data"]["retracted"] is True
    assert any("RETRACT" in w.upper() for w in out["report_data"]["warnings"])


def test_normal_article_not_flagged_retracted():
    out = _run('Y. LeCun, Y. Bengio, and G. Hinton, "Deep learning," Nature, '
               'vol. 521, no. 7553, pp. 436-444, 2015.')
    assert not out.get("retracted")
    assert (out.get("formatted") or "").strip(), "expected a formatted reference"
