-- FashioNXT casting database
-- One row per applicant per season; overwritten on reapply (matched by email).
-- Auto-cleanup handled by app/cleanup.py, not by the database itself.

CREATE TYPE category AS ENUM ('female', 'male', 'non_binary');
CREATE TYPE casting_status AS ENUM ('pending', 'yes', 'maybe', 'no');
CREATE TYPE pool AS ENUM ('pool_a', 'pool_b', 'backup');
CREATE TYPE applicant_source AS ENUM ('form', 'manual');
CREATE TYPE designer_response AS ENUM ('one', 'two');
CREATE TYPE photo_source AS ENUM ('application', 'in_app');

-- One audition cycle in one city (e.g. "Portland 2026", "Seattle 2026")
CREATE TABLE audition_event (
    id              SERIAL PRIMARY KEY,
    city            TEXT NOT NULL,
    season_label    TEXT NOT NULL,
    start_date      DATE,
    end_date        DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per person, current season only. Reapplying overwrites in place.
CREATE TABLE applicant (
    id                          SERIAL PRIMARY KEY,
    email                       TEXT NOT NULL UNIQUE,
    full_name                   TEXT NOT NULL,
    phone                       TEXT,

    address_street              TEXT,
    address_city                TEXT,
    address_state               TEXT,
    address_zip                 TEXT,
    address_country             TEXT,

    agency_name                 TEXT,
    agency_address              TEXT,

    category                    category NOT NULL,
    height_no_shoes             TEXT,
    dress_size                  TEXT,
    jacket_size                 TEXT,
    sample_size_spec            TEXT,

    willing_without_lodging     BOOLEAN DEFAULT FALSE,
    available_show_days         TEXT,
    available_editorial         BOOLEAN DEFAULT FALSE,

    instagram_handle            TEXT,
    instagram_followers         INTEGER,
    facebook_handle              TEXT,
    facebook_followers          INTEGER,
    tiktok_handle                TEXT,
    tiktok_followers             INTEGER,
    notable_achievements        TEXT,

    interested_editorial         BOOLEAN DEFAULT FALSE,
    interested_promo_partner     BOOLEAN DEFAULT FALSE,
    interested_content_services  BOOLEAN DEFAULT FALSE,
    interested_workshops         BOOLEAN DEFAULT FALSE,

    consent_given                BOOLEAN DEFAULT FALSE,
    talent_release_signed        BOOLEAN DEFAULT FALSE,
    signature_name                TEXT,
    signature_date                DATE,
    is_minor                      BOOLEAN DEFAULT FALSE,
    guardian_name                 TEXT,

    event_id                     INTEGER REFERENCES audition_event(id) ON DELETE SET NULL,
    audition_number               INTEGER,
    casting_status                 casting_status NOT NULL DEFAULT 'pending',
    preselect                      BOOLEAN DEFAULT FALSE,
    source                         applicant_source NOT NULL DEFAULT 'form',

    created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (event_id, audition_number)
);

CREATE INDEX idx_applicant_email ON applicant(email);
CREATE INDEX idx_applicant_updated_at ON applicant(updated_at);
CREATE INDEX idx_applicant_event ON applicant(event_id);

-- 1:1 detailed measurements, filled in by support staff at check-in
CREATE TABLE measurement (
    applicant_id     INTEGER PRIMARY KEY REFERENCES applicant(id) ON DELETE CASCADE,
    tattoos          BOOLEAN,
    piercings        BOOLEAN,
    eye_color        TEXT,
    hair_color       TEXT,
    height           TEXT,
    bust_chest       TEXT,
    hip_size         TEXT,
    waist_size       TEXT,
    arm_length       TEXT,
    inseam           TEXT,
    shoe_size        TEXT,
    dress_size       TEXT,
    jacket_size      TEXT,
    avail_thursday   BOOLEAN DEFAULT FALSE,
    avail_friday     BOOLEAN DEFAULT FALSE,
    avail_saturday   BOOLEAN DEFAULT FALSE,
    swim_ok          BOOLEAN,
    lingerie_ok      BOOLEAN,
    see_through_ok   BOOLEAN,
    notes            TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Multiple photos per applicant, replaced wholesale on reapply
CREATE TABLE photo (
    id            SERIAL PRIMARY KEY,
    applicant_id  INTEGER NOT NULL REFERENCES applicant(id) ON DELETE CASCADE,
    url           TEXT NOT NULL,
    source        photo_source NOT NULL,
    tag           TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_photo_applicant ON photo(applicant_id);

-- Independently editable — a model can be moved in/out of a pool anytime
CREATE TABLE pool_assignment (
    applicant_id  INTEGER PRIMARY KEY REFERENCES applicant(id) ON DELETE CASCADE,
    pool          pool NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One deck per designer per event
CREATE TABLE deck (
    id             SERIAL PRIMARY KEY,
    event_id       INTEGER NOT NULL REFERENCES audition_event(id) ON DELETE CASCADE,
    designer_name  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Which models are in a given designer's deck, and their response
CREATE TABLE deck_model (
    id                  SERIAL PRIMARY KEY,
    deck_id             INTEGER NOT NULL REFERENCES deck(id) ON DELETE CASCADE,
    applicant_id        INTEGER NOT NULL REFERENCES applicant(id) ON DELETE CASCADE,
    designer_response   designer_response,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (deck_id, applicant_id)
);

-- Shareable no-login link for a designer to view/mark their deck
CREATE TABLE deck_link (
    id            SERIAL PRIMARY KEY,
    deck_id       INTEGER NOT NULL UNIQUE REFERENCES deck(id) ON DELETE CASCADE,
    access_token  TEXT NOT NULL UNIQUE,
    expires_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);