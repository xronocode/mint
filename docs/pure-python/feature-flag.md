# Feature Flag for Dual Engine Support

Implement `MINT_ENGINE` environment variable or CLI flag `--engine python|js`.
Default: python

Use in code:
if settings.ENGINE == 'python':
    from mint_python.execution import ...
