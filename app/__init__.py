"""Simple Multi-Agent Chat server package.

`__version__` is the single source of truth for the server's version:
`GET /meta` (app/main.py) reads it directly, and it is the value packaging
(pyproject.toml, once it lands) should read too -- never hardcode this
number a second place.
"""

__version__ = "0.6.0"
