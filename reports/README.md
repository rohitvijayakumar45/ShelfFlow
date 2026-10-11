# Validation artifacts

`pytest.xml` is produced by `python -m pytest --junitxml=reports/pytest.xml`. `installed-python.txt` captures the implementation machine's installed packages, including optional OpenCV and PlatformIO tooling; it is not the deployment lockfile. Direct runtime requirements are pinned in pyproject.toml and frontend dependency tree in apps/package-lock.json.

No physical capture benchmark, custom model accuracy or hardware acceptance report has been generated. `docs/status.md` records verified and unverified work separately.
