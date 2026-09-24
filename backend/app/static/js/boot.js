(async function bootstrapSession() {
  try {
    const me = await fetch("/auth/me");
    if (!me.ok) return; // no valid session cookie - stay on the login screen
    const data = await me.json();
    role = data.role;
    userEmail = data.email;
    onLoggedIn();
  } catch (e) {
    // Network error on page load - stay on the login screen, same as a 401.
  }
})();
