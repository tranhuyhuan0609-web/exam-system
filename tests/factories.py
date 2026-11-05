"""Test data helpers."""

from app.application.categories import create_category
from app.models import Category


async def make_tree(
    session, spec: dict, parent: Category | None = None, out: dict | None = None
) -> dict[str, Category]:
    """Create categories from a nested dict {"Vật lí 11": {"Chương 1": {"Bài 1": {}}}}; returns name -> Category.

    Roots get a slug derived from their position so several test subjects can coexist.
    """
    out = {} if out is None else out
    for index, (name, children) in enumerate(spec.items(), start=1):
        slug = None if parent else f"subject-{len(out) + index}-{abs(hash(name)) % 10_000}"
        node = await create_category(session, name, parent, slug=slug)
        out[name] = node
        await make_tree(session, children or {}, node, out)
    return out
