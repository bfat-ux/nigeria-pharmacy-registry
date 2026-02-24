# Nigeria Pharmacy Registry

An open registry of pharmacy and medicine vendor locations across Nigeria.

**Live at [openhealthregistry.org](https://openhealthregistry.org/)**

## Current Status

- **6,891 pharmacies** across all 36 states + FCT
- **Sources**: GRID3 Nigeria, OpenStreetMap, Google Places
- **First SMS campaign**: 98 messages sent to Ogun State pharmacies (Feb 2026)
- **347 tests passing**
- **Stage**: Working prototype — deployed on a Raspberry Pi 5

## What This Does

1. **Ingests** pharmacy location data from multiple open sources
2. **Deduplicates** records using fuzzy name matching + geospatial proximity
3. **Progressively verifies** via SMS contact confirmation (L0 → L1)
4. **Exposes** a REST API + FHIR R4 endpoints + dashboard

## Validation Ladder

Records start untrusted and earn trust through evidence:

| Level | Label | How Earned | Count |
|-------|-------|------------|-------|
| L0 | Mapped | Ingested from data source | ~6,887 |
| L1 | Contact Confirmed | SMS/phone reply received | ~3 |
| L2 | Evidence Documented | GPS + photo from field visit | ~1 |
| L3 | Regulator Verified | PCN/NAFDAC cross-reference | 0 |
| L4 | High-Assurance | In-person audit (future) | 0 |

## Quick Start

### Prerequisites

- Docker (for the database) or PostgreSQL 17+ with PostGIS
- Python 3.10+

### Setup

```bash
# Start the database
docker compose up -d

# Install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the API + dashboard
python serve.py
```

The dashboard is at `http://localhost:8000`. The database is at
`localhost:5432` (user: `npr`, password: `npr_local_dev`, database: `npr_registry`).

### Run Tests

```bash
python -m pytest tests/ -q
```

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Database | PostgreSQL + PostGIS |
| API | Python / FastAPI |
| Entity resolution | Python (rapidfuzz) |
| SMS | Africa's Talking |
| Hosting | Raspberry Pi 5 + nginx + Let's Encrypt |

## Documentation

- **[METHODOLOGY.md](METHODOLOGY.md)** — How this was built, what worked, what didn't, lessons learned
- **[CLAUDE.md](CLAUDE.md)** — Original project plan and coding standards

## What This Is NOT

- Not a patient data system — locations only, no patient or transaction data
- Not a replacement for PCN/NAFDAC registries — a complement designed to eventually sync with them

## License

TBD
