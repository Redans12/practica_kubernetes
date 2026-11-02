# D04 · Modelos de datos, consistencia y reglas de negocio

**Proyecto:** Gestión de canchas deportivas (pádel, fútbol 7 y tenis)
**Alumno:** Andres · No. de control 22121298 · Infraestructura de Servicios · Instituto Tecnológico de Morelia
**Estado:** v0.3 · modelo aprobado el 26-oct-2026 · implementado en `backend/` (migraciones `0001` y `0002`, reglas 1 a 4 con pruebas)

> Diagramas editables: `er-postgres.mmd` y `estados-reserva.mmd` (Mermaid). Imágenes: `er-postgres.png` y `estados-reserva.png`.

## 1. Fuente de verdad y reparto entre motores

| Motor | Rol | Qué guarda |
|---|---|---|
| PostgreSQL | **Fuente de verdad** | Usuarios, canchas, material, reservas, pagos, jugadores, lista de espera y outbox |
| MongoDB | Datos derivados y flexibles | Bitácora/historial de reservas y ficha flexible de cada cancha |

No se asume atomicidad entre ambos motores. Ver sección 5 (outbox).

## 2. Diccionario de datos: PostgreSQL

Los intentos de login se guardan en la base (no en memoria) para que el límite funcione igual con varias réplicas del backend.

Convenciones: claves primarias UUID; fechas `timestamptz` en UTC; bajas lógicas con `activo`/`activa`.

### `usuario`
| Campo | Tipo | Restricción | Descripción |
|---|---|---|---|
| id | uuid | PK | Identificador |
| email | varchar(255) | UNIQUE, NOT NULL | Correo de acceso |
| nombre | varchar(120) | NOT NULL | Nombre mostrado |
| password_hash | varchar(255) | NOT NULL | Hash de la contraseña (nunca texto plano) |
| rol | varchar(20) | CHECK IN (`jugador`,`administrador`) | Permisos |
| ranking_puntos | int | NOT NULL, default 1000, CHECK ≥ 0 | Puntaje para el nivel |
| bloqueado_hasta | timestamptz | NULL | Bloqueo de horario estelar (regla 1) |
| intentos_fallidos | int | NOT NULL, default 0, CHECK ≥ 0 | Intentos de login fallidos consecutivos |
| login_bloqueado_hasta | timestamptz | NULL | Bloqueo temporal de login (límite de intentos) |
| activo | boolean | NOT NULL, default true | Baja lógica |
| creado_en | timestamptz | NOT NULL | Alta |

### `cancha`
| Campo | Tipo | Restricción | Descripción |
|---|---|---|---|
| id | uuid | PK | |
| nombre | varchar(80) | UNIQUE, NOT NULL | |
| deporte | varchar(20) | CHECK IN (`padel`,`futbol7`,`tenis`) | |
| tarifa_hora | numeric(10,2) | CHECK > 0 | Costo por turno de una hora |
| deposito | numeric(10,2) | CHECK ≥ 0 | Depósito que se cobra al reservar |
| activa | boolean | default true | Baja lógica (no se borran canchas con historial) |
| creado_en | timestamptz | NOT NULL | |

### `material`
| Campo | Tipo | Restricción | Descripción |
|---|---|---|---|
| id | uuid | PK | |
| nombre | varchar(80) | UNIQUE, NOT NULL | |
| tipo | varchar(40) | NOT NULL | Raqueta, pelotas, chalecos, etc. |
| cantidad_total | int | CHECK ≥ 0 | Existencias |
| precio_renta | numeric(10,2) | CHECK ≥ 0 | |
| activo | boolean | default true | Baja lógica |

### `reserva`
| Campo | Tipo | Restricción | Descripción |
|---|---|---|---|
| id | uuid | PK | |
| usuario_id | uuid | FK → usuario | Quien reserva |
| cancha_id | uuid | FK → cancha | |
| periodo | tstzrange | NOT NULL | Turno de una hora `[inicio, fin)` |
| estado | varchar(20) | CHECK (ver sección 4) | Estado del ciclo de vida |
| es_partido_abierto | boolean | default false | Si requiere quórum |
| deposito | numeric(10,2) | CHECK ≥ 0 | Depósito cobrado |
| penalizacion | numeric(10,2) | default 0, CHECK ≥ 0 | Monto descontado por cancelación |
| expira_en | timestamptz | NULL | Solo en `borrador` (10 min) |
| creado_en / actualizado_en | timestamptz | NOT NULL | |

**Regla 4 (un turno, una reserva):**
```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE reserva ADD CONSTRAINT reserva_sin_traslape
  EXCLUDE USING gist (cancha_id WITH =, periodo WITH &&)
  WHERE (estado IN ('borrador','confirmada','en_juego'));
```
Los estados `completada`, `liberada_espera`, `reasignada` y `expirada` no bloquean el horario.

### `reserva_material`
| Campo | Tipo | Restricción |
|---|---|---|
| reserva_id | uuid | PK, FK → reserva (ON DELETE CASCADE) |
| material_id | uuid | PK, FK → material |
| cantidad | int | CHECK > 0 |

### `pago` (pasarela simulada)
| Campo | Tipo | Restricción |
|---|---|---|
| id | uuid | PK |
| reserva_id | uuid | FK → reserva |
| tipo | varchar(20) | CHECK IN (`deposito`,`penalizacion`) |
| monto | numeric(10,2) | CHECK > 0 |
| estado | varchar(20) | CHECK IN (`pendiente`,`aprobado`,`rechazado`) |
| creado_en | timestamptz | NOT NULL |

### `reserva_jugador` (quórum de partidos abiertos)
| Campo | Tipo | Restricción |
|---|---|---|
| reserva_id | uuid | PK, FK → reserva |
| usuario_id | uuid | PK, FK → usuario |
| confirmado | boolean | default false |

### `lista_espera`
| Campo | Tipo | Restricción |
|---|---|---|
| id | uuid | PK |
| cancha_id | uuid | FK → cancha |
| periodo | tstzrange | NOT NULL |
| usuario_id | uuid | FK → usuario |
| creado_en | timestamptz | NOT NULL (define el orden de llegada) |

### `outbox_evento`
| Campo | Tipo | Restricción |
|---|---|---|
| id | uuid | PK (se usa como `evento_id` en Mongo) |
| tipo | varchar(40) | NOT NULL |
| payload | jsonb | NOT NULL |
| creado_en | timestamptz | NOT NULL |
| procesado_en | timestamptz | NULL = pendiente |
| intentos | int | default 0 |

### Índices
| Índice | Para qué |
|---|---|
| `reserva (usuario_id)` | "Mis reservas" |
| `reserva (cancha_id)` + gist de la restricción | Disponibilidad y regla 4 |
| `reserva (estado, periodo)` | CronJobs de quórum y expiración; reporte |
| `lista_espera (cancha_id, periodo, creado_en)` | Siguiente en la fila |
| `outbox_evento (creado_en) WHERE procesado_en IS NULL` (parcial) | Pendientes de publicar |
| `usuario (email)` (UNIQUE) | Login |

### Transacciones
Requieren transacción ACID: crear reserva (reserva + materiales + outbox), pagar/confirmar (reserva + pago + outbox), cancelar (reserva + penalización + usuario + outbox) y reasignar (reserva original + reserva nueva + lista de espera + outbox).

### Cuentas de base de datos (mínimo privilegio)
| Cuenta | Uso | Privilegios |
|---|---|---|
| `canchas_migrador` | Job de migraciones | DDL sobre el esquema de la aplicación |
| `canchas_app` | Backend | SELECT/INSERT/UPDATE (DELETE solo donde aplique); sin DDL ni superusuario |
| `canchas_monitor` | Exporter | Solo lectura de estadísticas (rol `pg_monitor`) |
| `canchas_respaldo` | Respaldos | Solo lectura |

## 3. Modelo de MongoDB

### `bitacora_reserva`: historial consultable (visible en la pestaña "Historial")
```json
{
  "evento_id": "uuid (= outbox_evento.id)",
  "reserva_id": "uuid",
  "cancha_id": "uuid",
  "usuario_id": "uuid",
  "tipo": "cambio_estado | luz_token | penalizacion | pago",
  "de_estado": "confirmada",
  "a_estado": "liberada_espera",
  "detalle": { "regla": 1, "horas_anticipacion": 3.2, "penalizacion": 50 },
  "ts": "ISODate"
}
```
- **Índices:** `{ evento_id: 1 }` único (idempotencia), `{ reserva_id: 1, ts: -1 }`, `{ cancha_id: 1, ts: -1 }`.
- **Validación:** `$jsonSchema` con `evento_id`, `reserva_id`, `tipo` y `ts` obligatorios, y `tipo` dentro del enum.
- **Consultas representativas:** historial de una reserva ordenado por fecha; eventos de luz de una cancha en un rango; conteo de cancelaciones tardías por usuario.

### `ficha_cancha`: documento flexible (visible en el detalle de cancha para el administrador)
```json
{
  "cancha_id": "uuid",
  "dimensiones": { "largo_m": 20, "ancho_m": 10 },
  "piso": "cesped sintetico",
  "techada": false,
  "iluminacion": { "potencia_w": 800, "zonas": 4 },
  "mantenimientos": [ { "fecha": "ISODate", "tipo": "cambio de red", "nota": "..." } ],
  "extras": { "...": "campos propios de cada deporte" }
}
```
- **Índices:** `{ cancha_id: 1 }` único; `{ "mantenimientos.fecha": -1 }`.
- **Por qué no relacional:** cada deporte tiene atributos distintos y los mantenimientos crecen en lista.

## 4. Flujo con estados de la reserva

Estados: `borrador`, `confirmada`, `en_juego`, `completada`, `liberada_espera`, `reasignada`, `expirada`.

| Estado actual | Evento | Siguiente | Regla | Quién lo dispara |
|---|---|---|---|---|
| borrador | Pago del depósito aprobado | confirmada | 2 | Backend (el organizador) |
| borrador | `expira_en` vencido, o cancelación del borrador | expirada | Un turno, una reserva | CronJob / Jugador |
| confirmada | Check-in en puerta (desde 10 min antes del inicio) | en_juego | 3 | Administrador |
| en_juego | Fin del turno | completada | Actualiza ranking | CronJob |
| confirmada | Cancelación del jugador o no-show (15 min sin check-in) | liberada_espera | 1 | Jugador / CronJob |
| confirmada | Quórum incompleto 2 h antes | liberada_espera | 2 | CronJob |
| liberada_espera | Alguien en espera toma el turno | reasignada | 4 | Backend |

Al reasignar, la reserva original queda en `reasignada` (terminal) y se crea una reserva nueva en `borrador` para quien estaba en espera, que paga la suya. Cualquier transición que no esté en la tabla se rechaza.

## 5. Reglas de negocio → dónde se aplican

| Regla | Dónde vive | Verificación |
|---|---|---|
| 1. Cancelación progresiva | Servicio de cancelación (calcula 0/50/100 % del depósito: 4 h o más = 0 %, de 1 h a menos de 4 h = 50 %, menos de 1 h = 100 %); si <1 h, fija `usuario.bloqueado_hasta = ahora + 7 días` (aplica a horario estelar: **18:00 a 22:00, lunes a viernes**, hora de Morelia). Si cancela el administrador no hay penalización | Pruebas con cancelación a 5 h, 3 h y 30 min |
| 2. Quórum | CronJob a 2 h del inicio: cupo completo del deporte (pádel 4, tenis 2, fútbol 7 14) y 100 % de los jugadores con asistencia confirmada y pago simulado, o libera a espera. El organizador paga el depósito para confirmar el borrador; el quórum se revisa después, 2 h antes | Partido con un jugador sin confirmar |
| 3. Luz condicional | Endpoint de token: solo si `confirmada` y hora dentro de ±10 min del inicio; el token vence al terminar el turno; evento a bitácora | Token antes, durante y después de la ventana |
| 4. Un turno, una reserva | Restricción EXCLUDE en PostgreSQL | Dos inserciones traslapadas: la segunda falla |

## 6. Consistencia entre motores (patrón outbox)

1. En la misma transacción de PostgreSQL se guarda el cambio de negocio y una fila en `outbox_evento`.
2. Un publicador lee las filas pendientes (`procesado_en IS NULL`) y las inserta en `bitacora_reserva` con `evento_id = outbox_evento.id`.
3. Al confirmar la inserción, marca `procesado_en`. Si Mongo falla, la fila sigue pendiente y se reintenta (`intentos + 1`).
4. El índice único sobre `evento_id` hace el reintento idempotente: una entrega repetida se rechaza como duplicado y se trata como éxito.
5. **Reconciliación:** consulta periódica que compara eventos de PostgreSQL contra Mongo y reporta faltantes.

**Prueba a demostrar (E04/E10):** apagar MongoDB, hacer 5 cambios de estado, encender MongoDB y verificar que aparecen 5 eventos, sin pérdidas ni duplicados.

## 7. Semilla de datos (≥ 100 registros, sintéticos)

| Entidad | Cantidad aprox. |
|---|---|
| usuario | 20 (1 administrador, 19 jugadores; correos `@ejemplo.test`) |
| cancha | 9 (3 por deporte) |
| material | 8 |
| reserva | 70 en distintos estados |
| reserva_material, pago, reserva_jugador | derivados de las reservas |

Total de registros de negocio: más de 100. El script será idempotente (se puede correr varias veces sin duplicar).


## 8. Decisiones de implementación del backend (v0.3)

| Tema | Decisión |
|---|---|
| Turnos | Duración de 1 a 3 horas, que empiezan en punto; reservas con máximo 60 días de anticipación |
| Partido abierto | Se crea con al menos 3 h de anticipación para dejar tiempo de juntar al grupo antes del corte de 2 h |
| Cupo | Pádel 4, tenis 2, fútbol 7 14 jugadores |
| Pago | Pasarela simulada: el pago del depósito se registra en `pago`; la confirmación de un jugador invitado marca `reserva_jugador.confirmado` |
| Reasignación | Solo cuando alguien espera **exactamente** ese turno (misma cancha, mismo periodo); el primero en llegar tiene prioridad |
| Bajas | Lógicas (`activa`/`activo`). La cuenta de la aplicación solo tiene `DELETE` en `lista_espera`, `reserva_jugador` y `reserva_material` (migración 0002) |
| Ficha de cancha | Se guarda directamente en MongoDB (`PUT /api/canchas/{id}/ficha`); si Mongo no responde, esa operación devuelve 503 y el resto de la API sigue funcionando |
| Tareas | `python -m app.tareas expirar \| publicar \| quorum \| no-show \| finalizar \| reconciliar` (CronJobs) |
| Reloj | Todo el negocio usa un único reloj (`app/tiempo.py`), lo que permite probar las reglas por horas de anticipación sin esperar |
