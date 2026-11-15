"""Imports every ORM model so that Base.metadata is complete.

Used by Alembic and by the tests. Application code imports the models it needs directly;
it never imports this module. Each V11.3 module adds its `internal.models` here.
"""

from app import models as legacy_models  # noqa: F401
from app.modules.access.internal import models as access_models  # noqa: F401
from app.modules.ai_gateway.internal import models as ai_gateway_models  # noqa: F401
from app.modules.analytics.internal import models as analytics_models  # noqa: F401
from app.modules.assembly.internal import models as assembly_models  # noqa: F401
from app.modules.assets.internal import models as assets_models  # noqa: F401
from app.modules.assignments.internal import models as assignments_models  # noqa: F401
from app.modules.attempts.internal import models as attempts_models  # noqa: F401
from app.modules.audit.internal import models as audit_models  # noqa: F401
from app.modules.authoring.internal import models as authoring_models  # noqa: F401
from app.modules.billing.internal import models as billing_models  # noqa: F401
from app.modules.classes.internal import models as classes_models  # noqa: F401
from app.modules.config.internal import models as config_models  # noqa: F401
from app.modules.content.internal import models as content_models  # noqa: F401
from app.modules.crawler.internal import models as crawler_models  # noqa: F401
from app.modules.curriculum.internal import models as curriculum_models  # noqa: F401
from app.modules.dashboard.internal import models as dashboard_models  # noqa: F401
from app.modules.dedup.internal import models as dedup_models  # noqa: F401
from app.modules.delivery.internal import models as delivery_models  # noqa: F401
from app.modules.drive_sync.internal import models as drive_sync_models  # noqa: F401
from app.modules.error_notebook.internal import models as error_notebook_models  # noqa: F401
from app.modules.exam_import.internal import models as exam_import_models  # noqa: F401
from app.modules.explanations.internal import models as explanations_models  # noqa: F401
from app.modules.extraction.internal import models as extraction_models  # noqa: F401
from app.modules.flashcards.internal import models as flashcards_models  # noqa: F401
from app.modules.frameworks.internal import models as frameworks_models  # noqa: F401
from app.modules.generation.internal import models as generation_models  # noqa: F401
from app.modules.gradebook.internal import models as gradebook_models  # noqa: F401
from app.modules.grading.internal import models as grading_models  # noqa: F401
from app.modules.ingestion.internal import models as ingestion_models  # noqa: F401
from app.modules.knowledge_graph.internal import models as knowledge_graph_models  # noqa: F401
from app.modules.layout.internal import models as layout_models  # noqa: F401
from app.modules.learning_path.internal import models as learning_path_models  # noqa: F401
from app.modules.mastery.internal import models as mastery_models  # noqa: F401
from app.modules.notifications.internal import models as notifications_models  # noqa: F401
from app.modules.outbox.internal import models as outbox_models  # noqa: F401
from app.modules.parsers.internal import models as parsers_models  # noqa: F401
from app.modules.practice.internal import models as practice_models  # noqa: F401
from app.modules.privacy.internal import models as privacy_models  # noqa: F401
from app.modules.quality.internal import models as quality_models  # noqa: F401
from app.modules.questions.internal import models as questions_models  # noqa: F401
from app.modules.recommendations.internal import models as recommendations_models  # noqa: F401
from app.modules.review.internal import models as review_models  # noqa: F401
from app.modules.search.internal import models as search_models  # noqa: F401
from app.modules.taxonomy.internal import models as taxonomy_models  # noqa: F401
from app.modules.teacher_copilot.internal import models as teacher_copilot_models  # noqa: F401
from app.modules.tutor.internal import models as tutor_models  # noqa: F401
from app.modules.vertical_packs.internal import models as vertical_packs_models  # noqa: F401
from app.platform import models as platform_models  # noqa: F401
