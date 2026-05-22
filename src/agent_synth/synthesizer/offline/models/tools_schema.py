from dataclasses import dataclass, field
from typing import List, Optional, Any
from enum import Enum
import json

from ..exception.exception import InvalidInputValueError
from ..exception.error_codes import ErrorCode, SyntheticDataGenerationComponent

class ParameterType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    NUMBER = "number"  # JSON Schema numeric type (alias for float)
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    ENUM = "enum"  # for parameters that must be one of a predefined set of string values
    NULL = "null"  # JSON Schema null type (no value / void)


@dataclass
class ParameterSchema:
    name: str
    type: ParameterType
    description: str
    required: bool = True
    enum: Optional[List[str]] = None  # allowed values for constrained parameters [business, economy, basic_economy]

    properties: Optional[List["ParameterSchema"]] = None #for object type
    items_schema: Optional["ParameterSchema"] = None #for array type, items should have the same schema

    # --- Getters ---
    def get_name(self) -> str:
        return self.name

    def get_type(self) -> ParameterType:
        return self.type

    def get_description(self) -> str:
        return self.description

    def get_required(self) -> bool:
        return self.required

    def get_enum(self) -> Optional[List[str]]:
        return self.enum

    def get_properties(self) -> Optional[List["ParameterSchema"]]:
        return self.properties

    def get_property_by_name(self, name: str) -> Optional["ParameterSchema"]:
        if self.properties:
            for p in self.properties:
                if p.name == name:
                    return p
        return None

    def get_items_schema(self) -> Optional["ParameterSchema"]:
        return self.items_schema

    # --- Setters ---
    def set_name(self, name: str):
        self.name = name

    def set_type(self, type: ParameterType):
        self.type = type

    def set_description(self, description: str):
        self.description = description

    def set_required(self, required: bool):
        self.required = required

    def set_enum(self, enum: Optional[List[str]]):
        self.enum = enum

    def set_properties(self, properties: Optional[List["ParameterSchema"]]):
        self.properties = properties

    def add_property(self, prop: "ParameterSchema"):
        if self.properties is None:
            self.properties = []
        self.properties.append(prop)

    def set_items_schema(self, items_schema: Optional["ParameterSchema"]):
        self.items_schema = items_schema

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "type": self.type.value,
            "description": self.description,
            "required": self.required,
        }
        if self.enum:
            result["enum"] = self.enum
        if self.properties:
            result["properties"] = [p.to_dict() for p in self.properties]
        if self.items_schema:
            result["items_schema"] = self.items_schema.to_dict()
        return result

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "ParameterSchema":
        """Create ParameterSchema from dictionary."""
        if "type" not in data:
            raise InvalidInputValueError(
                internal_code=ErrorCode.MISSING_VALUE.value,
                message="ParameterSchema requires 'type' field",
                component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value
            )

        properties = None
        if "properties" in data and data["properties"]:
            if isinstance(data["properties"], list):
                # List of parameter dicts
                properties = [cls.from_dict(p) for p in data["properties"]]
            elif isinstance(data["properties"], dict):
                # Dict with property names as keys
                properties = [
                    cls.from_dict({"name": prop_name, **prop_spec})
                    for prop_name, prop_spec in data["properties"].items()
                ]

        items_schema = None
        if "items_schema" in data and data["items_schema"]:
            items_schema = cls.from_dict(data["items_schema"])
        elif "items" in data and data["items"]:
            # Handle 'items' key as well
            items_schema = cls.from_dict(data["items"])

        return cls(
            name=data.get("name", ""),
            type=ParameterType(data["type"]),
            description=data.get("description", ""),
            required=data.get("required", True),
            enum=data.get("enum"),
            properties=properties,
            items_schema=items_schema
        )


@dataclass
class InputSchema:
    parameters: List[ParameterSchema]

    # --- Getters ---
    def get_parameters(self) -> List[ParameterSchema]:
        return self.parameters

    def get_parameter_by_name(self, name: str) -> Optional[ParameterSchema]:
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    # --- Setters ---
    def set_parameters(self, parameters: List[ParameterSchema]):
        self.parameters = parameters

    def add_parameter(self, param: ParameterSchema):
        self.parameters.append(param)

    def to_dict(self) -> dict:
        return {"parameters": [p.to_dict() for p in self.parameters]}

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


@dataclass
class OutputField:
    name: Optional[str]
    type: ParameterType
    description: str

    properties: Optional[List["OutputField"]] = None  # for object type
    items_schema: Optional["OutputField"] = None  # for array type, items should have the same schema

    # --- Getters ---
    def get_name(self) -> Optional[str]:
        return self.name

    def get_type(self) -> ParameterType:
        return self.type

    def get_description(self) -> str:
        return self.description

    def get_properties(self) -> Optional[List["OutputField"]]:
        return self.properties

    def get_property_by_name(self, name: str) -> Optional["OutputField"]:
        if self.properties:
            for p in self.properties:
                if p.name == name:
                    return p
        return None

    def get_items_schema(self) -> Optional["OutputField"]:
        return self.items_schema

    # --- Setters ---
    def set_name(self, name: Optional[str]):
        self.name = name

    def set_type(self, type: ParameterType):
        self.type = type

    def set_description(self, description: str):
        self.description = description

    def set_properties(self, properties: Optional[List["OutputField"]]):
        self.properties = properties

    def add_property(self, prop: "OutputField"):
        if self.properties is None:
            self.properties = []
        self.properties.append(prop)

    def set_items_schema(self, items_schema: Optional["OutputField"]):
        self.items_schema = items_schema

    def to_dict(self) -> dict:
        result: dict[str, Any] = {
            "type": self.type.value,
            "description": self.description
        }
        if self.name:
            result["name"] = self.name
        if self.properties:
            result["properties"] = [p.to_dict() for p in self.properties]
        if self.items_schema:
            result["items_schema"] = self.items_schema.to_dict()
        return result

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "OutputField":
        """Create OutputField from dictionary."""
        if "type" not in data:
            raise InvalidInputValueError(
                internal_code=ErrorCode.MISSING_VALUE.value,
                message="OutputField requires 'type' field",
                component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value
            )

        properties = None
        if "properties" in data and data["properties"]:
            if isinstance(data["properties"], list):
                # List of field dicts
                properties = [cls.from_dict(p) for p in data["properties"]]
            elif isinstance(data["properties"], dict):
                # Dict with property names as keys
                properties = [
                    cls.from_dict({"name": prop_name, **prop_spec})
                    for prop_name, prop_spec in data["properties"].items()
                ]

        items_schema = None
        if "items_schema" in data and data["items_schema"]:
            items_schema = cls.from_dict(data["items_schema"])
        elif "items" in data and data["items"]:
            # Handle 'items' key as well
            items_schema = cls.from_dict(data["items"])

        return cls(
            name=data.get("name"),
            type=ParameterType(data["type"]),
            description=data.get("description", ""),
            properties=properties,
            items_schema=items_schema
        )


@dataclass
class OutputSchema:
    fields: List[OutputField]

    # --- Getters ---
    def get_fields(self) -> List[OutputField]:
        return self.fields

    def get_field_by_name(self, name: str) -> Optional[OutputField]:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    # --- Setters ---
    def set_fields(self, fields: List[OutputField]):
        self.fields = fields

    def add_field(self, field_obj: OutputField):
        self.fields.append(field_obj)

    def to_dict(self) -> dict:
        return {"fields": [f.to_dict() for f in self.fields]}

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


@dataclass
class ToolSchema:
    name: str
    description: str
    input_schema: InputSchema
    output_schema: OutputSchema
    input_param_names: List[str] = field(default_factory=list)
    output_field_names: List[str] = field(default_factory=list)

    # --- Getters ---
    def get_name(self) -> str:
        return self.name

    def get_description(self) -> str:
        return self.description

    def get_input_schema(self) -> InputSchema:
        return self.input_schema

    def get_output_schema(self) -> OutputSchema:
        return self.output_schema

    def get_input_param_names(self) -> List[str]:
        return self.input_param_names

    def get_output_field_names(self) -> List[str]:
        return self.output_field_names

    def get_input_parameter_by_name(self, name: str) -> Optional[ParameterSchema]:
        return self.input_schema.get_parameter_by_name(name)

    def get_output_field_by_name(self, name: str) -> Optional[OutputField]:
        return self.output_schema.get_field_by_name(name)

    # --- Setters ---
    def set_name(self, name: str):
        self.name = name

    def set_description(self, description: str):
        self.description = description

    def set_input_schema(self, input_schema: InputSchema):
        self.input_schema = input_schema

    def set_output_schema(self, output_schema: OutputSchema):
        self.output_schema = output_schema

    def set_input_param_names(self, names: List[str]):
        self.input_param_names = names

    def set_output_field_names(self, names: List[str]):
        self.output_field_names = names

    # --- Type inference methods ---
    def get_input_types(self) -> List[str]:
        """Infer input types from parameter names (e.g. user_id → user)."""
        types = set()
        for param in self.input_schema.get_parameters():
            name = param.get_name().lower()
            if name.endswith("_id"):
                types.add(name[:-3])
            elif name.endswith("_number"):
                types.add(name[:-7])
        return list(types)

    def get_output_types(self) -> List[str]:
        """Derive output types from output field names."""
        return [f.get_name().lower() for f in self.output_schema.get_fields() if f.get_name()]

    def get_returns_description(self) -> str:
        """Serialize output schema as a returns string for LLM prompts."""
        fields = self.output_schema.get_fields()
        if len(fields) == 1:
            return f"{fields[0].get_name()} ({fields[0].get_type().value})"
        return ", ".join(f.get_name() or f.get_type().value for f in fields)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema.to_dict(),
            "output_schema": self.output_schema.to_dict(),
            "input_param_names": self.input_param_names,
            "output_field_names": self.output_field_names
        }

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "ToolSchema":
        """
        Create ToolSchema from dictionary. Supports multiple JSON formats.
        """
        if "name" not in data:
            raise InvalidInputValueError(
                internal_code=ErrorCode.MISSING_VALUE.value,
                message="ToolSchema requires 'name' field",
                component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value
            )

        # Parse input schema
        input_params = []
        input_param_names = []

        # Handle both formats: dict with parameter names as keys, or list of parameters
        input_data = data.get("input_schema", data.get("input", {}))

        if isinstance(input_data, dict) and "parameters" in input_data:
            # Format: {"parameters": [{...}, {...}]}
            for param in input_data["parameters"]:
                input_params.append(ParameterSchema.from_dict(param))
                input_param_names.append(param["name"])
        elif isinstance(input_data, dict):
            # Format: {"param1": {...}, "param2": {...}}
            for param_name, param_spec in input_data.items():
                param_dict = {"name": param_name, **param_spec}
                input_params.append(ParameterSchema.from_dict(param_dict))
                input_param_names.append(param_name)

        # Parse output schema
        output_fields = []
        output_field_names = []

        output_data = data.get("output_schema", data.get("output", {}))

        if isinstance(output_data, dict) and "fields" in output_data:
            # Format: {"fields": [{...}, {...}]}
            for output_field in output_data["fields"]:
                output_fields.append(OutputField.from_dict(output_field))
                if output_field.get("name"):
                    output_field_names.append(output_field["name"])
        elif isinstance(output_data, dict) and "properties" in output_data:
            # Format: {"type": "object", "properties": {...}} or properties as list
            if isinstance(output_data["properties"], list):
                # Properties as list: [{"name": "field1", ...}, ...]
                for output_field in output_data["properties"]:
                    output_fields.append(OutputField.from_dict(output_field))
                    if output_field.get("name"):
                        output_field_names.append(output_field["name"])
            elif isinstance(output_data["properties"], dict):
                # Properties as dict: {"field1": {...}, "field2": {...}}
                for field_name, field_spec in output_data["properties"].items():
                    field_dict = {"name": field_name, **field_spec}
                    output_fields.append(OutputField.from_dict(field_dict))
                    output_field_names.append(field_name)
        elif isinstance(output_data, dict) and "type" in output_data:
            # Simple scalar output
            output_fields.append(OutputField.from_dict({
                "name": data["name"] + "_result",
                **output_data
            }))
            output_field_names.append(data["name"] + "_result")

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input_schema=InputSchema(parameters=input_params),
            output_schema=OutputSchema(fields=output_fields),
            input_param_names=data.get("input_param_names", input_param_names),
            output_field_names=data.get("output_field_names", output_field_names)
        )
