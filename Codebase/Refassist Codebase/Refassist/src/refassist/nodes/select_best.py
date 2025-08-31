from ..state import PipelineState
from ..tools.scoring import score_candidate, is_trustworthy_match
from ..tools.utils import normalize_text, token_similarity, authors_to_list

def normalize_author_name(author: str) -> str:
    """Normalize author name by keeping initials and surname, removing extra spaces, and converting to lowercase."""
    parts = author.strip().split()
    if not parts:
        return ""
    # Keep initials (e.g., 'J.' or 'J') and surname, ignore invalid entries like 'et al.'
    if parts[-1].lower() in ['al.', 'et', 'et.']:
        return ""
    initials = [p for p in parts[:-1] if p[0].isalpha() and (len(p) == 1 or p.endswith('.'))]
    surname = parts[-1] if parts[-1][0].isalpha() else ""
    return " ".join(initials + [surname]).lower().strip()

def count_matching_fields(ex: dict, cand: dict) -> tuple[int, list[str]]:
    """Count matching fields between extracted and candidate with strict author matching."""
    fields_to_compare = ["title", "authors", "year", "journal_name", "volume", "issue", "pages", "doi"]
    matches = 0
    matching_fields = []

    # Title match (stricter threshold)
    ex_title = normalize_text(ex.get("title", ""))
    cand_title = normalize_text(cand.get("title", ""))
    if ex_title and cand_title and token_similarity(ex_title, cand_title) >= 0.9:
        matches += 1
        matching_fields.append("title")

    # Authors match (strict: require exact or near-exact list match)
    ex_authors = [normalize_author_name(a) for a in authors_to_list(ex.get("authors", [])) if normalize_author_name(a)]
    cand_authors = [normalize_author_name(a) for a in authors_to_list(cand.get("authors", [])) if normalize_author_name(a)]
    if ex_authors and cand_authors:
        # Compare ordered lists to account for order
        match_ratio = sum(1 for a, b in zip(ex_authors, cand_authors) if a == b) / max(len(ex_authors), len(cand_authors))
        if match_ratio >= 0.9 and len(ex_authors) == len(cand_authors):
            matches += 1
            matching_fields.append("authors")

    # Year match
    ex_year = normalize_text(str(ex.get("year", "")))
    cand_year = normalize_text(str(cand.get("year", "")))
    if ex_year and cand_year and ex_year == cand_year:
        matches += 1
        matching_fields.append("year")

    # Journal name match
    ex_journal = normalize_text(ex.get("journal_name", ""))
    cand_journal = normalize_text(cand.get("journal_name", ""))
    if ex_journal and cand_journal and token_similarity(ex_journal, cand_journal) >= 0.8:
        matches += 1
        matching_fields.append("journal_name")

    # Volume match
    ex_volume = normalize_text(ex.get("volume", ""))
    cand_volume = normalize_text(cand.get("volume", ""))
    if ex_volume and cand_volume and ex_volume == cand_volume:
        matches += 1
        matching_fields.append("volume")

    # Issue match
    ex_issue = normalize_text(ex.get("issue", ""))
    cand_issue = normalize_text(cand.get("issue", ""))
    if ex_issue and cand_issue and ex_issue == cand_issue:
        matches += 1
        matching_fields.append("issue")

    # Pages match
    ex_pages = normalize_text(ex.get("pages", ""))
    cand_pages = normalize_text(cand.get("pages", ""))
    if ex_pages and cand_pages and ex_pages == cand_pages:
        matches += 1
        matching_fields.append("pages")

    # DOI match
    ex_doi = normalize_text(ex.get("doi", "")).lower().replace("doi:", "")
    cand_doi = normalize_text(cand.get("doi", "")).lower().replace("doi:", "")
    if ex_doi and cand_doi and ex_doi == cand_doi:
        matches += 1
        matching_fields.append("doi")

    return matches, matching_fields

def select_best(state: PipelineState) -> PipelineState:
    ex = state["extracted"]
    candidates = state.get("candidates", [])
    if not candidates:
        state["best"] = {}
        state["matching_fields"] = []
        return state

    best = None
    best_score = -1
    best_matches = 0
    best_matching_fields = []

    for cand in candidates:
        # Calculate current score using existing scoring function
        score = score_candidate(ex, cand)
        # Count matching fields
        matches, matching_fields = count_matching_fields(ex, cand)

        # Prioritize candidates with matching title or journal and correct authors
        if (matches > best_matches or 
            (matches == best_matches and score > best_score) or
            ("authors" in matching_fields and matches >= best_matches)):
            best = cand
            best_score = score
            best_matches = matches
            best_matching_fields = matching_fields

    # Require at least 3 matching fields, including title or journal, and trustworthy candidate
    required_fields = {"title", "journal_name"}
    if best and best_matches >= 3 and any(f in best_matching_fields for f in required_fields) and is_trustworthy_match(ex, best):
        state["best"] = best
        state["matching_fields"] = best_matching_fields
    else:
        state["best"] = {}
        state["matching_fields"] = []

    return state