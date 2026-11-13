"""Imports every ORM model so that Base.metadata is complete.

Used by Alembic and by the tests. Application code imports the models it needs directly;
it never imports this module. Each V11.3 module adds its `internal.models` here.
"""

from app import models as legacy_models  # noqa: F401
from app.modules.outbox.internal import models as outbox_models  # noqa: F401
from app.platform import models as platform_models  # noqa: F401
