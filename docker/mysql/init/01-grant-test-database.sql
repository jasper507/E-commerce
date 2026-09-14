-- The application user can access the development database created by the
-- official image and this one isolated Django test database only.
GRANT ALL PRIVILEGES ON test_project_one.* TO 'project_one'@'%';
