IF OBJECT_ID(N'embedding_profiles', N'U') IS NULL
CREATE TABLE embedding_profiles (
    employee_id   NVARCHAR(64)  NOT NULL PRIMARY KEY,
    profile_text  NVARCHAR(MAX) NOT NULL,
    updated_at    DATETIME2     NOT NULL
);
