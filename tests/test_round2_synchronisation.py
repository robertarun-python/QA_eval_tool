"""
Handling waits is judged from the candidate's code, the same way for everyone
(owner decision 2026-09-27) - a browser test without waits can pass a run by
luck, so the run result can't be the judge. Deterministic, no AI.
"""
import pytest

from app.services import round2_automation_policy as policy
from app.services import scoring_service

from .test_practice_run import JAVA, JAVASCRIPT, PYTHON

JAVA_NO_WAITS = '''
WebDriver driver = new RemoteWebDriver(new URL(System.getenv("SELENIUM_GRID_URL")), options);
// TODO maybe add a WebDriverWait later
driver.findElement(By.id("login")).click();
System.out.println(driver.findElement(By.id("message")).getText());
'''
PY_SLEEPS = '''
driver = webdriver.Remote(os.environ["SELENIUM_GRID_URL"], options=o)
driver.find_element(By.ID, "login").click()
time.sleep(2)
print(driver.find_element(By.ID, "message").text)
'''
JS_IMPLICIT = '''
const driver = await new Builder().usingServer(process.env.SELENIUM_GRID_URL).forBrowser("chrome").build();
await driver.manage().setTimeouts({ implicit: 5000 });
await driver.findElement(By.id("login")).click();
'''
JAVA_IMPLICIT = '''
WebDriver driver = new RemoteWebDriver(url, options);
driver.manage().timeouts().implicitlyWait(Duration.ofSeconds(5));
driver.findElement(By.id("login")).click();
'''
PY_READ_ONLY = '''
driver = webdriver.Remote(os.environ["SELENIUM_GRID_URL"], options=o)
driver.get(os.environ["PRACTICE_APP_URL"])
print(driver.title)
'''
API_ONLY = '''
r = urllib.request.urlopen(os.environ["PRACTICE_API_URL"] + "search-books?term=x")
assert r.status == 200
'''


@pytest.mark.parametrize("code, verdict", [
    (JAVA, "waits"), (PYTHON, "waits"), (JAVASCRIPT, "waits"),
    (JAVA_NO_WAITS, "no_waits"),        # a comment mentioning a wait doesn't count
    (PY_SLEEPS, "fixed_sleeps_only"),
    (JS_IMPLICIT, "waits"), (JAVA_IMPLICIT, "waits"),
    (PY_READ_ONLY, "not_needed"),
    (API_ONLY, "no_browser"), ("", "no_browser"), (None, "no_browser"),
])
def test_synchronisation_verdicts(code, verdict):
    assert policy.synchronisation(code)["verdict"] == verdict


def test_the_scorer_is_told_how_to_judge_it():
    prompt = scoring_service.llm_service._load_prompt("round2_automation_scoring.txt")
    assert "synchronisation" in prompt and "EVEN IF execution_result passed" in prompt
