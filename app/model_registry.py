"""Imports every ORM model so that Base.metadata is complete.

Used by Alembic and by the tests. Application code imports the models it needs directly;
it never imports this module. Each V11.3 module adds its `internal.models` here.
"""

from app import models as legacy_models  # noqa: F401
from app.modules.access.internal import models as access_models  # noqa: F401
from app.modules.ai_gateway.internal import models as ai_gateway_models  # noqa: F401
from app.modules.assets.internal import models as assets_models  # noqa: F401
from app.modules.audit.internal import models as audit_models  # noqa: F401
from app.modules.authoring.internal import models as authoring_models  # noqa: F401
from app.modules.config.internal import models as config_models  # noqa: F401
from app.modules.content.internal import models as content_models  # noqa: F401
from app.modules.curriculum.internal import models as curriculum_models  # noqa: F401
from app.modules.dedup.internal import models as dedup_models  # noqa: F401
from app.modules.exam_import.internal import models as exam_import_models  # noqa: F401
from app.modules.extraction.internal import models as extraction_models  # noqa: F401
from app.modules.ingestion.internal import models as ingestion_models  # noqa: F401
from app.modules.layout.internal import models as layout_models  # noqa: F401
from app.modules.outbox.internal import models as outbox_models  # noqa: F401
from app.modules.parsers.internal import models as parsers_models  # noqa: F401
from app.modules.privacy.internal import models as privacy_models  # noqa: F401
from app.modules.quality.internal import models as quality_models  # noqa: F401
from app.modules.questions.internal import models as questions_models  # noqa: F401
from app.modules.review.internal import models as review_models  # noqa: F401
from app.modules.taxonomy.internal import models as taxonomy_models  # noqa: F401
from app.platform import models as platform_models  # noqa: F401
