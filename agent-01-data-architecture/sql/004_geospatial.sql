-- =============================================================================
-- 004_geospatial.sql
-- PostGIS setup, spatial indexes, and geospatial helper functions
-- All geospatial columns use SRID 4326 (WGS84) per CLAUDE.md.
-- =============================================================================

-- =============================================================================
-- Enable PostGIS extension
-- =============================================================================
create extension if not exists postgis;

-- enable trigram extension (used by name_trgm index in 001_core_schema.sql)
create extension if not exists pg_trgm;

-- =============================================================================
-- Spatial indexes on pharmacy_locations.geolocation
-- =============================================================================
-- The geolocation column is geography(point, 4326), defined in 001.
-- A GIST index is required for efficient spatial queries.
create index if not exists idx_pharmacy_locations_geolocation
    on pharmacy_locations using gist (geolocation);

-- =============================================================================
-- Helper function: find_pharmacies_within_radius
-- Returns pharmacies within a given radius (km) of a lat/lon point.
-- =============================================================================
create or replace function find_pharmacies_within_radius(
    p_lat double precision,
    p_lon double precision,
    p_radius_km double precision
) returns table (
    id                      uuid,
    name                    text,
    facility_type           facility_type,
    operational_status      operational_status,
    current_validation_level validation_level,
    address_line_1          text,
    lga                     text,
    state                   text,
    latitude                double precision,
    longitude               double precision,
    distance_km             double precision
) as $$
begin
    return query
    select
        pl.id,
        pl.name,
        pl.facility_type,
        pl.operational_status,
        pl.current_validation_level,
        pl.address_line_1,
        pl.lga,
        pl.state,
        st_y(pl.geolocation::geometry) as latitude,
        st_x(pl.geolocation::geometry) as longitude,
        round(
            (st_distance(
                pl.geolocation,
                st_setsrid(st_makepoint(p_lon, p_lat), 4326)::geography
            ) / 1000.0)::numeric, 3
        )::double precision as distance_km
    from pharmacy_locations pl
    where pl.geolocation is not null
      and st_dwithin(
            pl.geolocation,
            st_setsrid(st_makepoint(p_lon, p_lat), 4326)::geography,
            p_radius_km * 1000  -- st_dwithin uses meters for geography type
          )
    order by distance_km asc;
end;
$$ language plpgsql stable;

comment on function find_pharmacies_within_radius is
    'Returns all pharmacies within p_radius_km kilometers of the given lat/lon, ordered by distance ascending.';

-- =============================================================================
-- Helper function: find nearest N pharmacies to a point
-- =============================================================================
create or replace function find_nearest_pharmacies(
    p_lat double precision,
    p_lon double precision,
    p_limit integer default 10
) returns table (
    id                      uuid,
    name                    text,
    facility_type           facility_type,
    operational_status      operational_status,
    current_validation_level validation_level,
    address_line_1          text,
    lga                     text,
    state                   text,
    latitude                double precision,
    longitude               double precision,
    distance_km             double precision
) as $$
begin
    return query
    select
        pl.id,
        pl.name,
        pl.facility_type,
        pl.operational_status,
        pl.current_validation_level,
        pl.address_line_1,
        pl.lga,
        pl.state,
        st_y(pl.geolocation::geometry) as latitude,
        st_x(pl.geolocation::geometry) as longitude,
        round(
            (st_distance(
                pl.geolocation,
                st_setsrid(st_makepoint(p_lon, p_lat), 4326)::geography
            ) / 1000.0)::numeric, 3
        )::double precision as distance_km
    from pharmacy_locations pl
    where pl.geolocation is not null
    order by pl.geolocation <-> st_setsrid(st_makepoint(p_lon, p_lat), 4326)::geography
    limit p_limit;
end;
$$ language plpgsql stable;

comment on function find_nearest_pharmacies is
    'Returns the N nearest pharmacies to the given lat/lon using KNN index scan, ordered by distance ascending.';

-- =============================================================================
-- Helper function: compute bounding box for a state or LGA
-- Useful for map viewport queries.
-- =============================================================================
create or replace function pharmacy_bbox(
    p_state text,
    p_lga text default null
) returns table (
    min_lat double precision,
    min_lon double precision,
    max_lat double precision,
    max_lon double precision,
    pharmacy_count bigint
) as $$
begin
    return query
    select
        st_ymin(st_extent(pl.geolocation::geometry)::geometry) as min_lat,
        st_xmin(st_extent(pl.geolocation::geometry)::geometry) as min_lon,
        st_ymax(st_extent(pl.geolocation::geometry)::geometry) as max_lat,
        st_xmax(st_extent(pl.geolocation::geometry)::geometry) as max_lon,
        count(*)::bigint as pharmacy_count
    from pharmacy_locations pl
    where pl.geolocation is not null
      and pl.state = p_state
      and (p_lga is null or pl.lga = p_lga);
end;
$$ language plpgsql stable;

comment on function pharmacy_bbox is
    'Returns the bounding box (min/max lat/lon) and count of pharmacies for a given state, optionally filtered by LGA.';
