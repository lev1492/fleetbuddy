# Fabbrix Farm Manager – plugin per Ultimaker Cura (5.x, SDK 8).
# Aggiunge il dispositivo di uscita "Invia a Fabbrix Farm Manager" a tutte le stampanti, UltiMaker comprese.
from . import FabbrixOutputDevice


def getMetaData():
    return {}


def register(app):
    return {"output_device": FabbrixOutputDevice.FabbrixOutputDevicePlugin()}
