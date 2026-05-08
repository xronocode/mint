# Project Structure for Pure Python Edition

```text
src/
├── mint/                  # Old JS-based implementation (legacy)
└── mint_python/           # New pure Python implementation (recommended)

src/mint_python/
├── __init__.py
├── core/
├── sdk/
├── execution/
├── rules/
├── grace/
├── mcp/
├── cli/
└── tests/

# Feature flag
MINT_ENGINE=python  # or js
```