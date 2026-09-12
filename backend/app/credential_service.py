"""
Credential generation for bulk-uploaded candidates (see routers/hr.py's
upload endpoint). Pure functions, no DB/IO, so they're trivial to unit
test in isolation - see tests/test_credentials.py.

`derive_password` used to compute a password deterministically from the
candidate's own email ("idfc@" + the first 4 letters of the local part) -
predictable on purpose, so HR could communicate a login by eye with no
password manager. That was flagged as a real vulnerability by both an
automated security scan and a manual review of this repo (2026-08-26 and
2026-09-12): anyone who knows or guesses a candidate's email can compute
their password with zero interaction with the real mailbox - a full
authentication bypass. `generate_temporary_password` replaces it with a
real random password, generated once per new account and returned to HR
exactly once in the upload response (see schemas.BulkUploadRowResult and
candidate_upload_service.process_upload_rows) - HR is responsible for
getting it to the candidate out of band, same as before, just no longer
guessable by a stranger.
"""
import secrets

# Excludes visually ambiguous characters (0/O, 1/l/I) - a human (HR, then
# the candidate) has to read this off a screen and retype it correctly.
_PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
_PASSWORD_LENGTH = 12  # ~68 bits of entropy from the alphabet above - plenty for a short-lived screening login


def derive_username(email: str) -> str:
    """The email's local part (before '@'), lowercased - e.g.
    "John.Doe@Acme.com" -> "john.doe". Still deterministic and still
    derived from the email on purpose: the username isn't a secret, and
    a predictable one is exactly what lets HR read a roster and know each
    candidate's login without a lookup table."""
    return email.split("@", 1)[0].lower()


def generate_temporary_password() -> str:
    """A random, non-guessable temporary password for a newly
    bulk-uploaded candidate account. Not derived from the email or
    anything else about the candidate - see this module's docstring for
    why that used to be the case and why it changed."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH))
