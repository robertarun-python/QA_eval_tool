"""
Checklist runner for a Python practice app. Run as a separate process by
practice_app.checker - never imported by the server, because the practice
app is generated code.

Reads {"env_path": ..., "checklists": [...]} as JSON on stdin and prints one
JSON result list on stdout: for each checklist, the value each step returned
(normalised: dict keys in snake_case, objects as plain data) or the error it
raised. Each checklist runs against a freshly loaded copy of the app.
"""
import json
import re
import sys


def _snake(name):
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", str(name)).lower()


def _plain(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {_snake(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if hasattr(value, "__dict__"):
        return {_snake(k): _plain(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def _field(obj, name):
    if isinstance(obj, dict):
        if name in obj:
            return obj[name]
        return {_snake(k): v for k, v in obj.items()}.get(name)
    return getattr(obj, name, None)


def _resolve(arg, saved):
    if isinstance(arg, dict) and "ref" in arg:
        path = arg["ref"].split(".")
        value = saved[path[0]]
        for part in path[1:]:
            value = _field(value, part)
        return value
    return arg


def _target(namespace, call):
    obj = namespace
    for part in call.split("."):
        obj = obj[part] if isinstance(obj, dict) else getattr(obj, part)
    return obj


def main():
    job = json.load(sys.stdin)
    source = open(job["env_path"], encoding="utf-8").read()
    try:
        code = compile(source, "practice_app.py", "exec")
    except SyntaxError as e:
        code, compile_error = None, f"SyntaxError: {e.msg} (line {e.lineno})"
    results = []
    for checklist in job["checklists"]:
        namespace = {"__name__": "practice_app"}  # not __main__: the app's own main block stays idle
        steps = []
        try:
            if code is None:
                raise SyntaxError(compile_error)
            exec(code, namespace)
        except Exception as e:  # the app itself fails to load
            results.append({"id": checklist["id"], "load_error": f"{type(e).__name__}: {e}", "steps": []})
            continue
        saved = {}
        for step in checklist["steps"]:
            try:
                value = _target(namespace, step["call"])(*[_resolve(a, saved) for a in step.get("args", [])])
            except Exception as e:
                steps.append({"error": f"{type(e).__name__}: {e}"})
                break
            if step.get("save_as"):
                saved[step["save_as"]] = value
            steps.append({"value": _plain(value)})
        results.append({"id": checklist["id"], "steps": steps})
    json.dump(results, sys.stdout)


if __name__ == "__main__":
    main()
