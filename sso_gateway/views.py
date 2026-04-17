import logging
from urllib.parse import urlencode

import jwt
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.views import View

logger = logging.getLogger(__name__)

SESSION_COURSE_KEY = 'sso_gateway_pending_course_id'
SESSION_SABERES_KEY = 'sso_gateway_saberes_data'

_ERROR_TEMPLATE = 'sso_gateway/error.html'
_ERROR_MSG_GENERICO = 'No fue posible procesar tu solicitud. Intenta desde saberesmx nuevamente.'


def _verify_saberes_token(token):
    """
    Verifica JWT de saberesmx.
    Valida: firma RS256, iss, aud, exp, jti, course_id.
    Retorna (payload, None) si válido, (None, motivo) si inválido.
    El motivo es solo para logs — nunca se muestra al usuario.
    """
    public_key = getattr(settings, 'SSO_GATEWAY_SABERES_PUBLIC_KEY', None)
    if not public_key:
        logger.error("[SSOGateway] SSO_GATEWAY_SABERES_PUBLIC_KEY no configurado.")
        return None, "public key no configurada"

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer="saberesmx",
            audience="cursos.aprende.gob.mx",
            options={"require": ["exp", "iss", "aud", "jti", "course_id"]},
        )
        return payload, None

    except jwt.ExpiredSignatureError:
        logger.warning("[SSOGateway] Token expirado.")
        return None, "token expirado"
    except jwt.InvalidIssuerError:
        logger.warning("[SSOGateway] iss inválido.")
        return None, "iss inválido"
    except jwt.InvalidAudienceError:
        logger.warning("[SSOGateway] aud inválido.")
        return None, "aud inválido"
    except jwt.MissingRequiredClaimError as e:
        logger.warning("[SSOGateway] Claim faltante: %s", e)
        return None, f"claim faltante: {e}"
    except jwt.InvalidTokenError as e:
        logger.warning("[SSOGateway] Token inválido: %s", e)
        return None, f"token inválido: {e}"


def _course_exists(course_id):
    """Verifica que el curso existe en CourseOverview antes de procesar."""
    try:
        from opaque_keys.edx.keys import CourseKey
        from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
        course_key = CourseKey.from_string(course_id)
        return CourseOverview.objects.filter(id=course_key).exists()
    except Exception as e:
        logger.warning("[SSOGateway] Error validando course_id=%s: %s", course_id, e)
        return False


def _check_and_consume_jti(jti, ttl=600):
    """
    Anti-replay via Redis (django.core.cache).
    cache.add() es atómico — retorna False si la key ya existe.
    TTL 10 min > exp 5 min del JWT → cubre toda la ventana de validez.
    """
    cache_key = f"sso_gateway:jti:{jti}"
    return cache.add(cache_key, 1, timeout=ttl)


class EnrollRedirectView(View):
    """
    Punto de entrada desde plataformas externas (saberesmx u otras).

    Flujo con token JWT (saberesmx):
      GET /enroll-redirect/?token=<jwt_firmado_rs256>

    Flujo directo sin token (pruebas/manual):
      GET /enroll-redirect/?course_id=<id>
    """

    def get(self, request):
        token = request.GET.get('token', '').strip()

        if token:
            return self._handle_token_flow(request, token)

        course_id = request.GET.get('course_id', '').strip()
        if not course_id:
            logger.warning("[SSOGateway] Petición sin token ni course_id.")
            return redirect('/dashboard')

        if not request.user.is_authenticated:
            return self._redirect_to_sso(request, course_id, saberes_data=None)

        return self._enroll_and_redirect(request, course_id)

    # ------------------------------------------------------------------

    def _handle_token_flow(self, request, token):
        payload, error = _verify_saberes_token(token)
        if not payload:
            return self._bad_request(request, f"JWT rechazado: {error}")

        version = payload.get('version', '1')
        if version != '1':
            logger.warning(
                "[SSOGateway] version desconocida en JWT: %s — procesando igual.",
                version,
            )

        jti = payload.get('jti', '')
        if not _check_and_consume_jti(jti):
            return self._bad_request(request, f"jti ya consumido: {jti}")

        course_id = (payload.get('course_id') or '').strip()
        if not course_id:
            return self._bad_request(request, "course_id vacío en JWT")

        if not _course_exists(course_id):
            return self._bad_request(request, f"course_id no existe: {course_id}")

        saberes_data = {
            'jti':         payload.get('jti'),
            'version':     payload.get('version', '1'),
            'estado':      payload.get('estado', ''),
            'ocupacion':   payload.get('ocupacion', ''),
            'maximo_nivel': payload.get('maximo_nivel', ''),
            'eres_docente': bool(payload.get('eres_docente', False)),
            'source':      'saberesmx',
        }

        if not request.user.is_authenticated:
            return self._redirect_to_sso(request, course_id, saberes_data)

        return self._enroll_and_redirect(request, course_id)

    def _redirect_to_sso(self, request, course_id, saberes_data):
        request.session[SESSION_COURSE_KEY] = course_id

        if saberes_data:
            request.session[SESSION_SABERES_KEY] = saberes_data

        next_url = f'/enroll-redirect/?course_id={course_id}'
        params = urlencode({'next': next_url})

        logger.info(
            "[SSOGateway] → SSO. course_id=%s source=%s",
            course_id,
            saberes_data.get('source') if saberes_data else 'direct',
        )
        return redirect(f'/auth/login/llavemx/?{params}')

    def _enroll_and_redirect(self, request, course_id):
        try:
            from opaque_keys.edx.keys import CourseKey
            from common.djangoapps.student.models import CourseEnrollment

            course_key = CourseKey.from_string(course_id)
            CourseEnrollment.enroll(request.user, course_key, check_access=True)
            logger.info(
                "[SSOGateway] Inscripción confirmada. user_id=%s course=%s",
                request.user.id, course_id,
            )
        except Exception as exc:
            logger.exception(
                "[SSOGateway] Error inscripción. user_id=%s course=%s: %s",
                getattr(request.user, 'id', '?'), course_id, exc,
            )

        return redirect(f'/courses/{course_id}/courseware/')

    def _bad_request(self, request, log_msg):
        logger.warning("[SSOGateway] Bad request: %s", log_msg)
        return TemplateResponse(
            request,
            _ERROR_TEMPLATE,
            {'mensaje': _ERROR_MSG_GENERICO},
            status=400,
        )
