CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TYPE platform_enum  AS ENUM ('instagram', 'threads');
CREATE TYPE job_status_enum AS ENUM ('pending', 'running', 'completed', 'failed');
CREATE TYPE job_type_enum  AS ENUM ('profile', 'posts', 'followers', 'following', 'posts_detail', 'post');

CREATE TABLE accounts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform     platform_enum NOT NULL,
    username     VARCHAR(255) NOT NULL,
    display_name VARCHAR(255),
    bio          TEXT,
    followers    INTEGER DEFAULT 0,
    following    INTEGER DEFAULT 0,
    post_count   INTEGER DEFAULT 0,
    is_private   BOOLEAN DEFAULT FALSE,
    raw_data     JSONB,
    scraped_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (platform, username)
);

CREATE TABLE scrape_jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform    platform_enum NOT NULL,
    job_type    job_type_enum NOT NULL,
    target      VARCHAR(255) NOT NULL,
    status      job_status_enum DEFAULT 'pending',
    rq_job_id   VARCHAR(255),
    queue_name  VARCHAR(50) DEFAULT 'default',
    result      JSONB,
    error       TEXT,
    enqueued_at TIMESTAMPTZ DEFAULT NOW(),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE posts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    platform   platform_enum NOT NULL,
    post_id    VARCHAR(255) NOT NULL,
    caption    TEXT,
    media_type VARCHAR(50),
    media_url  TEXT,
    permalink  TEXT,
    likes      INTEGER DEFAULT 0,
    comments   INTEGER DEFAULT 0,
    shares     INTEGER DEFAULT 0,
    views      INTEGER DEFAULT 0,
    raw_data   JSONB,
    posted_at  TIMESTAMPTZ,
    scraped_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (platform, post_id)
);

CREATE TABLE metrics (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id     UUID REFERENCES posts(id) ON DELETE CASCADE,
    likes       INTEGER DEFAULT 0,
    comments    INTEGER DEFAULT 0,
    shares      INTEGER DEFAULT 0,
    views       INTEGER DEFAULT 0,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_accounts_platform_username ON accounts(platform, username);
CREATE INDEX idx_scrape_jobs_status         ON scrape_jobs(status);
CREATE INDEX idx_posts_account_id           ON posts(account_id);
CREATE INDEX idx_posts_platform_post_id     ON posts(platform, post_id);
