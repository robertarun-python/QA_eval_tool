#!/bin/sh
# One-time download of the browser-automation tools the Round 2 practice
# environment uses (never committed - see .gitignore). Re-run to repair.
#   Chrome for Testing + matching chromedriver (mac-arm64), Selenium server (Grid),
#   sqlite-jdbc (Java database access), Selenium for Python and JavaScript.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
V="$ROOT/vendor"
mkdir -p "$V" && cd "$V"
CFT=154.0.8037.57
SELENIUM=4.49.0
SQLITE_JDBC=3.49.1.0
curl -sSfL -o chrome.zip "https://storage.googleapis.com/chrome-for-testing-public/$CFT/mac-arm64/chrome-mac-arm64.zip"
curl -sSfL -o chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/$CFT/mac-arm64/chromedriver-mac-arm64.zip"
unzip -q -o chrome.zip && unzip -q -o chromedriver.zip && rm chrome.zip chromedriver.zip
xattr -dr com.apple.quarantine . 2>/dev/null || true
curl -sSfL -o selenium-server.jar "https://github.com/SeleniumHQ/selenium/releases/download/selenium-$SELENIUM/selenium-server-$SELENIUM.jar"
curl -sSfL -o sqlite-jdbc.jar "https://repo1.maven.org/maven2/org/xerial/sqlite-jdbc/$SQLITE_JDBC/sqlite-jdbc-$SQLITE_JDBC.jar"
# JSON for Java API tests (org.json, Gson, Jackson), each checked against Maven Central's SHA-1
M=https://repo1.maven.org/maven2
for spec in "org/json/json/20240303/json-20240303.jar:json.jar" "com/google/code/gson/gson/2.11.0/gson-2.11.0.jar:gson.jar" \
            "com/fasterxml/jackson/core/jackson-databind/2.17.2/jackson-databind-2.17.2.jar:jackson-databind.jar" \
            "com/fasterxml/jackson/core/jackson-core/2.17.2/jackson-core-2.17.2.jar:jackson-core.jar" \
            "com/fasterxml/jackson/core/jackson-annotations/2.17.2/jackson-annotations-2.17.2.jar:jackson-annotations.jar"; do
  rel=${spec%%:*}; jar=${spec##*:}
  curl -sSfL -o "$jar" "$M/$rel"
  [ "$(curl -sSfL "$M/$rel.sha1" | cut -c1-40)" = "$(shasum -a 1 "$jar" | cut -c1-40)" ] || { echo "checksum mismatch: $jar"; rm -f "$jar"; exit 1; }
done
PY="$(cd "$ROOT/backend" && "$ROOT/.venv/bin/python" -c 'from app.services import execution_service as e; print(e.PYTHON)')"
"$PY" -m pip install --quiet --target python "selenium==4.36.0"
mkdir -p node && (cd node && [ -f package.json ] || npm init -y >/dev/null) && (cd node && npm install --silent "selenium-webdriver@$SELENIUM")
# macOS App Nap slows headless Chrome after a while and its pages then drop input (see practice_run._no_app_nap)
defaults write com.google.chrome.for.testing NSAppSleepDisabled -bool YES 2>/dev/null || true
echo "vendor ready in $V"
