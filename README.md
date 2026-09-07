# SIVAB — Módulo de validación de acceso

Programas fuente de la **Guía de Laboratorio 03 — Estándares y Métricas de Calidad
de Software (2026-262)**, Universidad Manuela Beltrán.
Tema: norma **ISO/IEC 29110:2016**, actividad **SI.5 — Integración y Pruebas**.

Autores: David Alejandro Zambrano Argüello · Samuel Ossa Escobar

## Qué contiene

| Archivo | Rol |
|---|---|
| `sivab/db.py` | Esquema relacional y conexión (una por hilo) |
| `sivab/service.py` | Lógica de negocio: emisión y validación atómica |
| `sivab/api.py` | API REST con FastAPI (capa delgada) |
| `tests/test_validacion.py` | Casos CP-01 a CP-06 del Plan de Pruebas |

## Requisito crítico que se verifica

> **RF-03.** Una boleta emitida puede validarse **exactamente una vez**, incluso si
> dos validadores presentan el mismo código en el mismo instante.

El mecanismo no es un `if` en Python: es un `UPDATE` condicional ejecutado dentro
de una transacción `BEGIN IMMEDIATE`. Ver el docstring de `validar_boleta()`.

## Cómo ejecutar

```bash
pip install -r requirements.txt

# Suite de pruebas (CP-01 a CP-06)
python -m pytest -v

# API REST — documentación interactiva en http://127.0.0.1:8000/docs
uvicorn sivab.api:app --reload
```

## Trazabilidad con el plan de pruebas

| Caso | Prueba | Severidad |
|---|---|---|
| CP-01 | `test_cp01_emision_con_aforo_disponible` (+ caso límite sin aforo) | Alta |
| CP-02 | `test_cp02_primera_validacion_autoriza` | Alta |
| CP-03 | `test_cp03_segunda_validacion_rechaza` | Alta |
| CP-04 | `test_cp04_validacion_concurrente_autoriza_solo_una` | Alta |
| CP-05 | `test_cp05_boleta_de_otro_evento_rechaza` | Media |
| CP-06 | `test_cp06_rendimiento_bajo_carga` (alcance reducido) | Alta |

## Relación con la revisión por pares

- **H-01** — el rechazo por reuso ahora devuelve fecha, hora y punto de acceso de
  la validación previa. Verificado en `test_cp03`.
- **H-02** — CP-04 no existía en el plan original; se añadió y se automatizó.
  Se comprobó que la prueba **detecta** el defecto: al quitar la transacción,
  autoriza 15 de 20 accesos con la misma boleta.

## Definición de Hecho (H-04)

Una tarea se cierra solo si: tiene prueba automatizada, pasó revisión por pares
e integró en `main` con la suite en verde.
