"""
Capa de persistencia de SIVAB.

Se usa SQLite porque viene en la biblioteca estandar de Python y permite demostrar
el mecanismo anti-duplicacion sin instalar un motor externo. El mismo codigo funciona
en PostgreSQL o MySQL cambiando la conexion: la garantia no depende del motor sino
de la restriccion UNIQUE y del UPDATE condicional (ver service.py).
"""
import sqlite3
import threading

ESQUEMA = """
CREATE TABLE IF NOT EXISTS evento (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre        TEXT    NOT NULL,
    aforo_total   INTEGER NOT NULL CHECK (aforo_total > 0)
);

CREATE TABLE IF NOT EXISTS boleta (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    evento_id     INTEGER NOT NULL REFERENCES evento(id),
    asistente     TEXT    NOT NULL,
    codigo        TEXT    NOT NULL,
    estado        TEXT    NOT NULL DEFAULT 'emitida'
                  CHECK (estado IN ('emitida', 'consumida', 'anulada')),
    -- El codigo debe ser unico en TODO el sistema, no solo dentro del evento:
    -- si se repitiera entre eventos, un codigo filtrado del evento A podria
    -- colisionar con uno legitimo del evento B.
    UNIQUE (codigo)
);

CREATE TABLE IF NOT EXISTS auditoria (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    boleta_id     INTEGER REFERENCES boleta(id),
    codigo        TEXT    NOT NULL,
    evento_id     INTEGER,
    resultado     TEXT    NOT NULL,   -- autorizado | rechazado
    motivo        TEXT,
    punto_acceso  TEXT,
    momento       TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def conectar(ruta: str = 'sivab.db') -> sqlite3.Connection:
    """
    Abre una conexion lista para uso concurrente.

    Error comun 1: olvidar check_same_thread=False. Sin el, SQLite rechaza usar la
    conexion desde otro hilo y la prueba de concurrencia (CP-04) falla por un motivo
    que no tiene nada que ver con la logica de negocio.

    Error comun 2: dejar el journal por defecto. Con WAL, un lector no bloquea a un
    escritor y se reduce el "database is locked" bajo carga (CP-06).

    Error comun 3: dejar isolation_level en su valor por defecto. Python abriria las
    transacciones de forma implicita y perderiamos el control de cuando empieza cada
    una; aqui las abrimos explicitamente con BEGIN IMMEDIATE.
    """
    con = sqlite3.connect(ruta, check_same_thread=False, timeout=10)
    con.row_factory = sqlite3.Row
    con.isolation_level = None          # control manual de transacciones
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')  # SQLite las ignora si no se activan
    return con


def crear_esquema(con: sqlite3.Connection) -> None:
    con.executescript(ESQUEMA)


class Base:
    """
    Una conexion SQLite POR HILO.

    Por que existe esta clase: sqlite3 no permite dos transacciones simultaneas
    sobre la misma conexion ("cannot start a transaction within a transaction").
    Si CP-04 compartiera una sola conexion entre los 20 hilos, la prueba fallaria
    por un problema de infraestructura y no por la logica que queremos verificar.

    Error comun: concluir de ese fallo que "SQLite no soporta concurrencia" y
    quitar la prueba. Lo que no soporta es compartir la conexion; con una por hilo
    —que es justo lo que hace un pool de conexiones en produccion— la garantia de
    unicidad se cumple y CP-04 la demuestra.
    """

    def __init__(self, ruta: str = 'sivab.db'):
        self.ruta = ruta
        self._local = threading.local()
        self._todas = []
        self._lock = threading.Lock()

    @property
    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, 'con', None)
        if con is None:
            con = conectar(self.ruta)
            self._local.con = con
            with self._lock:
                self._todas.append(con)
        return con

    def execute(self, sql, parametros=()):
        return self._con.execute(sql, parametros)

    def executescript(self, sql):
        return self._con.executescript(sql)

    def close(self):
        with self._lock:
            for con in self._todas:
                con.close()
            self._todas.clear()
