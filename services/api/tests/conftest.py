"""Variables de entorno de prueba: tienen que estar puestas ANTES de importar app.*,
porque `settings` se construye al importar."""
import os

os.environ.setdefault("CALLBOT_SSO_SECRET", "s" * 40)
os.environ.setdefault("ADMIN_USER", "operador")
os.environ.setdefault("ADMIN_PASSWORD", "clave-de-prueba-larga")
os.environ.setdefault("FRAME_ANCESTORS", "https://crm.santarosa.lat")
os.environ.setdefault("INTERNAL_TOKEN", "token-interno-de-prueba")
