from typing import List

from ..exception.exception import InvalidInputValueError
from ..exception.error_codes import ErrorCode, SyntheticDataGenerationComponent
from ..models.agent_data_sample import SubGoal


class SyntheticDataInputValidator:
    """
    Validates the input for synthetic data generation, ensuring that all required fields are present and correctly formatted.
    """

    @staticmethod
    def validate_prd_content(prd_content: str) -> None:
        if not prd_content or not isinstance(prd_content, str):
            raise InvalidInputValueError(internal_code=ErrorCode.MISSING_VALUE.value, message="PRD content is missing or not a string.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)

    @staticmethod
    def validate_subgoals(subgoals: List[SubGoal]) -> None:
        if not subgoals or not isinstance(subgoals, list):
            raise InvalidInputValueError(internal_code=ErrorCode.MISSING_VALUE.value, message="Subgoals list is missing or not a list.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)
        for subgoal in subgoals:
            if not isinstance(subgoal, SubGoal):
                raise InvalidInputValueError(internal_code=ErrorCode.INVALID_VALUE.value, message=f"One of the items in the subgoal list is not an instance of SubGoal.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)
            if not subgoal.details or not isinstance(subgoal.details, str):
                raise InvalidInputValueError(internal_code=ErrorCode.MISSING_VALUE.value, message=f"One of the items in the subgoal list is missing details or details is not a string.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)

    @staticmethod
    def validate_tool_schemas(tool_schemas: List) -> None:
        if not tool_schemas or not isinstance(tool_schemas, list):
            raise InvalidInputValueError(internal_code=ErrorCode.MISSING_VALUE.value, message="Tool schemas list is missing or not a list.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)


    @staticmethod
    def validate_weight_probabilities(pattern_node_weight_prob: float, pattern_chain_weight_prob: float, pattern_dag_weight_prob: float) -> None:
        total = pattern_node_weight_prob + pattern_chain_weight_prob + pattern_dag_weight_prob
        if not (0 <= pattern_node_weight_prob <= 1):
            raise InvalidInputValueError(internal_code=ErrorCode.INVALID_VALUE.value, message="Pattern node weight probability must be between 0 and 1.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)
        if not (0 <= pattern_chain_weight_prob <= 1):
            raise InvalidInputValueError(internal_code=ErrorCode.INVALID_VALUE.value, message="Pattern chain weight probability must be between 0 and 1.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)
        if not (0 <= pattern_dag_weight_prob <= 1):
            raise InvalidInputValueError(internal_code=ErrorCode.INVALID_VALUE.value, message="Pattern DAG weight probability must be between 0 and 1.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)
        if abs(total - 1.0) > 1e-3:
            raise InvalidInputValueError(internal_code=ErrorCode.INVALID_VALUE.value, message="The sum of pattern weight probabilities must equal 1.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)


    @staticmethod
    def validate_max_path_length(max_path_length: int) -> None:
        if not isinstance(max_path_length, int) or max_path_length <= 0:
            raise InvalidInputValueError(internal_code=ErrorCode.INVALID_VALUE.value, message="Max path length must be a positive integer.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)

    @staticmethod
    def validate_subgoal_limit(subgoal_limit: int) -> None:
        if not isinstance(subgoal_limit, int) or subgoal_limit <= 0:
            raise InvalidInputValueError(internal_code=ErrorCode.INVALID_VALUE.value, message="Subgoal limit must be a positive integer.", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)
        if subgoal_limit > 3:
            raise InvalidInputValueError(internal_code=ErrorCode.INVALID_VALUE.value, message="Subgoal limit must be less than or equal to 3", component_code=SyntheticDataGenerationComponent.SYNTHETIC_DATA_GENERATION_ERROR_CODE.value)