IF OBJECT_ID(N'read_shift_requests', N'U') IS NULL
CREATE TABLE read_shift_requests (
    request_id                NVARCHAR(64)  NOT NULL PRIMARY KEY,
    shift_id                  NVARCHAR(64)  NOT NULL,
    request_type              NVARCHAR(32)  NOT NULL,
    employee_id               NVARCHAR(64)  NOT NULL,
    full_name                 NVARCHAR(120) NOT NULL,
    professional_role         NVARCHAR(64)  NOT NULL,
    status                    NVARCHAR(64)  NOT NULL,
    note                      NVARCHAR(300) NOT NULL,
    replacement_employee_id   NVARCHAR(64)  NULL,
    replacement_name          NVARCHAR(120) NULL
);

IF OBJECT_ID(N'read_availability_constraints', N'U') IS NULL
CREATE TABLE read_availability_constraints (
    constraint_id     NVARCHAR(64)  NOT NULL PRIMARY KEY,
    employee_id       NVARCHAR(64)  NOT NULL,
    start_date        DATE          NOT NULL,
    end_date          DATE          NOT NULL,
    constraint_type   NVARCHAR(32)  NOT NULL,
    note              NVARCHAR(300) NOT NULL
);
