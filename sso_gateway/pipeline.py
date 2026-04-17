import logging

logger = logging.getLogger(__name__)

SESSION_COURSE_KEY = 'sso_gateway_pending_course_id'
SESSION_SABERES_KEY = 'sso_gateway_saberes_data'

_SUPPORTED_BACKENDS = {'llavemx'}

# ---------------------------------------------------------------------------
# Mapeos saberes → códigos de ExtraInfo
# ---------------------------------------------------------------------------

_SABERES_ESTADO_TO_CODE = {
    'aguascalientes': '1',
    'baja california': '2',
    'baja california sur': '3',
    'campeche': '4',
    'chiapas': '5',
    'chihuahua': '6',
    'coahuila de zaragoza': '7',
    'colima': '8',
    'ciudad de méxico': '9',
    'durango': '10',
    'estado de méxico': '11',
    'guanajuato': '12',
    'guerrero': '13',
    'hidalgo': '14',
    'jalisco': '15',
    'michoacán de ocampo': '16',
    'morelos': '17',
    'nayarit': '18',
    'nuevo león': '19',
    'oaxaca': '20',
    'puebla': '21',
    'querétaro arteaga': '22',
    'querétaro': '22',
    'quintana roo': '23',
    'san luis potosí': '24',
    'sinaloa': '25',
    'sonora': '26',
    'tabasco': '27',
    'tamaulipas': '28',
    'tlaxcala': '29',
    'veracruz': '30',
    'yucatán': '31',
    'zacatecas': '32',
    'méxico': '11',
}

# Perfil de saberes → ocupacion ExtraInfo
_SABERES_OCUPACION_TO_CODE = {
    'estudiante':              '1',
    'docente':                 '2',
    'personal de apoyo':       '2',  # educativo
    'personal administrativo': '5',  # gobierno
    'público en general':      '7',  # otro
}

# Nivel educativo de saberes → maximo_nivel ExtraInfo
_SABERES_NIVEL_TO_CODE = {
    'inicial':                        '0',
    'preescolar':                     '0',
    'primaria':                       '1',
    'secundaria':                     '2',
    'bachillerato':                   '3',
    'técnico superior universitario': '4',
    'licenciatura':                   '5',
    'especialidad':                   '6',
    'maestría':                       '7',
    'doctorado':                      '8',
}


def _map_estado(texto):
    return _SABERES_ESTADO_TO_CODE.get((texto or '').lower().strip(), '0')


def _map_ocupacion(texto):
    return _SABERES_OCUPACION_TO_CODE.get((texto or '').lower().strip(), '0')


def _map_nivel(texto):
    return _SABERES_NIVEL_TO_CODE.get((texto or '').lower().strip(), '0')


def _set_if_empty(instance, field, new_value):
    """
    Escribe new_value en instance.field solo si el campo está vacío.
    Retorna True si escribió, False si no tocó nada.
    """
    current = getattr(instance, field, None)
    if new_value and not current:
        setattr(instance, field, new_value)
        return True
    return False


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def fill_extrainfo_from_details(backend, details, user=None, is_new=False, *args, **kwargs):
    """
    Rellena ExtraInfo campo por campo — solo escribe si el campo está vacío.
    Nunca sobreescribe datos existentes del usuario.

    Fuentes:
      - Llave MX (details[]): nombres, primer_apellido, segundo_apellido, curp, municipio
      - saberes (sesión):     estado, ocupacion, maximo_nivel, eres_docente
    """
    if getattr(backend, 'name', None) not in _SUPPORTED_BACKENDS:
        return

    if user is None:
        return

    try:
        from custom_reg_form.models import ExtraInfo
    except ImportError:
        logger.error("[SSOGateway] custom_reg_form no instalado.")
        return

    request = kwargs.get('request') or getattr(backend.strategy, 'request', None)
    saberes = (request.session.get(SESSION_SABERES_KEY) or {}) if request else {}
    source  = saberes.get('source', 'llavemx_direct')

    details      = details or {}
    es_extranjero = bool(details.get('es_extranjero', False))

    extrainfo, created = ExtraInfo.objects.get_or_create(user=user)

    # --- Datos de Llave MX ---
    nombres          = (details.get('nombres') or '').strip().upper()
    primer_apellido  = (details.get('primer_apellido') or '').strip().upper()
    segundo_apellido = (details.get('segundo_apellido') or '').strip().upper()
    curp             = (details.get('curp') or '').strip().upper()
    municipio        = (details.get('municipio') or '').strip().upper()
    pais             = '' if es_extranjero else 'MX'

    # --- Datos de saberes ---
    estado_code    = _map_estado(saberes.get('estado', ''))
    ocupacion_code = _map_ocupacion(saberes.get('ocupacion', ''))
    nivel_code     = _map_nivel(saberes.get('maximo_nivel', ''))
    eres_docente   = bool(saberes.get('eres_docente', False))

    # Extranjero fuerza valores específicos
    if es_extranjero:
        municipio   = 'FUERA DE MÉXICO'
        estado_code = '33'
        if not curp:
            curp = 'XEXX010101HDFXXX04'

    # --- Escribir campo por campo solo si está vacío ---
    changed = []

    if _set_if_empty(extrainfo, 'nombres', nombres):               changed.append('nombres')
    if _set_if_empty(extrainfo, 'primer_apellido', primer_apellido): changed.append('primer_apellido')
    if _set_if_empty(extrainfo, 'segundo_apellido', segundo_apellido): changed.append('segundo_apellido')
    if _set_if_empty(extrainfo, 'curp', curp):                     changed.append('curp')
    if _set_if_empty(extrainfo, 'municipio', municipio):           changed.append('municipio')
    if _set_if_empty(extrainfo, 'pais', pais):                     changed.append('pais')
    if estado_code    != '0' and _set_if_empty(extrainfo, 'estado', estado_code):       changed.append('estado')
    if ocupacion_code != '0' and _set_if_empty(extrainfo, 'ocupacion', ocupacion_code): changed.append('ocupacion')
    if nivel_code     != '0' and _set_if_empty(extrainfo, 'maximo_nivel', nivel_code):  changed.append('maximo_nivel')

    # eres_docente: solo activa, nunca desactiva
    if eres_docente and not extrainfo.eres_docente:
        extrainfo.eres_docente = True
        changed.append('eres_docente')

    if changed:
        extrainfo.save(update_fields=changed)

    _set_user_source(user, source)

    logger.info(
        "[SSOGateway] ExtraInfo %s user_id=%s campos=%s source=%s",
        'creado' if created else 'actualizado',
        user.id, changed or 'ninguno', source,
    )


def enroll_pending_course(backend, user=None, *args, **kwargs):
    """
    Inscribe al usuario en el curso guardado en sesión.
    Marca CourseEnrollmentAttribute con fuente de inscripción.
    """
    if user is None:
        return

    request = kwargs.get('request') or getattr(backend.strategy, 'request', None)
    if not request:
        return

    course_id = request.session.pop(SESSION_COURSE_KEY, None)
    saberes   = request.session.pop(SESSION_SABERES_KEY, {})

    if not course_id:
        return

    source = (saberes or {}).get('source', 'llavemx_direct')

    try:
        from opaque_keys import InvalidKeyError
        from opaque_keys.edx.keys import CourseKey
        from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAttribute

        course_key = CourseKey.from_string(course_id)
        enrollment, created = CourseEnrollment.enroll(user, course_key, check_access=True)

        CourseEnrollmentAttribute.objects.update_or_create(
            enrollment=enrollment,
            namespace='sso_gateway',
            name='source',
            defaults={'value': source},
        )

        logger.info(
            "[SSOGateway] %s user_id=%s course=%s source=%s",
            'Inscrito' if created else 'Ya inscrito',
            user.id, course_id, source,
        )
    except Exception as exc:
        logger.exception(
            "[SSOGateway] Error inscripción user_id=%s course=%s: %s",
            user.id, course_id, exc,
        )


def _set_user_source(user, source):
    """Guarda la fuente de registro en UserAttribute."""
    try:
        from common.djangoapps.student.models import UserAttribute
        UserAttribute.set_user_attribute(user, 'registration_source', source)
    except Exception as exc:
        logger.warning("[SSOGateway] No se pudo guardar UserAttribute: %s", exc)
