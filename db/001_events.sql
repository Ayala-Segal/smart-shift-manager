CREATE TABLE events (
    event_id        BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    aggregate_type  NVARCHAR(64)  NOT NULL,
    aggregate_id    NVARCHAR(64)  NOT NULL,
    event_type      NVARCHAR(64)  NOT NULL,
    payload_json    NVARCHAR(MAX) NOT NULL,
    version         INT           NOT NULL,
    occurred_at     DATETIME2     NOT NULL
        CONSTRAINT DF_events_occurred_at DEFAULT SYSUTCDATETIME(),
    actor_id        NVARCHAR(64)  NOT NULL,
    CONSTRAINT UQ_events_aggregate_version UNIQUE (aggregate_id, version)
);
