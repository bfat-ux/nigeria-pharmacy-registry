# Nigeria Pharmacy Registry

A national dispensing-endpoint infrastructure layer for Nigeria's digital medication ecosystem.

## What This Is

An open registry of pharmacy and medicine vendor dispensing locations across Nigeria, designed to be:

- **Continuously ingestible** — new data sources can be added incrementally
- **Progressively validated** — records climb a trust ladder (L0–L4) as evidence accumulates
- **Interoperable** — FHIR R4 Location/Organization mappings for health information exchange
- **Regulator-alignment ready** — schema supports future PCN, NAFDAC, and NHIA data synchronization

## What This Is NOT

- Not a patient data system
- Not a prescription or dispensing transaction system
- Not a replacement for official regulator registries — it's a complement designed to eventually sync with them

## Validation Ladder

| Level | Label | How It's Earned |
|-------|-------|-----------------|
| L0 | Mapped | Ingested from a data source |
| L1 | Contact Confirmed | Outbound contact verification |
| L2 | Evidence Documented | Location + photo evidence |
| L3 | Regulator/Partner Verified | Official dataset cross-reference |
| L4 | High-Assurance (Future) | In-person audit or biometric |

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (for the database)
- Python 3.10+

### 1. Start the database

```bash
docker compose up -d
```

This launches PostgreSQL 16 + PostGIS 3.4 and automatically runs all schema
migrations on first startup. The database is available at `localhost:5432`
(user: `npr`, password: `npr_local_dev`, database: `npr_registry`).

### 2. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Verify

```bash
# Check the database is healthy
docker compose ps

# Connect and inspect
psql -h localhost -U npr -d npr_registry -c "\dt"
```

## Project Structure

See `CLAUDE.md` for the full workstream breakdown and execution order.

## Technology Stack

- PostgreSQL + PostGIS (relational + geospatial)
- Python (data pipelines, entity resolution)
- TypeScript (API layer)
- FHIR R4 (interoperability)
- OpenAPI 3.1 (API specification)

## License

TBD — to be determined based on project governance decisions.
