from pydantic import BaseModel

from tau2_optimized_harness_real_10.data_model.tasks import Task


class GetTasksRequest(BaseModel):
    """
    Request for getting tasks
    """

    domain: str


class GetTasksResponse(BaseModel):
    """
    Response for getting tasks
    """

    tasks: list[Task]
