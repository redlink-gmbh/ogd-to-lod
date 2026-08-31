from dataclasses import dataclass, field


@dataclass
class Property:
    property_uri: str
    label: str | None          # rdfs:label
    role: str                  # "dimension" | "measure" | "attribute"
    datatype: str | None       # xsd:decimal, xsd:string,

@dataclass
class MappingTemplate:
    branch: str
    dsnr: str
    properties: list[Property]
    cube_shape: str


@dataclass
class ReuseContext:
    MappingTemplates: list[MappingTemplate] = field(default_factory=list)