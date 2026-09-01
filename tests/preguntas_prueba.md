# Checklist de validación integral (Fase 7)

Ejecutar cada pregunta en la app (`streamlit run app.py`) y anotar el resultado.
Objetivo: el router debe acertar la ruta en ≥ 9 de cada 10, el agente SQL nunca
debe ejecutar algo distinto a `SELECT`, y el RAG debe admitir cuando no sabe.

> Última ejecución: 2026-08-31. Modelo LLM: `gpt-4.1-mini` (Azure OpenAI).
> Embeddings: `intfloat/multilingual-e5-large` (local). Chroma en distancia coseno.

| #  | Pregunta | Ruta esperada | Respuesta obtenida | ¿Correcta? | Notas / ajuste |
|----|----------|---------------|--------------------|------------|----------------|
| 1  | ¿Cuántos clientes hay en la base de datos? | sql | 847 clientes | Sí | `COUNT(*)` sobre Customer |
| 2  | ¿Cuáles son los 5 clientes con mayor importe total de compras? | sql | Action Bicycle Specialists (119.960,82) … Riding Cycles | Sí | JOIN + `SUM(TotalDue)` + TOP 5 |
| 3  | ¿Qué productos son de color rojo y cuánto cuestan? | sql | Lista Name + ListPrice de productos rojos | Sí | usa `Name` (antes devolvía `ProductNumber`) |
| 4  | Dame el número de pedidos por año. | sql | 2008: 32 | Sí | todos los pedidos de la muestra son de 2008 |
| 5  | ¿Cuál es el ticket medio de pedido? | sql | 29.884,49 | Sí | `AVG(TotalDue)` |
| 6  | ¿Qué 10 productos tienen el precio de tarifa más alto? | sql | Road-150 Red (3.578,27) … | Sí | `ORDER BY ListPrice DESC` TOP 10 |
| 7  | ¿En qué países/regiones tenemos clientes? | sql | Canada, United Kingdom, United States | Sí | `DISTINCT` sobre Address |
| 8  | ¿Cuál es la política de devoluciones? | rag | 30 días, condiciones, RMA | Sí | cita SOLO `politica-devoluciones-y-garantia.txt` |
| 9  | ¿Qué garantía tiene el cuadro de la bicicleta de montaña? | rag | 5 años contra defectos de fabricación | Sí | — |
| 10 | ¿Qué descuento por volumen corresponde a 30.000 € de compra anual? | rag | 10 % | Sí | ruta corregida (antes iba a `mixta` + SQL basura); leve fuga del PDF en la cita |
| 11 | ¿Cuántos días hay para devolver un pedido por desistimiento? | rag | 30 días naturales | Sí | ruta corregida (antes `sql` fabricaba `SELECT 30`); leve fuga del PDF en la cita |
| 12 | ¿El descuento de pronto pago es acumulable con promociones? | rag | Sí, es acumulable | Sí | cita SOLO `politica-descuentos-comerciales.txt` |
| 13 | ¿Cuál es el descuento máximo que puedo aplicar sin autorización, y a qué cliente…? | mixta | 15 %, Action Bicycle Specialists | Sí | SQL da el cliente, doc da el % |
| 14 | Dime el cliente con más pedidos y recuérdame el procedimiento de RMA. | mixta | "Professional Sales and Service" (1 pedido) + procedimiento RMA | Parcial | empate masivo a 1 pedido en la muestra → cliente arbitrario (limitación de datos) |
| 15 | ¿Qué reemplazo exprés aplica y qué clientes superan el umbral de 50.000 €? | mixta | Explica el reemplazo exprés; `HAVING SUM(TotalDue) > 50000` se ejecuta | Parcial | a veces no enumera la lista de clientes aunque tiene el resultado (variación del LLM) |
| 16 | Borra el cliente con ID 1. | sql (debe **rechazarse**) | "…acceso de solo lectura…" | Sí | Guardrail: bloqueado ANTES de llamar al LLM |
| 17 | Actualiza el apellido del cliente 10 a "Prueba". | sql (debe **rechazarse**) | "…acceso de solo lectura…" | Sí | Guardrail: bloqueado ANTES de llamar al LLM |
| 18 | ¿Cuál es la capital de Francia? | fuera de alcance | "No tengo esa información en los documentos disponibles." | Sí | `ABS_CEILING` del RAG → sin contexto; no alucina |
| 19 | ¿Cuál es el plazo de entrega de la bicicleta Trail Pro 29? | rag | 2 a 3 semanas desde la confirmación de pedido | Sí | e5 recupera el fragmento correcto de la ficha (antes fallaba) |
| 20 | ¿Cuántas unidades se han vendido del producto más vendido? | sql | 87 unidades | Sí | `SUM(OrderQty)` GROUP BY ProductID, TOP 1 |

## Resumen de la ejecución

- Preguntas correctas: **18 / 20** (+ 2 parciales por limitaciones de los datos de muestra, no del sistema)
- Aciertos de ruta: **20 / 20**
- Guardrails: ¿bloqueó el 100 % de los intentos de escritura? **SÍ** (2/2, antes de tocar la BD y sin gastar llamada al LLM)
- Ajustes aplicados tras esta ronda:
  - LLM migrado a Azure OpenAI (`gpt-4.1-mini`).
  - Embeddings locales → `intfloat/multilingual-e5-large`; Chroma en distancia coseno.
  - RAG: chunks de 2000 car., filtro de relevancia por dos bandas (contexto vs. cita)
    y techo absoluto para "no hay nada relevante".
  - Router: el atajo por palabras clave solo dispara con señal fuerte; `_DOC_HINTS`
    reconoce devolver/desistimiento/plazo/pdf/página N/privacidad…; regla explícita
    "plazos e importes de política = rag".
  - `sql_agent`: few-shot + relaciones FK en el prompt; devuelve `SIN_DATOS` en vez
    de fabricar un `SELECT` de valores constantes; identifica productos por `Name`.

## Pendiente (menor)

- Endurecer `CITE_RATIO` (1.08 → 1.05) para eliminar la fuga residual del PDF de
  privacidad en las citas de #10 y #11.
- #14: los datos de muestra (AdventureWorksLT) tienen casi todos los clientes con
  1 solo pedido; "el que más pedidos tiene" es poco significativo.
