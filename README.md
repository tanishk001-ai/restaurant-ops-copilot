# Restaurant Ops Copilot

An AI-powered demand forecasting and procurement agent for restaurants. It predicts dish-level demand, converts forecasts into raw-material needs, computes inventory shortfalls, and drafts an Instamart replenishment cart for human approval — all through a planner-verifier agent loop.

Built on a synthetic Swiggy-compatible MCP server so the full stack works without live API access. When Swiggy Builders Club grants access, one config flag (`MCP_MODE=real`) points the agent at the real endpoint.

## Architecture

```
User (restaurant owner) → Agent Layer (Planner → Verifier → Human Gate)
                                  ↕
            Forecasting Engine | Procurement Engine | MCP Client (swappable)
                                  ↕
              PostgreSQL + pgvector (orders, inventory, recipes, forecasts)
```

## Quick start

```bash
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
docker-compose up db        # start Postgres
pip install -e ".[dev]"
pytest                      # should pass (no tests yet in Phase 0)
```

## Project structure

```
synthetic_mcp/    synthetic Swiggy-compatible MCP server
data_gen/         synthetic data generators and seeders
forecasting/      Prophet + XGBoost demand models
procurement/      BOM explosion, shortfall, draft cart
agent/            planner, verifier, human approval gate, NL-ops
mcp_client/       swappable MCP client (synthetic | real)
api/              FastAPI backend
frontend/         single-page dashboard
db/               schema, migrations, seed data
tests/            pytest suite + agent eval harness
```

## Status

- [x] Phase 0 — Project scaffold
- [ ] Phase 1 — Synthetic Swiggy MCP server + seeded data
- [ ] Phase 2 — Forecasting engine (Prophet + XGBoost)
- [ ] Phase 3 — Procurement engine (BOM, shortfall, draft cart)
- [ ] Phase 4 — Planner-verifier agent + NL-ops
- [ ] Phase 5 — FastAPI backend + dashboard
- [ ] Phase 6 — Testing + agent eval harness
- [ ] Phase 7 — Deployment
- [ ] Phase 8 — GitHub polish + Builders Club application

## Legal note

Swiggy does not yet permit third-party development on its live MCP servers (security review in progress). This project builds and tests against a synthetic MCP server that mirrors Swiggy's tool surface. The swap to the real endpoint requires only changing `MCP_MODE=real` and supplying credentials.

## License

MIT
