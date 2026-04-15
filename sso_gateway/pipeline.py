import logging

logger = logging.getLogger(__name__)

# Backends SSO cuyos details[] mapean a campos de ExtraInfo.
# Extender aquí si se agrega otro proveedor gubernamental.
_SUPPORTED_BACKENDS = {'llavemx'}


def fill_extrainfo_from_details(backend, details, user=None, is_new=False, *args, **kwargs):
    """
    Rellena custom_reg_form.ExtraInfo con los datos que entregó el proveedor SSO.

    Cuándo corre:
      - Solo para backends en _SUPPORTED_BACKENDS.
      - Usuario nuevo (is_new=True): crea y llena ExtraInfo.
      - Usuario existente con curp vacío: actualiza campos faltantes.
      - Usuario existente con curp ya guardado: no sobreescribe.

    Campos que toma de details[] (mapeados por llavemx_oauth.get_user_details):
      nombres, primer_apellido, segundo_apellido, curp, municipio, es_extranjero

    Campos que NO se mapean desde Llave MX (el usuario los completa en el form):
      estado        → estadoNacimiento ≠ estado de residencia
      ocupacion, maximo_nivel, eres_docente, cct, funcion, nivel_Educativo, etc.
    """
    if getattr(backend, 'name', None) not in _SUPPORTED_BACKENDS:
        return

    if user is None:
        logger.warning("[SSOGateway] fill_extrainfo: no hay user en el pipeline, se omite.")
        return

    try:
        from custom_reg_form.models import ExtraInfo
    except ImportError:
        logger.error(
            "[SSOGateway] custom_reg_form no está instalado. "
            "fill_extrainfo_from_details no puede ejecutarse."
        )
        return

    details = details or {}
    es_extranjero = bool(details.get('es_extranjero', False))

    extrainfo, created = ExtraInfo.objects.get_or_create(user=user)

    # Usuario existente con curp ya guardado → respetar datos del usuario
    if not created and extrainfo.curp and not is_new:
        logger.debug(
            "[SSOGateway] ExtraInfo ya completo para user_id=%s, se omite actualización.",
            user.id,
        )
        return

    nombres          = (details.get('nombres') or '').strip().upper()
    primer_apellido  = (details.get('primer_apellido') or '').strip().upper()
    segundo_apellido = (details.get('segundo_apellido') or '').strip().upper()
    curp             = (details.get('curp') or '').strip().upper()
    municipio        = (details.get('municipio') or '').strip().upper()

    if es_extranjero:
        municipio = 'FUERA DE MÉXICO'
        if not curp:
            curp = 'XEXX010101HDFXXX04'

    extrainfo.nombres          = nombres or extrainfo.nombres
    extrainfo.primer_apellido  = primer_apellido or extrainfo.primer_apellido
    extrainfo.segundo_apellido = segundo_apellido or extrainfo.segundo_apellido
    extrainfo.curp             = curp or extrainfo.curp
    extrainfo.municipio        = municipio or extrainfo.municipio
    extrainfo.pais             = 'MX' if not es_extranjero else extrainfo.pais

    extrainfo.save(update_fields=[
        'nombres', 'primer_apellido', 'segundo_apellido',
        'curp', 'municipio', 'pais',
    ])

    logger.info(
        "[SSOGateway] ExtraInfo %s para user_id=%s | curp=%s",
        'creado' if created else 'actualizado',
        user.id, curp,
    )


def enroll_pending_course(backend, user=None, *args, **kwargs):
    """
    Inscribe al usuario en el curso guardado en sesión por EnrollRedirectView.

    La sesión guarda 'sso_gateway_pending_course_id' antes de salir al SSO.
    Este step corre al final del pipeline, cuando el usuario ya está autenticado.
    """
    if user is None:
        return

    request = kwargs.get('request') or getattr(backend.strategy, 'request', None)
    if not request:
        logger.warning("[SSOGateway] enroll_pending_course: no hay request, se omite.")
        return

    course_id = request.session.pop('sso_gateway_pending_course_id', None)
    if not course_id:
        return

    try:
        from opaque_keys import InvalidKeyError
        from opaque_keys.edx.keys import CourseKey
        from common.djangoapps.student.models import CourseEnrollment

        course_key = CourseKey.from_string(course_id)
        enrollment, created = CourseEnrollment.enroll(
            user, course_key, check_access=True
        )
        logger.info(
            "[SSOGateway] %s user_id=%s en course=%s",
            'Inscrito' if created else 'Ya inscrito',
            user.id, course_id,
        )
    except InvalidKeyError:
        logger.error("[SSOGateway] course_id inválido: %s", course_id)
    except Exception as exc:
        logger.exception(
            "[SSOGateway] Error al inscribir user_id=%s en course=%s: %s",
            user.id, course_id, exc,
        )
