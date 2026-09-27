# Sealed blind-test scenarios

`SEALED_blind_scenarios.json` holds 20 Round 1 scenarios written by the AI on
2026-09-26, BEFORE the practice-app engine was built, the way different HR
people would write them. They were stored without being read, so the engine
could not be tailored to them. They are opened only for the blind test.

Fingerprint (sha256, first 16 hex): `134473ee9d655278`
Check: `python -c "import hashlib;print(hashlib.sha256(open('tests/blind/SEALED_blind_scenarios.json','rb').read()).hexdigest()[:16])"`
