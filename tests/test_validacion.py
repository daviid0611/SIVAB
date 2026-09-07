"""
Suite de pruebas automatizadas de SIVAB.

Implementa los casos CP-01 a CP-06 del Plan de Pruebas de la Sesion 4
(Guia 03 - Estandares y Metricas de Calidad de Software, ISO/IEC 29110:2016).
La documentacion de cada caso sigue la estructura de ISO/IEC/IEEE 29119-3.
"""
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from sivab.db import Base, crear_esquema
from sivab.service import (AforoAgotado, aforo_disponible, crear_evento,
                           emitir_boleta, ultima_validacion, validar_boleta)


@pytest.fixture()
def con(tmp_path):
    """
    Base de datos limpia por prueba.

    Error comun: usar ':memory:' aqui. Cada hilo abriria SU PROPIA base vacia y
    CP-04 pasaria siempre, sin probar nada. Se usa un archivo temporal real.
    """
    ruta = tmp_path / 'sivab_test.db'
    c = Base(str(ruta))
    crear_esquema(c)
    yield c
    c.close()


@pytest.fixture()
def evento(con):
    return crear_evento(con, 'Concierto de prueba', aforo_total=500)


# ---------------------------------------------------------------- CP-01
def test_cp01_emision_con_aforo_disponible(con, evento):
    """CP-01 | Severidad alta | Emitir una boleta para un evento con aforo libre."""
    libres_antes = aforo_disponible(con, evento)

    boleta = emitir_boleta(con, evento, asistente='David Zambrano')

    assert boleta['estado'] == 'emitida'
    assert len(boleta['codigo']) == 32          # 16 bytes en hexadecimal
    assert aforo_disponible(con, evento) == libres_antes - 1


def test_cp01b_emision_sin_aforo(con):
    """CP-01 (caso limite) | El aforo es una restriccion, no una sugerencia."""
    pequeno = crear_evento(con, 'Aforo minimo', aforo_total=1)
    emitir_boleta(con, pequeno, 'Asistente 1')

    with pytest.raises(AforoAgotado):
        emitir_boleta(con, pequeno, 'Asistente 2')


# ---------------------------------------------------------------- CP-02
def test_cp02_primera_validacion_autoriza(con, evento):
    """CP-02 | Severidad alta | La primera validacion autoriza y consume la boleta."""
    boleta = emitir_boleta(con, evento, 'Samuel Ossa')

    res = validar_boleta(con, boleta['codigo'], evento, punto_acceso='PUERTA-NORTE')

    assert res.autorizado is True
    assert res.motivo == 'acceso_autorizado'

    estado = con.execute('SELECT estado FROM boleta WHERE id = ?',
                         (boleta['id'],)).fetchone()['estado']
    assert estado == 'consumida'

    # El evento debe quedar registrado en auditoria (trazabilidad).
    registro = ultima_validacion(con, boleta['codigo'])
    assert registro is not None and registro['punto_acceso'] == 'PUERTA-NORTE'


# ---------------------------------------------------------------- CP-03
def test_cp03_segunda_validacion_rechaza(con, evento):
    """
    CP-03 | Severidad alta | La misma boleta NO puede validarse dos veces.

    Este es el caso que verifica la restriccion critica del sistema en su forma
    secuencial: alguien fotografia la boleta, la reenvia y un segundo asistente
    la presenta despues.
    """
    boleta = emitir_boleta(con, evento, 'Asistente con boleta duplicada')
    validar_boleta(con, boleta['codigo'], evento)

    res = validar_boleta(con, boleta['codigo'], evento, punto_acceso='PUERTA-SUR')

    assert res.autorizado is False
    assert res.motivo == 'boleta_ya_utilizada'

    # Hallazgo H-01 de la revision por pares: el rechazo debe permitir saber
    # cuando y donde se uso antes, para resolver la disputa en la puerta.
    previa = ultima_validacion(con, boleta['codigo'])
    assert previa['punto_acceso'] == 'PUERTA-1'
    assert previa['momento'] is not None

    # El estado no debe cambiar por un intento fallido.
    estado = con.execute('SELECT estado FROM boleta WHERE id = ?',
                         (boleta['id'],)).fetchone()['estado']
    assert estado == 'consumida'


# ---------------------------------------------------------------- CP-04
def test_cp04_validacion_concurrente_autoriza_solo_una(con, evento):
    """
    CP-04 | Severidad alta | Dos terminales presentan el mismo codigo a la vez.

    Este caso es el que NO existia en el plan original y que reporto el equipo
    revisor (hallazgo H-02). Sin el, una condicion de carrera dejaria entrar a dos
    personas con la misma boleta y las pruebas seguirian en verde.

    Se lanzan 20 validaciones simultaneas del mismo codigo: exactamente una debe
    quedar autorizada.
    """
    boleta = emitir_boleta(con, evento, 'Asistente concurrente')
    intentos = 20

    def validar(n):
        return validar_boleta(con, boleta['codigo'], evento, punto_acceso=f'PUERTA-{n}')

    with ThreadPoolExecutor(max_workers=intentos) as pool:
        resultados = list(pool.map(validar, range(intentos)))

    autorizados = [r for r in resultados if r.autorizado]
    assert len(autorizados) == 1, (
        f'Se autorizaron {len(autorizados)} accesos con la misma boleta. '
        'Hay una condicion de carrera en validar_boleta().'
    )
    assert all(r.motivo == 'boleta_ya_utilizada' for r in resultados if not r.autorizado)

    # La auditoria debe registrar los 20 intentos, no solo el exitoso.
    total = con.execute('SELECT COUNT(*) c FROM auditoria WHERE codigo = ?',
                        (boleta['codigo'],)).fetchone()['c']
    assert total == intentos


# ---------------------------------------------------------------- CP-05
def test_cp05_boleta_de_otro_evento_rechaza(con, evento):
    """CP-05 | Severidad media | Una boleta del evento A no sirve en el evento B."""
    otro = crear_evento(con, 'Evento distinto', aforo_total=100)
    boleta = emitir_boleta(con, evento, 'Asistente equivocado')

    res = validar_boleta(con, boleta['codigo'], otro)

    assert res.autorizado is False
    assert res.motivo == 'evento_no_corresponde'

    # La boleta debe seguir sirviendo en SU evento: un rechazo por evento
    # equivocado no puede quemarla.
    assert validar_boleta(con, boleta['codigo'], evento).autorizado is True


# ---------------------------------------------------------------- CP-06
def test_cp06_rendimiento_bajo_carga(con, evento):
    """
    CP-06 | Severidad alta | Rendimiento del servicio de validacion.

    ALCANCE REDUCIDO: el plan define 200 usuarios concurrentes durante 5 minutos.
    Eso corresponde a una prueba de carga con herramienta dedicada (Locust, k6),
    no a una prueba unitaria. Aqui se ejecuta una version reducida: 200 boletas
    distintas validadas concurrentemente, midiendo el percentil 95 de latencia y
    verificando que no aparezcan validaciones duplicadas bajo carga.
    """
    total = 200
    boletas = [emitir_boleta(con, evento, f'Asistente {i}') for i in range(total)]

    def cronometrar(b):
        inicio = time.perf_counter()
        res = validar_boleta(con, b['codigo'], evento)
        return (time.perf_counter() - inicio) * 1000, res

    with ThreadPoolExecutor(max_workers=20) as pool:
        medidas = list(pool.map(cronometrar, boletas))

    latencias = sorted(m[0] for m in medidas)
    p95 = latencias[int(len(latencias) * 0.95) - 1]
    autorizados = sum(1 for _, r in medidas if r.autorizado)

    assert autorizados == total, 'Alguna validacion legitima fue rechazada bajo carga'
    assert p95 < 2000, f'p95 = {p95:.1f} ms, supera el umbral de 2000 ms'

    consumidas = con.execute(
        "SELECT COUNT(*) c FROM boleta WHERE evento_id = ? AND estado = 'consumida'",
        (evento,)).fetchone()['c']
    assert consumidas == total

    print(f'\nCP-06 | mediana {statistics.median(latencias):.1f} ms | p95 {p95:.1f} ms')
