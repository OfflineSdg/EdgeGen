import re
from typing import Any, Dict, List

from ..exception.error_codes import ErrorCode
from ..exception.exception import EvaluationError
from ..constants import COMPLETE_INCOMPLETE_GRADE_PATTERN, STATUS_200, COMPLETE_INCOMPLETE_PAIR


def get_majority_voted_score(score_to_vote_count: Dict[Any, int]):
    return max(score_to_vote_count, key=score_to_vote_count.get)

def get_config_or_default(config: Dict[str, Any], config_key: str, default: Any):
    if config and config_key in config:
        return config[config_key]
    return default

def match_to_int(completion, regex_pattern, grade_choices):
    """
    Parse a completion string and return binary score based on grade.

    Args:
        completion: The completion string containing a grade
        regex_pattern: Regex pattern to extract the grade
        grade_choices: List of 2 strings [positive_grade, negative_grade]
                      e.g., ["C", "I"] for Complete/Incomplete
                      e.g., ["A", "N"] for Applicable/Not applicable

    Returns:
        1 if grade matches positive_grade, 0 if matches negative_grade
    """
    match = re.search(regex_pattern, completion)
    if not match:
        raise EvaluationError(internal_code=ErrorCode.INVALID_JUDGE_RESPONSE_FORMAT_ERROR.value,
                              message=f"Could not find the judge grade from the completion: {completion}")
    grade = match.group(1).upper()  # Normalize to uppercase for comparison

    if grade == grade_choices[0].upper():
        correct_int = 1
    elif grade == grade_choices[1].upper():
        correct_int = 0
    else:
        raise EvaluationError(internal_code=ErrorCode.INVALID_JUDGE_RESPONSE_FORMAT_ERROR.value,
                              message=f"Invalid judge grade from the completion: {completion}")
    return correct_int

def map_subgoal_validations_to_binary_matrix(completions: List[str]) -> List[int]:
    binary_matrix = []
    for completion in completions:
        try:
            # Supports both C/I (completion) and A/N (applicability) grades
            score = match_to_int(completion, COMPLETE_INCOMPLETE_GRADE_PATTERN, COMPLETE_INCOMPLETE_PAIR)
            binary_matrix.append(score)
        except EvaluationError:
            # TODO: assume the completion includes the specific matching pattern
            continue  # Skip invalid responses
    return binary_matrix

def tally_votes(complete_cnt, incomplete_cnt, invalid_cnt, completions, regex_pattern, grade_choices):
    for completion in completions:
        try:
            score = match_to_int(completion, regex_pattern, grade_choices)
            if score == 1:
                complete_cnt += 1
            elif score == 0:
                incomplete_cnt += 1
        except EvaluationError:
            invalid_cnt += 1
    return complete_cnt, incomplete_cnt, invalid_cnt

def tally_judge_voting(complete_cnt, incomplete_cnt, invalid_cnt, judge_responses, regex_pattern, grade_choices):
    completions = []
    for judge_response in judge_responses:
        if judge_response.status != STATUS_200:
            invalid_cnt += 1
        else:
            completions.append(judge_response.completion)
    complete_cnt, incomplete_cnt, invalid_cnt = tally_votes(complete_cnt, incomplete_cnt, invalid_cnt, completions, regex_pattern, grade_choices)
    return complete_cnt, incomplete_cnt, invalid_cnt

def validate_inputs_for_pass_k_initialisation(k_value: int, num_trials: int):
        
    if not num_trials:
        raise EvaluationError(ErrorCode.INVALID_VALUE.value, "num_trials is invalid and must be provided.")
    
    if k_value <= 0:
            raise EvaluationError(ErrorCode.INVALID_VALUE.value, f"k_value ({k_value}) must be greater than 0")
        
    if num_trials <= 0:
        raise EvaluationError(ErrorCode.INVALID_VALUE.value, f"num_trials ({num_trials}) must be greater than 0")
    
    if k_value > num_trials:
        raise EvaluationError(ErrorCode.INVALID_VALUE.value, f"k_value ({k_value}) cannot be greater than num_trials ({num_trials})")