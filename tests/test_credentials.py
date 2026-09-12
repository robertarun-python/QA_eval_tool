from app.credential_service import derive_username, generate_temporary_password


def test_derive_username_is_lowercased_local_part():
    assert derive_username("John.Doe@Acme.com") == "john.doe"
    assert derive_username("jane@example.org") == "jane"


def test_generated_password_is_random_not_derived_from_email():
    # The old scheme ("idfc@" + first 4 letters of the email) was
    # deterministic on purpose - flagged as a real vulnerability (anyone
    # who knows the email can compute the password) and replaced. Two
    # calls, even for logically "the same" candidate, must not produce
    # the same password.
    a = generate_temporary_password()
    b = generate_temporary_password()
    assert a != b


def test_generated_password_has_a_reasonable_length_and_alphabet():
    password = generate_temporary_password()
    assert len(password) == 12
    # No visually ambiguous characters (0/O, 1/l/I) - a human has to
    # read and retype this.
    assert not set(password) & set("0O1lI")
