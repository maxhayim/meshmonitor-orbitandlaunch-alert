# Contributing to Orbit and Launch Alert

Thank you for your interest in contributing to Orbit and Launch Alert! We welcome contributions from the community and want to make the process clear and straightforward.

## Ways to Contribute

- Report bugs or request features using [GitHub Issues](https://github.com/maxhayim/meshmonitor-orbitandlaunch-alert/issues).
- Improve documentation (README, docs/, code comments, examples).
- Submit Pull Requests (PRs) with bug fixes, improvements, or new features.

## Code Style

- Follow **PEP 8** (Python style guide) for clean, consistent code.
- Add comments where needed for clarity.
- Use docstrings for functions, classes, and modules.
- Keep things simple (KISS principle). Standard library first; new dependencies need a strong reason and a compatible license.
- Keep Python 3.8 compatibility.
- Keep mesh messages short (200 characters max) and emoji-led.
- Never invent data: no guessed launch outcomes, no unverified frequencies, no visibility promises.

## Pull Request Guidelines

1. **Fork** the repository.
2. Create a new branch for your feature or bugfix:
   ```bash
   git checkout -b feature/my-new-feature
   ```
3. Commit changes with clear, descriptive messages.
4. Run the tests (below) and make sure they pass.
5. Update `CHANGELOG.md` and the docs when behavior or configuration changes.
6. Open a Pull Request and describe your changes.

## Tests

```bash
pip install -r requirements.txt pytest
python3 -m pytest -q
```

The suite is fully offline. Use fixtures in `tests/fixtures/` or mocked responses, never live API calls. New features need tests.

## Community

We value respectful collaboration. By contributing, you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md).

Thank you for helping make this project welcoming!
