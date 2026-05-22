# Generation config keys
BATCH_SIZE = "batch_size"
PATTERN_WEIGHT_NODE_PROBABILITY = "pattern_weight_node_probability"
PATTERN_WEIGHT_CHAIN_PROBABILITY = "pattern_weight_chain_probability"
PATTERN_WEIGHT_DAG_PROBABILITY = "pattern_weight_dag_probability"
MAX_PATH_LENGTH = "max_path_length"
DEPENDENCY_TYPE = "dependency_type"
# Violation generation config keys
SUBGOAL_LIMIT = "subgoal_limit"
RESOURCE = "resource"
TEMPORAL = "temporal"

# Tool chain verification config keys
VERIFY_TOOL_CHAIN = "verify_tool_chain"
VERIFY_ALIGNMENT = "verify_alignment"
VERIFY_EXECUTION = "verify_execution"
MAX_REFINEMENTS = "max_refinements"
MAX_REPAIR_ATTEMPTS = "max_repair_attempts"
CACHE_CONSTRAINTS = "cache_constraints"

# Generation defaults
DEFAULT_BATCH_SIZE = 5
DEFAULT_PATTERN_WEIGHT_NODE = 0.1
DEFAULT_PATTERN_WEIGHT_CHAIN = 0.45
DEFAULT_PATTERN_WEIGHT_DAG = 0.45
DEFAULT_MAX_PATH_LENGTH = 5
DEFAULT_DEPENDENCY_TYPE = RESOURCE

# Violation generation
DEFAULT_SUBGOAL_LIMIT = 3

# Tool chain verification defaults
DEFAULT_VERIFY_TOOL_CHAIN = True
DEFAULT_VERIFY_ALIGNMENT = True
DEFAULT_VERIFY_EXECUTION = True
DEFAULT_MAX_REFINEMENTS = 2
DEFAULT_MAX_REPAIR_ATTEMPTS = 2
DEFAULT_CACHE_CONSTRAINTS = True

from http import HTTPStatus

COMPLETE_INCOMPLETE_GRADE_PATTERN = r"(?i)GRADE\s*:\s*([CPI])(.*)$"
COMPLETE_INCOMPLETE_PAIR = ["C", "I"]

STATUS_200 = HTTPStatus.OK
STATUS_429 = HTTPStatus.TOO_MANY_REQUESTS
STATUS_500 = HTTPStatus.INTERNAL_SERVER_ERROR
STATUS_404 = HTTPStatus.NOT_FOUND


MAX_RETRY_ATTEMPTS_EXCEEDED = "Maximum retry attempts exceeded."
