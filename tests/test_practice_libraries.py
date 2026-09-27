"""The libraries candidates reach for are there in the real practice environment.
Measured with the real assistant (2026-09-27): its Java API test used org.json's
JSONObject and didn't compile; its JavaScript one used require("node-fetch") and
crashed. Now available (tools/setup_vendor.sh): Java org.json, Gson, Jackson,
JUnit 5 and TestNG assertions; Python requests; JavaScript node-fetch and axios.
Each is used against a real practice app here. No AI calls."""
import json
from pathlib import Path

import pytest

from app.services import execution_service
from app.services.practice_engine import practice_run

HERE = Path(__file__).parent / "fixtures" / "practice_engine"
SPEC = json.loads((HERE / "library_spec.json").read_text())
pytestmark = pytest.mark.skipif(not all(execution_service.toolchain_available(l) for l in ("java", "python", "javascript"))
                                or not all((practice_run.vendor() / j).exists() for j in practice_run.JAVA_JARS),
                                reason="a toolchain or the vendor libraries are missing (tools/setup_vendor.sh)")

GSON_AND_JACKSON = """
import java.net.URI;
import java.net.http.*;
import com.google.gson.JsonParser;
import com.fasterxml.jackson.databind.ObjectMapper;

public class Main {
    public static void main(String[] args) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(System.getenv("PRACTICE_API_URL") + "login"))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString("{\\"email\\": \\"testuser@library.test\\", \\"password\\": \\"Test@123\\"}")).build();
        String body = HttpClient.newHttpClient().send(req, HttpResponse.BodyHandlers.ofString()).body();
        System.out.println("gson: " + JsonParser.parseString(body).getAsJsonObject().get("name").getAsString());
        System.out.println("jackson: " + new ObjectMapper().readTree(body).get("name").asText());
    }
}
"""


def test_the_real_assistants_org_json_api_test_now_runs():
    code = (HERE / "real_outputs" / "assistant" / "java_api_orgjson.java").read_text()
    result = practice_run.run("java", code, SPEC, browser=False)
    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    assert "Test User" in result.stdout


def test_gson_and_jackson_work_too():
    result = practice_run.run("java", GSON_AND_JACKSON, SPEC, browser=False)
    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    assert "gson: Test User" in result.stdout and "jackson: Test User" in result.stdout


ASSERTIONS = """
public class Main {
    public static void main(String[] args) {
        org.junit.jupiter.api.Assertions.assertEquals(2, 1 + 1);
        org.testng.Assert.assertEquals("a", "a");
        try { org.junit.jupiter.api.Assertions.assertEquals("Test User", "Someone"); }
        catch (AssertionError e) { System.out.println("junit caught: " + e.getMessage()); }
        System.out.println("assertions ok");
    }
}
"""

PY_REQUESTS = """
import os, requests
r = requests.post(os.environ["PRACTICE_API_URL"] + "login", json={"email": "testuser@library.test", "password": "Test@123"})
print(r.status_code, r.json()["name"])
"""

JS_AXIOS = """
const axios = require("axios");
axios.post(process.env.PRACTICE_API_URL + "login", { email: "testuser@library.test", password: "Test@123" })
  .then((r) => console.log(r.status, r.data.name)).catch((e) => { console.error(e.message); process.exit(1); });
"""


def test_junit_and_testng_assertions_work_in_main():
    result = practice_run.run("java", ASSERTIONS, SPEC, browser=False)
    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    assert "junit caught" in result.stdout and "assertions ok" in result.stdout


def test_the_real_assistants_node_fetch_api_test_now_runs():
    code = (HERE / "real_outputs" / "assistant" / "javascript_api_node_fetch.js").read_text()
    result = practice_run.run("javascript", code, SPEC, browser=False)
    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    assert "PASS" in result.stdout and "FAIL" not in result.stdout


def test_python_requests_and_javascript_axios_work():
    for language, code in (("python", PY_REQUESTS), ("javascript", JS_AXIOS)):
        result = practice_run.run(language, code, SPEC, browser=False)
        assert result.exit_code == 0, f"[{language}] " + (result.stdout or "") + (result.stderr or "")
        assert "200 Test User" in result.stdout, f"[{language}] {result.stdout}"
