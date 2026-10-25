SELECT current_database();

-- Cho phép kết nối tới database
GRANT CONNECT
ON DATABASE epl_raw_storage_v2
TO epl_cleaner;

-- Cho phép truy cập schema raw
GRANT USAGE
ON SCHEMA raw
TO epl_cleaner;

-- Chỉ cho phép đọc các bảng trong raw
GRANT SELECT
ON ALL TABLES IN SCHEMA raw
TO epl_cleaner;

-- Mặc định các phiên làm việc ở chế độ read-only
ALTER ROLE epl_cleaner
SET default_transaction_read_only = on;


-- kiểm tra cấp quuyền
SELECT
    has_database_privilege(
        'epl_cleaner',
        'epl_raw_storage_v2',
        'CONNECT'
    ) AS can_connect,

    has_schema_privilege(
        'epl_cleaner',
        'raw',
        'USAGE'
    ) AS can_access_raw,

    has_table_privilege(
        'epl_cleaner',
        'raw.raw_records',
        'SELECT'
    ) AS can_select,

    has_table_privilege(
        'epl_cleaner',
        'raw.raw_records',
        'INSERT'
    ) AS can_insert,

    has_table_privilege(
        'epl_cleaner',
        'raw.raw_records',
        'UPDATE'
    ) AS can_update,

    has_table_privilege(
        'epl_cleaner',
        'raw.raw_records',
        'DELETE'
    ) AS can_delete;