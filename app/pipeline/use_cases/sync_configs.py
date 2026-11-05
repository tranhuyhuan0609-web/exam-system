"""configs/sites/<site_id>.yaml is the single source of truth; this upserts them into crawler_sites."""

from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CrawlerSite
from app.pipeline.domain.models import SiteConfig


def load_configs(directory: str | Path) -> list[SiteConfig]:
    configs = []
    for path in sorted(Path(directory).glob("*.yaml")):
        config = SiteConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if config.site_id != path.stem:
            raise ValueError(f"{path.name}: site_id must equal the file name ({config.site_id!r})")
        configs.append(config)
    return configs


async def sync_configs(session: AsyncSession, directory: str | Path) -> list[str]:
    """Returns the site ids whose stored config changed. Rows of removed files are kept (disable them in YAML)."""
    changed = []
    for config in load_configs(directory):
        data = config.model_dump(mode="json")
        site = await session.get(CrawlerSite, config.site_id)
        if site is None:
            session.add(CrawlerSite(site_id=config.site_id, name=config.name, enabled=config.enabled, config=data))
            changed.append(config.site_id)
        elif site.config != data or site.name != config.name or site.enabled != config.enabled:
            site.config, site.name, site.enabled = data, config.name, config.enabled
            changed.append(config.site_id)
    await session.commit()
    return changed
