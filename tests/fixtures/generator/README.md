# Exportaciones reales del generador

Dos archivos producidos el 26 de octubre de 2026 por el generador del frontend (`src/features/generator/search.ts`, SHA-256 `0d45228705e23089965b78ca3ca6f6edbb65b158500c34f15c71c6d88faddfc9`, método `proteinforge-candidates-v1`) con la misma serialización que su descarga, `JSON.stringify(result, null, 2)`. Se ejecutó localmente, sin red ni modelos. GRAVY [−0,5; 0,5] activo y masa desactivada en ambos.

- `default-export.json`: 40 residuos, 1.000 evaluaciones por brazo, semilla 42; 4.784 bytes, SHA-256 `c276bf594f50d9e89730ffc12ed3d96539bc08431828cf0081b07be5f1587de0`.
- `maximum-export.json`: 300 residuos, 5.000 evaluaciones por brazo, semilla 42; 7.531 bytes, SHA-256 `429e6cef00e312b470ec4f6f0d79f8703f3be0019eb14bd5d13e37bafd0ab5cd`.

Se conservan con LF (`.gitattributes`) porque el hash de los bytes exactos identifica cada ejecución guardada. Las pruebas que cambian semilla, candidatos o selección generan casos sintéticos de forma, no nuevas ejecuciones del generador. Ninguna secuencia implica función, plegamiento ni estabilidad.
