# Nigeria Pharmacy Registry — Methodology

> Last updated: 2026-02-24

## What This Document Is

An honest account of how the Nigeria Pharmacy Registry was built, what
worked, what didn't, and where the project stands today. Written for
anyone picking this up — including future-us.

---

## 1. The Problem

Nigeria has over 100,000 pharmacy and patent medicine vendor (PPMV)
locations. No single dataset maps them all. The Pharmacy Council of
Nigeria (PCN) maintains a premises register, but it's not digitally
accessible in a form that health-tech platforms can consume. GRID3,
OpenStreetMap, and Google Places each capture fragments. None of them
verify whether a pharmacy is actually open, reachable, or licensed.

The goal: build an open registry of dispensing locations that can be
continuously updated and progressively verified, starting from freely
available data and climbing toward regulator-grade trust.

## 2. What We Built

### The Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Database | PostgreSQL 17 + PostGIS | On Raspberry Pi 5 (production) and local Mac (dev) |
| API | Python / FastAPI | Not TypeScript as originally planned — Python was simpler for a solo build |
| Entity resolution | Python (rapidfuzz, custom scorers) | Composite name + geo + phone matching |
| SMS verification | Africa's Talking | Gateway-agnostic outbox pattern, AT as first provider |
| Hosting | Raspberry Pi 5 + nginx + Let's Encrypt | At openhealthregistry.org |

### What Actually Runs

The working system has four parts:

**1. Ingestion pipelines** (`agent-02-data-acquisition/scripts/`)
- Pulls pharmacy data from GRID3 (health facility dataset), OpenStreetMap
  Overpass API, and Google Places API
- Normalizes into a canonical schema, assigns L0 (Mapped) status
- Writes provenance records for every ingested record
- Result: 6,891 pharmacies across all 36 states + FCT

**2. Deduplication engine** (`agent-03-deduplication/algorithms/`)
- Blocks by state, then scores candidate pairs on name similarity
  (trigram + token-sort), geographic proximity (PostGIS), phone number
  match, and external ID overlap
- Composite weighted score → auto-merge (≥0.95), review queue (0.70–0.95),
  or no-match (<0.70)
- Configurable thresholds in `merge_rules.yaml`

**3. API + Dashboard** (`agent-05-platform-api/`)
- REST API: search, nearest-pharmacy, validation history, FHIR Location
  endpoints, SMS campaign management, regulator cross-reference
- Tiered API keys (admin, read) with bcrypt-hashed storage
- Rate limiting (in-memory token bucket)
- Dashboard at `/` — map view, pharmacy list, verification queue
- 347 tests passing

**4. SMS verification system** (`agent-05-platform-api/src/sms_processor.py`,
   `scripts/sms_gateway.py`, `routes/sms.py`, `routes/sms_webhooks_at.py`)
- Campaign-based bulk SMS: target pharmacies by state/LGA/status filters
- Outbox pattern: API creates messages, gateway script polls and sends
- Reply parsing: YES/Y/ok → operating, NO/CLOSED → closed, MOVED → relocated
- Any valid reply promotes the pharmacy from L0 → L1 (Contact Confirmed)
- Africa's Talking webhook adapter for delivery reports and inbound replies

### What's Documentation Only

The `agent-0X` directory structure was a planning framework — 8 "agents"
(workstreams) to organize the build. Several directories contain only
design documents, not running code:

| Directory | Contents | Status |
|-----------|----------|--------|
| `agent-01-data-architecture/` | SQL schema (runs), FHIR mappings (used), governance doc | **Active** — schema is the foundation |
| `agent-02-data-acquisition/` | Ingestion scripts (run), source templates (used) | **Active** |
| `agent-03-deduplication/` | Algorithms (run), config (used) | **Active** |
| `agent-04-verification/` | Evidence schema (used by API), SOPs + workflows (docs only) | **Partial** — schema is live, SOPs are aspirational |
| `agent-05-platform-api/` | Full API + dashboard (runs) | **Active** — the core of the system |
| `agent-06-policy-risk/` | Risk register, NDPA checklist, threat model, misuse playbook | **Docs only** — good design thinking, not operationalized |
| `agent-07-integration/` | Architecture overview, dependency tracker, contracts | **Docs only** — project management artifacts |
| `agent-08-regulatory-integration/` | Sync architecture, partnership playbook, governance rules | **Docs only** — aspirational, no regulator relationships exist yet |

## 3. The Verification Ladder

The core design idea: records start untrusted (L0) and earn trust through
evidence accumulation.

| Level | Label | How Earned | Records at This Level |
|-------|-------|------------|----------------------|
| L0 | Mapped | Ingested from a data source | ~6,887 |
| L1 | Contact Confirmed | SMS/phone reply received | ~3 |
| L2 | Evidence Documented | GPS + photo from field visit | ~1 |
| L3 | Regulator Verified | Cross-referenced with PCN/NAFDAC data | 0 |
| L4 | High-Assurance | In-person audit (future) | 0 |

The key insight: even a "CLOSED" or "MOVED" SMS reply promotes to L1.
L1 means "we confirmed a human is reachable at this contact" — it's about
the communication channel, not the operating status. Operating status is
captured separately in the evidence record.

### SMS Verification — What Happened

- **First campaign**: Ogun State pilot, February 2026
- **Messages sent**: 98 (all pharmacies in Ogun with phone numbers)
- **Delivery**: ~78 delivered, ~20 failed (Benin Republic "Pharmacie..."
  records with invalid Nigerian phone numbers)
- **Reply rate**: Unknown at time of writing — webhook endpoints are live
  but no replies have been confirmed yet
- **Cost**: ~$0.40 via Africa's Talking (~$0.005/SMS to Nigerian numbers)
- **AT wallet balance**: ~$10.60 remaining

**Honest assessment**: Sending 98 SMS is a proof of concept, not a
validation of the approach. If the reply rate turns out to be near zero,
the SMS-first L1 strategy needs rethinking. Possible issues: recipients
don't recognize the sender ID, the message gets filtered as spam, or the
phone numbers in our data are stale. We don't know yet.

## 4. Data Sources and Coverage

| Source | Type | Records | Coverage | Quality |
|--------|------|---------|----------|---------|
| GRID3 Nigeria | Open dataset | ~5,600 pharmacies | National, but sparse in some states | Has GPS coordinates, phone numbers for ~30% |
| OpenStreetMap | Open / crowdsourced | ~800 | Urban-biased (Lagos, Abuja, Port Harcourt) | Variable — some have names only |
| Google Places API | Commercial | ~500 (Lagos area) | Limited free-tier extract | Good name/address, phone numbers, but costly to scale |

**Total after deduplication**: 6,891 canonical records.

**Coverage gaps**: GRID3 has the broadest coverage but many records lack
phone numbers (needed for L1 SMS verification). OSM is urban-concentrated.
Google Places is high-quality but extracting national coverage would require
significant API costs.

**States with most phone-equipped L0 records** (ready for SMS campaigns):
Lagos (88), Rivers (89), Edo (86), Delta (84), Ogun (98, already contacted).

## 5. Infrastructure

### Production (Raspberry Pi 5)

The system runs on a Raspberry Pi 5 at `openhealthregistry.org`.

- nginx reverse proxy → uvicorn on port 3004
- PostgreSQL 17 with the same schema
- SSL via Let's Encrypt / Certbot
- **No process manager** — server dies between SSH sessions
- **Environment variables not persisted** — AT credentials must be re-exported each time
- **No backups** — if the SD card fails, the database is gone

This is a prototype deployment. It works for demonstrating the system and
running SMS campaigns, but it is not production infrastructure.

### What Needs to Change for Real Production

1. Move to a VPS ($5-10/mo) with persistent storage and automated backups
2. systemd service for uvicorn (auto-restart on crash/reboot)
3. Environment variables in a `.env` file or systemd unit
4. PostgreSQL backup cron job (pg_dump to off-device storage)
5. Monitoring/alerting (at minimum: is the process running?)

## 6. What Worked

1. **The validation ladder as a design primitive.** Separating "we know
   this place exists in a dataset" from "we've confirmed contact" from
   "a regulator vouches for it" created clean separation of concerns.
   The append-only status history means you can always trace how a record
   got to its current level.

2. **Gateway-agnostic SMS outbox.** The API creates messages in an outbox
   table; a separate gateway script polls and sends. Switching from AT to
   Twilio or a local bulk SMS provider means writing a new 50-line gateway
   script, not redesigning the system.

3. **Starting with real data, not synthetic data.** Ingesting GRID3 + OSM +
   Google Places from day one forced design decisions around messy names,
   missing coordinates, and duplicate entries that wouldn't have surfaced
   with test fixtures.

4. **Comprehensive test suite early.** 347 tests meant we could refactor
   confidently and catch regressions from SMS integration, webhook changes,
   and regulator sync additions.

## 7. What Didn't Work (or Hasn't Yet)

1. **The 8-agent directory structure.** Conceived as parallel workstreams,
   but this was always a solo build. The "agents" became directories with
   docs that describe a system larger than what exists. The actual code is
   concentrated in three directories: `agent-02` (ingestion), `agent-03`
   (dedup), and `agent-05` (API). The rest is planning artifacts.

2. **TypeScript API layer (abandoned).** CLAUDE.md specifies TypeScript for
   the API. In practice, Python/FastAPI was chosen because the ingestion
   and dedup pipelines were already Python, and maintaining two languages
   for a solo project added friction without benefit.

3. **Regulator relationships (not started).** The system was designed to
   sync with PCN/NAFDAC/NHIA, but no conversations with these bodies have
   happened. The sync architecture and partnership playbook exist as docs,
   but they describe a future that requires relationship-building, not code.

4. **SMS reply rate (unknown).** The first campaign went out but reply data
   hasn't come back yet. This is the single most important metric for the
   project's L1 strategy. If pharmacies don't reply to SMS, the whole
   contact-confirmation approach needs rethinking.

5. **Pi deployment fragility.** Running a "live" service on a Raspberry Pi
   without systemd, backups, or persistent env vars was expedient but
   creates real risk. One power cycle and the database could be gone.

## 8. Costs So Far

| Item | Cost | Notes |
|------|------|-------|
| Africa's Talking SMS | ~$0.40 | 98 messages at ~$0.005 each |
| AT wallet loaded | $11.00 | Enough for ~2,200 more SMS |
| Google Places API | $0 | Free tier quota |
| Domain (openhealthregistry.org) | TBD | |
| Raspberry Pi 5 | Already owned | |
| VPS (future) | ~$5-10/mo | Recommended next step |
| **Total spent** | **~$11.40** | |

## 9. Files in This Repository

### Running Code

```
agent-02-data-acquisition/scripts/    # Ingestion (GRID3, OSM, Google Places, geocoding)
agent-03-deduplication/algorithms/    # Entity resolution (name, geo, composite)
agent-03-deduplication/scripts/       # Cross-source dedup runner
agent-05-platform-api/src/            # FastAPI app (API, auth, SMS, verification, FHIR)
agent-05-platform-api/scripts/        # Key management, DB migration, SMS gateway
agent-05-platform-api/src/static/     # Dashboard HTML
agent-07-integration/                 # Enum contract check script
tests/                                # 347 tests
serve.py                              # Entry point for local/production server
```

### Schema and Configuration

```
agent-01-data-architecture/sql/       # 8 DDL scripts (PostgreSQL + PostGIS)
agent-01-data-architecture/fhir/      # FHIR R4 Location/Organization mappings
agent-02-data-acquisition/sources/    # Source registry + raw data extracts
agent-02-data-acquisition/templates/  # Ingestion format templates
agent-03-deduplication/config/        # Merge rules (thresholds, weights)
agent-04-verification/schemas/        # Evidence capture JSON schema
agent-05-platform-api/api/            # OpenAPI 3.1 spec
```

### Design Documents

```
agent-01-data-architecture/docs/      # Data governance, FHIR mapping spec
agent-02-data-acquisition/docs/       # Coverage report template
agent-03-deduplication/docs/          # Dedup methodology
agent-04-verification/docs/           # Verification SOPs, reverification schedule
agent-04-verification/workflows/      # Dispute workflow
agent-05-platform-api/docs/           # API developer guide
agent-05-platform-api/middleware/     # Auth, rate limiting, audit logging specs
agent-06-policy-risk/                 # Risk register, NDPA, threat model, misuse playbook
agent-07-integration/                 # Architecture overview, dependency tracker, contracts
agent-08-regulatory-integration/      # Sync architecture, partnership playbook, governance
```

## 10. What Comes Next

In priority order:

1. **Check SMS reply rate.** If Ogun replies are near-zero after a week,
   investigate why before sending more campaigns. Consider: different
   sender ID, WhatsApp Business API, or phone call verification.

2. **Stabilize production.** systemd service, persistent env vars, automated
   PostgreSQL backups. Move off Pi to a VPS if the project continues.

3. **Find one real user.** A pharmacy association, NGO, or state health
   ministry that would actually query this data. The registry's value is
   zero until someone besides us consumes it.

4. **Talk to PCN.** Even an informal conversation about data formats and
   willingness to share/receive data is worth more than any amount of
   FHIR mapping or sync architecture documentation.

5. **Clean the dataset.** Remove ~20 Benin Republic "Pharmacie..." records
   from the Ogun set. Backfill phone numbers for states where GRID3 data
   is missing them.

---

## Appendix: How This Was Built

This project was built by a solo developer using Claude Code (Anthropic's
AI coding assistant) over approximately 2 weeks in February 2026. The
8-agent structure was the original project plan; Claude Code executed each
workstream's prompts sequentially. The planning documents were generated
first, then progressively replaced by working code as the project moved
from design to implementation.

The build sequence:
1. Schema design + governance docs (agents 01, 06, 08)
2. Ingestion pipelines + real data (agent 02)
3. Deduplication algorithms (agent 03)
4. API + dashboard (agent 05)
5. Verification workflows + SMS system (agents 04, 05)
6. Pi deployment + first live campaign

Total development time: ~2 weeks from first commit to first SMS sent.
