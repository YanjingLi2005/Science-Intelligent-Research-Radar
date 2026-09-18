"""CCF venue rank mapping for CS/AI source cards.

This is a lightweight, opinionated map of common CCF-A/B/C venues. It is used
to attach a ``ccf_rank`` to SourceRecords so the product can surface
"CS/AI venue quality" without calling an external service.
"""

from __future__ import annotations

_CCF_A = {
    "neurips", "nips", "icml", "iclr", "acl", "cvpr", "iccv", "aaai",
    "ijcai", "sigmod", "sosp", "osdi", "ccs", "sp", "oakland", "usenix security",
    "mobisys", "sigcomm", "nsdi", "www", "kdd", "icde", "icse", "fse",
    "ase", "tse", "tosem", "tpami", "ijcv", "jmlr", "tois", "tos", "ton",
    "tifs", "tdsc", "tc", "tpds", "tocs", "toc",
}
_CCF_B = {
    "coling", "emnlp", "naacl", "eccv", "icra", "iros", "aistats", "uai",
    "cogSci", "icassp", "interspeech", "sigdial", "cscw", "ubicomp", "imc",
    "sigmetrics", "icdcs", "ipdps", "icpp", "ics", "sc", "hpc", "icws",
    "middleware", "socc", "eurosys", "fast", "usenix atc", "isca", "hpca",
    "micro", "asplos", "pldi", "popl", "icfp", "oopsla", "sas", "cav",
    "tacas", "ijcai", "ecai", "pakdd", "icdm", "sdm", "cikm", "wsdm",
    "recsys", "sigir", "ecir", "ictir", "chi", "uist", "isscc", "date",
    "aspdac", "itc", "ets", "vlsi", "iccad", "dac", "ieee_tpds", "tcad",
}
_CCF_C = {
    "acl_workshops", "iconip", "icann", "ijcnn", "icpr", "icb", "icdar",
    "icmcs", "ism", "icmew", "mmsys", "nossdav", "icmr", "ismm", "issta",
    "icst", "issre", "csb", "isbra", "bicob", "apbc", "recomb", "ismb",
    "bionformatics", "briefings_in_bioinformatics", "bmc_bioinformatics",
}


def _normalize(venue: str | None) -> str:
    if not venue:
        return ""
    return " ".join(venue.strip().lower().replace("-", " ").split())


def ccf_rank_for_venue(venue: str | None) -> str | None:
    """Return 'A', 'B', or 'C' for a known CCF venue, else None."""
    normalized = _normalize(venue)
    if not normalized:
        return None
    # Match full normalized venue first (e.g. "IEEE Transactions on Pattern
    # Analysis and Machine Intelligence" won't hit the short token map).
    full_tokens = set(normalized.split())
    for token in normalized.split():
        if token in _CCF_A:
            return "A"
        if token in _CCF_B:
            return "B"
        if token in _CCF_C:
            return "C"
    # A few well-known full names that don't reduce to a single token.
    if "pattern analysis" in normalized and "machine intelligence" in normalized:
        return "A"
    if "machine learning research" in normalized:
        return "A"
    if "knowledge and data engineering" in normalized:
        return "A"
    if "software engineering" in normalized and "transactions" in normalized:
        return "A"
    if "parallel and distributed" in normalized and "transactions" in normalized:
        return "A"
    if "information forensics" in normalized and "security" in normalized:
        return "A"
    if "dependable and secure computing" in normalized:
        return "A"
    if "mobile computing" in normalized and "transactions" in normalized:
        return "A"
    if "networking" in normalized and "transactions" in normalized:
        return "A"
    if "computer-aided design" in normalized:
        return "A"
    if "evolutionary computation" in normalized:
        return "B"
    if "artificial intelligence" in normalized and "transactions" in normalized:
        return "A"
    if "learning technologies" in normalized:
        return "B"
    if "affective computing" in normalized:
        return "B"
    return None
