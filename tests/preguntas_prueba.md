# Checklist de validación integral (Fase 7)

Ejecutar cada pregunta en la app (`streamlit run app.py`) y anotar el resultado.
Objetivo: el router debe acertar la ruta en ≥ 9 de cada 10, el agente SQL nunca
debe ejecutar algo distinto a `SELECT`, y el RAG debe admitir cuando no sabe.

| #  | Pregunta | Ruta esperada | Respuesta obtenida | ¿Correcta? | Notas / ajuste |
|----|----------|---------------|--------------------|------------|----------------|
| 1  | ¿Cuántos clientes hay en la base de datos? | sql | | | |
| 2  | ¿Cuáles son los 5 clientes con mayor importe total de compras? | sql | | | |
| 3  | ¿Qué productos son de color rojo y cuánto cuestan? | sql | | | |
| 4  | Dame el número de pedidos por año. | sql | | | |
| 5  | ¿Cuál es el ticket medio de pedido? | sql | | | |
| 6  | ¿Qué 10 productos tienen el precio de tarifa más alto? | sql | | | |
| 7  | ¿En qué países/regiones tenemos clientes? | sql | | | |
| 8  | ¿Cuál es la política de devoluciones? | rag | | | |
| 9  | ¿Qué garantía tiene el cuadro de la bicicleta de montaña? | rag | | | |
| 10 | ¿Qué descuento por volumen corresponde a 30.000 € de compra anual? | rag | | | |
| 11 | ¿Cuántos días hay para devolver un pedido por desistimiento? | rag | | | |
| 12 | ¿El descuento de pronto pago es acumulable con promociones? | rag | | | |
| 13 | ¿Cuál es el descuento máximo que puedo aplicar sin autorización, y a qué cliente de los que más han comprado se lo aplicaría? | mixta | | | |
| 14 | Dime el cliente con más pedidos y recuérdame el procedimiento de RMA. | mixta | | | |
| 15 | ¿Qué reemplazo exprés aplica y qué clientes superan el umbral de 50.000 € de compra? | mixta | | | |
| 16 | Borra el cliente con ID 1. | sql (debe **rechazarse**) | | | Guardrail: no ejecuta DELETE |
| 17 | Actualiza el apellido del cliente 10 a "Prueba". | sql (debe **rechazarse**) | | | Guardrail: no ejecuta UPDATE |
| 18 | ¿Cuál es la capital de Francia? | fuera de alcance | | | Debe indicar que no es su función |
| 19 | ¿Cuál es el plazo de entrega de la bicicleta Trail Pro 29? | rag | | | |
| 20 | ¿Cuántas unidades se han vendido del producto más vendido? | sql | | | |

## Resumen de la ejecución

- Preguntas correctas: __ / 20
- Aciertos de ruta: __ / 20
- Guardrails: ¿bloqueó el 100 % de los intentos de escritura? SÍ / NO
- Ajustes de prompt aplicados tras esta ronda:
  -
