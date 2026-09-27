"""Java API tests in the real practice environment use a JSON library - measured
(2026-09-27): the real assistant's Java API test used org.json's JSONObject and
didn't compile, so most Java API tests would have failed. org.json, Gson and
Jackson are on the classpath now (tools/setup_vendor.sh). No AI calls."""
import json
from pathlib import Path

import pytest

from app.services import execution_service
from app.services.practice_engine import practice_run

HERE = Path(__file__).parent / "fixtures" / "practice_engine"
SPEC = json.loads((HERE / "library_spec.json").read_text())
pytestmark = pytest.mark.skipif(not execution_service.toolchain_available("java")
                                or not all((practice_run.vendor() / j).exists() for j in practice_run.JAVA_JARS),
                                reason="java or the vendor jars not installed (tools/setup_vendor.sh)")

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
