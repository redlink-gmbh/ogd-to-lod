from ogd_to_lod.mapping_lookup.models import MappingTemplate
from ogd_to_lod.mapping_lookup.parser import collect_mapping_templates


class MappingService:

    def reuse_mapping(self, csv_schema: dict) -> MappingTemplate:

        templates = collect_mapping_templates()

        print(templates)


        raise NotImplementedError("not implemented yet")

