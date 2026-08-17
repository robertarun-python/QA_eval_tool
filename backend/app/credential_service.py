"""
Deterministic credential generation for bulk-uploaded candidates (see
routers/hr.py's upload endpoint). Pure functions, no DB/IO, so they're
trivial to unit test in isolation - see tests/test_credentials.py.

The scheme is deliberately simple (predictable, not randomized) so HR can
communicate a candidate's login by eye without a password manager - a
conscious tradeoff for an internal screening tool with short-lived,
non-sensitive access, confirmed with the app's owner rather than assumed.
"""


def derive_username(email: str) -> str:
    """The email's local part (before '@'), lowercased - e.g.
    "John.Doe@Acme.com" -> "john.doe"."""
    return email.split("@", 1)[0].lower()


def derive_password(email: str) -> str:
    """"idfc@" + the first 4 characters of the lowercased local part -
    e.g. "John.Doe@acme.com" -> "idfc@john". Lowercased for the same
    reason derive_username is: one consistent, easy-to-communicate rule
    rather than two different casing conventions. Slicing past the end
    of a short local part is safe in Python (just yields fewer than 4
    characters), so no special-casing is needed there."""
    local_part = derive_username(email)
    return f"idfc@{local_part[:4]}"
