/* ===========================================================================
   02_smoke_test.sql
   Comprobaciones rápidas. Ejecutar CONECTADO COMO 'agente_readonly'
   (así verificas de paso que las credenciales del .env funcionan).
   =========================================================================== */

-- Debe devolver un número > 0
SELECT COUNT(*) AS total_clientes FROM SalesLT.Customer;

-- Debe devolver 10 filas
SELECT TOP 10 CustomerID, FirstName, LastName, CompanyName
FROM SalesLT.Customer
ORDER BY CustomerID;

-- Top 5 clientes por importe total comprado
SELECT TOP 5
       c.CustomerID,
       c.CompanyName,
       SUM(soh.TotalDue) AS total_comprado
FROM SalesLT.Customer c
JOIN SalesLT.SalesOrderHeader soh ON soh.CustomerID = c.CustomerID
GROUP BY c.CustomerID, c.CompanyName
ORDER BY total_comprado DESC;

-- Esta sentencia DEBE fallar con error de permisos (prueba del candado de escritura)
-- Descomenta para probar:
-- UPDATE SalesLT.Customer SET LastName = 'HACK' WHERE CustomerID = 1;
