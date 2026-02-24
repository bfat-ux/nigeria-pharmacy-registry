"""FHIR R4 interoperability endpoints — Location and Organization resources."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import db
from ..auth import require_tier
from ..db import extras
from ..helpers import iso, level_label

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# FHIR value-map helpers
# ---------------------------------------------------------------------------

_FHIR_LOCATION_STATUS = {
    "operational": "active",
    "temporarily_closed": "suspended",
    "permanently_closed": "inactive",
    "unknown": "active",
}

_FHIR_FACILITY_TYPE = {
    "pharmacy": {"code": "PHARM", "display": "Community Pharmacy"},
    "ppmv": {"code": "PPMV", "display": "Patent and Proprietary Medicine Vendor"},
    "hospital_pharmacy": {"code": "HOSPHARM", "display": "Hospital Pharmacy"},
}

_FHIR_VALIDATION_LEVEL = {
    "L0_mapped": {"code": "L0", "display": "Mapped"},
    "L1_contact_confirmed": {"code": "L1", "display": "Contact Confirmed"},
    "L2_evidence_documented": {"code": "L2", "display": "Evidence Documented"},
    "L3_regulator_verified": {"code": "L3", "display": "Regulator/Partner Verified"},
    "L4_high_assurance": {"code": "L4", "display": "High-Assurance"},
}

_FHIR_ORG_ACTIVE = {
    "operational": True,
    "temporarily_closed": True,
    "permanently_closed": False,
    "unknown": True,
}

_FHIR_EXT_ID_SYSTEM = {
    "pcn_premises_id": "https://pcn.gov.ng/premises",
    "nhia_facility_id": "https://nhia.gov.ng/facilities",
    "osm_node_id": "https://www.openstreetmap.org/node",
    "grid3_id": "https://grid3.gov.ng/facilities",
    "google_place_id": "https://maps.google.com/place",
}

_FHIR_CONTACT_SYSTEM = {
    "phone": "phone",
    "email": "email",
    "whatsapp": "other",
}

NPR_BASE = "https://nigeria-pharmacy-registry.internal/fhir"

# ---------------------------------------------------------------------------
# FHIR resource builders
# ---------------------------------------------------------------------------


def build_fhir_location(
    row: dict,
    contacts: list[dict],
    ext_ids: list[dict],
) -> dict:
    """Build a FHIR R4 Location resource from DB rows."""
    pharmacy_id = str(row["id"])
    op_status = row.get("operational_status") or "unknown"
    fac_type = row.get("facility_type") or "pharmacy"
    val_level = row.get("current_validation_level") or "L0_mapped"

    resource: dict[str, Any] = {
        "resourceType": "Location",
        "id": pharmacy_id,
        "meta": {
            "profile": [f"{NPR_BASE}/StructureDefinition/NPR-Location"],
            "lastUpdated": iso(row.get("updated_at")),
        },
        "name": row["name"],
        "status": _FHIR_LOCATION_STATUS.get(op_status, "active"),
    }

    ft = _FHIR_FACILITY_TYPE.get(fac_type, {"code": fac_type, "display": fac_type})
    resource["type"] = [
        {
            "coding": [
                {
                    "system": f"{NPR_BASE}/CodeSystem/facility-type",
                    "code": ft["code"],
                    "display": ft["display"],
                }
            ],
            "text": ft["display"],
        }
    ]

    address: dict[str, Any] = {"use": "work", "country": row.get("country") or "NG"}
    lines = []
    if row.get("address_line_1"):
        lines.append(row["address_line_1"])
    if row.get("address_line_2"):
        lines.append(row["address_line_2"])
    if lines:
        address["line"] = lines
    if row.get("lga"):
        address["district"] = row["lga"]
    if row.get("state"):
        address["state"] = row["state"]
    if row.get("postal_code"):
        address["postalCode"] = row["postal_code"]

    if row.get("ward"):
        address["extension"] = [
            {
                "url": f"{NPR_BASE}/StructureDefinition/address-ward",
                "valueString": row["ward"],
            }
        ]

    resource["address"] = address

    lat = row.get("latitude")
    lon = row.get("longitude")
    if lat is not None and lon is not None:
        resource["position"] = {
            "latitude": float(lat),
            "longitude": float(lon),
        }

    telecoms = []
    for c in contacts:
        sys = _FHIR_CONTACT_SYSTEM.get(c["contact_type"], "other")
        tc: dict[str, Any] = {
            "system": sys,
            "value": c["contact_value"],
            "use": "work",
        }
        if c.get("is_primary"):
            tc["rank"] = 1
        if sys == "other" and c["contact_type"] == "whatsapp":
            tc["extension"] = [
                {
                    "url": f"{NPR_BASE}/StructureDefinition/telecom-platform",
                    "valueString": "whatsapp",
                }
            ]
        telecoms.append(tc)
    if telecoms:
        resource["telecom"] = telecoms

    identifiers = [
        {
            "system": f"{NPR_BASE}/pharmacy-id",
            "value": pharmacy_id,
        }
    ]
    for eid in ext_ids:
        sys = _FHIR_EXT_ID_SYSTEM.get(eid["identifier_type"], f"{NPR_BASE}/id/{eid['identifier_type']}")
        identifiers.append(
            {
                "system": sys,
                "value": eid["identifier_value"],
            }
        )
    resource["identifier"] = identifiers

    resource["managingOrganization"] = {
        "reference": f"Organization/org-{pharmacy_id}",
        "display": row["name"],
    }

    extensions = []

    vl = _FHIR_VALIDATION_LEVEL.get(val_level, {"code": val_level, "display": val_level})
    extensions.append(
        {
            "url": f"{NPR_BASE}/StructureDefinition/validation-level",
            "valueCoding": {
                "system": f"{NPR_BASE}/CodeSystem/validation-level",
                "code": vl["code"],
                "display": vl["display"],
            },
        }
    )

    if row.get("primary_source"):
        extensions.append(
            {
                "url": f"{NPR_BASE}/StructureDefinition/primary-source",
                "valueString": row["primary_source"],
            }
        )

    if op_status == "unknown":
        extensions.append(
            {
                "url": "http://hl7.org/fhir/StructureDefinition/data-absent-reason",
                "valueCode": "unknown",
            }
        )

    resource["extension"] = extensions

    return resource


def build_fhir_organization(
    row: dict,
    contacts: list[dict],
    ext_ids: list[dict],
) -> dict:
    """Build a FHIR R4 Organization resource from DB rows."""
    pharmacy_id = str(row["id"])
    op_status = row.get("operational_status") or "unknown"
    fac_type = row.get("facility_type") or "pharmacy"

    resource: dict[str, Any] = {
        "resourceType": "Organization",
        "id": f"org-{pharmacy_id}",
        "meta": {
            "profile": [f"{NPR_BASE}/StructureDefinition/NPR-Organization"],
            "lastUpdated": iso(row.get("updated_at")),
        },
        "name": row["name"],
        "active": _FHIR_ORG_ACTIVE.get(op_status, True),
    }

    ft = _FHIR_FACILITY_TYPE.get(fac_type, {"code": fac_type, "display": fac_type})
    resource["type"] = [
        {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/organization-type",
                    "code": "prov",
                    "display": "Healthcare Provider",
                },
                {
                    "system": f"{NPR_BASE}/CodeSystem/facility-type",
                    "code": ft["code"],
                    "display": ft["display"],
                },
            ],
            "text": ft["display"],
        }
    ]

    identifiers = [
        {
            "system": f"{NPR_BASE}/organization-id",
            "value": f"org-{pharmacy_id}",
        }
    ]
    for eid in ext_ids:
        sys = _FHIR_EXT_ID_SYSTEM.get(eid["identifier_type"], f"{NPR_BASE}/id/{eid['identifier_type']}")
        identifiers.append({"system": sys, "value": eid["identifier_value"]})
    resource["identifier"] = identifiers

    telecoms = []
    for c in contacts:
        sys = _FHIR_CONTACT_SYSTEM.get(c["contact_type"], "other")
        tc: dict[str, Any] = {"system": sys, "value": c["contact_value"], "use": "work"}
        if c.get("is_primary"):
            tc["rank"] = 1
        telecoms.append(tc)
    if telecoms:
        resource["telecom"] = telecoms

    named_contacts = []
    for c in contacts:
        if c.get("contact_person"):
            named_contacts.append(
                {
                    "name": {"text": c["contact_person"]},
                    "telecom": [
                        {
                            "system": _FHIR_CONTACT_SYSTEM.get(c["contact_type"], "other"),
                            "value": c["contact_value"],
                        }
                    ],
                }
            )
    if named_contacts:
        resource["contact"] = named_contacts

    address: dict[str, Any] = {"use": "work", "country": row.get("country") or "NG"}
    lines = []
    if row.get("address_line_1"):
        lines.append(row["address_line_1"])
    if row.get("address_line_2"):
        lines.append(row["address_line_2"])
    if lines:
        address["line"] = lines
    if row.get("lga"):
        address["district"] = row["lga"]
    if row.get("state"):
        address["state"] = row["state"]
    if row.get("postal_code"):
        address["postalCode"] = row["postal_code"]
    resource["address"] = [address]

    return resource


def _fhir_bundle(
    resources: list[dict],
    total: int,
    base_url: str,
    resource_type: str,
) -> dict:
    """Wrap a list of FHIR resources in a searchset Bundle."""
    entries = []
    for r in resources:
        entries.append(
            {
                "fullUrl": f"{base_url}/api/fhir/{resource_type}/{r['id']}",
                "resource": r,
                "search": {"mode": "match"},
            }
        )
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "entry": entries,
    }


def _fhir_query_pharmacy(
    pharmacy_id: str,
) -> tuple[dict | None, list[dict], list[dict]]:
    """Fetch a pharmacy row with lat/lon, its contacts, and external IDs."""
    with db.get_conn() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT pl.*,
                       ST_Y(pl.geolocation::geometry) AS latitude,
                       ST_X(pl.geolocation::geometry) AS longitude
                FROM pharmacy_locations pl
                WHERE pl.id = %s
                """,
                (pharmacy_id,),
            )
            row = cur.fetchone()
            if not row:
                return None, [], []

            cur.execute(
                "SELECT * FROM contacts WHERE pharmacy_id = %s ORDER BY is_primary DESC",
                (pharmacy_id,),
            )
            contacts = cur.fetchall()

            cur.execute(
                "SELECT * FROM external_identifiers WHERE pharmacy_id = %s AND is_current = true",
                (pharmacy_id,),
            )
            ext_ids = cur.fetchall()

    return row, list(contacts), list(ext_ids)


# ---------------------------------------------------------------------------
# FHIR endpoints
# ---------------------------------------------------------------------------


@router.get("/api/fhir/metadata")
async def fhir_metadata(request: Request):
    """FHIR R4 CapabilityStatement — describes what this server supports."""
    base = str(request.base_url).rstrip("/")
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": "2026-02-24",
        "kind": "instance",
        "fhirVersion": "4.0.1",
        "format": ["json"],
        "implementation": {
            "description": "Nigeria Pharmacy Registry FHIR R4 read-only endpoint",
            "url": f"{base}/api/fhir",
        },
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {
                        "type": "Location",
                        "profile": f"{NPR_BASE}/StructureDefinition/NPR-Location",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type"},
                        ],
                        "searchParam": [
                            {"name": "name", "type": "string"},
                            {"name": "address-state", "type": "string"},
                            {"name": "type", "type": "token"},
                            {"name": "status", "type": "token"},
                            {"name": "_count", "type": "number"},
                            {"name": "_offset", "type": "number"},
                        ],
                    },
                    {
                        "type": "Organization",
                        "profile": f"{NPR_BASE}/StructureDefinition/NPR-Organization",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type"},
                        ],
                        "searchParam": [
                            {"name": "name", "type": "string"},
                            {"name": "address-state", "type": "string"},
                            {"name": "type", "type": "token"},
                            {"name": "active", "type": "token"},
                            {"name": "_count", "type": "number"},
                            {"name": "_offset", "type": "number"},
                        ],
                    },
                ],
            }
        ],
    }


@router.get(
    "/api/fhir/Location/{pharmacy_id}",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def fhir_location_read(pharmacy_id: str):
    """Read a single pharmacy as a FHIR R4 Location resource."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        row, contacts, ext_ids = _fhir_query_pharmacy(pharmacy_id)
        if not row:
            raise HTTPException(
                status_code=404,
                detail={
                    "resourceType": "OperationOutcome",
                    "issue": [
                        {
                            "severity": "error",
                            "code": "not-found",
                            "diagnostics": f"Location/{pharmacy_id} not found",
                        }
                    ],
                },
            )
        return build_fhir_location(row, contacts, ext_ids)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FHIR Location read failed for %s", pharmacy_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/api/fhir/Location",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def fhir_location_search(
    request: Request,
    name: str | None = Query(None, description="Name search (partial match)"),
    address_state: str | None = Query(None, alias="address-state", description="State filter"),
    type: str | None = Query(None, description="Facility type code (PHARM, PPMV, HOSPHARM)"),
    status: str | None = Query(None, description="FHIR status (active, suspended, inactive)"),
    _count: int = Query(50, ge=1, le=200, alias="_count"),
    _offset: int = Query(0, ge=0, alias="_offset"),
):
    """Search pharmacies returned as FHIR R4 Location Bundle."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        conditions: list[str] = []
        params: list[Any] = []

        if name:
            conditions.append("pl.name ILIKE %s")
            params.append(f"%{name}%")
        if address_state:
            conditions.append("pl.state ILIKE %s")
            params.append(address_state)
        if type:
            code_to_enum = {"PHARM": "pharmacy", "PPMV": "ppmv", "HOSPHARM": "hospital_pharmacy"}
            db_type = code_to_enum.get(type.upper(), type)
            conditions.append("pl.facility_type = %s::facility_type")
            params.append(db_type)
        if status:
            status_to_enum = {"active": "operational", "suspended": "temporarily_closed", "inactive": "permanently_closed"}
            db_status = status_to_enum.get(status.lower())
            if db_status:
                conditions.append("pl.operational_status = %s::operational_status")
                params.append(db_status)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(f"SELECT count(*) FROM pharmacy_locations pl{where}", params)
                total = cur.fetchone()["count"]

                cur.execute(
                    f"""
                    SELECT pl.*,
                           ST_Y(pl.geolocation::geometry) AS latitude,
                           ST_X(pl.geolocation::geometry) AS longitude
                    FROM pharmacy_locations pl
                    {where}
                    ORDER BY pl.state, pl.name
                    LIMIT %s OFFSET %s
                    """,
                    params + [_count, _offset],
                )
                rows = cur.fetchall()

                pharmacy_ids = [str(r["id"]) for r in rows]
                contacts_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}
                ext_ids_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}

                if pharmacy_ids:
                    cur.execute(
                        "SELECT * FROM contacts WHERE pharmacy_id = ANY(%s::uuid[]) ORDER BY is_primary DESC",
                        (pharmacy_ids,),
                    )
                    for c in cur.fetchall():
                        contacts_map[str(c["pharmacy_id"])].append(c)

                    cur.execute(
                        "SELECT * FROM external_identifiers WHERE pharmacy_id = ANY(%s::uuid[]) AND is_current = true",
                        (pharmacy_ids,),
                    )
                    for e in cur.fetchall():
                        ext_ids_map[str(e["pharmacy_id"])].append(e)

        resources = []
        for r in rows:
            pid = str(r["id"])
            resources.append(
                build_fhir_location(r, contacts_map[pid], ext_ids_map[pid])
            )

        base = str(request.base_url).rstrip("/")
        return _fhir_bundle(resources, total, base, "Location")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FHIR Location search failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/api/fhir/Organization/{org_id}",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def fhir_organization_read(org_id: str):
    """Read a single pharmacy organization as a FHIR R4 Organization resource."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    pharmacy_id = org_id.removeprefix("org-")

    try:
        row, contacts, ext_ids = _fhir_query_pharmacy(pharmacy_id)
        if not row:
            raise HTTPException(
                status_code=404,
                detail={
                    "resourceType": "OperationOutcome",
                    "issue": [
                        {
                            "severity": "error",
                            "code": "not-found",
                            "diagnostics": f"Organization/{org_id} not found",
                        }
                    ],
                },
            )
        return build_fhir_organization(row, contacts, ext_ids)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FHIR Organization read failed for %s", org_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/api/fhir/Organization",
    dependencies=[Depends(require_tier("registry_read"))],
)
async def fhir_organization_search(
    request: Request,
    name: str | None = Query(None, description="Name search (partial match)"),
    address_state: str | None = Query(None, alias="address-state", description="State filter"),
    type: str | None = Query(None, description="Facility type code (PHARM, PPMV, HOSPHARM)"),
    active: str | None = Query(None, description="true or false"),
    _count: int = Query(50, ge=1, le=200, alias="_count"),
    _offset: int = Query(0, ge=0, alias="_offset"),
):
    """Search pharmacy organizations as FHIR R4 Organization Bundle."""
    if not db.is_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        conditions: list[str] = []
        params: list[Any] = []

        if name:
            conditions.append("pl.name ILIKE %s")
            params.append(f"%{name}%")
        if address_state:
            conditions.append("pl.state ILIKE %s")
            params.append(address_state)
        if type:
            code_to_enum = {"PHARM": "pharmacy", "PPMV": "ppmv", "HOSPHARM": "hospital_pharmacy"}
            db_type = code_to_enum.get(type.upper(), type)
            conditions.append("pl.facility_type = %s::facility_type")
            params.append(db_type)
        if active is not None:
            if active.lower() == "false":
                conditions.append("pl.operational_status = 'permanently_closed'::operational_status")
            elif active.lower() == "true":
                conditions.append("pl.operational_status != 'permanently_closed'::operational_status")

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with db.get_conn() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(f"SELECT count(*) FROM pharmacy_locations pl{where}", params)
                total = cur.fetchone()["count"]

                cur.execute(
                    f"""
                    SELECT pl.*,
                           ST_Y(pl.geolocation::geometry) AS latitude,
                           ST_X(pl.geolocation::geometry) AS longitude
                    FROM pharmacy_locations pl
                    {where}
                    ORDER BY pl.state, pl.name
                    LIMIT %s OFFSET %s
                    """,
                    params + [_count, _offset],
                )
                rows = cur.fetchall()

                pharmacy_ids = [str(r["id"]) for r in rows]
                contacts_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}
                ext_ids_map: dict[str, list[dict]] = {pid: [] for pid in pharmacy_ids}

                if pharmacy_ids:
                    cur.execute(
                        "SELECT * FROM contacts WHERE pharmacy_id = ANY(%s::uuid[]) ORDER BY is_primary DESC",
                        (pharmacy_ids,),
                    )
                    for c in cur.fetchall():
                        contacts_map[str(c["pharmacy_id"])].append(c)

                    cur.execute(
                        "SELECT * FROM external_identifiers WHERE pharmacy_id = ANY(%s::uuid[]) AND is_current = true",
                        (pharmacy_ids,),
                    )
                    for e in cur.fetchall():
                        ext_ids_map[str(e["pharmacy_id"])].append(e)

        resources = []
        for r in rows:
            pid = str(r["id"])
            resources.append(
                build_fhir_organization(r, contacts_map[pid], ext_ids_map[pid])
            )

        base = str(request.base_url).rstrip("/")
        return _fhir_bundle(resources, total, base, "Organization")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FHIR Organization search failed")
        raise HTTPException(status_code=500, detail=str(e))
