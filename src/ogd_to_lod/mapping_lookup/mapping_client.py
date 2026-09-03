from ogd_to_lod.mapping_lookup.matching import rank_templates
from ogd_to_lod.mapping_lookup.models import ReuseMappingTemplate
from ogd_to_lod.mapping_lookup.parser import collect_mapping_templates


class MappingService:

    def __init__(self, api: str | None):
        """Initialize with a api URL.

        Args:
            api: mapping templates api.
        """
        self._api = api

    def reuse_mapping(self, csv_schema: dict) -> ReuseMappingTemplate | None:

        if self._api is None:
            return None

        templates = collect_mapping_templates(self._api)

        top_matches = rank_templates(csv_schema, templates)

        if not top_matches:
            return None

        return ReuseMappingTemplate(template_matches=top_matches)


