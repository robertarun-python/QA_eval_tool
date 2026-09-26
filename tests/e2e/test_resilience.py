"""The page when the network drops: a plain message, never "Failed to fetch"."""
from .conftest import HR, login


def test_a_dropped_connection_gives_a_plain_message(app_page):
    page = app_page
    login(page, HR)
    page.route("**/hr/scenarios*", lambda route: route.abort("internetdisconnected"))
    message = page.evaluate("api('/hr/scenarios').then(() => 'no error', (e) => e.message)")
    assert message == ("Couldn't reach the server - check your internet connection and try again. "
                       "Anything already saved is safe.")
