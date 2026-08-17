from app.credential_service import derive_username, derive_password


def test_derive_username_is_lowercased_local_part():
    assert derive_username("John.Doe@Acme.com") == "john.doe"
    assert derive_username("jane@example.org") == "jane"


def test_derive_password_scheme():
    assert derive_password("John.Doe@Acme.com") == "idfc@john"
    assert derive_password("jane@example.org") == "idfc@jane"


def test_derive_password_handles_short_local_part():
    # Slicing past the end of a short string is safe in Python - no
    # special-casing needed for a local part under 4 characters.
    assert derive_password("al@example.com") == "idfc@al"


def test_derivation_is_deterministic():
    email = "test.user@example.com"
    assert derive_username(email) == derive_username(email)
    assert derive_password(email) == derive_password(email)
