-- ============================================================
-- Accent-insensitive exercise search
-- ============================================================
-- Vietnamese users routinely type without diacritics ("nguc" for
-- "ngực"), which an ILIKE against the stored name never matches.
-- unaccent() strips the marks on both sides of the comparison.
--
-- Installed into `extensions` (the Supabase convention); that schema
-- is already on the default search_path, so `unaccent(...)` resolves
-- unqualified.

CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;
