-- Feature flags table supporting global flags and per-shop overrides.
CREATE TABLE IF NOT EXISTS feature_flags (
    id SERIAL PRIMARY KEY,
    flag_key VARCHAR(255) NOT NULL,
    shop_id VARCHAR(255) NULL,
    enabled BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partial unique indexes ensure at most one global flag and at most one flag per shop.
CREATE UNIQUE INDEX IF NOT EXISTS uq_feature_flags_global
    ON feature_flags (flag_key)
    WHERE shop_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_feature_flags_shop
    ON feature_flags (flag_key, shop_id)
    WHERE shop_id IS NOT NULL;

-- Experiments configuration table.
CREATE TABLE IF NOT EXISTS experiments (
    id SERIAL PRIMARY KEY,
    experiment_key VARCHAR(255) NOT NULL UNIQUE,
    variants JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Idempotent experiment exposure log.
CREATE TABLE IF NOT EXISTS exposures (
    id BIGSERIAL PRIMARY KEY,
    experiment_key VARCHAR(255) NOT NULL,
    shop_id VARCHAR(255) NOT NULL,
    variant VARCHAR(255) NOT NULL,
    exposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_exposures_key_shop UNIQUE (experiment_key, shop_id)
);
