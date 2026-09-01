from ogd_to_lod.mapping_lookup.matching import rank_templates
from ogd_to_lod.mapping_lookup.models import ReuseContext
from ogd_to_lod.mapping_lookup.parser import collect_mapping_templates


class MappingService:

    def reuse_mapping(self, csv_schema: dict) -> ReuseContext | None:

        templates = collect_mapping_templates()

        top_matches = rank_templates(csv_schema, templates)

        if not top_matches:
            return None

        return ReuseContext(template_matches=top_matches)


