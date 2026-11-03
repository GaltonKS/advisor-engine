"""One APIRouter per area of the API. `main.create_app` includes them all.

Routes move engine output around and compute nothing; shared dependencies and helpers
(who is signed in, firm-scoped reads, audit) are in `api/deps.py`.
"""
