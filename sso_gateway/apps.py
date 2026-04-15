import logging

from django.apps import AppConfig

try:
    from openedx.core.djangoapps.plugins.constants import ProjectType
except ImportError:
    class ProjectType:
        LMS = "lms.djangoapp"

logger = logging.getLogger(__name__)


class SSOGatewayConfig(AppConfig):
    name = "sso_gateway"
    verbose_name = "SSO Gateway"
    _pipeline_patched = False

    plugin_app = {
        "url_config": {
            ProjectType.LMS: {
                "namespace": "sso_gateway",
                "relative_path": "urls",
            }
        }
    }

    def ready(self):
        try:
            self._inject_pipeline_steps()
        except Exception:
            logger.exception("[SSOGateway] Error during pipeline injection")

    def _inject_pipeline_steps(self):
        """
        Inject custom pipeline steps into SOCIAL_AUTH_PIPELINE.

        Positions:
          fill_extrainfo_from_details
            → AFTER  load_extra_data      (UserSocialAuth.extra_data is ready)
            → BEFORE ensure_user_information  (ExtraInfo exists before form check)

          enroll_pending_course
            → AFTER  ensure_user_information  (user fully registered/authenticated)
        """
        if self._pipeline_patched:
            return

        from django.conf import settings

        pipeline = list(getattr(settings, "SOCIAL_AUTH_PIPELINE", []))

        self._insert_step(
            pipeline,
            step="sso_gateway.pipeline.fill_extrainfo_from_details",
            after="social_core.pipeline.social_auth.load_extra_data",
            before="common.djangoapps.third_party_auth.pipeline.ensure_user_information",
        )

        self._insert_step(
            pipeline,
            step="sso_gateway.pipeline.enroll_pending_course",
            after="common.djangoapps.third_party_auth.pipeline.ensure_user_information",
            before=None,  # append al final si no hay nada después
        )

        setattr(settings, "SOCIAL_AUTH_PIPELINE", pipeline)
        SSOGatewayConfig._pipeline_patched = True
        logger.info("[SSOGateway] SOCIAL_AUTH_PIPELINE actualizado: %s", pipeline)

    @staticmethod
    def _insert_step(pipeline, step, after=None, before=None):
        """
        Inserta `step` en `pipeline` de forma idempotente.

        Estrategia:
          1. Si ya está → no hace nada.
          2. Si `after` existe → inserta inmediatamente después.
          3. Si `before` existe → inserta inmediatamente antes.
          4. Si ninguno existe → agrega al final.
        """
        if step in pipeline:
            logger.debug("[SSOGateway] Step ya presente, omitido: %s", step)
            return

        if after and after in pipeline:
            idx = pipeline.index(after)
            pipeline.insert(idx + 1, step)
            logger.info("[SSOGateway] Inserted '%s' after '%s'", step, after)
            return

        if before and before in pipeline:
            idx = pipeline.index(before)
            pipeline.insert(idx, step)
            logger.info("[SSOGateway] Inserted '%s' before '%s'", step, before)
            return

        pipeline.append(step)
        logger.warning(
            "[SSOGateway] Anchors not found for '%s' — appended at end. "
            "after=%s before=%s",
            step, after, before,
        )
