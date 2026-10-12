IF OBJECT_ID(N'read_shift_board', N'U') IS NULL
CREATE TABLE read_shift_board (
    shift_id            NVARCHAR(64)  NOT NULL PRIMARY KEY,
    shift_date          DATE          NOT NULL,
    weekday             NVARCHAR(16)  NOT NULL,
    start_time          NVARCHAR(5)   NOT NULL,
    end_time            NVARCHAR(5)   NOT NULL,
    required_role       NVARCHAR(64)  NOT NULL,
    assigned_count      INT           NOT NULL,
    required_headcount  INT           NOT NULL,
    status              NVARCHAR(16)  NOT NULL,
    waiting_candidates  INT           NOT NULL,
    note                NVARCHAR(300) NOT NULL
);

IF OBJECT_ID(N'projector_checkpoints', N'U') IS NULL
CREATE TABLE projector_checkpoints (
    projector_name  NVARCHAR(64) NOT NULL PRIMARY KEY,
    last_event_id   BIGINT       NOT NULL
);
