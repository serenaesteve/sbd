"""Conector de SQLAlchemy para SBD.

    create_engine("sbd:///datos/biblioteca.base")          abre el fichero de la base
    create_engine("sbd+https://:CLAVE@sbd.miweb.es/base")  por la API de un `sbd servidor`

SBD no habla SQL; este paquete es el puente. SQLAlchemy genera SQL, `sql.py` lo traduce al
árbol de órdenes de SBD, y `dbapi.py` lo ejecuta con la interfaz estándar de Python (PEP 249).
"""
from sqlalchemy.dialects import registry

registry.register('sbd', 'sqlalchemy_sbd.dialecto', 'DialectoSBD')
registry.register('sbd.http', 'sqlalchemy_sbd.dialecto', 'DialectoSBDServidor')
registry.register('sbd.https', 'sqlalchemy_sbd.dialecto', 'DialectoSBDServidor')
