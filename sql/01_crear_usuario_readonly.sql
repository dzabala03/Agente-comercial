/* ===========================================================================
   01_crear_usuario_readonly.sql
   Crea el login/usuario de SOLO LECTURA que usará el agente.
   Ejecutar CONECTADO COMO 'sa' contra la base AdventureWorksLT ya restaurada.

   Cambia 'CAMBIA_esta_password_1!' por una contraseña fuerte y ponla luego
   en tu .env como SQL_SERVER_PASSWORD.
   =========================================================================== */

-- 1. Login a nivel de servidor
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'agente_readonly')
BEGIN
    CREATE LOGIN [agente_readonly]
        WITH PASSWORD = N'CAMBIA_esta_password_1!',
             CHECK_POLICY = ON;
END
GO

-- 2. Usuario dentro de la base AdventureWorksLT
USE [AdventureWorksLT];
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'agente_readonly')
BEGIN
    CREATE USER [agente_readonly] FOR LOGIN [agente_readonly];
END
GO

-- 3. Permisos: SOLO lectura
ALTER ROLE [db_datareader] ADD MEMBER [agente_readonly];
GO

-- 4. Denegar explícitamente escritura y ejecución (defensa en profundidad).
--    OJO: NO denegar CONTROL a nivel de base de datos: CONTROL es un permiso
--    "paraguas" y un DENY CONTROL bloquea incluso la conexión a la BD
--    ("Cannot open database ... requested by the login").
DENY INSERT, UPDATE, DELETE TO [agente_readonly];
DENY EXECUTE TO [agente_readonly];
GO

PRINT 'Usuario agente_readonly listo con permisos de solo lectura.';
GO
