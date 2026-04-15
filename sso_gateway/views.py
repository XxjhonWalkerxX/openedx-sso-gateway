import logging
from urllib.parse import urlencode

from django.shortcuts import redirect
from django.views import View

logger = logging.getLogger(__name__)

SESSION_KEY = 'sso_gateway_pending_course_id'


class EnrollRedirectView(View):
    """
    Punto de entrada desde plataformas externas (saberesmx u otras).

    Flujo completo:
      1. Plataforma externa genera link:
            https://cursos.aprende.gob.mx/enroll-redirect/?course_id=<course_key>

      2. Usuario no autenticado:
            → guarda course_id en sesión
            → redirige a Llave MX SSO con ?next= apuntando de regreso aquí
            → pipeline corre: fill_extrainfo_from_details + enroll_pending_course
            → Llave MX redirige de regreso a este view

      3. Usuario autenticado (regreso del SSO o acceso directo):
            → intenta inscripción como safety net (pipeline ya la hizo, es idempotente)
            → redirige a courseware del curso
    """

    def get(self, request):
        course_id = request.GET.get('course_id', '').strip()

        if not course_id:
            logger.warning("[SSOGateway] EnrollRedirectView: course_id ausente.")
            return redirect('/dashboard')

        if not request.user.is_authenticated:
            return self._redirect_to_sso(request, course_id)

        return self._enroll_and_redirect(request, course_id)

    def _redirect_to_sso(self, request, course_id):
        """Guarda course_id en sesión y manda al usuario a autenticarse con Llave MX."""
        request.session[SESSION_KEY] = course_id

        # next= apunta de regreso a este mismo view para continuar el flujo post-SSO
        next_url = request.get_full_path()
        params = urlencode({'next': next_url})

        logger.info(
            "[SSOGateway] Usuario no autenticado. course_id=%s guardado en sesión. "
            "Redirigiendo a SSO.",
            course_id,
        )
        return redirect(f'/auth/login/llavemx/?{params}')

    def _enroll_and_redirect(self, request, course_id):
        """
        Inscribe al usuario y redirige al courseware.

        Safety net: el pipeline step enroll_pending_course ya inscribió al usuario
        si venía del flujo SSO. Esta llamada es idempotente — no falla si ya está inscrito,
        y cubre el caso de usuarios autenticados que acceden directo a la URL.
        """
        try:
            from opaque_keys import InvalidKeyError
            from opaque_keys.edx.keys import CourseKey
            from common.djangoapps.student.models import CourseEnrollment

            course_key = CourseKey.from_string(course_id)
            CourseEnrollment.enroll(request.user, course_key, check_access=True)
            logger.info(
                "[SSOGateway] Inscripción confirmada. user_id=%s course=%s",
                request.user.id, course_id,
            )
        except (ImportError, Exception) as exc:
            logger.exception(
                "[SSOGateway] Error en inscripción. user_id=%s course=%s error=%s",
                getattr(request.user, 'id', '?'), course_id, exc,
            )

        return redirect(f'/courses/{course_id}/courseware/')
