"""
Logica de negocio de SIVAB: emision y validacion de boletas.

Requisito critico del sistema (RF-03): una boleta emitida puede validarse
EXACTAMENTE UNA VEZ, incluso si dos validadores presentan el mismo codigo en el
mismo instante desde terminales distintas.
"""
import secrets
import sqlite3
from dataclasses import dataclass


class AforoAgotado(Exception):
    """Se intento emitir una boleta para un evento sin cupos disponibles."""


@dataclass(frozen=True)
class Resultado:
    """Respuesta de una validacion en puerta."""
    autorizado: bool
    motivo: str
    boleta_id: int | None = None


def _generar_codigo() -> str:
    """
    Codigo de un solo uso.

    Error comun: usar random.randint o uuid4 truncado para esto. random no es
    criptograficamente seguro y un codigo predecible se puede adivinar; secrets si
    lo es. 16 bytes en base32 dan un espacio de busqueda inviable de recorrer.
    """
    return secrets.token_hex(16).upper()


def crear_evento(con: sqlite3.Connection, nombre: str, aforo_total: int) -> int:
    cur = con.execute('INSERT INTO evento (nombre, aforo_total) VALUES (?, ?)',
                      (nombre, aforo_total))
    return cur.lastrowid


def aforo_disponible(con: sqlite3.Connection, evento_id: int) -> int:
    """Cupos que quedan = aforo total menos boletas vigentes (emitidas o consumidas)."""
    fila = con.execute(
        """SELECT e.aforo_total - COUNT(b.id) AS libres
           FROM evento e
           LEFT JOIN boleta b ON b.evento_id = e.id AND b.estado <> 'anulada'
           WHERE e.id = ?
           GROUP BY e.id""",
        (evento_id,)
    ).fetchone()
    return fila['libres'] if fila else 0


def emitir_boleta(con: sqlite3.Connection, evento_id: int, asistente: str) -> sqlite3.Row:
    """
    CP-01. Emite una boleta si el evento tiene aforo disponible.

    La verificacion de aforo y la insercion van dentro de la MISMA transaccion.
    Si se hicieran por separado, dos emisiones simultaneas podrian leer el mismo
    cupo libre y vender la ultima boleta dos veces.
    """
    con.execute('BEGIN IMMEDIATE')
    try:
        if aforo_disponible(con, evento_id) <= 0:
            con.execute('ROLLBACK')
            raise AforoAgotado(f'El evento {evento_id} no tiene cupos disponibles')
        codigo = _generar_codigo()
        cur = con.execute(
            'INSERT INTO boleta (evento_id, asistente, codigo) VALUES (?, ?, ?)',
            (evento_id, asistente, codigo)
        )
        boleta_id = cur.lastrowid
        con.execute('COMMIT')
    except AforoAgotado:
        raise
    except Exception:
        con.execute('ROLLBACK')
        raise
    return con.execute('SELECT * FROM boleta WHERE id = ?', (boleta_id,)).fetchone()


def validar_boleta(con: sqlite3.Connection, codigo: str, evento_id: int,
                   punto_acceso: str = 'PUERTA-1') -> Resultado:
    """
    CP-02 a CP-05. Valida una boleta en puerta y la invalida de forma atomica.

    ---------------------------------------------------------------------------
    ESTE ES EL NUCLEO DE LA GUIA. El mecanismo anti-duplicacion NO es un if en
    Python: es un UPDATE condicional en una sola sentencia.

        UPDATE boleta SET estado='consumida'
        WHERE codigo=? AND evento_id=? AND estado='emitida'

    El motor evalua la condicion y escribe dentro de la misma operacion atomica,
    asi que de dos peticiones simultaneas solo una obtiene rowcount == 1.

    ERROR COMUN (condicion de carrera TOCTOU, time-of-check to time-of-use):

        fila = SELECT estado FROM boleta WHERE codigo=?     # <-- lee
        if fila['estado'] == 'emitida':                     # <-- decide
            UPDATE boleta SET estado='consumida' ...        # <-- escribe

    Entre el SELECT y el UPDATE otro hilo puede colarse: ambos leen 'emitida',
    ambos deciden autorizar y la boleta entra dos veces. En pruebas manuales nunca
    se reproduce porque la ventana dura microsegundos; solo aparece con carga real.
    Ese fue el hallazgo H-02 de la revision por pares.

    PRECISION IMPORTANTE (verificada experimentalmente con CP-04):
    aqui hay DOS defensas, no una, y conviene no confundirlas.

      1. BEGIN IMMEDIATE toma el bloqueo de escritura al abrir la transaccion, asi
         que en SQLite las validaciones quedan serializadas. Si se quita la
         transaccion, CP-04 falla: en la prueba se autorizaron 15 de 20 accesos.
      2. El UPDATE condicional es la defensa que sigue funcionando en motores que
         NO serializan por defecto (PostgreSQL o MySQL en READ COMMITTED), donde
         dos transacciones pueden leer 'emitida' a la vez. Por eso el codigo no
         depende del comportamiento particular de SQLite.

    Conservar ambas es lo que permite migrar de SQLite a PostgreSQL sin reabrir
    el agujero.
    ---------------------------------------------------------------------------
    """
    con.execute('BEGIN IMMEDIATE')
    try:
        boleta = con.execute('SELECT * FROM boleta WHERE codigo = ?', (codigo,)).fetchone()

        if boleta is None:
            res = Resultado(False, 'codigo_inexistente')
        elif boleta['evento_id'] != evento_id:
            # CP-05: boleta valida, pero de otro evento.
            res = Resultado(False, 'evento_no_corresponde', boleta['id'])
        else:
            cur = con.execute(
                """UPDATE boleta SET estado = 'consumida'
                   WHERE codigo = ? AND evento_id = ? AND estado = 'emitida'""",
                (codigo, evento_id)
            )
            if cur.rowcount == 1:
                res = Resultado(True, 'acceso_autorizado', boleta['id'])
            else:
                # rowcount == 0: alguien mas la consumio primero, o esta anulada.
                res = Resultado(False, 'boleta_ya_utilizada', boleta['id'])

        con.execute(
            """INSERT INTO auditoria (boleta_id, codigo, evento_id, resultado, motivo, punto_acceso)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (res.boleta_id, codigo, evento_id,
             'autorizado' if res.autorizado else 'rechazado', res.motivo, punto_acceso)
        )
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise
    return res


def ultima_validacion(con: sqlite3.Connection, codigo: str) -> sqlite3.Row | None:
    """
    Dato que pedia el hallazgo H-01 de la revision por pares: cuando y donde se
    valido antes, para que el personal de puerta resuelva la disputa en sitio.
    """
    return con.execute(
        """SELECT momento, punto_acceso FROM auditoria
           WHERE codigo = ? AND resultado = 'autorizado'
           ORDER BY id DESC LIMIT 1""",
        (codigo,)
    ).fetchone()
