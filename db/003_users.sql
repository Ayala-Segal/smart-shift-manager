CREATE TABLE users (
    user_id            NVARCHAR(64)  NOT NULL PRIMARY KEY,
    full_name          NVARCHAR(120) NOT NULL,
    email              NVARCHAR(255) NOT NULL,
    password_hash      NVARCHAR(255) NOT NULL,
    role               NVARCHAR(16)  NOT NULL,
    professional_role  NVARCHAR(64)  NOT NULL
        CONSTRAINT DF_users_professional_role DEFAULT '',
    is_active          BIT           NOT NULL
        CONSTRAINT DF_users_is_active DEFAULT 1,
    CONSTRAINT UQ_users_email UNIQUE (email),
    CONSTRAINT CK_users_role CHECK (role IN ('manager', 'employee'))
);
