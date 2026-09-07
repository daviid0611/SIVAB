"""
API REST de SIVAB (capa delgada sobre service.py).

Se ejecuta con:  uvicorn sivab.api:app --reload
Documentacion interactiva en:  http://127.0.0.1:8000/docs

La logica de negocio NO vive aqui. La capa web solo traduce HTTP a llamadas del
servicio y de vuelta. Error comun: escribir el UPDATE dentro del endpoint; cuando
eso pasa, la regla de negocio queda atada al framework y no se puede probar sin
levantar un servidor.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from sivab.db import Base, crear_esquema
from sivab.service import (AforoAgotado, aforo_disponible, crear_evento,
                           emitir_boleta, ultima_validacion, validar_boleta)

app = FastAPI(title='SIVAB', version='1.0',
              description='Sistema de Validación de Acceso y control '
                          'Anti-duplicación de Boletería')

db = Base('sivab.db')


@app.on_event('startup')
def inicializar():
    crear_esquema(db)


class EventoIn(BaseModel):
    nombre: str = Field(min_length=1)
    aforo_total: int = Field(gt=0)


class BoletaIn(BaseModel):
    evento_id: int
    asistente: str = Field(min_length=1)


class ValidacionIn(BaseModel):
    codigo: str
    evento_id: int
    punto_acceso: str = 'PUERTA-1'


@app.post('/eventos', status_code=201)
def post_evento(datos: EventoIn):
    evento_id = crear_evento(db, datos.nombre, datos.aforo_total)
    return {'evento_id': evento_id, 'aforo_disponible': aforo_disponible(db, evento_id)}


@app.post('/boletas', status_code=201)
def post_boleta(datos: BoletaIn):
    """RF-01 / CP-01: emision de boleta."""
    try:
        b = emitir_boleta(db, datos.evento_id, datos.asistente)
    except AforoAgotado as e:
        # 409 Conflict, no 400: la peticion es valida, el estado del recurso no lo permite.
        raise HTTPException(status_code=409, detail=str(e))
    return {'boleta_id': b['id'], 'codigo': b['codigo'], 'estado': b['estado']}


@app.post('/validaciones')
def post_validacion(datos: ValidacionIn):
    """RF-03 / CP-02 a CP-05: validacion en puerta."""
    res = validar_boleta(db, datos.codigo, datos.evento_id, datos.punto_acceso)
    if res.autorizado:
        return {'autorizado': True, 'motivo': res.motivo}

    # Hallazgo H-01: en el rechazo por reuso se devuelve cuando y donde se valido
    # antes, para que el personal de puerta resuelva la disputa en sitio.
    cuerpo = {'autorizado': False, 'motivo': res.motivo}
    if res.motivo == 'boleta_ya_utilizada':
        previa = ultima_validacion(db, datos.codigo)
        if previa:
            cuerpo['validada_en'] = previa['momento']
            cuerpo['punto_acceso_previo'] = previa['punto_acceso']
    return cuerpo


@app.get('/eventos/{evento_id}/aforo')
def get_aforo(evento_id: int):
    return {'evento_id': evento_id, 'disponible': aforo_disponible(db, evento_id)}
