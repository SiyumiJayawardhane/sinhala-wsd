from typing import List, Dict, Optional


def fleiss_kappa(matrix: List[List[int]]) -> Optional[float]:
    """
    Standard Fleiss' Kappa.
    matrix: one row per subject (sentence), one column per category (sense_id/REJECT).
    Each cell = number of raters who assigned that category to that subject.
    All rows must sum to the same n (number of raters per subject).
    """
    N = len(matrix)
    if N == 0:
        return None
    n = sum(matrix[0])
    if n < 2:
        return None

    num_categories = len(matrix[0])
    category_totals = [0] * num_categories
    sum_pi = 0.0

    for row in matrix:
        sq_sum = sum(count * count for count in row)
        sum_pi += (sq_sum - n) / (n * (n - 1))
        for j, count in enumerate(row):
            category_totals[j] += count

    p_bar = sum_pi / N
    p_e = sum((total / (N * n)) ** 2 for total in category_totals)

    if p_e == 1:
        return None
    return (p_bar - p_e) / (1 - p_e)


def agreement_stats(matrix: List[List[int]]) -> Dict[str, Optional[float]]:
    """Observed / expected / chance-corrected agreement for a Fleiss matrix.

    Same matrix shape as ``fleiss_kappa`` (one row per item, one column per
    category, cell = raters who chose that category; every row sums to n).

    Returns a dict:
      kappa      - Fleiss' kappa (None when undefined)
      observed   - mean pairwise observed agreement  (the "raw % agreement")
      expected   - chance agreement (p_e); high when one category dominates
      unanimity  - fraction of items every rater agreed on
      n_items    - items rated by all raters
      n_raters   - raters per item

    ``observed`` is what you report next to kappa when kappa is deflated by
    a dominant sense (the "kappa paradox").
    """
    out: Dict[str, Optional[float]] = {
        "kappa": None, "observed": None, "expected": None,
        "unanimity": None, "n_items": len(matrix), "n_raters": 0,
    }
    N = len(matrix)
    if N == 0:
        return out
    n = sum(matrix[0])
    out["n_raters"] = n
    if n < 2:
        return out

    num_categories = len(matrix[0])
    category_totals = [0] * num_categories
    sum_pi = 0.0
    unanimous = 0
    for row in matrix:
        sq_sum = sum(c * c for c in row)
        sum_pi += (sq_sum - n) / (n * (n - 1))
        if max(row) == n:
            unanimous += 1
        for j, c in enumerate(row):
            category_totals[j] += c

    observed = sum_pi / N
    expected = sum((t / (N * n)) ** 2 for t in category_totals)
    out["observed"] = observed
    out["expected"] = expected
    out["unanimity"] = unanimous / N
    out["kappa"] = None if expected == 1 else (observed - expected) / (1 - expected)
    return out


def build_agreement_matrix(
    votes_by_sentence: Dict[str, Dict[str, str]],
    annotator_names: List[str],
    categories: List[str],
) -> List[List[int]]:
    """
    votes_by_sentence: {sentence_id: {annotator_name: chosen_sense}}
    Only includes sentences where ALL annotator_names have voted.
    """
    matrix = []
    for sentence_id, votes in votes_by_sentence.items():
        if not all(name in votes for name in annotator_names):
            continue
        row = [0] * len(categories)
        for name in annotator_names:
            chosen = votes[name]
            if chosen in categories:
                row[categories.index(chosen)] += 1
        matrix.append(row)
    return matrix
