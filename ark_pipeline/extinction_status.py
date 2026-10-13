"""Verified IUCN assessments take precedence over OTT extinction flags."""


def extinction_status(iucn_category: str | None, ott_flags: str | None) -> dict:
    """Resolve classification without changing identity, branches or DNA evidence.

    Callers supply a verified IUCN species assessment. NE/missing assessments
    remain unresolved. A non-EX category means not classified EX, rather than
    proof of living individuals. EW is distinct from EX.
    """
    flags = {v.strip() for v in ott_flags.split(",")} if isinstance(ott_flags, str) else set()
    ott_extinct = bool(flags & {"extinct", "extinct_inherited"})
    known = iucn_category in {"LC", "NT", "VU", "EN", "CR", "DD", "EW", "EX"}
    if iucn_category not in {None, "NE", "LC", "NT", "VU", "EN", "CR", "DD", "EW", "EX"}:
        raise ValueError("Expected normalized IUCN extinction category")
    return {
        "ott_extinct_flag": ott_extinct,
        "effective_extinct": iucn_category == "EX" if known else None,
        "extinction_status_source": "IUCN" if known else "unresolved",
        "extinction_status_conflict": known and ott_extinct and iucn_category != "EX",
        "ott_extinction_overridden": known and ott_extinct and iucn_category != "EX",
    }
