-- Transfere la propriete des tables/sequences du schema "public" (donc pas
-- les objets systeme) de chaque base tenant, vers le role dedie au tenant.
-- Remplace REASSIGN OWNED BY (refuse par Postgres car "postgres" est aussi
-- lie a des objets requis par le systeme).
-- A executer connecte en superuser (postgres) :
--   psql -U postgres -h 127.0.0.1 -f scripts/transferer_proprietaire_tenants.sql

\c lecarnet_alpha
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO client_alpha', r.tablename);
    END LOOP;
    FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO client_alpha', r.sequencename);
    END LOOP;
END $$;

\c lecarnet_test
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO client_test', r.tablename);
    END LOOP;
    FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO client_test', r.sequencename);
    END LOOP;
END $$;

\c lecarnet_anonymus
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO anonymus', r.tablename);
    END LOOP;
    FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO anonymus', r.sequencename);
    END LOOP;
END $$;

\c lecarnet_bravo
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO client_bravo', r.tablename);
    END LOOP;
    FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO client_bravo', r.sequencename);
    END LOOP;
END $$;

\c lecarnet_huppe
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO huppe', r.tablename);
    END LOOP;
    FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO huppe', r.sequencename);
    END LOOP;
END $$;

\c lecarnet_client_de_nancy
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO client_de_nancy', r.tablename);
    END LOOP;
    FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO client_de_nancy', r.sequencename);
    END LOOP;
END $$;
