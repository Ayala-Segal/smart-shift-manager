IF OBJECT_ID(N'read_employee_hours', N'U') IS NULL
CREATE TABLE read_employee_hours (
    employee_id     NVARCHAR(64) NOT NULL,
    week_start      DATE         NOT NULL,
    approved_hours  FLOAT        NOT NULL,
    CONSTRAINT PK_read_employee_hours PRIMARY KEY (employee_id, week_start)
);

IF OBJECT_ID(N'read_approved_assignments', N'U') IS NULL
CREATE TABLE read_approved_assignments (
    assignment_id NVARCHAR(64) NOT NULL PRIMARY KEY,
    employee_id   NVARCHAR(64) NOT NULL,
    shift_id      NVARCHAR(64) NOT NULL,
    shift_date    DATE         NOT NULL,
    start_time    NVARCHAR(5)  NOT NULL,
    end_time      NVARCHAR(5)  NOT NULL,
    hours         FLOAT        NOT NULL,
    active        BIT          NOT NULL
);
