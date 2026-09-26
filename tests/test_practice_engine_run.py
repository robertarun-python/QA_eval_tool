"""
A candidate's Round 2 code runs exactly as the Run button runs it
(execution_service.run_code, inside the sandbox where there is one): their
file, with the engine-built practice app's engine file placed next to it.
Real Python, Node and Java runs - offline.
"""
import json
from pathlib import Path

import pytest

from app.services import execution_service
from app.services.practice_engine import render

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())

# What a candidate (or the assistant) writes under the TODO marker, per language.
TESTS = {
    "python": '''
def test_borrow():
    setup()
    assert UI.login("testuser@library.test", "Test@123")
    UI.open("Search")
    UI.open_book("BK-001")
    assert UI.borrow_book("BK-001"), UI.visible_message()
    assert Database.get_book("BK-001")["available_copies"] == 2
    print("PASS", UI.visible_message())
''',
    "javascript": '''
function testBorrow() {
  setup();
  if (!UI.login("testuser@library.test", "Test@123")) throw new Error("login");
  UI.open("Search");
  UI.openBook("BK-001");
  if (!UI.borrowBook("BK-001")) throw new Error(UI.visibleMessage());
  if (Database.getBook("BK-001").available_copies !== 2) throw new Error("stock");
  console.log("PASS", UI.visibleMessage());
}
testBorrow();
''',
    "java": '''
    static void testBorrow() {
        setup();
        if (!UI.login("testuser@library.test", "Test@123")) throw new AssertionError("login");
        UI.open("Search");
        UI.openBook("BK-001");
        if (!UI.borrowBook("BK-001")) throw new AssertionError(UI.visibleMessage());
        if (((Number) Database.getBook("BK-001").get("available_copies")).intValue() != 2) throw new AssertionError("stock");
        System.out.println("PASS " + UI.visibleMessage());
    }
''',
}


def _with_test(language: str, candidate: str) -> str:
    if language == "python":
        return candidate.replace('if __name__ == "__main__":\n    pass', TESTS["python"] + '\n\nif __name__ == "__main__":\n    test_borrow()')
    if language == "javascript":
        return candidate + TESTS["javascript"]
    return candidate.replace("    public static void main(String[] args) {\n    }",
                             TESTS["java"] + "\n    public static void main(String[] args) {\n        testBorrow();\n    }")


@pytest.mark.parametrize("language", render.LANGUAGES)
def test_a_candidate_test_runs_against_the_engine_file_beside_it(language):
    if not execution_service.toolchain_available(language):
        pytest.skip(f"{language} is not installed")
    candidate, support = render.files(SPEC, language)
    result = execution_service.run_code(language, _with_test(language, candidate), [], support_files=support)
    assert not result.infra_error and result.exit_code == 0, result.stderr[-800:]
    assert "PASS Book borrowed successfully. Due date: 24-Feb-2024" in result.stdout


@pytest.mark.parametrize("language", render.LANGUAGES)
def test_without_the_engine_file_the_candidate_file_alone_does_not_run(language):
    """Guards the wiring: the engine really comes from the file placed beside it."""
    if not execution_service.toolchain_available(language):
        pytest.skip(f"{language} is not installed")
    candidate, _ = render.files(SPEC, language)
    result = execution_service.run_code(language, _with_test(language, candidate), [])
    assert result.exit_code != 0


def test_support_file_names_cannot_leave_the_run_folder():
    with pytest.raises(ValueError):
        execution_service.run_code("python", "print(1)", [], support_files={"../x.py": ""})
