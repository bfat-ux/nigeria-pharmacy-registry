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

```bash
chmod +x setup.sh
./setup.sh
git init
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
