-- Cree un role PostgreSQL distinct par tenant, avec mot de passe base sur
-- le nom du tenant (consonnes en minuscule, voyelles en majuscule, + 12345!).
-- A executer une fois, connecte en tant que superuser (ex: postgres) :
--   psql -U postgres -h 127.0.0.1 -f scripts/creer_roles_tenants.sql

-- anonymus (base: lecarnet_anonymus)
CREATE ROLE anonymus WITH LOGIN PASSWORD 'AnOnymUs12345!';
ALTER DATABASE lecarnet_anonymus OWNER TO anonymus;

-- client_alpha (base: lecarnet_alpha)
CREATE ROLE client_alpha WITH LOGIN PASSWORD 'clIEntAlphA12345!';
ALTER DATABASE lecarnet_alpha OWNER TO client_alpha;

-- client_test (base: lecarnet_test)
CREATE ROLE client_test WITH LOGIN PASSWORD 'clIEnttEst12345!';
ALTER DATABASE lecarnet_test OWNER TO client_test;

-- client_bravo (base: lecarnet_bravo)
CREATE ROLE client_bravo WITH LOGIN PASSWORD 'clIEntbrAvO12345!';
ALTER DATABASE lecarnet_bravo OWNER TO client_bravo;

-- huppe (base: lecarnet_huppe)
CREATE ROLE huppe WITH LOGIN PASSWORD 'hUppE12345!';
ALTER DATABASE lecarnet_huppe OWNER TO huppe;

-- client_de_nancy (base: lecarnet_client_de_nancy)
CREATE ROLE client_de_nancy WITH LOGIN PASSWORD 'clIEntdEnAncy12345!';
ALTER DATABASE lecarnet_client_de_nancy OWNER TO client_de_nancy;
