"""
Tutor plugin para SSO Gateway.

Configurar antes de usar:

    tutor config save \
      --set SSO_GATEWAY_CLIENT_DOMAINS='["https://saberesmx.sep.gob.mx"]' \
      --set SSO_GATEWAY_SABERES_PUBLIC_KEY="$(cat /ruta/saberes_public.pem)"

    tutor local restart lms
"""
from tutor import hooks

hooks.Filters.ENV_PATCHES.add_item((
    "openedx-lms-common-settings",
    """
import os
import json

# ----- Plataformas externas autorizadas (saberesmx y futuras) -----
_SSO_GATEWAY_DOMAINS = json.loads(
    os.environ.get("SSO_GATEWAY_CLIENT_DOMAINS", "[]")
)

# CORS: permite que plataformas externas consuman GET /api/courses/v1/courses/
CORS_ORIGIN_WHITELIST = list(CORS_ORIGIN_WHITELIST)
for _domain in _SSO_GATEWAY_DOMAINS:
    if _domain not in CORS_ORIGIN_WHITELIST:
        CORS_ORIGIN_WHITELIST.append(_domain)

# CSRF: cubre el POST /api/courses/v1/courses/ (filtrado de cursos)
CSRF_TRUSTED_ORIGINS = list(CSRF_TRUSTED_ORIGINS)
for _domain in _SSO_GATEWAY_DOMAINS:
    _host = _domain.replace("https://", "").replace("http://", "")
    if _host not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_host)

LOGIN_REDIRECT_WHITELIST = list(LOGIN_REDIRECT_WHITELIST)
for _domain in _SSO_GATEWAY_DOMAINS:
    _host = _domain.replace("https://", "").replace("http://", "")
    if _host not in LOGIN_REDIRECT_WHITELIST:
        LOGIN_REDIRECT_WHITELIST.append(_host)

# ----- JWT RS256: llave pública de saberesmx -----
# saberesmx firma sus tokens con su private key.
# Aquí solo necesitas su public key para verificar.
SSO_GATEWAY_SABERES_PUBLIC_KEY = os.environ.get("SSO_GATEWAY_SABERES_PUBLIC_KEY", "")
""",
))
