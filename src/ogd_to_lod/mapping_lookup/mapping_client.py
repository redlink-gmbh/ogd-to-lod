from ogd_to_lod.config import MappingTemplateConfig
from ogd_to_lod.mapping_lookup.matching import rank_templates
from ogd_to_lod.mapping_lookup.models import ReuseMappingTemplate
from ogd_to_lod.mapping_lookup.parser import collect_mapping_templates


class MappingService:

    def __init__(self, config: MappingTemplateConfig | None):
        """Initialize with a config.

        Args:
            config: mapping config.
        """
        self._config = config

    def reuse_mapping(self, csv_schema: dict) -> ReuseMappingTemplate | None:

        if self._config is None:
            return None

        templates = collect_mapping_templates(self._config)

        top_matches = rank_templates(csv_schema, templates, self._config)

        if not top_matches:
            return None

        return ReuseMappingTemplate(template_matches=top_matches)


